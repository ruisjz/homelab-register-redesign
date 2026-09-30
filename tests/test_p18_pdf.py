"""P18 — relatórios em PDF gerados no servidor (Python stdlib)."""

import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from harness import ROOT, Client, ServerTestCase, pdf_pages, pdf_text
from ui_base import UITestCase

sys.path.insert(0, ROOT)
from backend import report  # noqa: E402  (mesmas regras de cálculo da interface)

EQUIPMENT_PDF = "/api/report/equipment/eq-srv01.pdf"


class TestPdfEquipamento(ServerTestCase):
    def text(self, **kw):
        status, body, _ = self.client.pdf(EQUIPMENT_PDF, **kw)
        self.assertEqual(status, 200)
        return pdf_text(body)

    def test_ficheiro_pdf_valido_para_descarregar(self):
        status, body, headers = self.client.pdf(EQUIPMENT_PDF)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="relatorio-srv01-2026-10-01.pdf"', headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"%PDF-1.4"))
        self.assertTrue(body.rstrip().endswith(b"%%EOF"))
        if shutil.which("qpdf"):
            path = os.path.join(self.server.tmp, "r.pdf")
            with open(path, "wb") as f:
                f.write(body)
            check = subprocess.run(["qpdf", "--check", path], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertEqual(os.listdir(os.path.join(self.server.data_dir, "tmp")), [])

    def test_conteudo_completo(self):
        self.client.upload("equipment", "eq-srv01", "fatura.pdf", b"%PDF", "application/pdf", "documentation")
        gone = self.client.upload("maintenance", "m-srv01-2", "foto.jpg", b"\xff\xd8", "image/jpeg", "photo")
        os.remove(os.path.join(self.server.data_dir, "attachments", gone[1]["attachment"]["id"]))
        text = self.text()
        for expected in (
            "srv01",
            "Nó principal do cluster",  # identificação e notas
            "32 GB DDR4 (2 × 16 GB)",
            "4,5 TB (2 dispositivos · SATA SSD/HDD)",  # hardware da P23
            "16 GB · DDR4",
            "500 GB · SATA SSD",  # componentes com tecnologia
            "Limpeza interna",
            "Testar RAM",
            "2026-09-15",  # preventivas
            "2026-03-15 — Manutenção preventiva",
            "Checklist feita: Limpeza interna · Ventoinhas",
            "HDD dados — Saúde: 100%",
            "DIMM A1 — Teste: MemTest86 · Resultado: Passou",
            "Valor: 71 °C",
            "Stress — Ferramenta: stress-ng · Resultado: Estável 30 min",
            "fatura.pdf",
            "Documentação",
            "em falta",  # anexos
        ):
            self.assertIn(expected, text)

    def test_retirado_e_movido_de(self):
        data = self.client.get_data()
        srv, nas = self.equipment(data, "eq-srv01"), self.equipment(data, "eq-nas01")
        next(c for c in srv["components"] if c["id"] == "c-srv01-hdd")["retired"] = True
        disk = next(c for c in nas["components"] if c["id"] == "c-nas01-hdd2")
        nas["components"].remove(disk)
        disk["previousEquipmentIds"] = ["eq-nas01"]
        srv["components"].append(disk)
        self.assertEqual(self.client.save(data)[0], 200)
        text = " ".join(self.text().split())  # o texto pode quebrar de linha nas células
        self.assertIn("Retirado · Operacional", text)
        self.assertIn("Disco 2 movido de nas01", text)
        # SSD 500 GB + disco transferido 8 TB; o HDD de 4 TB retirado já não conta
        self.assertIn("8,5 TB (2 dispositivos · SATA SSD/HDD)", text)

    def test_estado_das_preventivas_usa_a_data_do_dispositivo(self):
        self.assertIn("Atrasada", self.text(today="2026-10-01"))  # próxima em 2026-09-15
        late = self.text(today="2026-09-01")
        self.assertNotIn("Atrasada", late)
        self.assertIn("Agendada", late)

    def test_gerado_em_no_fuso_do_dispositivo(self):
        for offset, label in ((60, "UTC-01:00"), (0, "UTC+00:00"), (-60, "UTC+01:00")):
            with self.subTest(offset=offset):
                text = self.text(tz_offset=offset)
                stamp = re.search(r"Gerado em (\d\d/\d\d/\d{4} \d\d:\d\d) \((UTC[+-]\d\d:\d\d)\)", text)
                self.assertEqual(stamp.group(2), label)
                shown = datetime.strptime(stamp.group(1), "%d/%m/%Y %H:%M")
                local = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=offset)
                self.assertLess(abs((local - shown).total_seconds()), 120)

    def test_acentos_e_simbolos(self):
        data = self.client.get_data()
        data["equipment"][0]["notes"] = "Migração → cluster ≥ 3 nós «ok» (teste) \\ fim"
        self.assertEqual(self.client.save(data)[0], 200)
        self.assertIn("Migração -> cluster >= 3 nós «ok» (teste) \\ fim", self.text())

    def test_varias_paginas(self):
        data = self.client.get_data()
        srv = self.equipment(data, "eq-srv01")
        for i in range(40):
            srv["maintenance"].append(
                {
                    "id": f"m-extra-{i}",
                    "date": f"2024-{i % 12 + 1:02d}-10",
                    "type": "Verificação",
                    "description": "Registo de teste " * 8,
                    "checks": {},
                    "results": {},
                }
            )
        self.assertEqual(self.client.save(data)[0], 200)
        status, body, _ = self.client.pdf(EQUIPMENT_PDF)
        pages = pdf_pages(body)
        self.assertGreater(pages, 2)
        text = pdf_text(body)
        self.assertIn(f"Página {pages} de {pages}", text)
        self.assertEqual(len(re.findall(r"^2024-\d\d-10 — Verificação$", text, re.M)), 40)

    def test_erros(self):
        self.assertEqual(self.client.pdf("/api/report/equipment/eq-nao-existe.pdf")[0], 404)
        self.assertEqual(Client(self.server).pdf(EQUIPMENT_PDF)[0], 401)
        self.assertEqual(os.listdir(os.path.join(self.server.data_dir, "tmp")), [])


