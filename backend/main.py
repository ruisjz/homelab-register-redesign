import hashlib, ipaddress, json, logging, mimetypes, os, re, secrets, threading, time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs, quote
from .config import HOST, PORT, TRUSTED_PROXY_IPS, REQUEST_TIMEOUT
from .auth import valid, create, destroy, current, cookie, clear
from .report import equipment_report, maintenance_report
from .storage import (
    init,
    read_state,
    ConflictError,
    SchemaTooNewError,
    write,
    list_attachments,
    attachment_summary,
    save_attachment_file,
    get_attachment,
    update_attachment,
    delete_attachment,
    attachment_path,
    ATTACHMENT_OWNER_TYPES,
    create_full_snapshot_zip,
    validate_snapshot_zip,
    restore_snapshot_zip,
    heavy_operation,
    BusyError,
    RestoreInProgressError,
    new_tmp_path,
    ensure_free_space,
    cleanup_tmp,
    recover_interrupted_restore,
    CHUNK,
    MAX_ATTACHMENT_SIZE,
    MAX_SNAPSHOT_SIZE,
)

ROOT = Path(__file__).resolve().parent.parent
# P21: versão única da aplicação. É injetada no index.html e no sw.js ao servi-los
# (placeholder __APP_VERSION__) e devolvida em /api/health. Mudar só aqui a cada versão.
APP_VERSION = "fase5-2026-09-30"
TEMPLATED_STATIC = {"index.html", "sw.js"}
REPORT_EQUIPMENT_RE = re.compile(r"^/api/report/equipment/([A-Za-z0-9_-]{1,128})\.pdf$")
STATIC = ROOT / "static"
# Tempo máximo (s) de um snapshot validado ou backup preparado à espera. Configurável só para testes.
PENDING_TTL = int(os.getenv("PENDING_TTL", str(30 * 60)))
CONFLICT_MESSAGE = "Os dados foram alterados noutro dispositivo ou separador. A alteração não foi gravada."
MAX_JSON_SIZE = 16 * 1024 * 1024
# S12 (P25): antes do login só se aceita um corpo pequeno (utilizador e palavra-passe).
MAX_LOGIN_SIZE = 4 * 1024
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT = 15 * 60
# S12 (P25): teto de IPs seguidos no rate-limit, para a memória nunca crescer sem limite.
LOGIN_MAX_KEYS = 10_000
_login_failures = {}
_failures_lock = threading.RLock()  # P13: partilhado entre threads
log = logging.getLogger("homelab-register")


def _is_trusted_proxy(ip):
    return any(ip in net for net in TRUSTED_PROXY_IPS)


def client_ip(handler):
    """IP real do cliente (P10).

    Só se confia em X-Forwarded-For quando a ligação vem de um proxy de confiança.
    O cabeçalho é lido da direita para a esquerda, saltando os proxies de confiança:
    a parte da esquerda é escrita pelo cliente e pode ser forjada.
    """
    remote = handler.client_address[0]
    if not TRUSTED_PROXY_IPS:
        return remote
    try:
        if not _is_trusted_proxy(ipaddress.ip_address(remote)):
            return remote
    except ValueError:
        return remote
    forwarded = []
    for header in handler.headers.get_all("X-Forwarded-For") or []:
        forwarded += [v.strip() for v in header.split(",") if v.strip()]
    for value in reversed(forwarded):
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return remote
        if not _is_trusted_proxy(ip):
            return value
    return remote


def _origin_key(scheme, host, port):
    default = {"http": 80, "https": 443}.get(scheme)
    return (host.lower(), None if port in (None, default) else port)


def _request_host_keys(h):
    keys = set()
    values = [h.headers.get("Host", "")]
    try:
        if TRUSTED_PROXY_IPS and _is_trusted_proxy(ipaddress.ip_address(h.client_address[0])):
            values.append(h.headers.get("X-Forwarded-Host", ""))
    except ValueError:
        pass
    for value in values:
        value = value.split(",")[0].strip()
        if not value:
            continue
        try:
            u = urlparse("//" + value)
            if u.hostname:
                for scheme in ("http", "https"):
                    keys.add(_origin_key(scheme, u.hostname, u.port))
        except ValueError:
            continue
    return keys


