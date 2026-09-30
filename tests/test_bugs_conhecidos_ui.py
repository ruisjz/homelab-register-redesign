"""Bugs conhecidos da auditoria, no lado da interface (opcional, precisa de Playwright).

Mesma convenção de test_bugs_conhecidos_api.py: o teste descreve o comportamento
correto, está marcado como falha esperada e o marcador sai quando o bug é corrigido.
"""

import json
import os
import unittest

from ui_base import UITestCase

# Sem bugs de interface em aberto (B10 corrigido na P26).


if __name__ == "__main__":
    unittest.main()
