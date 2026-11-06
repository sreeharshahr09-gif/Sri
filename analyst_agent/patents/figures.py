"""A patent's PDF and its most representative figures, for the notable-patents cards.

The PDF comes from the export's link (a PDF, or a page that names its PDF, such as Google Patents) or, when
there is none, from a patent page looked up by publication number. Each PDF is cached on disk, and a PDF
dropped into the cache folder as <publication number>.pdf is used as it is (for links that need a login).

The figures are found without a vision model: every page is rendered and its ink split into connected parts.
Text is many small parts on lines; a drawing is a few large parts of strokes. The office's front-page figure
(the abstract drawing on US, CN, JP, KR and most EP front pages) comes first, then the first drawing sheet
that shows something else.
"""

from __future__ import annotations

import html
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import numpy as np

from .semantic import CACHE_DIR

PDF_DIR = CACHE_DIR / "pdf"
# Where a PDF is looked up by publication number when the export has no link; empty switches the lookup off.
LOOKUP_URL = os.environ.get("PATENT_PDF_LOOKUP", "https://patents.google.com/patent/{number}/en")
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_PAGES = 80
USER_AGENT = "Mozilla/5.0 (compatible; patent-landscape-report/1.0)"
_PDF_META = re.compile(r"""<meta[^>]+name=["']citation_pdf_url["'][^>]+content=["']([^"']+)["']""", re.IGNORECASE)
_PDF_META_REVERSED = re.compile(r"""<meta[^>]+content=["']([^"']+)["'][^>]+name=["']citation_pdf_url["']""",
                                re.IGNORECASE)
_PDF_HREF = re.compile(r"""href=["']([^"']+\.pdf(?:\?[^"']*)?)["']""", re.IGNORECASE)


@dataclass
class Figure:
    png: bytes
    label: str  # e.g. "Representative figure (front page)"
    description: str = ""  # what it shows, when a vision model chose it


@dataclass
class PatentPdf:
    number: str
    data: bytes | None = None
    source: str = ""  # where it came from: a URL, or "cache"
    url: str = ""  # its web address, also when it came from the cache
    note: str = ""  # why there is none
    figures: list[Figure] = field(default_factory=list)


def normalise_number(number: str) -> str:
    """'US 2016/0159143 A1' -> 'US20160159143A1', the form patent offices and Google Patents use."""
    return re.sub(r"[^A-Z0-9]", "", str(number or "").upper())


def patent_page_link(number: str) -> str:
    """The patent's page (Google Patents by default, with its PDF), built from the publication number alone;
    "" for a number that is no publication number (such as a row number) or when the lookup is switched off."""
    number = normalise_number(number)
    if not LOOKUP_URL or not re.match(r"^[A-Z]{2}\d", number):
        return ""
    return LOOKUP_URL.format(number=number)