def origin_allowed(h):
    """Proteção contra pedidos de outros sites (P11).

    1. Se o browser enviar Sec-Fetch-Site (todos os atuais enviam), só "same-origin" é aceite.
       Funciona mesmo que um proxy reescreva o Host.
    2. Sem Sec-Fetch-Site, o Origin tem de corresponder ao endereço acedido.
    3. Sem nenhum dos dois, o pedido é recusado: a app só é usada pelo browser.
    """
    site = (h.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site:
        return site == "same-origin"
    origin = (h.headers.get("Origin") or "").strip()
    if not origin or origin == "null":
        return False
    try:
        u = urlparse(origin)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        return _origin_key(u.scheme, u.hostname, u.port) in _request_host_keys(h)
    except ValueError:
        return False


def refuse_origin(h):
    log.warning(
        "Pedido recusado (origem): %s %s origin=%r sec-fetch-site=%r host=%r %s",
        h.command,
        urlparse(h.path).path,
        h.headers.get("Origin"),
        h.headers.get("Sec-Fetch-Site"),
        h.headers.get("Host"),
        login_context(h),
    )
    return send(h, 403, {"error": "Pedido recusado: não veio desta aplicação."})


def cleanup_login_failures(now=None):
    now = time.time() if now is None else now
    with _failures_lock:
        stale = [
            key
            for key, (failures, first, locked_until) in _login_failures.items()
            if (locked_until and locked_until <= now) or (not locked_until and now - first >= LOGIN_WINDOW)
        ]
        for key in stale:
            _login_failures.pop(key, None)


def login_allowed(handler):
    """S12 (P25): as falhas contam por IP, seja qual for o utilizador tentado; o nome enviado
    pelo cliente nunca fica guardado."""
    now = time.time()
    cleanup_login_failures(now)
    key = client_ip(handler)
    with _failures_lock:
        rec = _login_failures.get(key)
        if not rec:
            return True, 0
        failures, first, locked_until = rec
        if locked_until and locked_until > now:
            return False, int(locked_until - now) + 1
        if now - first >= LOGIN_WINDOW:
            _login_failures.pop(key, None)
    return True, 0


def login_failed(handler):
    now = time.time()
    key = client_ip(handler)
    with _failures_lock:
        if key not in _login_failures and len(_login_failures) >= LOGIN_MAX_KEYS:
            cleanup_login_failures(now)
            if len(_login_failures) >= LOGIN_MAX_KEYS:
                oldest = min(_login_failures, key=lambda k: _login_failures[k][1])
                _login_failures.pop(oldest, None)
        failures, first, locked_until = _login_failures.get(key, (0, now, 0))
        if now - first >= LOGIN_WINDOW:
            failures, first = 0, now
        failures += 1
        if failures >= LOGIN_MAX_FAILURES:
            locked_until = now + LOGIN_LOCKOUT
        else:
            locked_until = 0
        _login_failures[key] = (failures, first, locked_until)


def login_succeeded(handler):
    with _failures_lock:
        _login_failures.pop(client_ip(handler), None)


def login_context(h):
    """Para os logs: o IP usado no rate-limit, o IP da ligação e o X-Forwarded-For recebido.
    Na VPS, o IP da ligação é o do HAProxy: é esse que vai para TRUSTED_PROXY_IPS."""
    return (
        f"ip={client_ip(h)} ligação={h.client_address[0]} "
        f"x-forwarded-for={h.headers.get('X-Forwarded-For', '')!r}"
    )


def bad_request(h, path, e):
    """400 com mensagem útil em português, sem texto interno do Python (P13)."""
    if isinstance(e, json.JSONDecodeError):
        message = "O pedido não é JSON válido."
    elif isinstance(e, ValueError) and not isinstance(e, UnicodeError):
        message = str(e) or "Dados inválidos."
    else:
        message = "Dados inválidos."
    log.warning("Pedido inválido: %s %s (%s: %s)", h.command, path, type(e).__name__, e)
    return send(h, 400, {"error": message})


def server_error(h, path, e, message):
    """Regista o erro com traceback nos logs e responde com uma mensagem genérica (P13)."""
    if isinstance(e, TimeoutError):
        log.warning("Tempo esgotado a ler o pedido: %s %s (%s)", h.command, path, login_context(h))
        status, message = 408, "Pedido incompleto: tempo esgotado."
    else:
        log.exception("Erro interno: %s %s", h.command, path)
        status = 500
    try:
        send(h, status, {"error": message})
    except OSError:
        pass


def content_length(h):
    try:
        n = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        raise ValueError("Content-Length inválido.")
    if n < 0:
        raise ValueError("Content-Length inválido.")
    return n


def security_headers(h):
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("X-Frame-Options", "SAMEORIGIN")
    h.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    h.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")


def send(h, code, obj):
    b = json.dumps(obj, ensure_ascii=False).encode()
    h.send_response(code)
    security_headers(h)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(b)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(b)


def body(h, limit=MAX_JSON_SIZE):
    n = content_length(h)
    if n > limit:
        raise ValueError("O pedido JSON excede o limite permitido.")
    return json.loads(h.rfile.read(n) or b"{}")


def authed(h):
    if current(h.headers):
        return True
    send(h, 401, {"error": "Não autenticado."})
    return False


def receive_to_file(h, max_size, prefix, suffix=""):
    """P15: grava o corpo do pedido em data/tmp/, em blocos de 1 MiB, sem o ter todo em memória.

    Devolve (caminho, tamanho, sha256). Em qualquer falha, o temporário é apagado.
    """
    if "Content-Length" not in h.headers:
        raise ValueError("Falta o Content-Length do ficheiro.")
    n = content_length(h)
    if n == 0:
        raise ValueError("O ficheiro está vazio.")
    if n > max_size:
        raise ValueError(f"O ficheiro excede o limite de {max_size // (1024 * 1024)} MB.")
    ensure_free_space(n)
    path = new_tmp_path(prefix, suffix)
    digest = hashlib.sha256()
    try:
        with path.open("wb") as f:
            remaining = n
            while remaining:
                chunk = h.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError("Envio incompleto: a ligação foi interrompida.")
                digest.update(chunk)
                f.write(chunk)
                remaining -= len(chunk)
        return path, n, digest.hexdigest()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def send_file(h, path, content_type, extra_headers=None):
    """P15: envia um ficheiro em blocos de 1 MiB, com o Content-Length do stat."""
    size = path.stat().st_size
    h.send_response(200)
    security_headers(h)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(size))
    for k, v in (extra_headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    try:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                h.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        log.info("Transferência interrompida pelo cliente: %s", h.path)


def report_context(h):
    """P18/P19: a data de hoje e o fuso vêm do dispositivo (parâmetros today e tzOffset do browser)."""
    q = parse_qs(urlparse(h.path).query)
    try:
        offset = max(-14 * 60, min(14 * 60, int((q.get("tzOffset") or ["0"])[0])))
    except ValueError:
        offset = 0
    local = datetime.now(timezone.utc) - timedelta(minutes=offset)  # getTimezoneOffset(): UTC - local
    today = (q.get("today") or [""])[0]
    try:
        date.fromisoformat(today)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today):
            raise ValueError
    except ValueError:
        today = local.strftime("%Y-%m-%d")
    sign = "-" if offset > 0 else "+"
    hours, minutes = divmod(abs(offset), 60)
    return today, f"{local:%d/%m/%Y %H:%M} (UTC{sign}{hours:02d}:{minutes:02d})"


