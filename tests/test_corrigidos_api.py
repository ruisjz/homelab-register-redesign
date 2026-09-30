"""Bugs da auditoria já corrigidos (lado da API).

Estes testes estiveram em test_bugs_conhecidos_api.py como falha esperada.
Passaram quando a correção entrou e ficam aqui para garantir que o bug não volta.
O ID e a proposta que o corrigiu estão no nome de cada teste.
"""

import copy
import io
import json
import os
import subprocess
import sys
import socket
import sqlite3
import tempfile
import time
import urllib.request
import unittest
import zipfile

from harness import Client, Server, ServerTestCase, load_fixture


class TestCatalogoSemEquipamentos(ServerTestCase):
    load_dataset = False
    PLANS = {
        "equipment": [],
        "preventivePlans": [
            {"id": "p-1", "name": "Limpeza", "intervalMonths": 6, "active": True, "notes": ""}
        ],
    }

    def test_B05_preventivas_sobrevivem_ao_reinicio(self):
        self.assertEqual(self.client.save(copy.deepcopy(self.PLANS))[0], 200)
        self.server.restart()
        client = Client(self.server)
        client.login()
        self.assertEqual(len(client.get_data()["preventivePlans"]), 1)

    def test_B05_restore_sem_equipamentos_mantem_preventivas(self):
        self.client.save(copy.deepcopy(self.PLANS))
        snap = self.client.snapshot()
        self.client.save({"equipment": [], "preventivePlans": []})
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.client.get_data()["preventivePlans"]), 1)

    def test_P07_base_antiga_sem_marca_initialized_nao_e_apagada(self):
        self.client.save(copy.deepcopy(load_fixture()))
        self.server.stop()
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        con.execute("DELETE FROM app_meta WHERE key='initialized'")
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        self.assertEqual(len(client.get_data()["equipment"]), 3)

    def test_P07_primeiro_arranque_com_base_vazia_aplica_o_seed(self):
        data = self.client.get_data()
        self.assertEqual(data, {"equipment": [], "preventivePlans": []})


class TestResetRemovido(ServerTestCase):
    def test_B12_P06_reset_ja_nao_existe(self):
        before = self.client.get_data()
        for method in ("POST", "PUT"):
            with self.subTest(method=method):
                status, _ = self.client.request(method, "/api/reset")
                self.assertIn(status, (404, 405))
        self.assertEqual(self.client.get_data(), before)


class TestBackupAntesDoRestore(ServerTestCase):
    def backups(self):
        d = os.path.join(self.server.data_dir, "backups")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_B02_P05_restore_guarda_zip_do_estado_anterior(self):
        snap = self.client.snapshot()
        data = self.client.get_data()
        data["equipment"][0]["name"] = "ESTADO ANTES DO RESTORE"
        self.client.save(data)
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.backups(), [body["summary"]["safety_backup"]])
        with open(os.path.join(self.server.data_dir, "backups", self.backups()[0]), "rb") as f:
            status, body = self.client.restore(f.read(), validate_only=True)
        self.assertEqual(status, 200, "o backup automático tem de ser um snapshot válido")

    def test_P05_ficam_so_os_5_mais_recentes(self):
        snap = self.client.snapshot()
        names = [self.client.restore(snap)[1]["summary"]["safety_backup"] for _ in range(7)]
        self.assertEqual(self.backups(), sorted(names)[-5:])

    def test_P05_P16_anexo_em_falta_nao_impede_o_backup_de_seguranca(self):
        snap = self.client.snapshot()
        self.client.upload("equipment", "eq-srv01", "fica.txt", b"fica")
        gone = self.client.upload("equipment", "eq-srv01", "falta.txt", b"falta")[1]["attachment"]["id"]
        os.remove(os.path.join(self.server.data_dir, "attachments", gone))
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        name = body["summary"]["safety_backup"]
        self.assertTrue(name.endswith(".zip"), "o backup de segurança é sempre um ZIP")
        with open(os.path.join(self.server.data_dir, "backups", name), "rb") as f:
            status, check = self.client.restore(f.read(), validate_only=True)
        self.assertEqual(status, 200, check)
        self.assertEqual([m["id"] for m in check["summary"]["missing_attachments"]], [gone])

    def test_P05_restore_invalido_nao_cria_backup(self):
        self.assertEqual(self.client.restore(b"nao e zip")[0], 400)
        self.assertEqual(self.backups(), [])