def _get(session, url: str, timeout: float):
    """(body, content type) of a GET, at most MAX_PDF_BYTES; raises on an HTTP error."""
    resp = session.get(url, timeout=timeout, stream=True, headers={"User-Agent": USER_AGENT},
                       allow_redirects=True)
    resp.raise_for_status()
    chunks, size = [], 0
    for chunk in resp.iter_content(64 * 1024):
        size += len(chunk)
        if size > MAX_PDF_BYTES:
            resp.close()
            raise ValueError(f"larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks), resp.headers.get("Content-Type", "")


def pdf_link_in_page(page: str, base: str) -> str | None:
    """The PDF a patent page names: its citation_pdf_url, else its first link to a .pdf."""
    m = _PDF_META.search(page) or _PDF_META_REVERSED.search(page) or _PDF_HREF.search(page)
    return urljoin(base, html.unescape(m.group(1))) if m else None


def fetch_pdf(number: str, link: str = "", session=None, timeout: float = 30.0, lookup: str | None = None,
              cache_dir: Path | None = None) -> PatentPdf:
    """The patent's PDF: from the cache, the export's link, or the lookup by number (in that order)."""
    number = normalise_number(number)
    out = PatentPdf(number)
    folder = cache_dir or PDF_DIR
    cached = folder / f"{number}.pdf" if number else None
    if cached is not None and cached.is_file():
        out.data, out.source = cached.read_bytes(), "cache"
        address = cached.with_suffix(".url")
        out.url = address.read_text(encoding="utf-8").strip() if address.is_file() else ""
        return out
    lookup = LOOKUP_URL if lookup is None else lookup
    urls = [u for u in [link.strip() if re.match(r"https?://", link or "", re.IGNORECASE) else ""] if u]
    if lookup and number:
        urls.append(lookup.format(number=number))
    if not urls:
        out.note = "no link in the export and no publication number to look up"
        return out
    if session is None:
        import requests

        session = requests.Session()
    reasons = []
    for url in urls:
        try:
            body, kind = _get(session, url, timeout)
            if not body.startswith(b"%PDF"):  # a patent page: follow the PDF it names, once
                target = pdf_link_in_page(body[:2_000_000].decode("utf-8", "replace"), url) if "html" in kind or \
                    body.lstrip()[:1] == b"<" else None
                if not target:
                    reasons.append(f"{_host(url)}: no PDF found on the page")
                    continue
                body, kind = _get(session, target, timeout)
                url = target
            if not body.startswith(b"%PDF"):
                reasons.append(f"{_host(url)}: not a PDF")
                continue
        except Exception as exc:  # network, HTTP or size: try the next source
            reasons.append(f"{_host(url)}: {str(exc)[:120]}")
            continue
        out.data, out.source, out.url = body, url, url
        if cached is not None:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(body)
                cached.with_suffix(".url").write_text(url, encoding="utf-8")
            except OSError:
                pass
        return out
    out.note = "; ".join(reasons)
    return out


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url[:40]


# --------------------------------------------------------------------------- finding the figures

ANALYSIS_HEIGHT = 900  # pixels a page is rendered at to be analysed
OUTPUT_WIDTH = 1400  # pixels across a page rendered for the crop; the figure is then scaled to MAX_FIGURE
MAX_FIGURE = (760, 760)


@dataclass
class PageLayout:
    """A page's ink split into drawing parts and text-like parts (in analysis pixels)."""

    index: int
    height: int
    width: int
    ink: int  # ink pixels
    figure_ink: int  # ink in drawing parts
    box: tuple[int, int, int, int] | None  # (top, left, bottom, right) around all drawing regions, or None
    main_box: tuple[int, int, int, int] | None = None  # the largest drawing region, without a logo in the header
    main_ink: int = 0

    @property
    def is_drawing_sheet(self) -> bool:
        return self.box is not None and self.ink > 0.0015 * self.height * self.width and \
            self.figure_ink >= 0.55 * self.ink

    @property
    def has_figure(self) -> bool:
        """Whether the page holds a drawing of its own size, beside text (judged by the region, not by its share of
        the ink: dense CN, JP or KR text has far more ink than the abstract drawing below it)."""
        if self.main_box is None:
            return False
        top, left, bottom, right = self.main_box
        area = (bottom - top) * (right - left) / (self.height * self.width)
        return area >= 0.025 and self.main_ink >= 0.0015 * self.height * self.width


def _regions(parts: list[tuple[tuple[int, int, int, int], int]], gap_y: float, gap_x: float):
    """Drawing parts grouped into regions: parts closer than the gaps belong together. [(box, ink)]"""
    regions = [list(p) for p in parts]
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                (a, ia), (b, ib) = regions[i], regions[j]
                if (a[0] - gap_y < b[2] and b[0] - gap_y < a[2] and a[1] - gap_x < b[3] and b[1] - gap_x < a[3]):
                    regions[i] = [(min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])), ia + ib]
                    del regions[j]
                    merged = True
                    break
            if merged:
                break
    return [tuple(r) for r in regions]


