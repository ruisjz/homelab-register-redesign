"""Bugs da auditoria já corrigidos (lado da interface; opcional, precisa de Playwright)."""

import base64
import io
import os
import socket
import zipfile
import unittest

from harness import Client
from ui_base import UITestCase


class TestGravacoesSeguras(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def fresh_admin(self):
        admin = Client(self.server)
        admin.login()
        return admin

    def test_B04_P03_sessao_expirada_nao_deixa_o_ecra_diferente_do_servidor(self):
        self.server.restart()  # sessões em memória perdidas, como num reinício do contentor
        self.js("editEquipment('eq-srv01')")
        self.page.fill("#ef input[name=location]", "Bastidor 2")
        self.click_save()
        server_value = self.equipment(self.fresh_admin().get_data(), "eq-srv01")["location"]
        screen_value = self.page.evaluate("state.find(e => e.id === 'eq-srv01').location")
        self.assertEqual(screen_value, server_value)
        self.assertTrue(any("sessão terminou" in d for d in self.dialogs))

    def test_P03_depois_do_login_volta_ao_mesmo_equipamento(self):
        self.js("detail('eq-nas01')")
        self.server.restart()
        self.js("editEquipment('eq-nas01')")
        self.page.fill("#ef input[name=location]", "Outro sítio")
        self.click_save()
        self.page.wait_for_selector("#u")
        self.page.fill("#u", self.server.user)
        self.page.fill("#pw", self.server.password)
        self.page.click("button.accent")
        self.page.wait_for_selector("#side")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.evaluate("activeId"), "eq-nas01")

    def test_P03_servidor_em_baixo_repoe_o_estado_e_avisa(self):
        self.server.stop()
        self.js("deleteEquipment('eq-rpi01')")
        self.page.wait_for_timeout(300)
        self.assertIn("eq-rpi01", self.page.evaluate("state.map(e => e.id)"))
        self.assertTrue(any("Sem ligação ao servidor" in d for d in self.dialogs))

    def test_B11_P03_valor_invalido_nao_bloqueia_gravacoes_seguintes(self):
        self.js("editPlan('p-limpeza')")
        self.page.fill("#pf input[name=intervalMonths]", "2.5")
        self.click_save()
        self.assertTrue(any("Não foi possível gravar" in d for d in self.dialogs))
        self.assertEqual(
            self.page.evaluate("preventivePlans.find(p => p.id === 'p-limpeza').intervalMonths"), 6
        )
        self.js("closeModal()")
        self.js("editEquipment('eq-srv01')")
        self.page.fill("#ef input[name=location]", "Bastidor 3")
        self.click_save()
        self.assertEqual(self.equipment(self.client.get_data(), "eq-srv01")["location"], "Bastidor 3")

    def test_B03_P04_conflito_avisa_e_so_recarrega_se_confirmado(self):
        admin = self.fresh_admin()
        data = admin.get_data()
        self.equipment(data, "eq-srv01")["location"] = "Alterado no PC"
        self.assertEqual(admin.save(data)[0], 200)
        self.dismiss_dialogs = True  # utilizador escolhe "Cancelar"
        self.js("editEquipment('eq-nas01')")
        self.page.fill("#ef input[name=location]", "Alterado no telemóvel")
        self.click_save()
        self.assertTrue(any("alterados noutro dispositivo" in d for d in self.dialogs))
        self.assertEqual(self.page.evaluate("state.find(e => e.id === 'eq-nas01').location"), "Bastidor")
        self.assertEqual(self.page.evaluate("state.find(e => e.id === 'eq-srv01').location"), "Bastidor")
        self.assertEqual(self.equipment(admin.get_data(), "eq-nas01")["location"], "Bastidor")
        self.dismiss_dialogs = False  # agora "OK": recarrega e mostra os dados atuais
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(800)
        self.page.wait_for_selector("#side")
        self.assertEqual(
            self.page.evaluate("state.find(e => e.id === 'eq-srv01').location"), "Alterado no PC"
        )


