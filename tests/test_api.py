"""Comportamento atual que está correto e tem de continuar a funcionar.

Cada teste arranca o servidor real numa pasta temporária e grava a fixture
sintética de tests/fixtures/dataset.json. Nenhum teste toca em data/.
"""

import copy
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
import zipfile

from harness import Client, ServerTestCase, load_fixture


def without_ids(items):
    return [{k: v for k, v in x.items()} for x in items]


class TestAutenticacao(ServerTestCase):
    load_dataset = False

    def test_health_responde_sem_sessao(self):
        status, body = Client(self.server).request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_rotas_de_dados_exigem_sessao(self):
        anon = Client(self.server)
        for method, path in [
            ("GET", "/api/data"),
            ("PUT", "/api/data"),
            ("GET", "/api/attachments"),
            ("GET", "/api/attachments/summary"),
            ("GET", "/api/backup/full"),
        ]:
            with self.subTest(path=path, method=method):
                body = {"equipment": [], "preventivePlans": []} if method == "PUT" else None
                status, _ = anon.request(method, path, body)
                self.assertEqual(status, 401)

    def test_sessao_indica_estado(self):
        self.assertTrue(self.client.request("GET", "/api/session")[1]["authenticated"])
        self.assertFalse(Client(self.server).request("GET", "/api/session")[1]["authenticated"])

    def test_cookie_de_sessao_httponly_e_samesite(self):
        status, _, headers = Client(self.server).request(
            "POST", "/api/login", {"username": self.server.user, "password": self.server.password}, raw=True
        )
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_palavra_passe_errada_devolve_401(self):
        self.assertEqual(Client(self.server).login(password="errada")[0], 401)
        self.assertEqual(Client(self.server).login(user="outro")[0], 401)

    def test_logout_invalida_a_sessao(self):
        self.assertEqual(self.client.request("POST", "/api/logout")[0], 200)
        self.assertEqual(self.client.request("GET", "/api/data")[0], 401)

    def test_bloqueio_a_sexta_falha(self):
        other = Client(self.server)
        codes = [other.login(password=f"errada{i}")[0] for i in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)


