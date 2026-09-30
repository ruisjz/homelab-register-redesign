"""P19 — estado Offline, rotas por endereço, «voltar» do Android/PWA e datas locais (Açores)."""

import unittest

from ui_base import UITestCase


class TestRotas(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def route(self):
        self.page.wait_for_timeout(200)
        return self.page.evaluate("[location.hash, page, activeId]")

    def test_cada_pagina_tem_endereco(self):
        for name, hash_ in (
            ("equipment", "#/equipamentos"),
            ("maintenance", "#/manutencao"),
            ("smart", "#/smart"),
            ("backup", "#/backup"),
            ("dashboard", "#/"),
        ):
            with self.subTest(page=name):
                self.js(f"go('{name}')")
                self.assertEqual(self.route()[:2], [hash_, name])

    def test_voltar_e_avancar_como_no_android(self):
        self.js("go('equipment')")
        self.js("detail('eq-srv01')")
        self.assertEqual(self.route(), ["#/equipamentos/eq-srv01", "equipment", "eq-srv01"])
        self.page.go_back()
        self.assertEqual(self.route(), ["#/equipamentos", "equipment", None])
        self.assertIn("Equipamentos", self.page.inner_text("main"))
        self.page.go_back()
        self.assertEqual(self.route()[1], "dashboard")
        self.page.go_forward()
        self.assertEqual(self.route()[1], "equipment")

    def test_endereco_direto_e_recarregar(self):
        self.page.goto(self.server.url("/#/equipamentos/eq-nas01"))
        self.page.wait_for_selector("#side")
        self.assertEqual(self.route(), ["#/equipamentos/eq-nas01", "equipment", "eq-nas01"])
        self.page.reload()
        self.page.wait_for_selector("#side")
        self.assertEqual(self.route()[2], "eq-nas01")
        self.assertIn("nas01", self.page.inner_text("main"))

    def test_endereco_inexistente_mostra_a_lista(self):
        self.page.goto(self.server.url("/#/equipamentos/nao-existe"))
        self.page.wait_for_selector("#side")
        self.assertEqual(self.route()[1:], ["equipment", None])

    def test_detalhe_de_manutencao_voltar_regressa_de_onde_veio(self):
        self.js("detail('eq-srv01')")
        self.js("maintenanceDetail('eq-srv01','m-srv01-2')")
        self.assertEqual(self.route()[0], "#/manutencao/eq-srv01/m-srv01-2")
        self.page.click("button:has-text('← Voltar')")
        self.assertEqual(self.route()[0], "#/equipamentos/eq-srv01")

    def test_atalhos_da_app_instalada(self):
        manifest = self.page.evaluate("fetch('/static/manifest.json').then(r => r.json())")
        self.assertEqual([s["url"] for s in manifest["shortcuts"]], ["/#/equipamentos", "/#/manutencao"])
        for old, hash_ in (("/?page=equipment", "#/equipamentos"), ("/?page=maintenance", "#/manutencao")):
            with self.subTest(url=old):
                self.page.goto(self.server.url(old))
                self.page.wait_for_selector("#side")
                self.assertEqual(self.page.evaluate("[location.hash, location.search]"), [hash_, ""])


class TestVoltarComModal(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()
        self.js("go('equipment')")
        self.js("detail('eq-srv01')")

    def modal_open(self):
        self.page.wait_for_timeout(200)
        return self.page.evaluate("document.body.classList.contains('modal-open')")

    def test_voltar_fecha_primeiro_o_modal(self):
        self.js("editEquipment('eq-srv01')")
        self.assertTrue(self.modal_open())
        self.page.go_back()
        self.assertFalse(self.modal_open())
        self.assertEqual(self.page.evaluate("location.hash"), "#/equipamentos/eq-srv01")
        self.page.go_back()
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.evaluate("location.hash"), "#/equipamentos")

    def test_fechar_modal_e_navegar_nao_e_desfeito(self):
        self.js("editEquipment('eq-rpi01')")
        self.js("deleteEquipment('eq-rpi01')")  # fecha o modal e vai para a lista
        self.page.wait_for_timeout(400)
        self.assertEqual(self.page.evaluate("location.hash"), "#/equipamentos")
        self.assertFalse(self.modal_open())
        self.assertNotIn("eq-rpi01", [e["id"] for e in self.client.get_data()["equipment"]])

    def test_guardar_no_modal_mantem_o_detalhe(self):
        self.js("editEquipment('eq-srv01')")
        self.page.fill("#ef input[name=location]", "Bastidor 9")
        self.click_save()
        self.page.wait_for_timeout(300)
        self.assertEqual(
            self.page.evaluate("[location.hash, activeId]"), ["#/equipamentos/eq-srv01", "eq-srv01"]
        )
        self.assertIn("Bastidor 9", self.page.inner_text("main"))


class TestDatasAcores(UITestCase):
    """Açores: UTC-1 no inverno e UTC+0 no verão (mudança de hora à 00:00 / 01:00 locais)."""

    def at(self, iso):
        page = self.context.new_page()
        page.clock.set_fixed_time(iso)
        page.goto(self.server.url("/"))
        page.wait_for_selector("#side, #u")
        if page.query_selector("#u"):
            page.fill("#u", self.server.user)
            page.fill("#pw", self.server.password)
            page.click("button.accent")
            page.wait_for_selector("#side")
        return page

    def test_hoje_e_o_dia_local_mesmo_quando_em_utc_ja_e_outro_dia(self):
        for iso, expected in (
            ("2026-01-15T23:30:00-01:00", "2026-01-15"),  # inverno: em UTC já é dia 16
            ("2026-07-15T23:30:00+00:00", "2026-07-15"),  # verão
            ("2026-03-28T23:30:00-01:00", "2026-03-28"),  # véspera da mudança para a hora de verão
            ("2026-10-25T00:30:00-01:00", "2026-10-25"),  # depois de voltar à hora de inverno
        ):
            with self.subTest(at=iso):
                page = self.at(iso)
                self.assertEqual(page.evaluate("localToday()"), expected)
                page.evaluate("editMaintenance('eq-srv01')")
                self.assertEqual(page.input_value("#mf input[name=date]"), expected)
                page.close()

    def test_proxima_preventiva_e_estado_no_dia_certo(self):
        page = self.at("2026-09-15T23:30:00+00:00")
        self.assertEqual(page.evaluate("addMonths('2026-03-15', 6)"), "2026-09-15")
        status = page.evaluate(
            "planStatus(equipmentById('eq-srv01'), preventivePlans.find(p => p.id === 'p-limpeza'))"
        )
        self.assertEqual(
            (status["next"], status["state"]),
            ("2026-09-15", "Agendada"),
            "no próprio dia ainda não está atrasada",
        )
        page.close()
        page = self.at("2026-09-16T00:30:00+00:00")
        status = page.evaluate(
            "planStatus(equipmentById('eq-srv01'), preventivePlans.find(p => p.id === 'p-limpeza'))"
        )
        self.assertEqual(status["state"], "Atrasada")
        page.close()

    def test_soma_de_meses_nos_limites(self):
        self.open_app()
        cases = {
            ("2026-01-31", 1): "2026-02-28",
            ("2024-01-31", 1): "2024-02-29",
            ("2026-03-29", 6): "2026-09-29",
            ("2026-10-25", 5): "2027-03-25",
            ("2026-12-15", 1): "2027-01-15",
            ("2026-08-31", 18): "2028-02-29",
        }
        for (start, months), expected in cases.items():
            with self.subTest(start=start, months=months):
                self.assertEqual(self.page.evaluate(f"addMonths('{start}', {months})"), expected)

    def test_noutro_fuso_tambem_funciona(self):
        context = self.browser.new_context(timezone_id="Asia/Tokyo")
        self.addCleanup(context.close)
        page = context.new_page()
        page.clock.set_fixed_time("2026-01-15T08:30:00+09:00")  # em UTC ainda é dia 14
        page.goto(self.server.url("/"))
        self.assertEqual(page.evaluate("localToday()"), "2026-01-15")
        self.assertEqual(page.evaluate("addMonths('2026-03-15', 6)"), "2026-09-15")


class TestEstadoOffline(UITestCase):
    def test_equipamento_offline_mostra_offline(self):
        self.open_app()
        self.js("go('equipment')")
        card = self.page.locator(".equipment-card, article", has_text="rpi01").first
        self.assertIn("Offline", card.inner_text())
        self.js("detail('eq-rpi01')")
        self.assertIn("Estado\nOffline", self.page.inner_text(".overview"))
        self.js("detail('eq-srv01')")
        self.assertIn("Estado\nOperacional", self.page.inner_text(".overview"))


if __name__ == "__main__":
    unittest.main()