def page_layout(gray: np.ndarray, index: int = 0) -> PageLayout:
    """Where the drawing is on a page (a grey-scale image, 0 = black). Letters merge into words but not into
    lines; a part counts as drawing when it is tall, not a ruled line, and not a table or frame full of text.
    Rendered pages are analysed the same way whether the PDF has a text layer or is a scan."""
    from scipy import ndimage

    h, w = gray.shape
    ink = gray < 160
    margin_y, margin_x = int(0.02 * h), int(0.02 * w)  # scanner borders and punch holes
    ink[:margin_y], ink[-margin_y:], ink[:, :margin_x], ink[:, -margin_x:] = False, False, False, False
    total = int(ink.sum())
    if not total:
        return PageLayout(index, h, w, 0, 0, None)
    joined = ndimage.binary_dilation(ink, structure=np.ones((1, 3), bool))  # letters into words, not lines
    labels, n = ndimage.label(joined, structure=np.ones((3, 3), bool))
    counts = np.bincount(labels[ink], minlength=n + 1)
    boxes = ndimage.find_objects(labels)
    tall = []
    small_centres = []
    for i, sl in enumerate(boxes, start=1):
        if sl is None or not counts[i]:
            continue
        bh, bw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if max(bh, bw) / max(1, min(bh, bw)) > 25:  # a ruled line
            continue
        if bh >= 0.045 * h:
            tall.append((i, sl))
        else:
            small_centres.append(((sl[0].start + sl[0].stop) / 2, (sl[1].start + sl[1].stop) / 2, counts[i]))
    parts = []
    centres = np.array(small_centres) if small_centres else np.zeros((0, 3))
    for i, sl in tall:
        inside = ((centres[:, 0] > sl[0].start) & (centres[:, 0] < sl[0].stop)
                  & (centres[:, 1] > sl[1].start) & (centres[:, 1] < sl[1].stop)) if len(centres) else []
        text_ink = centres[inside, 2].sum() if len(centres) else 0
        if text_ink > 0.4 * counts[i]:  # a table or frame around text, not a drawing (numerals stay far below)
            continue
        parts.append(((sl[0].start, sl[1].start, sl[0].stop, sl[1].stop), int(counts[i])))
    if not parts:
        return PageLayout(index, h, w, total, 0, None)
    regions = _regions(parts, 0.06 * h, 0.06 * w)
    small = [sl for i, sl in enumerate(boxes, start=1) if sl is not None and counts[i]
             and sl[0].stop - sl[0].start < 0.045 * h]
    # A small region in the header band is a logo or a stamp (the EPO logo on an EP front page), not the figure.
    body = [r for r in regions if not (r[0][2] < 0.15 * h and (r[0][2] - r[0][0]) * (r[0][3] - r[0][1]) < 0.03 * h * w)]
    box = (min(r[0][0] for r in body), min(r[0][1] for r in body), max(r[0][2] for r in body),
           max(r[0][3] for r in body)) if body else None
    main = max(body, key=lambda r: r[1]) if body else None
    return PageLayout(index, h, w, total, int(sum(ink_ for _, ink_ in parts)),
                      _with_labels(box, small, h, w) if box else None,
                      _with_labels(main[0], small, h, w) if main else None, main[1] if main else 0)


def _with_labels(box, small, h: int, w: int):
    """The drawing's box grown to take in the reference numerals and "FIG. n" labels around it: small parts close
    to it that do not sit in a line of text (a line has many words side by side)."""
    rows = np.array([(sl[0].start + sl[0].stop) / 2 for sl in small]) if small else np.zeros(0)
    cols = np.array([(sl[1].start + sl[1].stop) / 2 for sl in small]) if small else np.zeros(0)
    top, left, bottom, right = box
    for _ in range(2):  # a label next to a numeral just taken in
        near_y, near_x = 0.08 * h, 0.06 * w
        for k, sl in enumerate(small):
            if not (top - near_y < rows[k] < bottom + near_y and left - near_x < cols[k] < right + near_x):
                continue
            if top <= rows[k] <= bottom and left <= cols[k] <= right:
                continue
            tall = sl[0].stop - sl[0].start
            in_line = ((np.abs(rows - rows[k]) < tall) & (np.abs(cols - cols[k]) < 0.3 * w)).sum()
            if in_line > 4:  # words of a sentence
                continue
            top, left = min(top, sl[0].start), min(left, sl[1].start)
            bottom, right = max(bottom, sl[0].stop), max(right, sl[1].stop)
    return top, left, bottom, right


def _render(page, height: int):
    scale = height / page.get_height()
    return page.render(scale=scale, grayscale=True).to_pil()


def _thumb(img) -> np.ndarray:
    from scipy import ndimage

    return ndimage.binary_dilation(np.asarray(img.convert("L").resize((64, 64))) < 200, iterations=2)


