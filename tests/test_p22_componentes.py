"""P22 — ciclo de vida dos componentes: retirar e transferir sem perder histórico.

Cenário base (fixture): o disco c-srv01-hdd tem histórico SMART em srv01 (m-srv01-2).
"""

import copy
import os
import sqlite3
import unittest

from harness import Client, ServerTestCase
from ui_base import UITestCase

DISK = "c-srv01-hdd"


def find(data, cid):
    """Todas as ocorrências do componente (tem de ser exatamente uma)."""
    return [(e, c) for e in data["equipment"] for c in e["components"] if c["id"] == cid]


def move(data, cid, target_id):
    """Transfere como a interface: mesmo objeto (mesmo ID), origem registada no histórico."""
    ((source, comp),) = find(data, cid)
    source["components"].remove(comp)
    comp["previousEquipmentIds"] = comp.get("previousEquipmentIds", []) + [source["id"]]
    next(e for e in data["equipment"] if e["id"] == target_id)["components"].append(comp)


def smart_refs(data, cid):
    return [
        (e["id"], m["id"])
        for e in data["equipment"]
        for m in e["maintenance"]
        for d in m["results"].get("smartDisks") or []
        if d.get("deviceId") == cid
    ]


class TestComponentesAPI(ServerTestCase):
    def test_criar_componente_fica_ativo_e_sem_origem(self):
        data = self.client.get_data()
        self.equipment(data, "eq-rpi01")["components"].append(
            {"id": "c-rpi01-sd", "name": "Cartão SD", "type": "storage", "capacity": "32 GB"}
        )
        self.assertEqual(self.client.save(data)[0], 200)
        ((_, comp),) = find(self.client.get_data(), "c-rpi01-sd")
        self.assertEqual((comp["retired"], comp["previousEquipmentIds"]), (False, []))

    def test_retirar_preserva_componente_historico_e_anexos(self):
        aid = self.client.upload("component", DISK, "fatura.pdf", b"%PDF", "application/pdf")[1][
            "attachment"
        ]["id"]
        data = self.client.get_data()
        refs = smart_refs(data, DISK)
        find(data, DISK)[0][1]["retired"] = True
        self.assertEqual(self.client.save(data)[0], 200)
        after = self.client.get_data()
        ((owner, comp),) = find(after, DISK)
        self.assertEqual((owner["id"], comp["retired"]), ("eq-srv01", True))
        self.assertEqual(smart_refs(after, DISK), refs)
        self.assertEqual(self.client.request("GET", f"/api/attachments/{aid}/download", raw=True)[0], 200)

    def test_transferir_A_B_e_depois_B_C_preserva_tudo(self):
        aid = self.client.upload("component", DISK, "foto.jpg", b"\xff\xd8foto", "image/jpeg")[1][
            "attachment"
        ]["id"]
        data = self.client.get_data()
        refs = smart_refs(data, DISK)
        fields = {k: v for k, v in find(data, DISK)[0][1].items() if k not in ("previousEquipmentIds",)}

        move(data, DISK, "eq-nas01")  # A -> B
        self.assertEqual(self.client.save(data)[0], 200)
        data = self.client.get_data()
        ((owner, comp),) = find(data, DISK)
        self.assertEqual((owner["id"], comp["previousEquipmentIds"]), ("eq-nas01", ["eq-srv01"]))

        # uma medição feita em B também fica ligada ao mesmo disco
        nas = self.equipment(data, "eq-nas01")
        nas["maintenance"][0]["results"]["smartDisks"].append(
            {"deviceId": DISK, "device": "HDD dados", "health": "97%"}
        )
        self.assertEqual(self.client.save(data)[0], 200)

        data = self.client.get_data()
        move(data, DISK, "eq-rpi01")  # B -> C
        self.assertEqual(self.client.save(data)[0], 200)
        after = self.client.get_data()
        ((owner, comp),) = find(after, DISK)  # exatamente uma cópia: sem duplicações
        self.assertEqual(owner["id"], "eq-rpi01")
        self.assertEqual(comp["previousEquipmentIds"], ["eq-srv01", "eq-nas01"])
        self.assertEqual({k: comp[k] for k in fields}, fields, "identidade e dados mantidos")
        self.assertEqual(smart_refs(after, DISK), refs + [("eq-nas01", "m-nas01-1")])
        listing = self.client.request("GET", f"/api/attachments?ownerType=component&ownerId={DISK}")[1]
        self.assertEqual([a["id"] for a in listing["attachments"]], [aid])
        self.assertEqual(self.client.request("GET", f"/api/attachments/{aid}/download", raw=True)[0], 200)
        # editar um registo antigo de A continua a gravar
        self.equipment(after, "eq-srv01")["maintenance"][0]["notes"] = "revisto"
        self.assertEqual(self.client.save(after)[0], 200)

    def test_nao_se_pode_duplicar_um_componente_entre_equipamentos(self):
        data = self.client.get_data()
        self.equipment(data, "eq-nas01")["components"].append(copy.deepcopy(find(data, DISK)[0][1]))
        self.assertEqual(self.client.save(data)[0], 400)
        self.assertEqual(len(find(self.client.get_data(), DISK)), 1)

    def test_historico_so_aceita_componentes_que_passaram_pelo_equipamento(self):
        data = self.client.get_data()
        self.equipment(data, "eq-srv01")["maintenance"][0]["results"]["smartDisks"][0][
            "deviceId"
        ] = "c-nas01-hdd1"
        self.assertEqual(self.client.save(data)[0], 400)

    def test_apagar_equipamento_que_partiria_historico_de_outro_e_recusado(self):
        data = self.client.get_data()
        move(data, DISK, "eq-nas01")
        self.assertEqual(self.client.save(data)[0], 200)
        data = self.client.get_data()
        data["equipment"] = [e for e in data["equipment"] if e["id"] != "eq-nas01"]
        self.assertEqual(self.client.save(data)[0], 400)
        self.assertEqual(len(find(self.client.get_data(), DISK)), 1)

    def test_campos_invalidos_sao_recusados(self):
        for field, value in (
            ("retired", "sim"),
            ("previousEquipmentIds", ["eq'x"]),
            ("previousEquipmentIds", "eq-a"),
        ):
            with self.subTest(field=field, value=value):
                data = self.client.get_data()
                find(data, DISK)[0][1][field] = value
                self.assertEqual(self.client.save(data)[0], 400)

    def test_migracao_v1_para_v2_com_snapshot_antes(self):
        before = self.client.get_data()
        self.server.stop()
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        con.execute("ALTER TABLE components DROP COLUMN retired")
        con.execute("ALTER TABLE components DROP COLUMN previous_equipment_ids")
        con.execute("PRAGMA user_version = 1")
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        self.assertEqual(client.get_data(), before)
        backups = os.listdir(os.path.join(self.server.data_dir, "backups"))
        self.assertTrue(any(b.startswith("pre-migration-v1-v") for b in backups), backups)