class TestRevisao(ServerTestCase):
    def test_B03_P04_cliente_desatualizado_nao_apaga_trabalho_de_outro(self):
        """P04: o PUT envia a revisão lida (baseRevision); se os dados mudaram, 409."""
        phone = Client(self.server)
        phone.login()
        stale = phone.get_payload()
        desk_data = self.client.get_data()
        desk_data["equipment"].append(
            {
                "id": "eq-novo",
                "name": "NAS novo",
                "components": [],
                "maintenance": [],
                "preventivePlanIds": [],
            }
        )
        self.assertEqual(self.client.save(desk_data)[0], 200)
        self.client.upload("equipment", "eq-novo", "fatura.pdf", b"%PDF", "application/pdf")
        body = copy.deepcopy(stale["data"])
        body["baseRevision"] = stale.get("revision")
        body["equipment"][0]["location"] = "alterado no telemóvel"
        phone.save(body)
        after = self.client.get_data()
        self.assertIn("eq-novo", [e["id"] for e in after["equipment"]])
        self.assertEqual(len(self.attachment_files()), 1)

    def test_P04_revisao_aumenta_a_cada_gravacao(self):
        first = self.client.get_payload()["revision"]
        self.assertIsInstance(first, int)
        status, body = self.client.save(self.client.get_data())
        self.assertEqual((status, body["revision"]), (200, first + 1))
        self.assertEqual(self.client.get_payload()["revision"], first + 1)

    def test_P04_revisao_desatualizada_e_recusada_sem_alterar_nada(self):
        stale = self.client.get_payload()
        self.client.save(self.client.get_data())
        before = self.client.get_data()
        body = dict(copy.deepcopy(stale["data"]), baseRevision=stale["revision"])
        body["equipment"][0]["name"] = "gravação antiga"
        status, answer = self.client.request("PUT", "/api/data", body)
        self.assertEqual(status, 409)
        self.assertEqual(answer["code"], "conflict")
        self.assertEqual(self.client.get_data(), before)

    def test_P04_pedido_sem_revisao_e_recusado(self):
        before = self.client.get_data()
        status, _ = self.client.request("PUT", "/api/data", copy.deepcopy(before))
        self.assertEqual(status, 409)
        self.assertEqual(self.client.get_data(), before)

    def test_P04_restore_invalida_revisoes_anteriores(self):
        snap = self.client.snapshot()
        stale = self.client.get_payload()
        self.assertEqual(self.client.restore(snap)[0], 200)
        body = dict(copy.deepcopy(stale["data"]), baseRevision=stale["revision"])
        self.assertEqual(self.client.request("PUT", "/api/data", body)[0], 409)

    def test_P04_gravacoes_simultaneas_so_uma_passa(self):
        import threading

        base = self.client.get_payload()
        results = []

        def attempt(name):
            other = Client(self.server)
            other.login()
            body = dict(copy.deepcopy(base["data"]), baseRevision=base["revision"])
            body["equipment"][0]["name"] = name
            results.append(other.request("PUT", "/api/data", body)[0])

        threads = [threading.Thread(target=attempt, args=(f"cliente {i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409, 409, 409])


class TestLogin(ServerTestCase):
    def test_S01_P10_palavra_passe_errada_com_acentos_da_401(self):
        self.assertEqual(Client(self.server).login(password="palavraçã")[0], 401)

    def test_S01_P10_login_com_palavra_passe_com_acentos(self):
        server = Server(password="Palavra-passe-çã-1").start()
        self.addCleanup(server.cleanup)
        self.assertEqual(Client(server).login()[0], 200)


class TestRateLimitAtrasDeProxy(ServerTestCase):
    load_dataset = False
    server_env = {"TRUSTED_PROXY_IPS": "127.0.0.1"}

    def test_S02_P10_x_forwarded_for_forjado_nao_contorna_o_bloqueio(self):
        anon = Client(self.server)
        codes = [
            anon.login(password=f"errada{i}", headers={"X-Forwarded-For": f"10.9.{i}.{i}, 192.168.1.50"})[0]
            for i in range(8)
        ]
        self.assertIn(429, codes)

    def test_P10_proxy_normal_usa_o_ip_do_cliente(self):
        codes = [
            Client(self.server).login(password=f"errada{i}", headers={"X-Forwarded-For": "203.0.113.7"})[0]
            for i in range(6)
        ]
        self.assertEqual(codes[-1], 429)
        other = Client(self.server).login(headers={"X-Forwarded-For": "203.0.113.8"})[0]
        self.assertEqual(other, 200, "outro cliente atrás do mesmo proxy não fica bloqueado")


class TestSemProxyDeConfianca(ServerTestCase):
    load_dataset = False

    def test_P10_x_forwarded_for_e_ignorado_sem_proxy_de_confianca(self):
        codes = [
            Client(self.server).login(password=f"errada{i}", headers={"X-Forwarded-For": f"10.0.0.{i}"})[0]
            for i in range(6)
        ]
        self.assertEqual(codes[-1], 429)


class TestOrigem(ServerTestCase):
    def test_S09_P11_put_so_e_aceite_onde_existe(self):
        """P11: hoje qualquer PUT fora dos anexos cai no handler de POST (ex.: PUT /api/login faz login)."""
        status, _ = Client(self.server).request(
            "PUT", "/api/login", {"username": self.server.user, "password": self.server.password}
        )
        self.assertIn(status, (404, 405))


class TestSeguranca(ServerTestCase):
    def test_S04_P11_pedido_de_outra_origem_e_recusado(self):
        before = self.client.get_data()
        data = copy.deepcopy(before)
        data["baseRevision"] = self.client.get_payload()["revision"]
        data["equipment"][0]["location"] = "alterado por outra origem"
        status, _ = self.client.request(
            "PUT",
            "/api/data",
            data,
            headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.client.get_data(), before)

    def test_P11_sem_indicacao_de_origem_e_recusado(self):
        before = self.client.get_data()
        data = dict(copy.deepcopy(before), baseRevision=self.client.get_payload()["revision"])
        status, _ = self.client.request("PUT", "/api/data", data, headers={"Origin": None})
        self.assertEqual(status, 403)
        self.assertEqual(self.client.get_data(), before)

    def test_P11_sec_fetch_site_same_origin_passa_mesmo_com_host_reescrito(self):
        data = dict(self.client.get_data(), baseRevision=self.client.get_payload()["revision"])
        status, _ = self.client.request(
            "PUT",
            "/api/data",
            data,
            headers={"Origin": "https://homelab.example", "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(status, 200)

    def test_P11_apagar_anexo_de_outra_origem_e_recusado(self):
        aid = self.client.upload("equipment", "eq-srv01", "a.txt", b"x")[1]["attachment"]["id"]
        status, _ = self.client.request(
            "DELETE", f"/api/attachments/{aid}", headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(len(self.attachment_files()), 1)

    def test_P11_login_de_outra_origem_e_recusado(self):
        status, _ = Client(self.server).login(headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)


class TestValidacao(ServerTestCase):
    def test_B11_P12_campo_null_nao_da_erro_500(self):
        data = self.client.get_data()
        data["equipment"][0]["hostname"] = None
        status, _ = self.client.save(data)
        self.assertIn(status, (200, 400))

    def test_S05_P12_restore_valida_os_dados_do_snapshot(self):
        snap = self.client.snapshot()
        out = io.BytesIO()
        with (
            tempfile.TemporaryDirectory() as td,
            zipfile.ZipFile(io.BytesIO(snap)) as src,
            zipfile.ZipFile(out, "w") as dst,
        ):
            for name in src.namelist():
                blob = src.read(name)
                if name == "database/homelab.db":
                    path = os.path.join(td, "x.db")
                    with open(path, "wb") as f:
                        f.write(blob)
                    con = sqlite3.connect(path)
                    con.execute("UPDATE equipment SET id='rpi''01' WHERE id='eq-rpi01'")
                    con.commit()
                    con.close()
                    with open(path, "rb") as f:
                        blob = f.read()
                dst.writestr(name, blob)
        self.assertEqual(self.client.restore(out.getvalue(), validate_only=True)[0], 400)

    def test_P12_null_fica_texto_vazio(self):
        data = self.client.get_data()
        data["equipment"][0]["hostname"] = None
        self.assertEqual(self.client.save(data)[0], 200)
        self.assertEqual(self.client.get_data()["equipment"][0]["hostname"], "")

    def test_P12_numero_em_campo_de_texto_fica_texto(self):
        data = self.client.get_data()
        data["equipment"][0]["serial"] = 12345
        self.assertEqual(self.client.save(data)[0], 200)
        self.assertEqual(self.client.get_data()["equipment"][0]["serial"], "12345")

    def _rejected(self, mutate):
        before = self.client.get_data()
        data = copy.deepcopy(before)
        mutate(data)
        status, body = self.client.save(data)
        self.assertEqual(status, 400, body)
        self.assertEqual(self.client.get_data(), before)
        return body["error"]

    def test_P12_texto_acima_do_limite_e_recusado(self):
        msg = self._rejected(lambda d: d["equipment"][0].__setitem__("notes", "x" * 10_001))
        self.assertIn("10000", msg)

    def test_P12_objeto_em_campo_de_texto_e_recusado(self):
        self._rejected(lambda d: d["equipment"][0].__setitem__("name", {"a": 1}))

    def test_P12_datas_invalidas_sao_recusadas(self):
        for bad in ("15/03/2026", "2026-13-01", "2026-02-30", '9999-12-31<img src=x onerror="1">'):
            with self.subTest(date=bad):
                msg = self._rejected(lambda d: d["equipment"][0]["maintenance"][0].__setitem__("date", bad))
                self.assertIn("AAAA-MM-DD", msg)

    def test_P12_data_vazia_e_aceite(self):
        data = self.client.get_data()
        data["equipment"][2]["created"] = ""
        self.assertEqual(self.client.save(data)[0], 200)

    def test_P12_valores_de_diagnostico_ficam_texto(self):
        data = self.client.get_data()
        data["equipment"][0]["maintenance"][0]["results"]["tempRows"][0]["value"] = 71
        self.assertEqual(self.client.save(data)[0], 200)
        row = self.client.get_data()["equipment"][0]["maintenance"][0]["results"]["tempRows"][0]
        self.assertEqual(row["value"], "71")


class TestRegistoEErros(ServerTestCase):
    def test_S06_P13_content_length_negativo_responde_400(self):
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nSec-Fetch-Site: same-origin\r\nContent-Type: application/json\r\n"
            b'Content-Length: -1\r\n\r\n{"username":"a"'
        )
        try:
            first = sock.recv(64)
        except socket.timeout:
            self.fail("o servidor não respondeu em 5 s")
        self.assertIn(b" 400 ", first)

    def test_P13_erro_interno_fica_registado_com_traceback(self):
        self.server.stop()
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        con.execute("UPDATE maintenance SET results_json='{' WHERE id='m-nas01-1'")
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        status, body = client.request("GET", "/api/data")
        self.assertEqual((status, body["error"]), (500, "Erro interno."))
        log = self.server.log()
        self.assertIn("Erro interno: GET /api/data", log)
        self.assertIn("Traceback", log)

    def test_P13_login_falhado_fica_no_log_sem_a_palavra_passe(self):
        Client(self.server).login(password="segredo-que-nao-pode-aparecer")
        log = self.server.log()
        self.assertIn("Login falhado", log)
        self.assertIn("ligação=127.0.0.1", log)
        self.assertNotIn("segredo-que-nao-pode-aparecer", log)

    def test_P13_json_invalido_da_mensagem_em_portugues(self):
        status, body = self.client.request(
            "PUT", "/api/data", b"{", headers={"Content-Type": "application/json"}
        )
        self.assertEqual((status, body["error"]), (400, "O pedido não é JSON válido."))

    def test_P13_login_com_corpo_que_nao_e_objeto_da_400(self):
        status, _ = Client(self.server).request("POST", "/api/login", [])
        self.assertEqual(status, 400)


class TestTimeout(ServerTestCase):
    load_dataset = False
    server_env = {"REQUEST_TIMEOUT": "2"}

    def test_P13_ligacao_parada_e_fechada(self):
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=8)
        self.addCleanup(sock.close)
        sock.sendall(
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nSec-Fetch-Site: same-origin\r\n"
            b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{"
        )
        started = time.time()
        try:
            answer = sock.recv(64)
        except socket.timeout:
            self.fail("o servidor não fechou a ligação parada")
        self.assertLess(time.time() - started, 6)
        self.assertTrue(answer == b"" or b" 408 " in answer, answer)


class TestDocker(ServerTestCase):
    load_dataset = False
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(self, name):
        with open(os.path.join(self.ROOT, name), encoding="utf-8") as f:
            return f.read()

    def test_S07_P14_imagem_nao_leva_dados_nem_credenciais(self):
        rules = {line.strip() for line in self.read(".dockerignore").splitlines()}
        for rule in ("data/", ".env", ".git/", "tests/"):
            self.assertIn(rule, rules)

    def test_P15_compose_limita_a_memoria_e_reinicia_o_servico(self):
        compose = self.read("docker-compose.yml")
        self.assertRegex(compose, r"(?m)^\s+mem_limit:\s*200m\s*$")
        self.assertRegex(compose, r"(?m)^\s+memswap_limit:\s*200m\s*$")
        self.assertRegex(compose, r"(?m)^\s+restart:\s*unless-stopped\s*$")

    def test_P16_compose_passa_as_variaveis_que_a_app_le(self):
        """Uma variável no .env só chega à app se o compose a passar ao contentor."""
        import re

        compose = self.read("docker-compose.yml")
        config = self.read(os.path.join("backend", "config.py"))
        read_by_app = set(re.findall(r'os\.getenv\(\s*["\']([A-Z_]+)["\']', config)) - {
            "DATA_DIR",
            "HOST",
            "PORT",
            "SESSION_TTL",
            "REQUEST_TIMEOUT",
            "SEED_PATH",
        }
        for name in sorted(read_by_app):
            with self.subTest(variable=name):
                self.assertRegex(compose, rf"(?m)^\s+{name}:")

    def test_P14_healthcheck_responde_com_a_app_a_correr(self):
        import re
        import subprocess
        import sys

        command = re.search(r'CMD python -c "(.+?)" \|\| exit 1', self.read("Dockerfile")).group(1)
        env = dict(os.environ, PORT=str(self.server.port))
        ok = subprocess.run([sys.executable, "-c", command], env=env, capture_output=True, timeout=10)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.server.stop()
        down = subprocess.run([sys.executable, "-c", command], env=env, capture_output=True, timeout=10)
        self.assertNotEqual(down.returncode, 0)


def _current_schema_version():
    """Versão do esquema que o código atual cria, lida do storage.py (evita números fixos nos testes)."""
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "backend", "storage.py"), encoding="utf-8") as f:
        return int(re.search(r"^SCHEMA_VERSION = (\d+)", f.read(), re.M).group(1))


SCHEMA_VERSION = _current_schema_version()


class TestMigracoes(ServerTestCase):
    def db_path(self):
        return os.path.join(self.server.data_dir, "homelab.db")

    def user_version(self):
        con = sqlite3.connect(self.db_path())
        try:
            return con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()

    def set_user_version(self, value):
        self.server.stop()
        con = sqlite3.connect(self.db_path())
        con.execute(f"PRAGMA user_version = {value}")
        con.commit()
        con.close()

    def backups(self):
        d = os.path.join(self.server.data_dir, "backups")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_R03_P17_base_nova_fica_na_versao_atual_sem_backup(self):
        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertEqual(self.backups(), [])

    def test_R03_P17_base_antiga_migra_com_snapshot_antes_e_so_uma_vez(self):
        before = self.client.get_data()
        self.set_user_version(0)
        self.server.start()
        client = Client(self.server)
        client.login()
        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertEqual(client.get_data(), before)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertTrue(
            backups[0].startswith(f"pre-migration-v0-v{SCHEMA_VERSION}-") and backups[0].endswith(".zip")
        )
        with open(os.path.join(self.server.data_dir, "backups", backups[0]), "rb") as f:
            status, body = client.restore(f.read(), validate_only=True)
        self.assertEqual(status, 200, body)
        self.server.restart()
        self.assertEqual(len(self.backups()), 1, "a migração não se repete no arranque seguinte")

    def test_R03_P17_base_de_versao_mais_recente_impede_o_arranque_sem_alterar_nada(self):
        self.set_user_version(99)
        with open(self.db_path(), "rb") as f:
            original = f.read()
        with self.assertRaises(RuntimeError):
            self.server.start()
        self.assertIn("versão mais recente", self.server.log())
        with open(self.db_path(), "rb") as f:
            self.assertEqual(f.read(), original)
        self.set_user_version(SCHEMA_VERSION)

    def test_R03_P17_base_no_formato_antigo_migra_sem_perder_dados(self):
        """BD de uma versão antiga: planos por equipamento (maintenance_plans), sem results_json."""
        self.server.stop()
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path() + suffix
            if os.path.exists(path):
                os.remove(path)
        con = sqlite3.connect(self.db_path())
        con.executescript(
            """
            CREATE TABLE equipment(id TEXT PRIMARY KEY, name TEXT NOT NULL, hostname TEXT NOT NULL DEFAULT '',
              role TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
              serial TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
              created TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', cpu TEXT NOT NULL DEFAULT '',
              motherboard TEXT NOT NULL DEFAULT '', ram TEXT NOT NULL DEFAULT '', gpu TEXT NOT NULL DEFAULT '',
              storage TEXT NOT NULL DEFAULT '', nic TEXT NOT NULL DEFAULT '', psu TEXT NOT NULL DEFAULT '',
              bios TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE components(id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL, name TEXT NOT NULL,
              type TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', serial TEXT NOT NULL DEFAULT '',
              capacity TEXT NOT NULL DEFAULT '', health TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE maintenance(id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL, date TEXT NOT NULL DEFAULT '',
              type TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', areas TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '');
            CREATE TABLE maintenance_plans(id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL, name TEXT NOT NULL,
              interval_months INTEGER NOT NULL DEFAULT 6, active INTEGER NOT NULL DEFAULT 1, notes TEXT NOT NULL DEFAULT '',
              sort_order INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO equipment(id,name) VALUES('eq-velho','Servidor antigo');
            INSERT INTO components(id,equipment_id,name,type) VALUES('c-velho','eq-velho','SSD','ssd');
            INSERT INTO maintenance(id,equipment_id,date,type) VALUES('m-velho','eq-velho','2024-05-01','Limpeza');
            INSERT INTO maintenance_plans(id,equipment_id,name,interval_months) VALUES('p-velho','eq-velho','Limpeza',6);
            INSERT INTO app_meta VALUES('initialized','1');
            """
        )
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        data = client.get_data()
        eq = self.equipment(data, "eq-velho")
        self.assertEqual(eq["components"][0]["type"], "storage")
        self.assertEqual(eq["maintenance"][0]["date"], "2024-05-01")
        self.assertEqual([p["name"] for p in data["preventivePlans"]], ["Limpeza"])
        self.assertEqual(eq["preventivePlanIds"], [data["preventivePlans"][0]["id"]])
        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(client.save(data)[0], 200)


class TestAnexosEmFalta(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.keep = self.client.upload("equipment", "eq-srv01", "fica.pdf", b"%PDF fica", "application/pdf")[
            1
        ]["attachment"]
        self.gone = self.client.upload(
            "component", "c-srv01-ssd", "SMART antigo.txt", b"falta", "text/plain"
        )[1]["attachment"]
        os.remove(os.path.join(self.server.data_dir, "attachments", self.gone["id"]))

    def zip_names_and_manifest(self, snap):
        with zipfile.ZipFile(io.BytesIO(snap)) as z:
            return set(z.namelist()), json.loads(z.read("manifest.json"))

    def test_R02_P16_backup_com_anexo_em_falta_e_criado_e_identifica_o_que_falta(self):
        status, snap, _ = self.client.request("GET", "/api/backup/full", raw=True)
        self.assertEqual(status, 200)
        names, manifest = self.zip_names_and_manifest(snap)
        self.assertIn(f"attachments/{self.keep['id']}", names)
        self.assertNotIn(f"attachments/{self.gone['id']}", names)
        missing = manifest["missing_attachments"]
        self.assertEqual(
            [(m["id"], m["original_name"], m["owner_id"]) for m in missing],
            [(self.gone["id"], "SMART antigo.txt", "c-srv01-ssd")],
        )
        self.assertEqual(manifest["application_state"]["missing_attachment_count"], 1)

    def test_P16_restore_com_anexos_em_falta_e_aceite_e_avisa(self):
        snap = self.client.snapshot()
        data = self.client.get_data()
        data["equipment"][0]["name"] = "ALTERADO"
        self.client.save(data)
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        self.assertEqual([m["id"] for m in body["summary"]["missing_attachments"]], [self.gone["id"]])
        self.assertEqual(self.client.get_data()["equipment"][0]["name"], "srv01")
        status, got, _ = self.client.request("GET", f"/api/attachments/{self.keep['id']}/download", raw=True)
        self.assertEqual((status, got), (200, b"%PDF fica"))
        self.assertNotIn(self.gone["id"], self.attachment_files(), "não se criam ficheiros falsos")
        listing = self.client.request("GET", "/api/attachments?ownerType=component&ownerId=c-srv01-ssd")[1][
            "attachments"
        ]
        self.assertEqual([(a["id"], a["missing"]) for a in listing], [(self.gone["id"], True)])
        status, body = self.client.request("GET", f"/api/attachments/{self.gone['id']}/download")
        self.assertEqual(status, 404)
        self.assertIn("em falta", body["error"])

    def _rezip(self, snap, edit_files=None, edit_manifest=None):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(snap)) as src, zipfile.ZipFile(out, "w") as dst:
            files = {n: src.read(n) for n in src.namelist()}
            manifest = json.loads(files.pop("manifest.json"))
            if edit_manifest:
                edit_manifest(manifest)
            if edit_files:
                edit_files(files)
            dst.writestr("manifest.json", json.dumps(manifest))
            for n, blob in files.items():
                dst.writestr(n, blob)
        return out.getvalue()

    def test_P16_snapshots_estruturalmente_invalidos_continuam_recusados(self):
        snap = self.client.snapshot()
        gone = self.gone["id"]
        cases = {
            "anexo em falta que afinal está no ZIP": self._rezip(
                snap, edit_files=lambda f: f.__setitem__(f"attachments/{gone}", b"x")
            ),
            "anexo em falta que não existe na BD": self._rezip(
                snap, edit_manifest=lambda m: m["missing_attachments"].append({"id": "att-inventado"})
            ),
            "anexo presente e em falta ao mesmo tempo": self._rezip(
                snap, edit_manifest=lambda m: m["missing_attachments"].append(dict(m["attachments"][0]))
            ),
            "anexo omitido sem ser declarado em falta": self._rezip(
                snap, edit_manifest=lambda m: m.__setitem__("missing_attachments", [])
            ),
            "sem base de dados": self._rezip(snap, edit_files=lambda f: f.pop("database/homelab.db")),
        }
        for label, blob in cases.items():
            with self.subTest(case=label):
                status, _ = self.client.restore(blob, validate_only=True)
                self.assertEqual(status, 400)


class TestOrfaosDepoisDoCommit(unittest.TestCase):
    def test_B13_P16_se_o_commit_falhar_o_ficheiro_do_anexo_fica(self):
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            script = """
import json, os, sqlite3
from backend import storage
storage.init()
data = {"equipment": [{"id": "eq-1", "name": "srv"}], "preventivePlans": []}
storage.write(data, 0)
c = sqlite3.connect(storage.DB_PATH)
c.execute("INSERT INTO attachments(id,owner_type,owner_id,original_name,display_name,category,mime_type,size,stored_name,created) VALUES('att-1','equipment','eq-1','a.txt','a','other','text/plain',1,'att-1','x')")
c.commit(); c.close()
(storage.attachment_dir() / "att-1").write_bytes(b"x")
real = storage._connect
class Falha(Exception): pass
class SemCommit:
    def __init__(self, c): self._c = c
    def __getattr__(self, name): return getattr(self._c, name)
    def commit(self): raise Falha()
storage._connect = lambda p: SemCommit(real(p))
try:
    storage.write({"equipment": [], "preventivePlans": []}, 1)
except Falha:
    pass
storage._connect = real
c = storage.db()
rows = c.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
print(json.dumps({"file": (storage.attachment_dir() / "att-1").is_file(), "rows": rows}))
"""
            env = dict(os.environ, DATA_DIR=td, AUTH_USERNAME="u", AUTH_PASSWORD="p")
            out = subprocess.run(
                [sys.executable, "-c", script], cwd=root, env=env, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), {"file": True, "rows": 1})


def wait_until(check, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return check()


class TestStreaming(ServerTestCase):
    """P15: transferências em blocos, códigos de uso único e temporários sempre limpos."""

    def tmp_files(self):
        d = os.path.join(self.server.data_dir, "tmp")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def cookie_header(self):
        jar = next(h.cookiejar for h in self.client.opener.handlers if hasattr(h, "cookiejar"))
        return "; ".join(f"{c.name}={c.value}" for c in jar)

    def raw_request(self, head_lines, body=b""):
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=10)
        head = "".join(line + "\r\n" for line in head_lines) + "\r\n"
        sock.sendall(head.encode() + body)
        return sock

    def test_P15_restore_com_um_so_envio_e_codigo_de_uso_unico(self):
        snap = self.client.snapshot()
        status, up = self.client.request(
            "POST", "/api/restore/upload", snap, headers={"Content-Type": "application/zip"}
        )
        self.assertEqual(status, 200, up)
        self.assertEqual(up["summary"]["equipment_count"], 3)
        wrong = self.client.request("POST", "/api/restore/confirm", {"token": "nao-existe", "confirm": True})
        self.assertEqual(wrong[0], 400)
        ok = self.client.request("POST", "/api/restore/confirm", {"token": up["token"], "confirm": True})
        self.assertEqual(ok[0], 200, ok[1])
        again = self.client.request("POST", "/api/restore/confirm", {"token": up["token"], "confirm": True})
        self.assertEqual(again[0], 400, "o código só serve uma vez")
        self.assertEqual(self.tmp_files(), [])

    def test_P15_restore_sem_confirmacao_explicita_e_recusado(self):
        up = self.client.request(
            "POST", "/api/restore/upload", self.client.snapshot(), headers={"Content-Type": "application/zip"}
        )[1]
        status, _ = self.client.request("POST", "/api/restore/confirm", {"token": up["token"]})
        self.assertEqual(status, 400)

    def test_P15_novo_envio_substitui_o_restore_pendente(self):
        snap = self.client.snapshot()
        first = self.client.request(
            "POST", "/api/restore/upload", snap, headers={"Content-Type": "application/zip"}
        )[1]
        second = self.client.request(
            "POST", "/api/restore/upload", snap, headers={"Content-Type": "application/zip"}
        )[1]
        self.assertEqual(len(self.tmp_files()), 1, "só pode haver um restore pendente")
        self.assertEqual(
            self.client.request("POST", "/api/restore/confirm", {"token": first["token"], "confirm": True})[
                0
            ],
            400,
        )
        self.assertEqual(
            self.client.request("POST", "/api/restore/confirm", {"token": second["token"], "confirm": True})[
                0
            ],
            200,
        )

    def test_P15_backup_em_dois_passos_e_codigo_de_uso_unico(self):
        self.client.upload("equipment", "eq-srv01", "a.bin", os.urandom(200_000))
        status, prep = self.client.prepare_backup()
        self.assertEqual(status, 200, prep)
        self.assertEqual((prep["attachment_count"], prep["missing_attachments"]), (1, []))
        status, content = self.client.download_backup(prep["token"])
        self.assertEqual(status, 200)
        self.assertEqual(len(content), prep["size"])
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            self.assertIsNone(z.testzip())
        self.assertEqual(self.client.download_backup(prep["token"])[0], 404, "o código só serve uma vez")
        self.assertTrue(wait_until(lambda: self.tmp_files() == []))

    def test_P15_envios_invalidos_nao_deixam_temporarios(self):
        self.assertEqual(
            self.client.request(
                "POST", "/api/restore/upload", b"nao e zip", headers={"Content-Type": "application/zip"}
            )[0],
            400,
        )
        self.assertEqual(self.client.upload("equipment", "eq-nao-existe", "a.txt", b"x")[0], 400)
        self.assertEqual(self.client.upload("equipment", "eq-srv01", "vazio.txt", b"")[0], 400)
        self.assertEqual(self.tmp_files(), [])
        self.assertEqual(self.attachment_files(), [])

    def test_P15_limite_e_verificado_antes_de_ler_o_corpo(self):
        sock = self.raw_request(
            [
                "POST /api/attachments?ownerType=equipment&ownerId=eq-srv01 HTTP/1.1",
                f"Host: 127.0.0.1:{self.server.port}",
                f"Origin: {self.server.url('')}",
                f"Cookie: {self.cookie_header()}",
                "Content-Type: application/octet-stream",
                f"Content-Length: {26 * 1024 * 1024}",
            ]
        )
        self.addCleanup(sock.close)
        self.assertIn(b" 400 ", sock.recv(200))
        self.assertEqual(self.tmp_files(), [])

    def test_P15_envio_interrompido_nao_deixa_temporarios(self):
        for path, ctype in [
            ("/api/attachments?ownerType=equipment&ownerId=eq-srv01", "application/pdf"),
            ("/api/restore/upload", "application/zip"),
        ]:
            with self.subTest(path=path):
                sock = self.raw_request(
                    [
                        f"POST {path} HTTP/1.1",
                        f"Host: 127.0.0.1:{self.server.port}",
                        f"Origin: {self.server.url('')}",
                        f"Cookie: {self.cookie_header()}",
                        f"Content-Type: {ctype}",
                        f"Content-Length: {10 * 1024 * 1024}",
                    ],
                    os.urandom(1024 * 1024),
                )
                sock.close()
                self.assertTrue(wait_until(lambda: self.tmp_files() == []), self.tmp_files())
        self.assertEqual(self.attachment_files(), [])

    def test_P15_download_interrompido_nao_deixa_temporarios(self):
        for i in range(3):
            self.client.upload("equipment", "eq-srv01", f"{i}.bin", os.urandom(8 * 1024 * 1024))
        token = self.client.prepare_backup()[1]["token"]
        req = urllib.request.Request(self.server.url(f"/api/backup/download/{token}"))
        resp = self.client.opener.open(req, timeout=30)
        resp.read(1024 * 1024)
        resp.close()
        self.assertTrue(wait_until(lambda: self.tmp_files() == [], timeout=15), self.tmp_files())

    def test_P15_temporarios_de_arranques_anteriores_sao_apagados(self):
        self.server.stop()
        tmp = os.path.join(self.server.data_dir, "tmp")
        os.makedirs(tmp, exist_ok=True)
        with open(os.path.join(tmp, "restore-upload-antigo.zip"), "wb") as f:
            f.write(b"x" * 1000)
        self.server.start()
        self.assertEqual(self.tmp_files(), [])

    def test_P15_ficheiros_estaticos_iguais_ao_disco(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("app.js", "app.css", "sw.js"):
            with self.subTest(file=name):
                status, content, headers = Client(self.server).request("GET", f"/static/{name}", raw=True)
                with open(os.path.join(root, "static", name), "rb") as f:
                    expected = f.read()
                if name == "sw.js":  # P21: o servidor injeta a versão única da app
                    version = Client(self.server).request("GET", "/api/health")[1]["version"]
                    expected = expected.replace(b"__APP_VERSION__", version.encode())
                self.assertEqual((status, content), (200, expected))
                self.assertEqual(int(headers["Content-Length"]), len(expected))

    def test_P15_snapshot_no_formato_da_fase_2_continua_aceite(self):
        fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "snapshot-formato-v1-fase2.zip"
        )
        with open(fixture, "rb") as f:
            snap = f.read()
        self.client.save({"equipment": [], "preventivePlans": []})
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        data = self.client.get_data()
        self.assertEqual([e["id"] for e in data["equipment"]], ["eq-srv01", "eq-nas01", "eq-rpi01"])
        att = self.client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1][
            "attachments"
        ][0]
        status, content, _ = self.client.request("GET", f"/api/attachments/{att['id']}/download", raw=True)
        self.assertEqual((status, content), (200, b"Fatura antiga\n" * 100))


class TestRestoreInterrompido(ServerTestCase):
    """P15/Docker: se o contentor morrer a meio de um restore (p. ex. mem_limit), o arranque
    seguinte repõe o estado anterior completo."""

    KILLER = """
import os, sys
os.environ["DATA_DIR"] = sys.argv[1]
sys.path.insert(0, sys.argv[2])
from backend import storage
original = storage.copy_stream
calls = {"n": 0}
def dying_copy(*a, **k):
    journal = storage.DATA_DIR / storage.RESTORE_JOURNAL
    if journal.exists() and not storage.DB_PATH.exists() or calls["n"]:
        calls["n"] += 1
        if calls["n"] >= int(sys.argv[4]):
            os._exit(137)
    return original(*a, **k)
storage.copy_stream = dying_copy
storage.restore_snapshot_zip(sys.argv[3])
"""

    def test_P15_restore_morto_a_meio_repoe_o_estado_anterior(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for kill_at, label in [(1, "a extrair a BD"), (2, "a extrair os anexos")]:
            with self.subTest(momento=label):
                self.client.upload("equipment", "eq-srv01", f"antes-{kill_at}.txt", b"estado anterior")
                data = self.client.get_data()
                data["equipment"][0]["name"] = f"ESTADO ANTERIOR {kill_at}"
                self.client.save(data)
                snap_path = os.path.join(self.server.tmp, f"snap-{kill_at}.zip")
                other = Client(self.server)
                other.login()
                with open(snap_path, "wb") as f:
                    f.write(other.snapshot())
                data["equipment"][0]["name"] = f"ESTADO ATUAL {kill_at}"
                self.client.save(data)
                before, files_before = self.client.get_data(), self.attachment_files()
                self.server.stop()
                run = subprocess.run(
                    [sys.executable, "-c", self.KILLER, self.server.data_dir, root, snap_path, str(kill_at)],
                    capture_output=True,
                    timeout=60,
                    env=self.server.env,
                )
                self.assertEqual(run.returncode, 137, run.stderr.decode()[-500:])
                self.server.start()
                self.client = Client(self.server)
                self.client.login()
                self.assertEqual(self.client.get_data(), before)
                self.assertEqual(self.attachment_files(), files_before)
                leftovers = [
                    n
                    for n in os.listdir(self.server.data_dir)
                    if n.startswith(".restore-previous-") or n == "restore-in-progress.json"
                ]
                self.assertEqual(leftovers, [])
                self.assertIn("Restore interrompido", self.server.log())


class TestOperacoesPesadas(ServerTestCase):
    def test_P15_so_um_backup_ou_restore_de_cada_vez(self):
        jar = next(h.cookiejar for h in self.client.opener.handlers if hasattr(h, "cookiejar"))
        cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=10)
        self.addCleanup(sock.close)
        sock.sendall(
            (
                "POST /api/restore/upload HTTP/1.1\r\nHost: x\r\nSec-Fetch-Site: same-origin\r\n"
                f"Cookie: {cookie}\r\nContent-Type: application/zip\r\nContent-Length: 10000000\r\n\r\n"
            ).encode()
            + b"PK"
            + b"\0" * 4096
        )
        time.sleep(0.5)  # o servidor está a receber o restore e tem o lock
        status, body = self.client.prepare_backup()
        self.assertEqual(status, 409, body)
        self.assertIn("em curso", body["error"])
        sock.close()  # envio interrompido: o lock é libertado e o temporário apagado
        deadline = time.time() + 10
        while time.time() < deadline:
            status, _ = self.client.prepare_backup()
            if status == 200:
                break
            time.sleep(0.2)
        self.assertEqual(status, 200)


class TestExpiracao(ServerTestCase):
    server_env = {"PENDING_TTL": "1"}

    def test_P15_snapshot_validado_expira_e_e_apagado(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "snap.zip")
            with open(path, "wb") as f:
                f.write(self.client.snapshot())
            status, body = self.client.restore_file(path, validate_only=True)
        self.assertEqual(status, 200, body)
        time.sleep(1.5)
        status, answer = self.client.request(
            "POST", "/api/restore/confirm", {"token": body["token"], "confirm": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("já não está disponível", answer["error"])
        self.assertEqual(os.listdir(os.path.join(self.server.data_dir, "tmp")), [])


class TestSnapshotAdulterado(ServerTestCase):
    """S11 (P24): os nomes dos ficheiros dos anexos vêm da BD do snapshot e não podem sair de attachments/."""

    def setUp(self):
        super().setUp()
        self.att = self.client.upload("equipment", "eq-srv01", "a.txt", b"conteudo", "text/plain")[1][
            "attachment"
        ]

    def _edit_db(self, snap, sql):
        with tempfile.TemporaryDirectory() as td:
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(snap)) as src, zipfile.ZipFile(out, "w") as dst:
                for name in src.namelist():
                    blob = src.read(name)
                    if name == "database/homelab.db":
                        path = os.path.join(td, "db")
                        with open(path, "wb") as f:
                            f.write(blob)
                        con = sqlite3.connect(path)
                        con.execute(sql)
                        con.commit()
                        con.close()
                        with open(path, "rb") as f:
                            blob = f.read()
                    dst.writestr(name, blob)
            return out.getvalue()

    def test_S11_P24_snapshot_com_nomes_de_ficheiro_adulterados_e_recusado(self):
        snap = self.client.snapshot()
        cases = {
            "caminho para fora da pasta": "UPDATE attachments SET stored_name='../homelab.db'",
            "caminho absoluto": "UPDATE attachments SET stored_name='/etc/passwd'",
            "nome diferente do ID": "UPDATE attachments SET stored_name='att-outro'",
            "tipo de dono inválido": "UPDATE attachments SET owner_type='../x'",
        }
        before = self.client.get_data()
        for label, sql in cases.items():
            with self.subTest(case=label):
                status, body = self.client.restore(self._edit_db(snap, sql), validate_only=True)
                self.assertEqual(status, 400, body)
                self.assertIn("anexo", body["error"])
        self.assertEqual(self.client.get_data(), before)
        self.assertTrue(os.path.isfile(os.path.join(self.server.data_dir, "homelab.db")))

    def test_S11_P24_nome_de_ficheiro_invalido_na_bd_nunca_sai_da_pasta_dos_anexos(self):
        """Defesa em profundidade: mesmo que a BD já tenha um nome adulterado, nada fora de attachments/ é lido nem apagado."""
        self.server.stop()
        db = os.path.join(self.server.data_dir, "homelab.db")
        con = sqlite3.connect(db)
        con.execute("UPDATE attachments SET stored_name='../homelab.db' WHERE id=?", (self.att["id"],))
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        status, payload, _ = client.request("GET", f"/api/attachments/{self.att['id']}/download", raw=True)
        self.assertEqual(status, 404)
        self.assertNotIn(b"SQLite format", payload)
        listing = client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1]
        self.assertTrue(listing["attachments"][0]["missing"])
        status, _ = client.request("DELETE", f"/api/attachments/{self.att['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(os.path.isfile(db), "apagar o anexo não pode apagar a base de dados")
        self.assertIsNotNone(client.get_data())


class TestLoginSemAutenticacao(ServerTestCase):
    """S12 (P25): antes do login, o servidor não aceita corpos grandes nem guarda o que o cliente envia."""

    load_dataset = False

    def test_S12_P25_login_com_corpo_grande_e_recusado_sem_o_ler(self):
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
        self.addCleanup(sock.close)
        head = [
            "POST /api/login HTTP/1.1",
            f"Host: 127.0.0.1:{self.server.port}",
            "Sec-Fetch-Site: same-origin",
            "Content-Type: application/json",
            f"Content-Length: {15 * 1024 * 1024}",
        ]
        sock.sendall(("".join(line + "\r\n" for line in head) + "\r\n").encode())
        self.assertIn(b" 400 ", sock.recv(200))

    def test_S12_P25_nomes_de_utilizador_diferentes_nao_contornam_o_bloqueio(self):
        anon = Client(self.server)
        codes = [anon.login(user=f"utilizador{i}", password="errada")[0] for i in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)

    def test_S12_P25_registo_de_falhas_nao_guarda_o_nome_e_tem_limite(self):
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = """
import json
from backend import main
class H:
    headers = {}
    def __init__(self, ip): self.client_address = (ip, 1)
for i in range(main.LOGIN_MAX_KEYS + 500):
    main.login_failed(H(f"10.{i // 65536}.{i // 256 % 256}.{i % 256}"))
keys = list(main._login_failures)
print(json.dumps({"count": len(keys), "longest": max(len(repr(k)) for k in keys)}))
"""
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DATA_DIR=td, AUTH_USERNAME="u", AUTH_PASSWORD="p")
            out = subprocess.run(
                [sys.executable, "-c", script], cwd=root, env=env, capture_output=True, text=True, timeout=60
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertLessEqual(result["count"], 10_000)
        self.assertLess(result["longest"], 100)


class TestMensagensDeErro(ServerTestCase):
    """R04, P27 e B14: erros com mensagem útil em vez de 500 ou texto corrompido."""

    def _replace_db(self, snap, edit):
        with tempfile.TemporaryDirectory() as td:
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(snap)) as src, zipfile.ZipFile(out, "w") as dst:
                for name in src.namelist():
                    blob = src.read(name)
                    if name == "database/homelab.db":
                        path = os.path.join(td, "db")
                        with open(path, "wb") as f:
                            f.write(blob)
                        blob = edit(path)
                    dst.writestr(name, blob)
            return out.getvalue()

    def test_R04_P27_snapshot_de_versao_mais_recente_da_mensagem_clara(self):
        def newer(path):
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 99")
            con.commit()
            con.close()
            with open(path, "rb") as f:
                return f.read()

        status, body = self.client.restore(self._replace_db(self.client.snapshot(), newer), validate_only=True)
        self.assertEqual(status, 400, body)
        self.assertIn("versão mais recente", body["error"])

    def test_R04_P27_snapshot_com_base_de_dados_danificada_da_400(self):
        cases = {
            "não é SQLite": lambda path: b"isto nao e uma base de dados" * 100,
            "sem tabelas": lambda path: open(path, "wb").close() or b"",
        }
        for label, edit in cases.items():
            with self.subTest(case=label):
                status, body = self.client.restore(
                    self._replace_db(self.client.snapshot(), edit), validate_only=True
                )
                self.assertEqual(status, 400, body)
                self.assertIn("base de dados", body["error"])

    def test_P27_registo_smart_sem_disco_explica_o_que_falta(self):
        data = self.client.get_data()
        data["equipment"][0]["maintenance"][0]["results"]["smartDisks"] = [{"health": "OK"}]
        status, body = self.client.save(data)
        self.assertEqual(status, 400)
        self.assertIn("não tem deviceId", body["error"])

    def test_B14_P27_titulo_do_pdf_mantem_acentos_e_travessao(self):
        import re

        status, pdf, _ = self.client.pdf("/api/report/equipment/eq-srv01.pdf")
        self.assertEqual(status, 200)
        raw = re.search(rb"/Title <([0-9A-Fa-f]+)>", pdf)
        self.assertIsNotNone(raw, pdf[-400:])
        self.assertEqual(bytes.fromhex(raw.group(1).decode()).decode("utf-16"), "Relatório — srv01")


class TestEndurecimento(ServerTestCase):
    """S13, P29: backup só a partir da própria app, CSP na página e configuração do proxy validada."""

    def test_S13_P29_backup_completo_pedido_por_outro_site_e_recusado(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                status, _, _ = self.client.request(
                    "GET", "/api/backup/full", headers={"Sec-Fetch-Site": site}, raw=True
                )
                self.assertEqual(status, 403)
        for site in ("same-origin", "none", None):
            with self.subTest(site=site):
                status, _, _ = self.client.request(
                    "GET", "/api/backup/full", headers={"Sec-Fetch-Site": site}, raw=True
                )
                self.assertEqual(status, 200)

    def test_P29_pagina_tem_csp_minima(self):
        status, _, headers = self.client.request("GET", "/", raw=True)
        self.assertEqual(status, 200)
        csp = headers.get("Content-Security-Policy", "")
        for directive in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'self'"):
            self.assertIn(directive, csp)

    def test_P29_pdf_anexado_continua_sem_csp_para_abrir_no_browser(self):
        aid = self.client.upload("equipment", "eq-srv01", "a.pdf", b"%PDF-1.4", "application/pdf")[1][
            "attachment"
        ]["id"]
        status, _, headers = self.client.request("GET", f"/api/attachments/{aid}/download", raw=True)
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Security-Policy"))


class TestProxyMalConfigurado(unittest.TestCase):
    def test_P29_trusted_proxy_ips_invalido_impede_o_arranque(self):
        server = Server(env={"TRUSTED_PROXY_IPS": "10.0.0.5,haproxy"})
        self.addCleanup(server.cleanup)
        with self.assertRaises(RuntimeError):
            server.start()
        self.assertIn("TRUSTED_PROXY_IPS", server.log())
        self.assertIn("haproxy", server.log())


class TestPedidosDuranteORestore(unittest.TestCase):
    """B16 (P30): pedidos que chegam enquanto o restore troca os ficheiros não se perdem em silêncio."""

    SCRIPT = """
import json, threading, time
from backend import storage
storage.init()
storage.write({"equipment": [{"id": "eq-1", "name": "antes"}], "preventivePlans": []}, 0)
snap = storage.DATA_DIR / "snap.zip"
storage.create_full_snapshot_zip(snap)
storage.write({"equipment": [{"id": "eq-1", "name": "depois"}], "preventivePlans": []}, 1)
summary = storage.validate_snapshot_zip(snap)
real = storage.copy_stream
swapping = threading.Event()
def slow(src, dst, limit=None, hasher=None):
    if getattr(dst, "name", "").endswith("homelab.db.part"):
        swapping.set()  # a BD atual já foi posta de lado; a do snapshot está a ser extraída
        time.sleep(7)
    return real(src, dst, limit, hasher)
storage.copy_stream = slow
restore = threading.Thread(target=storage.restore_snapshot_zip, args=(snap, summary))
restore.start()
assert swapping.wait(10)
def attach():
    path = storage.new_tmp_path("x")
    path.write_bytes(b"x")
    storage.save_attachment_file("equipment", "eq-1", "a.txt", "text/plain", path)
outcome = {}
for name, call in {
    "write": lambda: storage.write({"equipment": [{"id": "eq-1", "name": "durante"}], "preventivePlans": []}, 2),
    "read": storage.read_state,
    "attachment": attach,
}.items():
    try:
        call()
        outcome[name] = "ok"
    except Exception as e:
        outcome[name] = type(e).__name__
restore.join()
data, revision = storage.read_state()
outcome["final"] = data["equipment"][0]["name"]
outcome["write_after"] = storage.write(data, revision)[1] == revision + 1
print(json.dumps(outcome))
"""

    def test_B16_P30_gravacao_leitura_e_anexo_durante_o_restore_sao_recusados_com_aviso(self):
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DATA_DIR=td, AUTH_USERNAME="u", AUTH_PASSWORD="p", STATE_LOCK_WAIT="1")
            out = subprocess.run(
                [sys.executable, "-c", self.SCRIPT], cwd=root, env=env, capture_output=True, text=True, timeout=90
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(
            result,
            {
                "write": "RestoreInProgressError",
                "read": "RestoreInProgressError",
                "attachment": "RestoreInProgressError",
                "final": "antes",
                "write_after": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