class TestDados(ServerTestCase):
    def test_fixture_volta_igual(self):
        expected = load_fixture()
        data = self.client.get_data()
        self.assertEqual([e["id"] for e in data["equipment"]], [e["id"] for e in expected["equipment"]])
        self.assertEqual(
            [p["id"] for p in data["preventivePlans"]], [p["id"] for p in expected["preventivePlans"]]
        )
        for exp in expected["equipment"]:
            got = self.equipment(data, exp["id"])
            for key, value in exp.items():
                if key in ("components", "maintenance"):
                    continue
                with self.subTest(equipment=exp["id"], field=key):
                    self.assertEqual(
                        sorted(got[key]) if key == "preventivePlanIds" else got[key],
                        sorted(value) if key == "preventivePlanIds" else value,
                    )
            self.assertEqual(
                [{k: c[k] for k in ec} for c, ec in zip(got["components"], exp["components"])],
                exp["components"],
            )
            for c in got["components"]:
                self.assertEqual((c["retired"], c["previousEquipmentIds"]), (False, []))
            self.assertEqual([m["id"] for m in got["maintenance"]], [m["id"] for m in exp["maintenance"]])
            for m_exp, m_got in zip(exp["maintenance"], got["maintenance"]):
                with self.subTest(maintenance=m_exp["id"]):
                    for key in ("date", "type", "description", "areas", "notes", "checks"):
                        self.assertEqual(m_got[key], m_exp[key])
                    for key, rows in m_exp["results"].items():
                        self.assertEqual(
                            m_got["results"].get(key) or [], rows, "listas vazias podem ser omitidas"
                        )
        for p_exp in expected["preventivePlans"]:
            p_got = next(p for p in data["preventivePlans"] if p["id"] == p_exp["id"])
            for key in ("name", "intervalMonths", "active", "notes"):
                self.assertEqual(p_got[key], p_exp[key])

    def test_ordem_de_equipamentos_e_componentes_persiste(self):
        data = self.client.get_data()
        data["equipment"].reverse()
        srv = self.equipment(data, "eq-srv01")
        srv["components"].reverse()
        self.assertEqual(self.client.save(data)[0], 200)
        again = self.client.get_data()
        self.assertEqual([e["id"] for e in again["equipment"]], ["eq-rpi01", "eq-nas01", "eq-srv01"])
        self.assertEqual(
            [c["id"] for c in self.equipment(again, "eq-srv01")["components"]],
            [c["id"] for c in srv["components"]],
        )

    def test_apagar_equipamento_remove_dependentes(self):
        data = self.client.get_data()
        data["equipment"] = [e for e in data["equipment"] if e["id"] != "eq-nas01"]
        self.assertEqual(self.client.save(data)[0], 200)
        again = self.client.get_data()
        self.assertNotIn("eq-nas01", [e["id"] for e in again["equipment"]])
        self.assertEqual(len(again["preventivePlans"]), 3, "o catálogo não depende de um equipamento")

    def _assert_rejected(self, mutate, message_hint=None):
        before = self.client.get_data()
        data = copy.deepcopy(before)
        mutate(data)
        status, body = self.client.save(data)
        self.assertEqual(status, 400, body)
        if message_hint:
            self.assertIn(message_hint.lower(), json.dumps(body, ensure_ascii=False).lower())
        self.assertEqual(self.client.get_data(), before, "uma gravação rejeitada não pode alterar nada")

    def test_rejeita_id_de_equipamento_duplicado(self):
        self._assert_rejected(lambda d: d["equipment"].append(copy.deepcopy(d["equipment"][2])))

    def test_rejeita_id_de_componente_repetido_noutro_equipamento(self):
        def mutate(d):
            dup = copy.deepcopy(self.equipment(d, "eq-srv01")["components"][4])
            self.equipment(d, "eq-rpi01")["components"].append(dup)

        self._assert_rejected(mutate)

    def test_rejeita_id_com_caracteres_invalidos(self):
        def mutate(d):
            self.equipment(d, "eq-rpi01")["id"] = "rpi'01"

        self._assert_rejected(mutate)

    def test_rejeita_smart_ligado_a_componente_que_nao_e_disco(self):
        def mutate(d):
            self.equipment(d, "eq-srv01")["maintenance"][0]["results"]["smartDisks"][0][
                "deviceId"
            ] = "c-srv01-ram1"

        self._assert_rejected(mutate)

    def test_rejeita_smart_ligado_a_disco_de_outro_equipamento(self):
        def mutate(d):
            self.equipment(d, "eq-srv01")["maintenance"][0]["results"]["smartDisks"][0][
                "deviceId"
            ] = "c-nas01-hdd1"

        self._assert_rejected(mutate)

    def test_rejeita_ram_ligada_a_componente_que_nao_e_ram(self):
        def mutate(d):
            self.equipment(d, "eq-srv01")["maintenance"][0]["results"]["ramRows"][0][
                "ramComponentId"
            ] = "c-srv01-ssd"

        self._assert_rejected(mutate)

    def test_rejeita_preventiva_inexistente_numa_manutencao(self):
        def mutate(d):
            self.equipment(d, "eq-srv01")["maintenance"][0]["results"]["preventivePlanIds"].append(
                "p-nao-existe"
            )

        self._assert_rejected(mutate)

    def test_rejeita_preventiva_inexistente_associada_a_equipamento(self):
        self._assert_rejected(
            lambda d: self.equipment(d, "eq-rpi01")["preventivePlanIds"].append("p-nao-existe")
        )

    def test_rejeita_intervalo_decimal(self):
        def mutate(d):
            d["preventivePlans"][0]["intervalMonths"] = 2.5

        self._assert_rejected(mutate)

    def test_tipo_de_componente_legado_e_normalizado(self):
        data = self.client.get_data()
        self.equipment(data, "eq-rpi01")["components"].append(
            {
                "id": "c-rpi01-sd",
                "name": "Cartão SD",
                "type": "ssd",
                "model": "",
                "serial": "",
                "capacity": "32 GB",
                "health": "ok",
            }
        )
        self.assertEqual(self.client.save(data)[0], 200)
        comp = self.equipment(self.client.get_data(), "eq-rpi01")["components"][0]
        self.assertEqual(comp["type"], "storage")

    def test_P02_formato_antigo_em_lista_e_recusado(self):
        before = self.client.get_data()
        legacy = [{"id": "eq-old", "name": "Servidor antigo", "components": [], "maintenance": []}]
        status, _ = self.client.save(legacy)
        self.assertEqual(status, 400)
        self.assertEqual(self.client.get_data(), before)

    def test_pedido_com_origin_proprio_e_aceite(self):
        payload = self.client.get_payload()
        data = dict(payload["data"], baseRevision=payload["revision"])
        status, _ = self.client.request("PUT", "/api/data", data, headers={"Origin": self.server.url("")})
        self.assertEqual(status, 200)


