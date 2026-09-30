"""Interface: comportamento atual correto que tem de se manter (opcional, precisa de Playwright)."""

import json
import unittest

from harness import pdf_text
from ui_base import UITestCase

PAGES = [
    "go('dashboard')",
    "go('equipment')",
    "detail('eq-srv01')",
    "go('maintenance')",
    "go('smart')",
    "go('backup')",
]


class TestInterface(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def test_navegacao_por_todas_as_paginas_sem_erros_js(self):
        for expr in PAGES:
            with self.subTest(page=expr):
                self.js(expr)
        self.assertEqual(self.js_errors, [])

    def test_criar_equipamento_pela_interface(self):
        self.js("editEquipment()")
        self.page.fill("#ef input[name=name]", "pve02")
        self.click_save()
        names = [e["name"] for e in self.client.get_data()["equipment"]]
        self.assertIn("pve02", names)

    def test_editar_manutencao_existente(self):
        self.js("editMaintenance('eq-nas01', 'm-nas01-1')")
        self.page.fill("#mf input[name=description]", "SMART trimestral (revisto)")
        self.click_save()
        nas = self.equipment(self.client.get_data(), "eq-nas01")
        self.assertEqual(nas["maintenance"][0]["description"], "SMART trimestral (revisto)")
        self.assertEqual(len(nas["maintenance"][0]["results"]["smartDisks"]), 2)

    def test_componente_com_historico_nao_pode_ser_apagado(self):
        self.js("delComponent('eq-srv01', 'c-srv01-ssd')")
        self.assertTrue(self.dialogs, "deve explicar porque não pode apagar")
        comps = [c["id"] for c in self.equipment(self.client.get_data(), "eq-srv01")["components"]]
        self.assertIn("c-srv01-ssd", comps)

    def test_preventiva_usada_nao_pode_ser_apagada(self):
        self.js("delPlan('p-limpeza')")
        self.assertIn("p-limpeza", [p["id"] for p in self.client.get_data()["preventivePlans"]])

    def test_relatorio_do_equipamento(self):
        status, body, _ = self.client.pdf("/api/report/equipment/eq-srv01.pdf")
        self.assertEqual(status, 200)
        text = pdf_text(body)
        for expected in ("srv01", "EliteDesk 800 G3", "SSD sistema", "HDD dados"):
            self.assertIn(expected, text)


class TestInterfaceMobile(UITestCase):
    viewport = {"width": 390, "height": 844}
    mobile = True

    def test_sem_overflow_horizontal_a_390_px(self):
        self.open_app()
        for expr in PAGES + ["editMaintenance('eq-srv01')"]:
            with self.subTest(page=expr):
                self.js(expr)
                overflow = self.page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                self.assertLessEqual(overflow, 0)


if __name__ == "__main__":
    unittest.main()