class TestEditorDeManutencao(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def test_B08_P08_adicionar_linha_mantem_o_que_ja_foi_escrito(self):
        self.js("editMaintenance('eq-srv01')")
        self.page.click("button.tab:has-text('Temperaturas')")
        self.page.click("#tab-temp >> text=+ Adicionar")
        self.page.fill("#rows-temp input[data-k='sensor']", "CPU Package")
        self.page.click("#tab-temp >> text=+ Adicionar")
        values = self.page.eval_on_selector_all(
            "#rows-temp input[data-k='sensor']", "els => els.map(e => e.value)"
        )
        self.assertEqual(values[0], "CPU Package")

    def test_B09_P08_editar_manutencao_mantem_preventiva_desassociada(self):
        data = self.client.get_data()
        self.equipment(data, "eq-srv01")["preventivePlanIds"] = ["p-limpeza"]  # retira p-ram do equipamento
        self.assertEqual(self.client.save(data)[0], 200)
        self.open_app()
        self.js("editMaintenance('eq-srv01', 'm-srv01-2')")
        self.page.fill("#mf input[name=description]", "Limpeza e testes (corrigido)")
        self.click_save()
        m = self.equipment(self.client.get_data(), "eq-srv01")["maintenance"][0]
        self.assertIn("p-ram", m["results"]["preventivePlanIds"])

    def test_P08_remover_linha_mantem_as_outras(self):
        self.js("editMaintenance('eq-srv01')")
        self.page.click("button.tab:has-text('Temperaturas')")
        for _ in range(3):
            self.page.click("#tab-temp >> text=+ Adicionar")
        sensors = self.page.locator("#rows-temp input[data-k='sensor']")
        for i, name in enumerate(["CPU", "GPU", "NVMe"]):
            sensors.nth(i).fill(name)
        self.page.locator("#rows-temp .repeat").nth(0).locator("button:has-text('Remover')").click()
        values = self.page.eval_on_selector_all(
            "#rows-temp input[data-k='sensor']", "els => els.map(e => e.value)"
        )
        self.assertEqual(values, ["GPU", "NVMe"])


class TestEscape(UITestCase):
    def test_S03_P12_datas_sao_escapadas_no_ecra(self):
        """O servidor já recusa datas inválidas; aqui testa-se o escape no ecrã, com dados injetados na página."""
        self.open_app()
        self.page.evaluate(
            """() => { const d = '9999-12-31<img src=x onerror="window.__xss=1">';
               state[2].maintenance.push({id: 'm-x', date: d, type: 't', checks: {}, results: {preventivePlanIds: ['p-limpeza']}});
               state[2].preventivePlanIds = ['p-limpeza']; go('dashboard'); detail(state[2].id); }"""
        )
        self.page.wait_for_timeout(300)
        self.assertFalse(self.page.evaluate("window.__xss === 1"))


def lan_ip():
    """IP não-loopback desta máquina: em 127.0.0.1 o browser aceita cookies Secure por HTTP."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip


class TestCookieRecusado(UITestCase):
    load_dataset = False
    server_env = {"COOKIE_SECURE": "true", "HOST": "0.0.0.0"}

    def test_U07_P14_login_explica_quando_o_cookie_e_recusado(self):
        ip = lan_ip()
        if not ip:
            self.skipTest("sem IP de rede para simular um acesso por http://IP")
        self.page.goto(f"http://{ip}:{self.server.port}/")
        self.page.fill("#u", self.server.user)
        self.page.fill("#pw", self.server.password)
        self.page.click("button.accent")
        self.page.wait_for_timeout(1000)
        self.assertIn("COOKIE_SECURE=false", self.page.inner_text("body"))


class TestAnexoEmFaltaNaInterface(UITestCase):
    def test_P16_anexo_em_falta_fica_marcado_e_pode_ser_apagado(self):
        import os

        aid = self.client.upload("equipment", "eq-srv01", "perdido.pdf", b"%PDF", "application/pdf")[1][
            "attachment"
        ]["id"]
        os.remove(os.path.join(self.server.data_dir, "attachments", aid))
        self.open_app()
        self.js("manageAttachments('equipment', 'eq-srv01')")
        self.page.wait_for_timeout(300)
        row = self.page.locator(".attachment-row.missing")
        self.assertEqual(row.count(), 1)
        self.assertIn("EM FALTA", row.inner_text())
        self.assertEqual(row.locator("a").count(), 0, "sem link para um ficheiro que não existe")
        row.locator("button.danger").click()
        self.page.wait_for_timeout(500)
        self.assertEqual(
            self.client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1][
                "attachments"
            ],
            [],
        )


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestFicheirosPelaInterface(UITestCase):
    """P15: anexos, backup e restore pela interface, com o novo protocolo em streaming."""

    def setUp(self):
        super().setUp()
        self.open_app()

    def test_P15_anexo_pela_interface_chega_igual_e_miniatura_e_lazy(self):
        path = os.path.join(self.server.tmp, "fotografia ção.png")
        with open(path, "wb") as f:
            f.write(PNG_1PX)
        self.js("manageAttachments('equipment', 'eq-srv01')")
        self.page.wait_for_selector("#attachmentFile")
        self.page.set_input_files("#attachmentFile", path)
        self.page.fill("#attachmentName", "Fotografia do bastidor")
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(800)
        att = self.client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1][
            "attachments"
        ][0]
        self.assertEqual(
            (att["display_name"], att["original_name"], att["mime_type"]),
            ("Fotografia do bastidor", "fotografia ção.png", "image/png"),
        )
        content = self.client.request("GET", f"/api/attachments/{att['id']}/download", raw=True)[1]
        self.assertEqual(content, PNG_1PX)
        self.page.wait_for_selector("img.attachment-thumb")
        self.assertEqual(self.page.get_attribute("img.attachment-thumb", "loading"), "lazy")

    def test_P15_backup_e_restore_pela_interface(self):
        self.client.upload("equipment", "eq-srv01", "fatura.pdf", b"%PDF-1.4 teste", "application/pdf")
        original = self.client.get_data()
        self.js("go('backup')")
        with self.page.expect_download() as info:
            self.page.click("#fullBackupBtn")
        archive = os.path.join(self.server.tmp, "backup.zip")
        info.value.save_as(archive)
        with zipfile.ZipFile(archive) as z:
            self.assertIn("manifest.json", z.namelist())
            self.assertIsNone(z.testzip())
        changed = self.client.get_data()
        changed["equipment"][0]["name"] = "ALTERADO DEPOIS DO BACKUP"
        self.client.save(changed)
        self.open_app()
        self.js("go('backup')")
        self.page.set_input_files("input[type=file]", archive)
        self.page.click("#restoreBtn")
        self.page.wait_for_timeout(3000)
        self.assertTrue(any("restaurado com sucesso" in d for d in self.dialogs), self.dialogs)
        self.assertEqual(self.client.get_data(), original)


class TestImpressao(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def test_U01_P18_imprimir_pagina_de_manutencao_nao_sai_em_branco(self):
        self.js("go('maintenance')")
        self.page.emulate_media(media="print")
        visible = self.page.evaluate(
            "[...document.body.children].filter(e => getComputedStyle(e).display !== 'none').length"
        )
        self.assertGreater(visible, 0)


class TestEstadoDatasAtalhos(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def test_U04_P19_estado_offline_tem_rotulo_proprio(self):
        self.assertIn("Offline", self.page.evaluate("badge('offline')"))

    def test_U05_P19_proxima_data_certa_no_horario_de_verao(self):
        self.assertEqual(self.page.evaluate("addMonths('2026-03-15', 6)"), "2026-09-15")

    def test_U06_P19_atalho_pwa_abre_a_pagina_certa(self):
        self.open_app("/?page=equipment")
        self.assertEqual(self.page.evaluate("page"), "equipment")


class TestFocoModal(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def test_U12_P20_fechar_modal_nao_gera_erro_js(self):
        self.js("editEquipment('eq-srv01')")
        self.js("closeModal()")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.js_errors, [])


class TestLegadoSmartRam(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    COMPONENTS = """[{id: 'c-a', name: 'SSD sistema', type: 'storage', model: 'Samsung 870', serial: '', capacity: '500GB'},
                     {id: 'c-b', name: 'HDD dados', type: 'storage', model: 'WD Red', serial: 'WX123', capacity: '4TB'}]"""

    def test_B10_P26_linhas_smart_legadas_vao_para_o_disco_certo(self):
        ids = self.page.evaluate(
            f"""() => resolveSmartRows({{components: {self.COMPONENTS}}},
            [{{device: 'HDD dados'}}, {{device: 'WD Red 4TB'}}]).map(x => x.deviceId)"""
        )
        self.assertEqual(ids, ["c-b", "c-b"])

    def test_P26_texto_sem_correspondencia_fica_sem_disco(self):
        ids = self.page.evaluate(
            f"""() => resolveSmartRows({{components: {self.COMPONENTS}}},
            [{{device: 'Toshiba'}}, {{device: '   '}}]).map(x => x.deviceId ?? null)"""
        )
        self.assertEqual(ids, [None, None])

    def test_P26_modulos_ram_legados_ignoram_campos_vazios(self):
        ids = self.page.evaluate(
            """() => resolveRamRows({components: [
                {id: 'r-1', name: 'DIMM A', type: 'ram', model: '', serial: '', capacity: '8GB'},
                {id: 'r-2', name: 'DIMM B', type: 'ram', model: 'Kingston Fury', serial: 'K2', capacity: '16GB'}]},
              [{module: 'Kingston Fury 16GB'}, {module: 'DIMM A'}]).map(x => x.ramComponentId)"""
        )
        self.assertEqual(ids, ["r-2", "r-1"])


class TestEnterNosModais(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()
        self.js("detail('eq-srv01')")
        self.page.evaluate("window.__semRecarregar = true")

    def test_U08_P27_enter_no_nome_do_anexo_nao_recarrega_a_pagina(self):
        for opener in ("manageAttachments('equipment','eq-srv01')",):
            with self.subTest(opener=opener):
                self.js(opener)
                self.page.wait_for_selector("#attachmentName")
                self.page.fill("#attachmentName", "Fatura")
                self.page.press("#attachmentName", "Enter")
                self.page.wait_for_timeout(500)
                self.assertTrue(self.page.evaluate("window.__semRecarregar === true"), "a página recarregou")
                self.assertTrue(self.page.evaluate("document.body.classList.contains('modal-open')"))

    def test_U08_P27_enter_ao_editar_anexo_nao_recarrega_a_pagina(self):
        self.client.upload("equipment", "eq-srv01", "a.txt", b"x", "text/plain")
        self.js("manageAttachments('equipment','eq-srv01')")
        self.page.click(".attachment-row button:has-text('Editar')")
        self.page.wait_for_selector("#attachmentName")
        self.page.press("#attachmentName", "Enter")
        self.page.wait_for_timeout(500)
        self.assertTrue(self.page.evaluate("window.__semRecarregar === true"), "a página recarregou")


class TestApagarEquipamentoAvisa(UITestCase):
    """B15 (P28): a confirmação diz o que se perde além do equipamento."""

    def setUp(self):
        super().setUp()
        # O disco c-srv01-hdd tem SMART em srv01 (m-srv01-2) e passa para nas01.
        data = self.client.get_data()
        srv, nas = data["equipment"][0], data["equipment"][1]
        disk = next(c for c in srv["components"] if c["id"] == "c-srv01-hdd")
        srv["components"].remove(disk)
        disk["previousEquipmentIds"] = ["eq-srv01"]
        nas["components"].append(disk)
        self.assertEqual(self.client.save(data)[0], 200)
        self.client.upload("component", "c-srv01-cpu", "fatura.pdf", b"%PDF", "application/pdf")
        self.client.upload("maintenance", "m-srv01-1", "log.txt", b"x", "text/plain")
        self.open_app()

    def test_B15_P28_confirmacao_indica_anexos_e_historico_de_componentes_transferidos(self):
        self.dismiss_dialogs = True
        self.js("deleteEquipment('eq-srv01')")
        self.assertEqual(len(self.dialogs), 1, self.dialogs)
        message = self.dialogs[0]
        self.assertIn("2 anexos", message)
        self.assertIn("HDD", message)
        self.assertIn("nas01", message)
        self.assertIn("eq-srv01", [e["id"] for e in self.client.get_data()["equipment"]])

    def test_P28_sem_anexos_nem_historico_alheio_a_confirmacao_fica_simples(self):
        self.dismiss_dialogs = True
        self.js("deleteEquipment('eq-rpi01')")
        self.assertEqual(len(self.dialogs), 1, self.dialogs)
        self.assertNotIn("anexo", self.dialogs[0])
        self.assertNotIn("SMART", self.dialogs[0])


if __name__ == "__main__":
    unittest.main()