class TestAnexos(ServerTestCase):
    def test_upload_listagem_e_resumo(self):
        for owner_type, owner_id in [
            ("equipment", "eq-srv01"),
            ("component", "c-srv01-ssd"),
            ("maintenance", "m-srv01-2"),
        ]:
            with self.subTest(owner=owner_type):
                status, body = self.client.upload(
                    owner_type, owner_id, "fatura.pdf", b"%PDF-1.4 teste", "application/pdf", "documentation"
                )
                self.assertEqual(status, 201, body)
                status, body = self.client.request(
                    "GET", f"/api/attachments?ownerType={owner_type}&ownerId={owner_id}"
                )
                self.assertEqual(status, 200)
                self.assertEqual([a["original_name"] for a in body["attachments"]], ["fatura.pdf"])
        status, body = self.client.request("GET", "/api/attachments/summary")
        self.assertEqual(status, 200)
        self.assertEqual(len(self.attachment_files()), 3)

    def test_download_devolve_os_bytes_exatos(self):
        cases = {
            "aleatorio": os.urandom(300_000),
            "termina_cr": b"abc\r",
            "termina_lf": b"abc\n",
            "termina_crlf": b"abc\r\n",
            "so_crlf": b"\r\n\r\n",
            "tipo_boundary": b"--xyz\r\n--\r\nfim",
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                status, body = self.client.upload("equipment", "eq-srv01", name + ".bin", content)
                self.assertEqual(status, 201)
                status, got, _ = self.client.request(
                    "GET", f"/api/attachments/{body['attachment']['id']}/download", raw=True
                )
                self.assertEqual(status, 200)
                self.assertEqual(got, content)

    def test_disposicao_inline_so_para_tipos_seguros(self):
        pdf = self.client.upload("equipment", "eq-srv01", "a.pdf", b"%PDF", "application/pdf")[1][
            "attachment"
        ]["id"]
        html = self.client.upload("equipment", "eq-srv01", "a.html", b"<script>1</script>", "text/html")[1][
            "attachment"
        ]["id"]
        _, _, h_pdf = self.client.request("GET", f"/api/attachments/{pdf}/download", raw=True)
        _, _, h_html = self.client.request("GET", f"/api/attachments/{html}/download", raw=True)
        self.assertTrue(h_pdf["Content-Disposition"].startswith("inline"))
        self.assertTrue(h_html["Content-Disposition"].startswith("attachment"))
        self.assertEqual(h_html["X-Content-Type-Options"], "nosniff")

    def test_nomes_com_acentos_e_caminhos(self):
        status, body = self.client.upload(
            "equipment", "eq-srv01", "relatório_ção.pdf", b"%PDF", "application/pdf", name="Relatório SMART"
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["attachment"]["original_name"], "relatório_ção.pdf")
        status, body = self.client.upload("equipment", "eq-srv01", "../../etc/passwd", b"x")
        self.assertEqual(status, 201)
        self.assertEqual(body["attachment"]["original_name"], "passwd")

    def test_renomear_e_apagar(self):
        aid = self.client.upload("equipment", "eq-srv01", "a.txt", b"x", "text/plain")[1]["attachment"]["id"]
        status, body = self.client.request(
            "PUT", f"/api/attachments/{aid}", {"displayName": "Novo nome", "category": "smart"}
        )
        self.assertEqual(status, 200, body)
        listing = self.client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1][
            "attachments"
        ]
        self.assertEqual((listing[0]["display_name"], listing[0]["category"]), ("Novo nome", "smart"))
        self.assertEqual(self.client.request("DELETE", f"/api/attachments/{aid}")[0], 200)
        self.assertEqual(self.client.request("GET", f"/api/attachments/{aid}/download", raw=True)[0], 404)
        self.assertEqual(self.attachment_files(), [])

    def test_dono_inexistente_e_rejeitado(self):
        status, _ = self.client.upload("equipment", "eq-nao-existe", "a.txt", b"x")
        self.assertEqual(status, 400)
        self.assertEqual(self.attachment_files(), [])

    def test_apagar_equipamento_apaga_os_seus_anexos(self):
        self.client.upload("equipment", "eq-nas01", "a.txt", b"x")
        self.client.upload("component", "c-nas01-hdd1", "b.txt", b"y")
        keep = self.client.upload("equipment", "eq-srv01", "c.txt", b"z")[1]["attachment"]["id"]
        data = self.client.get_data()
        data["equipment"] = [e for e in data["equipment"] if e["id"] != "eq-nas01"]
        self.assertEqual(self.client.save(data)[0], 200)
        self.assertEqual(self.attachment_files(), [keep])


