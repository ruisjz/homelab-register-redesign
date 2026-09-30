"""Base dos testes de interface (opcionais): Playwright + Chromium, fora da imagem Docker.

Sem Playwright instalado, estes testes são ignorados (skipped) e o resto corre na mesma.
Instalação: pip install playwright && python -m playwright install chromium
"""

import unittest

from harness import ServerTestCase

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


class UITestCase(ServerTestCase):
    viewport = {"width": 1280, "height": 900}
    mobile = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if sync_playwright is None:
            raise unittest.SkipTest(
                "Playwright não instalado (pip install playwright && python -m playwright install chromium)"
            )
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch()
        except Exception as exc:  # browser em falta
            cls._pw.stop()
            raise unittest.SkipTest(f"Chromium indisponível: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.context = self.browser.new_context(
            viewport=self.viewport,
            is_mobile=self.mobile,
            has_touch=self.mobile,
            locale="pt-PT",
            timezone_id="Atlantic/Azores",  # fuso do utilizador,
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.js_errors, self.dialogs = [], []
        self.dismiss_dialogs = False
        self.page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        self.page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.dismiss() if self.dismiss_dialogs else dialog.accept()

    def open_app(self, path="/"):
        self.page.goto(self.server.url(path))
        self.page.wait_for_selector("#side, #u")
        if self.page.query_selector("#u"):
            self.page.fill("#u", self.server.user)
            self.page.fill("#pw", self.server.password)
            self.page.click("button.accent")
            self.page.wait_for_selector("#side")

    def js(self, expression):
        result = self.page.evaluate(expression)
        self.page.wait_for_timeout(150)
        return result

    def click_save(self):
        self.page.click("#drawer footer button.accent")
        self.page.wait_for_timeout(500)