def same_drawing(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two crops show the same drawing: most of their strokes overlap once scaled to one size."""
    union = (a | b).sum()
    return bool(union) and (a & b).sum() / union > 0.45


def _png(img, size: tuple[int, int] = MAX_FIGURE) -> bytes:
    from PIL import ImageOps

    img = ImageOps.autocontrast(img.convert("L"))
    img.thumbnail(size)
    buf = io.BytesIO()
    img.quantize(colors=16).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@dataclass
class Candidate:
    """A figure the page analysis found (or, when it found none, a whole page for a vision model to look at)."""

    image: object  # PIL image
    label: str
    found: bool = True  # False: a whole page offered only to the vision model


MAX_CANDIDATES = 6


def candidates(pdf, limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """The front-page figure, then drawing sheets that show something else, cropped; when no page holds a drawing,
    the first and last pages whole (as candidates for a vision model only)."""
    layouts = [page_layout(np.asarray(_render(pdf[i], ANALYSIS_HEIGHT).convert("L")).copy(), i)
               for i in range(min(len(pdf), MAX_PAGES))]
    picks: list[tuple[PageLayout, str]] = []
    if layouts and layouts[0].has_figure:
        picks.append((layouts[0], "Representative figure (front page)"))
    picks += [(lay, f"Drawing sheet, page {lay.index + 1}") for lay in layouts[1:] if lay.is_drawing_sheet]
    out, seen = [], []
    for lay, label in picks:
        if len(out) >= limit:
            break
        page = pdf[lay.index]
        big = _render(page, int(OUTPUT_WIDTH * page.get_height() / page.get_width()))
        factor = big.height / lay.height
        top, left, bottom, right = lay.main_box if lay.index == 0 and not lay.is_drawing_sheet else lay.box
        pad_y, pad_x = 0.03 * lay.height, 0.03 * lay.width
        crop = big.crop((int(max(0, left - pad_x) * factor), int(max(0, top - pad_y) * factor),
                         int(min(lay.width, right + pad_x) * factor), int(min(lay.height, bottom + pad_y) * factor)))
        thumb = _thumb(crop)
        if any(same_drawing(thumb, other) for other in seen):  # the front figure again on its sheet
            continue
        seen.append(thumb)
        out.append(Candidate(crop, label))
    if not out:
        pages = sorted(set(range(min(3, len(pdf)))) | set(range(max(0, len(pdf) - 3), len(pdf))))
        out = [Candidate(_render(pdf[i], 1100), f"Page {i + 1}", found=False) for i in pages[:limit]]
    return out


def find_figures(data: bytes, limit: int = 2, chooser=None) -> list[Figure]:
    """Up to `limit` figures. `chooser` (a vision model, see vision_chooser) picks and describes them among the
    candidates; without it, or when it fails, the front-page figure comes first, then other drawing sheets."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        cands = candidates(pdf)
        if chooser is not None and cands:
            try:
                chosen = chooser(cands)
            except Exception:  # the vision model is down, refuses images or answers badly: the page analysis decides
                chosen = None
            if chosen is not None:
                return [Figure(_png(cands[i].image), cands[i].label, description)
                        for i, description in chosen[:limit]]
        return [Figure(_png(c.image), c.label) for c in cands if c.found][:limit]
    finally:
        pdf.close()


VISION_SYSTEM = """\
You look at images taken from a patent PDF and pick its most representative drawings. You get the patent's \
number, title and the start of its abstract and first claim, then numbered images: crops of drawings, or whole \
pages when no drawing was found.
Reply with ONLY one JSON object: {"figures": [{"image": <its number>, "description": "<what the drawing shows, at \
most 25 words, naming the parts the claim is about>"}]}
List at most LIMIT images, the most representative first: the drawing that best shows the claimed invention, then \
one that adds something (another view, a graph of the effect). Leave out images that are only text, tables, \
formulas or bibliographic data. If no image is a drawing, reply {"figures": []}.\
"""


def vision_chooser(client, context: str, limit: int = 2):
    """A chooser for find_figures: one request to a vision model with the candidates as images. Returns
    [(candidate index, description)]; raises when the reply is unusable (find_figures then decides alone)."""
    import base64

    from .llmjson import chat_text, extract_json

    def choose(cands: list[Candidate]) -> list[tuple[int, str]]:
        parts: list[dict] = [{"type": "text", "text": f"{context}\n\n{len(cands)} images follow."}]
        for k, cand in enumerate(cands, start=1):
            data = base64.b64encode(_png(cand.image, (900, 900))).decode("ascii")
            parts += [{"type": "text", "text": f"Image {k}: {cand.label}"},
                      {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}]
        reply = chat_text(client, [{"role": "system", "content": VISION_SYSTEM.replace("LIMIT", str(limit))},
                                   {"role": "user", "content": parts}])
        data = extract_json(reply)
        items = data.get("figures") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("no figure list in the vision model's reply")
        chosen, used = [], set()
        for item in items:
            try:
                k = int(item.get("image")) - 1
            except (AttributeError, TypeError, ValueError):
                continue
            if 0 <= k < len(cands) and k not in used:
                used.add(k)
                chosen.append((k, " ".join(str(item.get("description", "")).split())[:300]))
        return chosen[:limit]

    return choose


def patent_figures(number: str, link: str = "", session=None, limit: int = 2, chooser=None, **fetch_args) -> PatentPdf:
    """The PDF and its figures; `note` says why there are none."""
    result = fetch_pdf(number, link, session=session, **fetch_args)
    if result.data is None:
        return result
    try:
        result.figures = find_figures(result.data, limit, chooser)
    except ImportError:
        result.note = "pypdfium2 is not installed (python -m pip install -r requirements.txt)"
    except Exception as exc:  # a damaged or unusual PDF: the card shows the link instead
        result.note = f"the PDF could not be read ({str(exc)[:100]})"
    else:
        if not result.figures:
            result.note = "no drawing found in the PDF"
    return result
