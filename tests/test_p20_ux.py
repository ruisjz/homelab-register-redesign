"""P20 — pequenos acertos de UX."""

import os
import unittest

from ui_base import UITestCase

RECORD_SEND = """() => {
  window.__sent = []; window.__progress = [];
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (body) {
    this.upload.addEventListener("progress", (e) => window.__progress.push(e.loaded));
    window.__sent.push({
      attachment: document.querySelector("#attachmentProgress")?.hidden,
      restore: document.querySelector("#restoreProgress")?.hidden,
      button: document.querySelector("#drawer footer button.accent")?.disabled,
    });
    return send.call(this, body);
  };
}"""


class TestModais(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()
        self.js("detail('eq-srv01')")

    def is_open(self):
        self.page.wait_for_timeout(150)
        return self.page.evaluate("document.body.classList.contains('modal-open')")

    def test_fechar_sem_alteracoes_nao_pergunta(self):
        for opener in (
            "editEquipment('eq-srv01')",
            "editMaintenance('eq-srv01')",
            "manageAttachments('equipment','eq-srv01')",
        ):
            with self.subTest(opener=opener):
                self.js(opener)
                self.page.fill("#attachmentSearch", "fatura") if "Attach" in opener else None
                self.page.click("#drawer header button")
                self.assertFalse(self.is_open())
        self.assertEqual(self.dialogs, [])

    def test_alteracoes_por_guardar_pedem_confirmacao_em_todas_as_formas_de_fechar(self):
        closers = {
            "×": lambda: self.page.click("#drawer header button"),
            "Cancelar": lambda: self.page.click("#drawer footer button:has-text('Cancelar')"),
            "clique fora": lambda: self.page.evaluate("document.querySelector('#drawer .shade').click()"),
            "Esc": lambda: self.page.keyboard.press("Escape"),
            "voltar": lambda: self.page.go_back(),
        }
        for name, close in closers.items():
            with self.subTest(close=name):
                self.dialogs.clear()
                self.js("editEquipment('eq-srv01')")
                self.page.fill("#ef input[name=location]", "Alterado")
                self.dismiss_dialogs = True  # «Cancelar» no aviso: fica no modal
                close()
                self.assertTrue(self.is_open())
                self.assertTrue(any("alterações por guardar" in d for d in self.dialogs))
                self.dismiss_dialogs = False  # «OK»: descarta
                close()
                self.assertFalse(self.is_open())
        self.assertEqual(self.equipment(self.client.get_data(), "eq-srv01")["location"], "Bastidor")

    def test_foco_volta_ao_botao_que_abriu(self):
        self.page.click("button:has-text('Editar')")
        self.page.click("#drawer header button")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.evaluate("document.activeElement.textContent"), "Editar")

    def test_erro_poe_o_foco_no_campo(self):
        self.js("editEquipment('eq-srv01')")
        self.page.fill("#ef input[name=name]", "")
        self.click_save()
        self.assertIn("Indica um nome.", self.dialogs)
        self.assertEqual(
            self.page.evaluate(
                "[document.activeElement.name, document.activeElement.getAttribute('aria-invalid')]"
            ),
            ["name", "true"],
        )

    def test_erro_na_manutencao_foca_o_disco_em_falta(self):
        self.js("editMaintenance('eq-srv01')")
        self.page.click("button.tab:has-text('SMART')")
        self.page.click("#tab-smart >> text=+ Adicionar")
        self.page.fill("#rows-smart input >> nth=0", "97%")
        self.click_save()
        self.assertTrue(any("disco de cada registo SMART" in d for d in self.dialogs))
        self.assertEqual(
            self.page.evaluate("document.activeElement.tagName + '|' + document.activeElement.value"),
            "SELECT|",
        )


