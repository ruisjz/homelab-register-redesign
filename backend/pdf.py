"""Gerador mínimo de PDF (P18), só com a biblioteca standard.

- Fontes padrão do PDF (Helvetica e Helvetica-Bold) com codificação WinAnsi (cp1252),
  que cobre o português. Símbolos fora do cp1252 são convertidos (ex.: "→" passa a "->").
- Texto com quebra de linha, parágrafos, pares rótulo/valor, tabelas com quebra de página
  (cabeçalho repetido) e rodapé "Página X de N".
- O ficheiro é escrito objeto a objeto; cada página é comprimida com zlib.
"""

import unicodedata
import zlib
from datetime import datetime, timezone

PAGE_W, PAGE_H = 595.28, 841.89  # A4
MARGIN = 42
FOOTER_SPACE = 30

# Larguras (1/1000 em) dos caracteres ASCII 32..126, das métricas padrão Adobe.
_HELVETICA = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278]
_HELVETICA += [556] * 10 + [278, 278, 584, 584, 584, 556, 1015]
_HELVETICA += [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833]
_HELVETICA += [722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
_HELVETICA += [278, 278, 278, 469, 556, 333]
_HELVETICA += [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833]
_HELVETICA += [556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500]
_HELVETICA += [334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278]
_BOLD += [556] * 10 + [333, 333, 584, 584, 584, 611, 975]
_BOLD += [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833]
_BOLD += [722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
_BOLD += [333, 278, 333, 584, 556, 333]
_BOLD += [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889]
_BOLD += [611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]
_BOLD += [389, 280, 389, 584]
_SPECIAL = {
    "—": 1000,
    "–": 556,
    "«": 556,
    "»": 556,
    "·": 278,
    "×": 584,
    "°": 400,
    "ª": 370,
    "º": 365,
    "•": 350,
    "€": 556,
    "…": 1000,
    "’": 222,
    "‘": 222,
    "“": 333,
    "”": 333,
    "\u00a0": 278,
    "ß": 611,
}
# Caracteres sem equivalente em cp1252: convertidos para texto legível.
_REPLACE = {
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "≥": ">=",
    "≤": "<=",
    "≠": "!=",
    "≈": "~",
    "✓": "v",
    "✔": "v",
    "✗": "x",
    "✕": "x",
    "\u2011": "-",
    "\u2212": "-",
    "\u2009": " ",
    "\u202f": " ",
    "\u200b": "",
}


def clean(text):
    """Texto pronto para as fontes padrão: normalizado e sem caracteres fora do cp1252."""
    text = unicodedata.normalize("NFC", str(text if text is not None else ""))
    for src, dst in _REPLACE.items():
        text = text.replace(src, dst)
    return text.encode("cp1252", "replace").decode("cp1252")


def char_width(ch, bold=False):
    table = _BOLD if bold else _HELVETICA
    code = ord(ch)
    if 32 <= code <= 126:
        return table[code - 32]
    if ch in _SPECIAL:
        return _SPECIAL[ch]
    base = unicodedata.normalize("NFD", ch)[0]
    if base != ch and 32 <= ord(base) <= 126:
        return table[ord(base) - 32]
    return 556


def text_width(text, size, bold=False):
    return sum(char_width(ch, bold) for ch in text) * size / 1000


def wrap(text, width, size, bold=False):
    """Quebra o texto em linhas que cabem em width; respeita as mudanças de linha."""
    lines = []
    for paragraph in clean(text).split("\n"):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, bold) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while text_width(word, size, bold) > width:  # palavra maior do que a linha
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _literal(text):
    raw = clean(text).encode("cp1252", "replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


class Document:
    """Documento A4 com cursor vertical; as páginas são criadas conforme o conteúdo."""

    def __init__(self, title, footer):
        self.title = title
        self.footer = footer
        self.pages = []
        self.width = PAGE_W - 2 * MARGIN
        self._new_page()

    # --- base -----------------------------------------------------------------
    def _new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = PAGE_H - MARGIN

    def ensure(self, height):
        """Muda de página se o bloco seguinte não couber. Devolve True se mudou."""
        if self.y - height < MARGIN + FOOTER_SPACE:
            self._new_page()
            return True
        return False

    def _text(self, x, y, text, size, bold=False, gray=0.0):
        font = b"F2" if bold else b"F1"
        self.ops.append(
            b"BT /%s %.2f Tf %.2f g %.2f %.2f Td %s Tj ET" % (font, size, gray, x, y, _literal(text))
        )

    def _rect(self, x, y, w, h, gray):
        self.ops.append(b"%.2f g %.2f %.2f %.2f %.2f re f" % (gray, x, y, w, h))

    def _line(self, x1, y1, x2, y2, gray=0.8, width=0.6):
        self.ops.append(b"%.2f G %.2f w %.2f %.2f m %.2f %.2f l S" % (gray, width, x1, y1, x2, y2))

    # --- blocos ---------------------------------------------------------------
    def spacer(self, height):
        self.y -= height

    def title_block(self, title, subtitle, meta):
        self._text(MARGIN, self.y - 18, title, 18, bold=True)
        self.y -= 26
        for text, size, gray in ((subtitle, 10.5, 0.25), (meta, 8.5, 0.45)):
            if text:
                for line in wrap(text, self.width, size):
                    self.y -= size * 1.4
                    self._text(MARGIN, self.y, line, size, gray=gray)
        self.y -= 6
        self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y, gray=0.6, width=1)
        self.y -= 6

    def heading(self, text, size=12.5):
        self.ensure(size + 40)  # não deixar um título sozinho no fim da página
        self.y -= size + 10
        self._text(MARGIN, self.y, text, size, bold=True)
        self.y -= 5

    def subheading(self, text, size=10.5):
        self.ensure(size + 26)
        self.y -= size + 6
        self._text(MARGIN, self.y, text, size, bold=True)
        self.y -= 2

    def paragraph(self, text, size=9.5, bold=False, gray=0.0, indent=0):
        if not str(text or "").strip():
            return
        for line in wrap(text, self.width - indent, size, bold):
            self.ensure(size * 1.4)
            self.y -= size * 1.4
            self._text(MARGIN + indent, self.y, line, size, bold, gray)

    def rule(self):
        self.ensure(10)
        self.y -= 5
        self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y)
        self.y -= 5

    def facts(self, pairs, columns=2, size=9.5):
        """Pares rótulo/valor em grelha; valores vazios aparecem como "—"."""
        col_w = self.width / columns
        for start in range(0, len(pairs), columns):
            row = pairs[start : start + columns]
            cells = [(label, wrap(str(value).strip() or "—", col_w - 10, size)) for label, value in row]
            height = 11 + max(len(lines) for _, lines in cells) * size * 1.35 + 5
            self.ensure(height)
            for i, (label, lines) in enumerate(cells):
                x = MARGIN + i * col_w
                self._text(x, self.y - 9, label, 7.5, gray=0.45)
                y = self.y - 11
                for line in lines:
                    y -= size * 1.35
                    self._text(x, y, line, size)
            self.y -= height

    def table(self, headers, rows, widths, size=8.5):
        """Tabela com quebra de linha nas células; o cabeçalho repete-se em cada página."""
        col_w = [self.width * w for w in widths]
        pad, line_h = 4, size * 1.3

        def draw_header():
            height = line_h + 2 * pad
            self._rect(MARGIN, self.y - height, self.width, height, 0.92)
            x = MARGIN
            for text, w in zip(headers, col_w):
                self._text(x + pad, self.y - pad - size, text, size, bold=True)
                x += w
            self.y -= height

        self.ensure(3 * line_h + 4 * pad)
        draw_header()
        for row in rows:
            cells = [wrap(str(v).strip() or "—", w - 2 * pad, size) for v, w in zip(row, col_w)]
            height = max(len(c) for c in cells) * line_h + 2 * pad
            if self.ensure(height):
                draw_header()
            x = MARGIN
            for lines, w in zip(cells, col_w):
                y = self.y - pad
                for line in lines:
                    y -= line_h
                    self._text(x + pad, y + 2, line, size)
                x += w
            self.y -= height
            self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y, gray=0.85, width=0.4)
        self.y -= 4

    # --- ficheiro -------------------------------------------------------------
    def save(self, path):
        total = len(self.pages)
        for number, ops in enumerate(self.pages, 1):
            y = MARGIN - 8
            self.ops = ops
            self._line(MARGIN, y + 12, PAGE_W - MARGIN, y + 12, gray=0.85, width=0.4)
            self._text(MARGIN, y, self.footer, 7.5, gray=0.45)
            label = f"Página {number} de {total}"
            self._text(PAGE_W - MARGIN - text_width(clean(label), 7.5), y, label, 7.5, gray=0.45)

        first_page = 5
        info_obj = first_page + 2 * total
        offsets = {}
        with open(path, "wb") as f:

            def obj(number, body):
                offsets[number] = f.tell()
                f.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")

            f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
            kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(total))
            obj(2, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, total))
            for number, font in ((3, b"Helvetica"), (4, b"Helvetica-Bold")):
                obj(
                    number,
                    b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % font,
                )
            for i, ops in enumerate(self.pages):
                page_obj = first_page + 2 * i
                obj(
                    page_obj,
                    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
                    b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
                    % (PAGE_W, PAGE_H, page_obj + 1),
                )
                stream = zlib.compress(b"\n".join(ops))
                obj(
                    page_obj + 1,
                    b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                    + stream
                    + b"\nendstream",
                )
            stamp = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ").encode()
            # B14 (P27): título em UTF-16 (texto do PDF), para acentos e «—» não virarem «?».
            title = ("\ufeff" + str(self.title)).encode("utf-16-be", "replace").hex().upper().encode()
            obj(
                info_obj,
                b"<< /Title <%s> /Producer (Homelab Register) /CreationDate (%s) >>" % (title, stamp),
            )
            xref = f.tell()
            f.write(b"xref\n0 %d\n0000000000 65535 f \n" % (info_obj + 1))
            for number in range(1, info_obj + 1):
                f.write(b"%010d 00000 n \n" % offsets[number])
            f.write(
                b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (info_obj + 1, info_obj, xref)
            )
