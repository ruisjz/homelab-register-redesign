"""P23 — RAM e armazenamento do equipamento calculados a partir dos componentes.

Fixture: srv01 tem 2 × 16 GB DDR4 e 2 discos (500 GB SATA SSD, 4 TB HDD); nas01 tem
2 discos de 8 TB HDD e nenhuma RAM; rpi01 não tem componentes.
"""

import json
import os
import sqlite3
import unittest

from harness import Client, ServerTestCase, pdf_text
from ui_base import UITestCase


class TestHardwareAPI(ServerTestCase):
    def test_tecnologia_e_guardada_nos_componentes(self):
        comps = {c["id"]: c for e in self.client.get_data()["equipment"] for c in e["components"]}
        self.assertEqual(comps["c-srv01-ram1"]["technology"], "DDR4")
        self.assertEqual(comps["c-srv01-ssd"]["technology"], "SATA SSD")
        self.assertEqual(comps["c-srv01-cpu"]["technology"], "")

    def test_equipamento_ja_nao_tem_ram_nem_armazenamento_manuais(self):
        data = self.client.get_data()
        self.assertTrue(all("ram" not in e and "storage" not in e for e in data["equipment"]))
        data["equipment"][0]["ram"] = "999 GB"  # um cliente antigo que ainda envie o campo
        self.assertEqual(self.client.save(data)[0], 200)
        self.assertNotIn("ram", self.client.get_data()["equipment"][0])

    def test_migracao_v2_para_v3_preenche_so_quando_e_claro(self):
        data = self.client.get_data()
        self.equipment(data, "eq-rpi01")["components"].append(
            {"id": "c-rpi01-emmc", "name": "Armazenamento interno", "type": "storage", "capacity": "32 GB"}
        )
        self.equipment(data, "eq-nas01")["components"].append(
            {"id": "c-nas01-ram", "name": "Módulo", "type": "ram", "capacity": "2 GB"}
        )
        self.equipment(data, "eq-nas01")["components"].append(
            {"id": "c-nas01-nvme", "name": "Cache Samsung 980 NVMe", "type": "storage", "capacity": "1 TB"}
        )
        self.assertEqual(self.client.save(data)[0], 200)
        self.server.stop()
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        con.execute("UPDATE components SET technology=''")  # como antes da v3
        con.execute("ALTER TABLE components DROP COLUMN technology")
        con.execute("ALTER TABLE equipment ADD COLUMN ram TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE equipment ADD COLUMN storage TEXT NOT NULL DEFAULT ''")
        for eid, ram, storage in (
            ("eq-srv01", "32GB DDR4", "NVMe 1TB"),  # RAM clara; 2 discos -> armazenamento ambíguo
            ("eq-nas01", "DDR3/DDR4", "HDD 16TB"),  # dois tipos de RAM -> ambíguo; 3 discos -> ambíguo
            ("eq-rpi01", "", "eMMC 32GB"),  # um só dispositivo -> claro
        ):
            con.execute("UPDATE equipment SET ram=?, storage=? WHERE id=?", (ram, storage, eid))
        con.execute("PRAGMA user_version = 2")
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        comps = {c["id"]: c["technology"] for e in client.get_data()["equipment"] for c in e["components"]}
        self.assertEqual((comps["c-srv01-ram1"], comps["c-srv01-ram2"]), ("DDR4", "DDR4"))
        # «NVMe 1TB» não serve com 2 discos; «HDD dados» é claro pelo nome; «SSD» sozinho não diz SATA/NVMe
        self.assertEqual((comps["c-srv01-ssd"], comps["c-srv01-hdd"]), ("", "HDD"))
        self.assertEqual(comps["c-nas01-ram"], "")
        self.assertEqual((comps["c-nas01-hdd1"], comps["c-nas01-hdd2"]), ("", ""))
        self.assertEqual(comps["c-nas01-nvme"], "NVMe", "claro pelo nome do próprio componente")
        self.assertEqual(comps["c-rpi01-emmc"], "eMMC")
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        cols = {r[1] for r in con.execute("PRAGMA table_info(equipment)")}
        version = con.execute("PRAGMA user_version").fetchone()[0]
        con.close()
        self.assertFalse({"ram", "storage"} & cols)
        self.assertEqual(version, 3)
        backups = os.listdir(os.path.join(self.server.data_dir, "backups"))
        self.assertTrue(any(b.startswith("pre-migration-v2-v3") for b in backups), backups)