class TestComponentesInterface(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def transfer(self, source, cid, target):
        self.js(f"transferComponent('{source}','{cid}')")
        self.page.select_option("#tf [name=target]", target)
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(500)

    def test_retirar_pela_interface(self):
        self.js(f"retireComponent('eq-srv01','{DISK}')")
        self.page.wait_for_timeout(400)
        ((_, comp),) = find(self.client.get_data(), DISK)
        self.assertTrue(comp["retired"])
        self.assertIn("Retirados", self.page.inner_text("main"))
        options = self.page.evaluate("maintenanceEquipmentId = 'eq-srv01'; smartOptions({})")
        self.assertNotIn(DISK, options, "retirado não aparece em novas manutenções")
        self.js(f"reactivateComponent('eq-srv01','{DISK}')")
        self.page.wait_for_timeout(400)
        self.assertFalse(find(self.client.get_data(), DISK)[0][1]["retired"])

    def test_transferir_A_B_C_pela_interface(self):
        self.transfer("eq-srv01", DISK, "eq-nas01")
        self.transfer("eq-nas01", DISK, "eq-rpi01")
        ((owner, comp),) = find(self.client.get_data(), DISK)
        self.assertEqual((owner["id"], comp["previousEquipmentIds"]), ("eq-rpi01", ["eq-srv01", "eq-nas01"]))
        self.js("detail('eq-rpi01')")
        self.assertIn("movido de nas01", self.page.inner_text("main"))

    def test_manutencao_antiga_mantem_o_disco_transferido(self):
        self.transfer("eq-srv01", DISK, "eq-nas01")
        self.js("editMaintenance('eq-srv01', 'm-srv01-2')")
        selected = self.page.eval_on_selector_all(
            "#rows-smart select", "els => els.map(e => [e.value, e.selectedOptions[0]?.textContent])"
        )
        self.assertIn(DISK, [v for v, _ in selected])
        self.assertTrue(any("agora em nas01" in (t or "") for _, t in selected))
        self.page.fill("#mf input[name=description]", "Limpeza e testes (revisto)")
        self.click_save()
        m = self.equipment(self.client.get_data(), "eq-srv01")["maintenance"][0]
        self.assertIn(DISK, [d["deviceId"] for d in m["results"]["smartDisks"]])

    def test_apagar_componente_com_historico_sugere_retirar(self):
        self.transfer("eq-srv01", DISK, "eq-nas01")
        self.js(f"delComponent('eq-nas01','{DISK}')")
        self.assertTrue(any("Retirar" in d for d in self.dialogs))
        self.assertEqual(len(find(self.client.get_data(), DISK)), 1)

    def test_apagar_componente_sem_historico_avisa_que_os_anexos_sao_apagados(self):
        self.client.upload("component", "c-srv01-cpu", "fatura.pdf", b"%PDF", "application/pdf")
        self.open_app()
        self.js("delComponent('eq-srv01','c-srv01-cpu')")
        self.assertTrue(any("anexo" in d and "definitivamente" in d for d in self.dialogs), self.dialogs)
        self.assertEqual(find(self.client.get_data(), "c-srv01-cpu"), [])
        self.assertEqual(self.attachment_files(), [])

    def test_apagar_equipamento_com_componente_com_historico_noutro_e_bloqueado(self):
        self.transfer("eq-srv01", DISK, "eq-nas01")
        self.js("deleteEquipment('eq-nas01')")
        self.assertTrue(any("Transfere" in d for d in self.dialogs))
        self.assertIn("eq-nas01", [e["id"] for e in self.client.get_data()["equipment"]])

    def test_pagina_smart_junta_o_historico_do_disco_entre_maquinas(self):
        self.transfer("eq-srv01", DISK, "eq-nas01")
        data = self.client.get_data()
        self.equipment(data, "eq-nas01")["maintenance"][0]["results"]["smartDisks"].append(
            {"deviceId": DISK, "device": "HDD dados", "health": "97%"}
        )
        self.assertEqual(self.client.save(data)[0], 200)
        self.open_app()
        self.js("go('smart')")
        card = self.page.locator(".smart-card", has_text="HDD dados")
        self.assertEqual(card.count(), 1)
        header = card.inner_text().split("\n")[0]
        self.assertIn("em nas01", header)
        self.assertEqual(set(header.split("medições em ")[1].split(", ")), {"srv01", "nas01"})
        self.assertIn("2 medições", card.inner_text())


if __name__ == "__main__":
    unittest.main()
