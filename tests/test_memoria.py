"""P15: pico real de memória do processo da app (VmHWM), em todos os caminhos de ficheiros.

Objetivo: o processo fica abaixo de 60 MB. Critério da fase 3: restore de 300 MB abaixo de 100 MB.
A VPS real tem ~300 MB de RAM e o contentor está limitado a 200 MB (mem_limit), mas o limite
não substitui este teste: a app tem de cumprir os 60 MB por si.

Só em Linux (lê /proc). O teste de 300 MB é lento e só corre com HR_TESTES_LENTOS=1.
"""

import os
import tempfile
import unittest
import urllib.request

from harness import ServerTestCase

LINUX = os.path.exists("/proc/self/status")
SLOW = os.environ.get("HR_TESTES_LENTOS") == "1"
LIMIT_MB = 60


@unittest.skipUnless(LINUX, "mede /proc/<pid>/status; só em Linux")
class TestMemoria(ServerTestCase):
    def peak_mb(self):
        with open(f"/proc/{self.server.proc.pid}/status") as f:
            kb = next(int(line.split()[1]) for line in f if line.startswith("VmHWM"))
        return kb / 1024

    def exercise(self, count, size):
        """Percorre todos os caminhos de ficheiros e devolve o pico medido depois de cada um."""
        peaks = {"arranque": self.peak_mb()}
        ids = []
        for i in range(count):
            status, body = self.client.upload("equipment", "eq-srv01", f"disco-{i}.bin", os.urandom(size))
            self.assertEqual(status, 201, body)
            ids.append(body["attachment"]["id"])
        peaks["upload de anexos"] = self.peak_mb()
        for aid in ids:
            resp = self.client.opener.open(
                urllib.request.Request(self.server.url(f"/api/attachments/{aid}/download"))
            )
            while resp.read(1024 * 1024):
                pass
        peaks["download de anexos"] = self.peak_mb()
        with tempfile.TemporaryDirectory() as td:
            status, prep = self.client.prepare_backup()
            self.assertEqual(status, 200, prep)
            peaks["criação do snapshot"] = self.peak_mb()
            archive = os.path.join(td, "snap.zip")
            self.assertEqual(self.client.download_backup(prep["token"], dest=archive)[0], 200)
            peaks["download do backup"] = self.peak_mb()
            status, body = self.client.restore_file(archive)
            self.assertEqual(status, 200, body)
            peaks["restore (envio, validação, backup automático e extração)"] = self.peak_mb()
        tmp = os.path.join(self.server.data_dir, "tmp")
        self.assertEqual(os.listdir(tmp) if os.path.isdir(tmp) else [], [], "temporários por limpar")
        return peaks

    def assert_peaks(self, peaks, total_mb):
        report = "\n".join(f"  {k}: {v:.1f} MB" for k, v in peaks.items())
        print(f"\nPico de memória do processo com {total_mb} MB de anexos:\n{report}")
        self.assertLess(max(peaks.values()), LIMIT_MB, report)

    def test_P15_pico_abaixo_de_60_mb_com_60_mb_de_anexos(self):
        self.assert_peaks(self.exercise(3, 20 * 1024 * 1024), 60)

    @unittest.skipUnless(SLOW, "lento (~1,5 GB de disco temporário); ativar com HR_TESTES_LENTOS=1")
    def test_R01_P15_restore_de_300_mb_abaixo_de_60_mb(self):
        self.assert_peaks(self.exercise(12, 25 * 1024 * 1024), 300)


if __name__ == "__main__":
    unittest.main()