class TestUploads(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def file(self, name, size):
        path = os.path.join(self.server.tmp, name)
        with open(path, "wb") as f:
            f.write(os.urandom(size))
        return path

    def test_anexo_nome_preenchido_e_progresso(self):
        self.js("manageAttachments('equipment','eq-srv01')")
        self.page.set_input_files("#attachmentFile", self.file("relatório-SMART.bin", 3 * 1024 * 1024))
        self.assertEqual(self.page.input_value("#attachmentName"), "relatório-SMART")
        self.page.evaluate(RECORD_SEND)
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(800)
        sent = self.page.evaluate("window.__sent")
        self.assertEqual(
            sent,
            [{"attachment": False, "restore": None, "button": True}],
            "progresso visível e botão bloqueado",
        )
        self.assertTrue(self.page.evaluate("window.__progress.length > 0"))
        files = self.client.request("GET", "/api/attachments?ownerType=equipment&ownerId=eq-srv01")[1][
            "attachments"
        ]
        self.assertEqual([a["display_name"] for a in files], ["relatório-SMART"])

    def test_nome_escrito_nao_e_substituido(self):
        self.js("manageAttachments('equipment','eq-srv01')")
        self.page.fill("#attachmentName", "Fatura")
        self.page.set_input_files("#attachmentFile", self.file("x.pdf", 10))
        self.assertEqual(self.page.input_value("#attachmentName"), "Fatura")

    def test_restore_mostra_progresso(self):
        path = os.path.join(self.server.tmp, "snap.zip")
        with open(path, "wb") as f:
            f.write(self.client.snapshot())
        self.js("go('backup')")
        self.page.evaluate(RECORD_SEND)
        self.page.set_input_files("#snapshotFile", path)
        self.dismiss_dialogs = True  # não confirmar o restore
        self.page.click("#restoreBtn")
        self.page.wait_for_timeout(800)
        self.assertEqual(self.page.evaluate("window.__sent[0].restore"), False)
        self.assertTrue(any("SNAPSHOT VÁLIDO" in d for d in self.dialogs))


class TestPesquisaNotasArranque(UITestCase):
    def test_pesquisa_so_nos_valores_visiveis(self):
        self.open_app()
        self.js("go('equipment')")
        cases = {
            "health": [],
            "retired": [],
            "eq-": [],
            "preventiveplanids": [],  # nomes internos e IDs não encontram nada («name» está em «Armazenamento»)
            "operacional": ["srv01", "nas01"],
            "offline": ["rpi01"],
            "ironwolf": ["nas01"],
            "ventoinha": ["srv01"],
            "32 gb ddr4": ["srv01"],
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.page.fill("#q", query)
                found = self.page.eval_on_selector_all(
                    "#elist h3, #elist .equip-card h3", "els => els.map(e => e.textContent.trim())"
                )
                self.assertEqual(sorted(found), sorted(expected))

    def test_notas_no_detalhe(self):
        self.open_app()
        self.js("detail('eq-srv01')")
        self.assertIn("Notas\nNó principal do cluster", self.page.inner_text("main"))

    def test_tentar_de_novo_sem_ligacao(self):
        self.open_app()
        self.server.stop()
        self.page.reload()
        self.page.wait_for_selector("main.fatal")
        self.assertIn("Sem ligação ao servidor", self.page.inner_text("main.fatal"))
        self.server.start()
        self.page.click("text=Tentar de novo")
        self.page.wait_for_selector("#side, #u")
        if self.page.query_selector("#u"):  # o reinício terminou a sessão
            self.page.fill("#u", self.server.user)
            self.page.fill("#pw", self.server.password)
            self.page.click("button.accent")
        self.page.wait_for_selector("#side")


class TestMobile(UITestCase):
    viewport = {"width": 390, "height": 844}
    mobile = True

    def test_detalhes_mobile(self):
        self.open_app()
        self.js("detail('eq-srv01')")
        columns = self.page.evaluate(
            "getComputedStyle(document.querySelector('.top-actions')).gridTemplateColumns.split(' ').length"
        )
        self.assertEqual(columns, 2, "botões do topo em 2 colunas")
        self.assertEqual(
            self.page.evaluate("getComputedStyle(document.querySelector('#side')).boxShadow"), "none"
        )
        self.js("editMaintenance('eq-srv01')")
        rights = self.page.eval_on_selector_all(
            "button.tab", "els => els.map(e => e.getBoundingClientRect().right)"
        )
        self.assertTrue(rights and max(rights) <= 390, f"todos os separadores visíveis: {rights}")


if __name__ == "__main__":
    unittest.main()
