"""P21 — service worker, cache e PWA (Chromium com service worker real)."""

import os
import re
import shutil
import unittest

from harness import ROOT, Server
from ui_base import UITestCase

NEW_VERSION = "teste-nova-versao"


class TestServiceWorker(UITestCase):
    load_dataset = True

    def controlled_page(self):
        """Página com o service worker ativo e a controlar (como numa app já instalada)."""
        self.open_app()
        self.page.evaluate("navigator.serviceWorker.ready")
        self.page.reload()
        self.page.wait_for_selector("#side, #u")
        self.page.wait_for_function("!!navigator.serviceWorker.controller", timeout=10000)

    def meta_version(self):
        return self.page.evaluate("document.querySelector('meta[name=app-version]').content")

    def start_new_version(self):
        """Pára o servidor atual e arranca uma cópia da app com outra APP_VERSION, no mesmo porto e dados."""
        copy = os.path.join(self.server.tmp, "nova-versao")
        shutil.copytree(
            ROOT, copy, ignore=shutil.ignore_patterns(".git", "data", "tests", "__pycache__", "patches*")
        )
        main = os.path.join(copy, "backend", "main.py")
        with open(main, encoding="utf-8") as f:
            code = re.sub(r'^APP_VERSION = ".*"$', f'APP_VERSION = "{NEW_VERSION}"', f.read(), flags=re.M)
        with open(main, "w", encoding="utf-8") as f:
            f.write(code)
        port = self.server.port
        self.server.stop()
        new = Server(data_dir=self.server.data_dir, root=copy)
        new.port, new.env["PORT"] = port, str(port)
        self.addCleanup(new.cleanup)
        return new.start()

    def test_abre_sem_ligacao_e_permite_tentar_de_novo(self):
        self.controlled_page()
        self.context.set_offline(True)
        self.page.reload()
        self.page.wait_for_selector("main.fatal")
        text = self.page.inner_text("main.fatal")
        self.assertIn("Sem ligação ao servidor", text)
        self.assertIn("Tentar de novo", text)
        self.page.click("text=Tentar de novo")  # ainda sem rede: volta a explicar
        self.page.wait_for_selector("main.fatal button:not([disabled])")
        self.assertIn("Sem ligação ao servidor", self.page.inner_text("main.fatal"))
        self.context.set_offline(False)  # a rede volta: a app tenta de novo sozinha
        self.page.wait_for_selector("#side", timeout=15000)

    def test_nova_versao_mostra_aviso_e_so_atualiza_quando_pedido(self):
        self.controlled_page()
        old = self.meta_version()
        self.start_new_version()
        self.page.evaluate("navigator.serviceWorker.getRegistration().then(r => r.update())")
        self.page.wait_for_selector("#update-banner", timeout=15000)
        self.assertIn("Nova versão disponível", self.page.inner_text("#update-banner"))
        self.assertEqual(self.meta_version(), old, "sem recarregamento automático")

        # formulário com alterações: «Atualizar» pede confirmação e, se cancelado, nada muda
        self.page.evaluate("editEquipment('eq-srv01')")
        self.page.fill("#ef input[name=location]", "A meio de uma edição")
        self.dismiss_dialogs = True
        self.page.click("#update-banner button")
        self.page.wait_for_timeout(500)
        self.assertTrue(any("alterações por guardar" in d for d in self.dialogs))
        self.assertEqual(self.meta_version(), old)
        self.assertEqual(self.page.input_value("#ef input[name=location]"), "A meio de uma edição")

        self.dismiss_dialogs = False
        with self.page.expect_navigation(timeout=15000):
            self.page.click("#update-banner button")
        self.page.wait_for_selector("#side, #u")
        self.assertEqual(self.meta_version(), NEW_VERSION)
        self.page.wait_for_function(
            f"caches.keys().then(k => k.length === 1 && k[0] === 'homelab-register-{NEW_VERSION}')",
            timeout=10000,
        )

    def test_js_e_css_nao_ficam_presos_mesmo_sem_atualizar(self):
        self.controlled_page()
        self.start_new_version()
        self.page.reload()  # o service worker antigo continua a controlar, mas pede à rede primeiro
        self.page.wait_for_selector("#side, #u")
        self.assertEqual(self.meta_version(), NEW_VERSION)
        loaded = self.page.evaluate("performance.getEntriesByType('resource').map(e => e.name)")
        self.assertTrue(any(f"app.js?v={NEW_VERSION}" in x for x in loaded), loaded)
        self.assertTrue(any(f"app.css?v={NEW_VERSION}" in x for x in loaded), loaded)

    def test_primeira_instalacao_nao_mostra_aviso(self):
        self.controlled_page()
        self.page.wait_for_timeout(500)
        self.assertEqual(self.page.locator("#update-banner").count(), 0)


class TestManifesto(UITestCase):
    load_dataset = False

    def test_manifesto_da_app_instalavel(self):
        self.open_app()
        m = self.page.evaluate("fetch('/static/manifest.json').then(r => r.json())")
        self.assertEqual((m["start_url"], m["display"]), ("/", "standalone"))
        sizes = {i["sizes"] for i in m["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        for icon in m["icons"]:
            status = self.page.evaluate(f"fetch('{icon['src']}').then(r => r.status)")
            self.assertEqual(status, 200)
        self.assertEqual([s["url"] for s in m["shortcuts"]], ["/#/equipamentos", "/#/manutencao"])


if __name__ == "__main__":
    unittest.main()