class TestPdfManutencoes(ServerTestCase):
    def test_todas_as_manutencoes(self):
        status, body, headers = self.client.pdf("/api/report/maintenance.pdf")
        self.assertEqual(status, 200)
        self.assertIn("manutencoes-2026-10-01.pdf", headers["Content-Disposition"])
        text = pdf_text(body)
        self.assertIn("3 intervenções em 2 equipamentos", text)
        for expected in (
            "srv01 (2)",
            "nas01 (1)",
            "2026-03-15",
            "2025-09-10",
            "2026-01-20",
            "Disco 2 — Saúde: 91%",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("rpi01", text)


class TestPdfInterface(UITestCase):
    def setUp(self):
        super().setUp()
        self.open_app()

    def download(self):
        with self.page.expect_download() as info:
            self.page.click("button:has-text('Exportar PDF')")
        with open(info.value.path(), "rb") as f:
            return info.value.suggested_filename, f.read()

    def test_detalhe_exporta_pdf(self):
        self.js("detail('eq-srv01')")
        self.assertEqual(self.page.locator("button:has-text('Relatório')").count(), 0)
        name, body = self.download()
        self.assertTrue(name.startswith("relatorio-srv01-") and name.endswith(".pdf"), name)
        self.assertTrue(body.startswith(b"%PDF"))
        offset = self.page.evaluate("new Date().getTimezoneOffset()")  # fuso do browser
        sign, (h, m) = ("-" if offset > 0 else "+"), divmod(abs(offset), 60)
        self.assertIn(f"(UTC{sign}{h:02d}:{m:02d})", pdf_text(body))

    def test_manutencao_exporta_pdf(self):
        self.js("go('maintenance')")
        self.assertEqual(self.page.locator("button:has-text('Imprimir')").count(), 0)
        name, body = self.download()
        self.assertTrue(name.startswith("manutencoes-") and name.endswith(".pdf"), name)
        self.assertIn("Relatório de manutenções", pdf_text(body))

    def test_calculos_iguais_aos_da_interface(self):
        cases = [
            [],
            [{"type": "ram", "capacity": "8GB", "technology": "DDR4"}] * 2,
            [{"type": "ram", "capacity": c, "technology": "DDR4"} for c in ("8GB", "4GB", "16GB", "4GB")],
            [{"type": "ram", "capacity": "16GB"}, {"type": "ram", "capacity": "?"}],
            [
                {"type": "ram", "capacity": "512 MB"},
                {"type": "ram", "capacity": "1,5 GB", "technology": "DDR3"},
            ],
            [
                {"type": "storage", "capacity": c, "technology": t}
                for c, t in (("4TB", "HDD"), ("500GB", "SATA SSD"), ("256GB", "NVMe"))
            ],
            [{"type": "storage", "capacity": "1 TB", "technology": "NVMe"}],
            [
                {"type": "storage", "capacity": "2TB", "retired": True},
                {"type": "storage", "capacity": "0,5 TB"},
            ],
        ]
        for components in cases:
            with self.subTest(components=components):
                js = self.page.evaluate(
                    "c => [ramSummary({components: c}), storageSummary({components: c})]", components
                )
                py = [
                    report.ram_summary({"components": components}),
                    report.storage_summary({"components": components}),
                ]
                self.assertEqual(py, js)


if __name__ == "__main__":
    unittest.main()
