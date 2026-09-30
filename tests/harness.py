"""Infraestrutura dos testes: arranca o servidor real numa pasta de dados temporária.

Nunca toca na pasta data/ do projeto. Funciona em Linux e Windows (só usa a stdlib).
"""

import copy
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
USER = "teste"
PASSWORD = "palavra-passe-teste-123"


def pdf_text(data):
    """Texto de um PDF gerado pela app (streams zlib com operadores Tj), sem dependências."""
    import re
    import zlib

    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        try:
            raw = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        for s in re.finditer(rb"\(((?:\\.|[^\\)])*)\) Tj", raw):
            out.append(re.sub(rb"\\(.)", rb"\1", s.group(1)).decode("cp1252"))
    return "\n".join(out)


def pdf_pages(data):
    import re

    return int(re.search(rb"/Count (\d+)", data).group(1))


def load_fixture(name="dataset.json"):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """Processo real de `python -m backend.main` com DATA_DIR temporário."""

    def __init__(self, user=USER, password=PASSWORD, env=None, data_dir=None, root=None):
        self.root = root or ROOT
        self.tmp = tempfile.mkdtemp(prefix="hr-test-")
        self.data_dir = data_dir or os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.user, self.password = user, password
        self.port = _free_port()
        self.env = dict(os.environ)
        for k in ("COOKIE_SECURE", "TRUSTED_PROXY_IPS", "SESSION_TTL"):
            self.env.pop(k, None)
        self.env.update(
            AUTH_USERNAME=user,
            AUTH_PASSWORD=password,
            DATA_DIR=self.data_dir,
            HOST="127.0.0.1",
            PORT=str(self.port),
            PYTHONIOENCODING="utf-8",
        )
        self.env.update(env or {})
        self.proc = None
        self.log_path = os.path.join(self.tmp, "server.log")

    def start(self):
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.main"],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=self._log,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("O servidor terminou ao arrancar:\n" + self.log())
            try:
                urllib.request.urlopen(self.url("/api/health"), timeout=1).read()
                return self
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("O servidor não respondeu em 15 s:\n" + self.log())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        if getattr(self, "_log", None):
            self._log.close()

    def restart(self):
        self.stop()
        return self.start()

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def log(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-4000:]
        except OSError:
            return ""


class Client:
    """Cliente HTTP com cookies, como o browser."""

    def __init__(self, server):
        self.server = server
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method, path, body=None, headers=None, raw=False, timeout=60):
        headers = dict(headers or {})
        if method not in ("GET", "HEAD"):
            # Como o browser: pedidos que alteram dados indicam a origem (P11).
            headers.setdefault("Origin", self.server.url(""))
        headers = {k: v for k, v in headers.items() if v is not None}
        data = None
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.server.url(path), data=data, method=method, headers=headers)
        try:
            resp = self.opener.open(req, timeout=timeout)
            status, payload, hdrs = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            status, payload, hdrs = e.code, e.read(), e.headers
        if raw:
            return status, payload, hdrs
        try:
            return status, json.loads(payload or b"{}")
        except ValueError:
            return status, payload

    def login(self, user=None, password=None, headers=None):
        return self.request(
            "POST",
            "/api/login",
            {
                "username": self.server.user if user is None else user,
                "password": self.server.password if password is None else password,
            },
            headers=headers,
        )

    def get_data(self):
        status, body = self.request("GET", "/api/data")
        assert status == 200, (status, body)
        return body["data"]

    def get_payload(self):
        """Resposta completa de GET /api/data (inclui metadados como a revisão)."""
        return self.request("GET", "/api/data")[1]

    def save(self, data):
        """Grava como um cliente atualizado: junta a revisão atual, salvo se o teste a indicar."""
        if isinstance(data, dict) and "baseRevision" not in data:
            data = dict(data, baseRevision=self.get_payload().get("revision"))
        return self.request("PUT", "/api/data", data)

    def upload(
        self,
        owner_type,
        owner_id,
        filename,
        content,
        mime="application/octet-stream",
        category="other",
        name=None,
    ):
        """Anexo enviado como corpo do pedido (P15), como faz o browser."""
        query = urllib.parse.urlencode({"ownerType": owner_type, "ownerId": owner_id, "category": category})
        headers = {
            "Content-Type": mime,
            "X-File-Name": urllib.parse.quote(filename),
            "X-Display-Name": urllib.parse.quote(filename if name is None else name),
        }
        return self.request("POST", f"/api/attachments?{query}", bytes(content), headers=headers)

    def pdf(self, path, today="2026-10-01", tz_offset=60):
        """Pede um relatório PDF como o browser (data e fuso do dispositivo; por omissão, Açores)."""
        return self.request("GET", f"{path}?today={today}&tzOffset={tz_offset}", raw=True)

    def snapshot(self):
        status, payload, _ = self.request("GET", "/api/backup/full", raw=True)
        assert status == 200, (status, payload[:200])
        return payload

    def prepare_backup(self):
        return self.request("POST", "/api/backup/prepare")

    def download_backup(self, token, dest=None):
        """Descarrega o backup preparado; com dest, grava em disco em blocos (como o browser)."""
        req = urllib.request.Request(self.server.url(f"/api/backup/download/{token}"))
        try:
            resp = self.opener.open(req, timeout=600)
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        if dest is None:
            return resp.status, resp.read()
        with open(dest, "wb") as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)
        return resp.status, dest

    def restore(self, zip_bytes, confirm=True, validate_only=False):
        """Restore em dois passos (P15): envio + validação, e depois confirmação com o código."""
        status, body = self.request(
            "POST", "/api/restore/upload", bytes(zip_bytes), headers={"Content-Type": "application/zip"}
        )
        return self._confirm(status, body, confirm, validate_only)

    def restore_file(self, path, confirm=True, validate_only=False):
        """Como restore(), mas envia o ficheiro a partir do disco, sem o ler todo para memória."""
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            req = urllib.request.Request(
                self.server.url("/api/restore/upload"),
                data=f,
                method="POST",
                headers={
                    "Content-Type": "application/zip",
                    "Content-Length": str(size),
                    "Origin": self.server.url(""),
                },
            )
            try:
                resp = self.opener.open(req, timeout=600)
                status, body = resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                status, body = e.code, json.loads(e.read() or b"{}")
        return self._confirm(status, body, confirm, validate_only)

    def _confirm(self, status, body, confirm, validate_only):
        if validate_only or status != 200:
            return status, body
        payload = {"token": body["token"], "confirm": True} if confirm else {"token": body["token"]}
        return self.request("POST", "/api/restore/confirm", payload)


class ServerTestCase(unittest.TestCase):
    """Cada teste recebe um servidor novo, com sessão iniciada e a fixture gravada."""

    load_dataset = True
    server_env = None
    password = PASSWORD

    def setUp(self):
        self.server = Server(password=self.password, env=self.server_env).start()
        self.addCleanup(self.server.cleanup)
        self.client = Client(self.server)
        if self.password is not None:
            status, body = self.client.login()
            if status != 200:
                self.skipTest(f"login indisponível neste cenário ({status})")
        if self.load_dataset:
            status, body = self.client.save(copy.deepcopy(load_fixture()))
            self.assertEqual(status, 200, body)

    def equipment(self, data, eid):
        return next(e for e in data["equipment"] if e["id"] == eid)

    def attachment_files(self):
        folder = os.path.join(self.server.data_dir, "attachments")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []
