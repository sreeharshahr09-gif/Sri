"""Patent-like PDFs for the figure tests: a front page (bibliography, abstract, the abstract drawing), text pages,
drawing sheets and a table page, either as scanned page images or drawn as vectors with text."""

from __future__ import annotations

import io
import math
import random

from PIL import Image, ImageDraw, ImageFont

W, H = 1275, 1650  # a letter page at 150 dpi
WORDS = ("tire tread belt carcass layer rubber cushion width thickness region radial direction equatorial plane "
         "shoulder portion ply cord angle modulus compound wherein said first second outer inner").split()


def _font(size: int):
    return ImageFont.load_default(size=size)


def _text_block(draw, rng, top: int, bottom: int, left: int = 110, right: int = W - 110, size: int = 22) -> int:
    font, y = _font(size), top
    while y < bottom:
        x = left
        while True:
            word = rng.choice(WORDS)
            width = draw.textlength(word + " ", font=font)
            if x + width > right:
                break
            draw.text((x, y), word, fill=0, font=font)
            x += width
        y += int(size * 1.45)
    return y


def _tyre(draw, cx: int, cy: int, scale: float, label: str, numerals: bool = True) -> None:
    """A tyre half cross-section: carcass and tread outlines, a belt package, an under-belt layer, numerals."""
    s = scale
    for r, width in ((1.0, 5), (0.82, 4), (0.62, 3)):
        box = [cx - 300 * s * r, cy - 220 * s * r, cx + 300 * s * r, cy + 220 * s * r]
        draw.arc(box, 200, 340, fill=0, width=width)
    for k in range(4):  # belt layers
        y = cy - 200 * s + 14 * k * s
        draw.line([cx - 200 * s, y, cx + 200 * s, y], fill=0, width=3)
    draw.rectangle([cx - 180 * s, cy - 140 * s, cx + 180 * s, cy - 120 * s], outline=0, width=3)  # the layer
    draw.line([cx - 280 * s, cy - 40 * s, cx - 260 * s, cy + 160 * s, cx - 200 * s, cy + 200 * s], fill=0, width=4)
    draw.line([cx + 280 * s, cy - 40 * s, cx + 260 * s, cy + 160 * s, cx + 200 * s, cy + 200 * s], fill=0, width=4)
    font = _font(int(26 * s))
    if numerals:
        for k, (dx, dy) in enumerate(((-330, -230), (240, -250), (220, -130), (-320, 120), (300, 150))):
            x, y = cx + dx * s, cy + dy * s
            draw.text((x, y), str(10 + 2 * k), fill=0, font=font)
            draw.line([x + 15 * s, y + 30 * s, x + 15 * s + (cx - x) * 0.15, y + 30 * s + (cy - y) * 0.15], fill=0,
                      width=2)
    draw.text((cx - 60 * s, cy + 250 * s), label, fill=0, font=_font(int(34 * s)))


def _chart(draw, cx: int, cy: int, scale: float, label: str) -> None:
    """A different drawing: a contact-pressure curve over the tread width."""
    s = scale
    draw.line([cx - 300 * s, cy + 150 * s, cx + 300 * s, cy + 150 * s], fill=0, width=4)
    draw.line([cx - 300 * s, cy + 150 * s, cx - 300 * s, cy - 200 * s], fill=0, width=4)
    pts = [(cx - 300 * s + i * 6 * s, cy + 100 * s - 220 * s * math.exp(-((i - 50) / 22) ** 2)) for i in range(101)]
    draw.line(pts, fill=0, width=4)
    draw.text((cx - 60 * s, cy + 220 * s), label, fill=0, font=_font(int(34 * s)))


def front_page(rng, figure: bool = True):
    page = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(page)
    draw.text((110, 70), "(19) United States", fill=0, font=_font(30))
    draw.text((110, 115), "(12) Patent Application Publication    (10) Pub. No.: US 2026/0307433 A1", fill=0,
              font=_font(26))
    draw.line([110, 165, W - 110, 165], fill=0, width=3)
    _text_block(draw, rng, 190, 600, right=W // 2 - 30, size=20)  # bibliography, two columns
    _text_block(draw, rng, 190, 600, left=W // 2 + 30, size=20)
    draw.text((W // 2 + 30, 620), "(57) ABSTRACT", fill=0, font=_font(24))
    _text_block(draw, rng, 660, 900 if figure else 1500, left=W // 2 + 30, size=20)
    if figure:
        _tyre(draw, W // 2, 1250, 0.9, "FIG. 1")
    return page


def text_page(rng):
    page = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(page)
    draw.text((W // 2 - 120, 60), "US 2026/0307433 A1", fill=0, font=_font(22))
    _text_block(draw, rng, 140, H - 120, right=W // 2 - 30)
    _text_block(draw, rng, 140, H - 120, left=W // 2 + 30)
    return page


def table_page(rng):
    page = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(page)
    _text_block(draw, rng, 120, 500)
    top, rows, cols = 560, 12, 5
    for r in range(rows + 1):
        draw.line([150, top + r * 60, W - 150, top + r * 60], fill=0, width=3)
    for c in range(cols + 1):
        x = 150 + c * (W - 300) // cols
        draw.line([x, top, x, top + rows * 60], fill=0, width=3)
    font = _font(22)
    for r in range(rows):
        for c in range(cols):
            draw.text((170 + c * (W - 300) // cols, top + r * 60 + 18),
                      rng.choice(["NR 40 phr", "Example 1", "1.25 mm", "tan d 0.12", "Comparative"]), fill=0, font=font)
    _text_block(draw, rng, top + rows * 60 + 60, H - 120)
    return page


def drawing_sheet(rng, number: int, kind: str = "tyre"):
    page = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(page)
    draw.text((110, 60), f"Patent Application Publication   Sheet {number} of 3   US 2026/0307433 A1", fill=0,
              font=_font(22))
    (_tyre if kind == "tyre" else _chart)(draw, W // 2, H // 2, 1.5, f"FIG. {number}")
    return page


def scanned_pdf(pages) -> bytes:
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buf.getvalue()


def us_application(seed: int = 1, front_figure: bool = True) -> bytes:
    """Front page with the abstract figure, two drawing sheets (FIG. 1 again, then another drawing), text."""
    rng = random.Random(seed)
    pages = [front_page(rng, front_figure), drawing_sheet(rng, 1), drawing_sheet(rng, 2, "chart"),
             text_page(rng), text_page(rng)]
    return scanned_pdf(pages)


def ep_application(seed: int = 2) -> bytes:
    """No front-page figure; description and a table first, the drawings at the end."""
    rng = random.Random(seed)
    return scanned_pdf([front_page(rng, figure=False), text_page(rng), table_page(rng), text_page(rng),
                        drawing_sheet(rng, 1), drawing_sheet(rng, 2, "chart")])


def text_only(seed: int = 3) -> bytes:
    """A composition patent: text and a table, no drawing."""
    rng = random.Random(seed)
    return scanned_pdf([front_page(rng, figure=False), text_page(rng), table_page(rng)])


def vector_pdf() -> bytes:
    """A born-digital PDF (text as text, drawing as vector paths): a text page and a drawing sheet."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    rng = random.Random(4)
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        fig = Figure(figsize=(8.5, 11))
        for i in range(45):
            fig.text(0.1, 0.95 - i * 0.02, " ".join(rng.choice(WORDS) for _ in range(12)), fontsize=9)
        pdf.savefig(fig)
        fig = Figure(figsize=(8.5, 11))
        ax = fig.add_axes((0.1, 0.25, 0.8, 0.5))
        t = [i / 50 for i in range(101)]
        ax.plot([x - 1 for x in t], [math.sin(3 * x) for x in t], color="black", linewidth=2)
        ax.plot([x - 1 for x in t], [0.5 * math.cos(2 * x) for x in t], color="black", linewidth=2)
        ax.set_axis_off()
        fig.text(0.45, 0.2, "FIG. 3", fontsize=16)
        pdf.savefig(fig)
    return buf.getvalue()


def _cjk_block(draw, rng, top: int, bottom: int, left: int = 110, right: int = W - 110, size: int = 24) -> None:
    """Dense lines of square characters, as on CN, JP and KR pages (each glyph a few strokes in a square)."""
    y = top
    while y < bottom:
        x = left
        while x + size < right:
            if rng.random() < 0.95:
                kind = rng.randrange(3)
                draw.rectangle([x + 2, y + 2, x + size - 3, y + size - 3], outline=0, width=2) if kind == 0 else None
                draw.line([x + 2, y + size // 2, x + size - 3, y + size // 2], fill=0, width=2)
                draw.line([x + size // 2, y + 2, x + size // 2, y + size - 3], fill=0, width=2) if kind else None
                if kind == 2:
                    draw.line([x + 4, y + 4, x + size - 5, y + size - 5], fill=0, width=2)
            x += size + 3
        y += int(size * 1.5)


def cn_publication(seed: int = 5) -> bytes:
    """An image-only CN/JP/KR-style publication: dense character text with the abstract drawing at the bottom of
    the front page, claims and description pages, then the drawings at the end."""
    rng = random.Random(seed)
    front = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(front)
    _cjk_block(draw, rng, 90, 520)  # bibliographic data
    _cjk_block(draw, rng, 560, 900)  # abstract
    _tyre(draw, W // 2, 1250, 0.85, "FIG. 1")
    pages = [front]
    for _ in range(3):
        page = Image.new("L", (W, H), 255)
        _cjk_block(ImageDraw.Draw(page), rng, 110, H - 110)
        pages.append(page)
    pages += [drawing_sheet(rng, 1), drawing_sheet(rng, 2, "chart")]
    return scanned_pdf(pages)
