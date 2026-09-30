"""Bugs conhecidos da auditoria (IDs da secção 7), no lado da API.

Cada teste descreve o comportamento CORRETO e está marcado com
@unittest.expectedFailure porque hoje falha. Quando uma correção entra, o
teste passa a dar "unexpected success" e a suite fica vermelha: nesse momento
remove-se o marcador desse teste. Assim nenhum bug é dado como corrigido por
engano e nenhuma correção fica sem teste.
"""

import copy
import json
import io
import os
import socket
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile

from harness import Client, Server, ServerTestCase, load_fixture

SLOW = os.environ.get("HR_TESTES_LENTOS") == "1"


class TestPerdaDeDados(ServerTestCase):
    @unittest.expectedFailure
    def test_B07_dados_smart_legados_na_bd_nao_bloqueiam_gravacoes(self):
        """Sem o Import JSON (P02), dados legados só entram por uma BD ou snapshot antigos (P09)."""
        self.server.stop()
        con = sqlite3.connect(os.path.join(self.server.data_dir, "homelab.db"))
        con.execute(
            "UPDATE maintenance SET results_json=? WHERE id='m-nas01-1'",
            (json.dumps({"smartDevice": "Disco 1", "smartStatus": "OK"}),),
        )
        con.commit()
        con.close()
        self.server.start()
        client = Client(self.server)
        client.login()
        data = client.get_data()
        data["equipment"][0]["name"] = "srv01 renomeado"
        status, body = client.save(data)
        self.assertEqual(status, 200, body)


if __name__ == "__main__":
    unittest.main()