def pdf_headers(filename):
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-") or "relatorio.pdf"
    return {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    }


# P29: CSP mínima só na página da app. Os handlers onclick inline obrigam a manter scripts
# inline; fica bloqueado o resto (plugins, <base>, ser embebida noutro site). Os anexos não
# levam CSP: no Chrome, object-src 'none' impediria o leitor de PDF.
PAGE_CSP = "object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"


def send_templated(h, path, content_type, extra_headers=None):
    """P21: index.html e sw.js levam a versão da app; nunca ficam em cache HTTP (no-cache)."""
    data = path.read_text(encoding="utf-8").replace("__APP_VERSION__", APP_VERSION).encode("utf-8")
    h.send_response(200)
    security_headers(h)
    h.send_header("Content-Type", f"{content_type}; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-cache")
    if content_type == "text/html":
        h.send_header("Content-Security-Policy", PAGE_CSP)
    for k, v in (extra_headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(data)


def backup_headers(created_at):
    filename = f"homelab-register-snapshot-{created_at.replace(':', '-').replace('+00:00', 'Z')}.zip"
    return {
        "Cache-Control": "no-store",
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }


# P15: restore em dois passos (um único envio) e backup em dois passos (download em streaming).
_pending_lock = threading.Lock()
_pending_restore = {}
_pending_backups = {}


def _discard(entry):
    try:
        entry["path"].unlink(missing_ok=True)
    except OSError:
        pass


def _expire_pending(now=None):
    now = now or time.time()
    with _pending_lock:
        if _pending_restore and now - _pending_restore["created"] > PENDING_TTL:
            _discard(_pending_restore)
            _pending_restore.clear()
        for token in [t for t, e in _pending_backups.items() if now - e["created"] > PENDING_TTL]:
            _discard(_pending_backups.pop(token))


def public_summary(summary):
    return {k: v for k, v in summary.items() if k != "manifest"}


class Handler(BaseHTTPRequestHandler):
    # P13: uma ligação parada não prende uma thread para sempre.
    timeout = REQUEST_TIMEOUT

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/health":
                return send(
                    self,
                    200,
                    {"ok": True, "service": "homelab-register", "version": APP_VERSION},
                )
            if p == "/api/session":
                return send(self, 200, {"authenticated": bool(current(self.headers))})
            if p == "/api/attachments/summary":
                return authed(self) and send(self, 200, {"counts": attachment_summary()})
            if p == "/api/attachments":
                if not authed(self):
                    return
                q = parse_qs(urlparse(self.path).query)
                return send(
                    self,
                    200,
                    {
                        "attachments": list_attachments(
                            (q.get("ownerType") or [""])[0], (q.get("ownerId") or [""])[0]
                        )
                    },
                )
            if p.startswith("/api/attachments/") and p.endswith("/download"):
                if not authed(self):
                    return
                aid = p.split("/")[3]
                a = get_attachment(aid)
                if not a:
                    return send(self, 404, {"error": "Anexo não encontrado."})
                f = attachment_path(a["stored_name"])
                if f is None or not f.is_file():
                    return send(
                        self,
                        404,
                        {
                            "error": "O ficheiro deste anexo está em falta (não existia no snapshot restaurado ou foi removido do disco)."
                        },
                    )
                safe_inline_mimes = {
                    "application/pdf",
                    "text/plain",
                    "text/csv",
                    "application/json",
                    "image/jpeg",
                    "image/png",
                    "image/gif",
                    "image/webp",
                    "image/bmp",
                }
                disp = "inline" if a["mime_type"] in safe_inline_mimes else "attachment"
                return send_file(
                    self,
                    f,
                    a["mime_type"],
                    {"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(a['original_name'])}"},
                )
            if p == "/api/backup/full":
                # S13 (P29): um GET que cria um snapshot não pode ser disparado por outro site
                # (o cookie SameSite=Lax segue em navegações de topo).
                if (self.headers.get("Sec-Fetch-Site") or "").strip().lower() in ("cross-site", "same-site"):
                    return refuse_origin(self)
                if not authed(self):
                    return
                archive = new_tmp_path("backup-full", ".zip")
                try:
                    # O lock cobre só a criação; o envio corre fora dele (o ficheiro é deste pedido).
                    with heavy_operation():
                        manifest = create_full_snapshot_zip(archive)
                    return send_file(self, archive, "application/zip", backup_headers(manifest["created_at"]))
                finally:
                    archive.unlink(missing_ok=True)
            if p.startswith("/api/backup/download/"):
                if not authed(self):
                    return
                token = p.rsplit("/", 1)[-1]
                _expire_pending()
                with _pending_lock:
                    entry = _pending_backups.pop(token, None)
                if not entry or not entry["path"].is_file():
                    return send(
                        self, 404, {"error": "Este backup já foi descarregado ou expirou. Cria outro."}
                    )
                try:
                    return send_file(
                        self, entry["path"], "application/zip", backup_headers(entry["created_at"])
                    )
                finally:
                    _discard(entry)
            if p == "/api/report/maintenance.pdf" or REPORT_EQUIPMENT_RE.match(p):
                # P18: PDF gerado no servidor para data/tmp/, enviado em blocos e apagado a seguir.
                if not authed(self):
                    return
                data, _ = read_state()
                today, generated = report_context(self)
                match = REPORT_EQUIPMENT_RE.match(p)
                if match and not any(e["id"] == match.group(1) for e in data["equipment"]):
                    return send(self, 404, {"error": "Equipamento não encontrado."})
                path = new_tmp_path("report", ".pdf")
                try:
                    if match:
                        name = next(e["name"] for e in data["equipment"] if e["id"] == match.group(1))
                        equipment_report(path, data, match.group(1), list_attachments(), today, generated)
                        filename = f"relatorio-{name}-{today}.pdf"
                    else:
                        maintenance_report(path, data, today, generated)
                        filename = f"manutencoes-{today}.pdf"
                    return send_file(self, path, "application/pdf", pdf_headers(filename))
                finally:
                    path.unlink(missing_ok=True)
            if p == "/api/data":
                if not authed(self):
                    return
                data, revision = read_state()
                return send(self, 200, {"data": data, "revision": revision})
            if p == "/":
                p = "/static/index.html"
            if p.startswith("/static/"):
                rel = unquote(p[len("/static/") :])
                f = (STATIC / rel).resolve()
                if f.is_relative_to(STATIC.resolve()) and f.is_file():
                    extra = {"Service-Worker-Allowed": "/"} if f.name == "sw.js" else {}
                    if f.name in TEMPLATED_STATIC and f.parent == STATIC.resolve():
                        return send_templated(self, f, mimetypes.guess_type(str(f))[0] or "text/plain", extra)
                    return send_file(
                        self, f, mimetypes.guess_type(str(f))[0] or "application/octet-stream", extra
                    )
            send(self, 404, {"error": "Não encontrado."})
        except RestoreInProgressError as e:
            send(self, 503, {"error": str(e)})
        except BusyError as e:
            send(self, 409, {"error": str(e)})
        except Exception as e:
            server_error(self, p, e, "Erro interno.")

    def do_POST(self):
        p = urlparse(self.path).path
        if not origin_allowed(self):
            return refuse_origin(self)
        try:
            if p == "/api/login":
                x = body(self, MAX_LOGIN_SIZE)
                if not isinstance(x, dict):
                    raise ValueError("Dados inválidos.")
                username = x.get("username", "")
                password = x.get("password", "")
                allowed, retry = login_allowed(self)
                if not allowed:
                    log.warning("Login bloqueado (demasiadas tentativas): %s", login_context(self))
                    self.send_response(429)
                    security_headers(self)
                    self.send_header("Retry-After", str(retry))
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {"error": "Demasiadas tentativas de login. Tente novamente mais tarde."},
                            ensure_ascii=False,
                        ).encode()
                    )
                    return
                if not valid(username, password):
                    log.warning("Login falhado: utilizador=%r %s", str(username)[:80], login_context(self))
                    login_failed(self)
                    return send(self, 401, {"error": "Utilizador ou palavra-passe inválidos."})
                login_succeeded(self)
                log.info("Login: %s", login_context(self))
                t = create()
                self.send_response(200)
                security_headers(self)
                cookie(self, t)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return
            if p == "/api/logout":
                destroy(current(self.headers))
                self.send_response(200)
                security_headers(self)
                clear(self)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return
            if not authed(self):
                return
            if p == "/api/restore/upload":
                # P15: um único envio. O ZIP fica em data/tmp/ já validado, à espera de confirmação.
                with heavy_operation():
                    path, size, _ = receive_to_file(self, MAX_SNAPSHOT_SIZE, "restore-upload", ".zip")
                    try:
                        summary = validate_snapshot_zip(path)
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
                    token = secrets.token_urlsafe(24)
                    with _pending_lock:
                        if _pending_restore:
                            _discard(_pending_restore)
                        _pending_restore.clear()
                        _pending_restore.update(
                            token=token, path=path, summary=summary, created=time.time(), stat=path.stat()
                        )
                return send(self, 200, {"ok": True, "token": token, "summary": public_summary(summary)})
            if p == "/api/restore/confirm":
                x = body(self)
                if not isinstance(x, dict) or x.get("confirm") is not True:
                    return send(self, 400, {"error": "É necessária confirmação para restaurar o snapshot."})
                _expire_pending()
                with _pending_lock:
                    entry = (
                        dict(_pending_restore) if _pending_restore.get("token") == x.get("token") else None
                    )
                    if entry:
                        _pending_restore.clear()
                gone = {"error": "O snapshot validado já não está disponível. Volta a escolher o ficheiro."}
                if not entry or not entry["path"].is_file():
                    return send(self, 400, gone)
                try:
                    st = entry["path"].stat()
                    if (st.st_size, st.st_mtime_ns) != (entry["stat"].st_size, entry["stat"].st_mtime_ns):
                        return send(self, 400, gone)
                    with heavy_operation():
                        summary = restore_snapshot_zip(entry["path"], summary=entry["summary"])
                finally:
                    _discard(entry)
                return send(self, 200, {"ok": True, "summary": public_summary(summary)})
            if p == "/api/backup/prepare":
                # P15: o ZIP é criado em data/tmp/; os avisos (anexos em falta) chegam à app e
                # o browser descarrega depois o ficheiro em streaming, direto para o disco.
                with heavy_operation():
                    path = new_tmp_path("backup", ".zip")
                    try:
                        manifest = create_full_snapshot_zip(path)
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
                token = secrets.token_urlsafe(24)
                _expire_pending()
                with _pending_lock:
                    while len(_pending_backups) >= 2:
                        _discard(_pending_backups.pop(next(iter(_pending_backups))))
                    _pending_backups[token] = {
                        "path": path,
                        "created": time.time(),
                        "created_at": manifest["created_at"],
                    }
                return send(
                    self,
                    200,
                    {
                        "ok": True,
                        "token": token,
                        "size": path.stat().st_size,
                        "attachment_count": manifest["application_state"]["attachment_count"],
                        "missing_attachments": manifest["missing_attachments"],
                    },
                )
            if p == "/api/attachments":
                # P15: o corpo do pedido é o próprio ficheiro; os metadados vêm na query e em cabeçalhos.
                q = parse_qs(urlparse(self.path).query)
                owner_type = (q.get("ownerType") or [""])[0]
                owner_id = (q.get("ownerId") or [""])[0]
                if owner_type not in ATTACHMENT_OWNER_TYPES:
                    raise ValueError("Destino de anexo inválido.")
                path, size, _ = receive_to_file(self, MAX_ATTACHMENT_SIZE, "attachment", ".part")
                try:
                    attachment = save_attachment_file(
                        owner_type,
                        owner_id,
                        unquote(self.headers.get("X-File-Name", "")),
                        (self.headers.get("Content-Type") or "").split(";")[0].strip().lower(),
                        path,
                        unquote(self.headers.get("X-Display-Name", "")),
                        (q.get("category") or ["other"])[0],
                    )
                finally:
                    path.unlink(missing_ok=True)
                return send(self, 201, {"attachment": attachment})
            if p == "/api/data":
                d = body(self)
                if not isinstance(d, dict):
                    raise ValueError("Os dados têm de ser um objeto JSON válido.")
                base = d.get("baseRevision")
                if not isinstance(base, int) or isinstance(base, bool):
                    return send(self, 409, {"error": CONFLICT_MESSAGE, "code": "conflict"})
                try:
                    ts, revision = write(d, base)
                except ConflictError as e:
                    return send(
                        self, 409, {"error": CONFLICT_MESSAGE, "code": "conflict", "revision": e.current}
                    )
                return send(self, 200, {"ok": True, "updatedAt": ts, "revision": revision})
            send(self, 404, {"error": "Não encontrado."})
        except RestoreInProgressError as e:
            send(self, 503, {"error": str(e)})
        except BusyError as e:
            send(self, 409, {"error": str(e)})
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            bad_request(self, p, e)
        except Exception as e:
            server_error(self, p, e, "Não foi possível processar o pedido.")

    def do_DELETE(self):
        p = urlparse(self.path).path
        if not origin_allowed(self):
            return refuse_origin(self)
        try:
            if not authed(self):
                return
            if p.startswith("/api/attachments/"):
                aid = p.split("/")[3]
                if delete_attachment(aid):
                    return send(self, 200, {"ok": True})
                return send(self, 404, {"error": "Anexo não encontrado."})
            send(self, 404, {"error": "Não encontrado."})
        except RestoreInProgressError as e:
            send(self, 503, {"error": str(e)})
        except Exception as e:
            server_error(self, p, e, "Não foi possível apagar o anexo.")

    def do_PUT(self):
        p = urlparse(self.path).path
        if not origin_allowed(self):
            return refuse_origin(self)
        if p.startswith("/api/attachments/"):
            try:
                if not authed(self):
                    return
                aid = p.split("/")[3]
                x = body(self)
                a = update_attachment(aid, x.get("displayName"), x.get("category"))
                if a:
                    return send(self, 200, {"attachment": a})
                return send(self, 404, {"error": "Anexo não encontrado."})
            except RestoreInProgressError as e:
                return send(self, 503, {"error": str(e)})
            except (ValueError, TypeError, KeyError, AttributeError) as e:
                return bad_request(self, p, e)
            except Exception as e:
                return server_error(self, p, e, "Não foi possível atualizar o anexo.")
        if p == "/api/data":
            return self.do_POST()
        # P11: PUT só existe para gravar dados e anexos (antes, qualquer PUT caía no POST).
        send(self, 405, {"error": "Método não permitido."})


if __name__ == "__main__":
    # P13: registo para stderr, que o Docker mostra em `docker logs`.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        recover_interrupted_restore()
        cleanup_tmp()
        init()
    except SchemaTooNewError as e:
        log.error("%s", e)
        raise SystemExit(1)
    log.info("Homelab Register a escutar em %s:%s", HOST, PORT)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