class TestSnapshot(ServerTestCase):
    def test_snapshot_tem_bd_anexos_e_hashes(self):
        content = b"conteudo do anexo"
        aid = self.client.upload("equipment", "eq-srv01", "a.txt", content)[1]["attachment"]["id"]
        with zipfile.ZipFile(io.BytesIO(self.client.snapshot())) as z:
            names = set(z.namelist())
            manifest = json.loads(z.read("manifest.json"))
            self.assertIn("database/homelab.db", names)
            self.assertIn(f"attachments/{aid}", names)
            entry = next(a for a in manifest["attachments"] if a["id"] == aid)
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())

    def test_validacao_devolve_contagens(self):
        status, body = self.client.restore(self.client.snapshot(), validate_only=True)
        self.assertEqual(status, 200, body)
        s = body["summary"]
        self.assertEqual(
            (s["equipment_count"], s["component_count"], s["maintenance_count"], s["preventive_plan_count"]),
            (3, 7, 3, 3),
        )

    def test_restore_repoe_dados_e_anexos(self):
        aid = self.client.upload("equipment", "eq-srv01", "a.txt", b"original")[1]["attachment"]["id"]
        snap = self.client.snapshot()
        before = self.client.get_data()
        data = self.client.get_data()
        data["equipment"][0]["name"] = "ALTERADO"
        self.client.save(data)
        self.client.request("DELETE", f"/api/attachments/{aid}")
        status, body = self.client.restore(snap)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.client.get_data(), before)
        status, got, _ = self.client.request("GET", f"/api/attachments/{aid}/download", raw=True)
        self.assertEqual((status, got), (200, b"original"))

    def test_restore_sem_confirmacao_e_recusado(self):
        snap = self.client.snapshot()
        data = self.client.get_data()
        data["equipment"][0]["name"] = "ALTERADO"
        self.client.save(data)
        self.assertEqual(self.client.restore(snap, confirm=False)[0], 400)
        self.assertEqual(self.client.get_data()["equipment"][0]["name"], "ALTERADO")

    def test_zip_invalido_e_recusado(self):
        self.assertEqual(self.client.restore(b"isto nao e um zip", validate_only=True)[0], 400)

    def _rezip(self, snap, edit):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(snap)) as src, zipfile.ZipFile(out, "w") as dst:
            files = {n: src.read(n) for n in src.namelist()}
            edit(files)
            for n, b in files.items():
                dst.writestr(n, b)
        return out.getvalue()

    def test_zip_com_caminho_perigoso_e_recusado(self):
        bad = self._rezip(self.client.snapshot(), lambda f: f.__setitem__("../fora.txt", b"x"))
        self.assertEqual(self.client.restore(bad, validate_only=True)[0], 400)

    def test_anexo_com_hash_errado_e_recusado(self):
        aid = self.client.upload("equipment", "eq-srv01", "a.txt", b"original")[1]["attachment"]["id"]
        bad = self._rezip(
            self.client.snapshot(), lambda f: f.__setitem__(f"attachments/{aid}", b"adulterado")
        )
        self.assertEqual(self.client.restore(bad, validate_only=True)[0], 400)


class TestEstaticos(ServerTestCase):
    load_dataset = False

    def test_raiz_serve_a_aplicacao(self):
        status, body, headers = Client(self.server).request("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("X-Frame-Options", headers)

    def test_versao_unica_no_index_no_service_worker_e_na_api(self):
        """P21: uma só versão (APP_VERSION), injetada pelo servidor; nada fica por substituir."""
        anon = Client(self.server)
        version = anon.request("GET", "/api/health")[1]["version"]
        _, index, h_index = anon.request("GET", "/", raw=True)
        _, sw, h_sw = anon.request("GET", "/static/sw.js", raw=True)
        for needle in (f"app.js?v={version}", f"app.css?v={version}", f'content="{version}"'):
            self.assertIn(needle.encode(), index)
        self.assertIn(f'const VERSION = "{version}"'.encode(), sw)
        self.assertNotIn(b"__APP_VERSION__", index + sw)
        self.assertEqual((h_index["Cache-Control"], h_sw["Cache-Control"]), ("no-cache", "no-cache"))
        self.assertEqual(h_sw["Service-Worker-Allowed"], "/")

    def test_path_traversal_nao_sai_de_static(self):
        anon = Client(self.server)
        for path in [
            "/static/../backend/config.py",
            "/static/%2e%2e/backend/config.py",
            "/static/..%2fbackend%2fconfig.py",
        ]:
            with self.subTest(path=path):
                status, body, _ = anon.request("GET", path, raw=True)
                self.assertNotEqual(status, 200)
                self.assertNotIn(b"AUTH_PASSWORD", body)


if __name__ == "__main__":
    unittest.main()