class TestHardwareInterface(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def summary(self, kind, eid):
        return self.page.evaluate(f"{kind}Summary(equipmentById('{eid}'))")

    def calc(self, kind, components):
        return self.page.evaluate(f"c => {kind}Summary({{components: c}})", components)

    def test_equipamento_sem_componentes(self):
        self.assertEqual((self.summary("ram", "eq-rpi01"), self.summary("storage", "eq-rpi01")), ("", ""))
        self.js("detail('eq-rpi01')")
        facts = self.page.inner_text(".overview")
        self.assertIn("RAM\n—", facts)
        self.assertIn("Armazenamento\n—", facts)

    def test_ram_com_varios_modulos(self):
        self.assertEqual(self.summary("ram", "eq-srv01"), "32 GB DDR4 (2 × 16 GB)")
        mixed = [{"type": "ram", "capacity": c, "technology": "DDR4"} for c in ("8GB", "4GB", "16GB", "4GB")]
        self.assertEqual(self.calc("ram", mixed), "32 GB DDR4 (4 módulos: 8 + 4 + 16 + 4 GB)")
        one = [{"type": "ram", "capacity": "8 GB", "technology": "DDR4"}]
        self.assertEqual(self.calc("ram", one), "8 GB DDR4 (1 × 8 GB)")

    def test_armazenamento_com_varios_dispositivos(self):
        self.assertEqual(self.summary("storage", "eq-srv01"), "4,5 TB (2 dispositivos · SATA SSD/HDD)")
        devices = [
            {"type": "storage", "capacity": "4TB", "technology": "HDD"},
            {"type": "storage", "capacity": "500GB", "technology": "SATA SSD"},
            {"type": "storage", "capacity": "256GB", "technology": "NVMe"},
        ]
        self.assertEqual(self.calc("storage", devices), "4,76 TB (3 dispositivos · HDD/SATA SSD/NVMe)")
        self.assertEqual(self.calc("storage", devices[2:]), "256 GB NVMe")

    def test_capacidade_ilegivel_e_indicada(self):
        mods = [{"type": "ram", "capacity": "16GB"}, {"type": "ram", "capacity": "?"}]
        self.assertEqual(
            self.calc("ram", mods), "≥ 16 GB (2 módulos: 16 + ? GB) — capacidade ilegível em algum módulo"
        )

    def test_componentes_retirados_nao_contam(self):
        self.js("retireComponent('eq-srv01','c-srv01-ram2')")
        self.js("retireComponent('eq-srv01','c-srv01-hdd')")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.summary("ram", "eq-srv01"), "16 GB DDR4 (1 × 16 GB)")
        self.assertEqual(self.summary("storage", "eq-srv01"), "500 GB SATA SSD")

    def test_componentes_transferidos_contam_so_no_destino(self):
        self.js("transferComponent('eq-srv01','c-srv01-ram2')")
        self.page.select_option("#tf [name=target]", "eq-nas01")
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(500)
        self.assertEqual(self.summary("ram", "eq-srv01"), "16 GB DDR4 (1 × 16 GB)")
        self.assertEqual(self.summary("ram", "eq-nas01"), "16 GB DDR4 (1 × 16 GB)")

    def test_formularios(self):
        self.js("editEquipment('eq-srv01')")
        self.assertEqual(self.page.locator("#ef [name=ram], #ef [name=storage]").count(), 0)
        self.assertIn("calculados a partir dos componentes", self.page.inner_text("#ef"))
        self.js("closeModal()")
        self.js("editComponent('eq-srv01','c-srv01-ram1')")
        self.assertEqual(self.page.input_value("#cf [name=technology]"), "DDR4")
        options = lambda: self.page.eval_on_selector_all(
            "#technology-options option", "o => o.map(x => x.value)"
        )
        self.assertIn("DDR5", options())
        self.page.select_option("#cf [name=type]", "storage")
        self.assertIn("NVMe", options())
        self.page.fill("#cf [name=technology]", "DDR5")
        self.page.select_option("#cf [name=type]", "ram")
        self.click_save()
        comps = {c["id"]: c for e in self.client.get_data()["equipment"] for c in e["components"]}
        self.assertEqual(comps["c-srv01-ram1"]["technology"], "DDR5")

    def test_cartao_detalhe_relatorio_e_pesquisa_usam_o_calculado(self):
        self.js("go('equipment')")
        self.assertIn("RAM 32 GB DDR4 (2 × 16 GB)", self.page.inner_text("#elist"))
        self.page.fill("#q", "32 gb ddr4")
        self.assertIn("srv01", self.page.inner_text("#elist"))
        self.assertNotIn("nas01", self.page.inner_text("#elist"))
        self.js("detail('eq-srv01')")
        self.assertIn("4,5 TB (2 dispositivos · SATA SSD/HDD)", self.page.inner_text(".overview"))
        report = pdf_text(self.client.pdf("/api/report/equipment/eq-srv01.pdf")[1])
        self.assertIn("32 GB DDR4 (2 × 16 GB)", report)
        self.assertIn("16 GB · DDR4", report)


if __name__ == "__main__":
    unittest.main()
