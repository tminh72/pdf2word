"""OCR cho PDF scan bằng Tesseract 5 (offline, CPU).

Luồng một trang:
  render 300 DPI -> xám/autocontrast -> dò đường kẻ bảng -> che vùng bảng
  -> OCR phần thân (psm 3) + OCR từng bảng (psm 6) -> gán chữ vào ô
  -> dựng dòng/đoạn -> Block/Table (IDM)

Tesseract chỉ trả về chữ + hộp bao, không có font/bold. Cỡ chữ được suy từ
chiều cao hộp; heading suy từ số mục (1., 2.1.), chữ IN HOA và khoảng trống.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from statistics import median
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from ..idm import BBox, Block, Cell, Document, Page, Span, Table
from .grid import RawGrid, find_grids, table_is_plausible
from .postprocess import (
    blocks_from_paragraphs,
    detect_headers_footers,
    detect_toc,
    resolve_layout,
    resolve_styles,
)
from .text import Char, TextLine

_FOOTER_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d{1,4})\s*$")


@dataclass
class OcrOptions:
    dpi: int = 300
    lang: str = "vie+eng"
    tesseract_cmd: str | None = None
    tessdata_dir: str | None = None
    min_conf: float = 25.0
    detect_tables: bool = True
    pages: tuple[int, int] | None = None
    page_breaks: bool = True
    body_font: str = "Times New Roman"
    debug: bool = False
    # hệ số quy đổi chiều cao hộp chữ (pixel) -> cỡ chữ point
    size_factor: float = 1.2
    progress: Callable[[int, int, str], None] | None = None
    cancel: Callable[[], bool] | None = None


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float
    block: int
    par: int
    line: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


# --------------------------------------------------------------------------
# Tesseract
# --------------------------------------------------------------------------


def find_tesseract(explicit: str | None = None) -> str:
    candidates = [explicit, os.environ.get("PDF2WORD_TESSERACT"), shutil.which("tesseract")]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "tesseract", "tesseract.exe"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "tesseract", "tesseract.exe"))
    candidates += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise RuntimeError(
        "Không tìm thấy tesseract.exe. Cài Tesseract 5 hoặc đặt biến môi trường PDF2WORD_TESSERACT."
    )


def default_tessdata() -> str | None:
    env = os.environ.get("PDF2WORD_TESSDATA")
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.abspath(os.path.join(here, "..", "..", "models", "tessdata"))
    if os.path.isdir(bundled):
        return bundled
    return None


def _run_tesseract(image: Image.Image, options: OcrOptions, psm: int) -> list[Word]:
    exe = find_tesseract(options.tesseract_cmd)
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    cmd = [exe, "stdin", "stdout"]
    tessdata = options.tessdata_dir or default_tessdata()
    if tessdata:
        cmd += ["--tessdata-dir", tessdata]
    cmd += ["-l", options.lang, "--psm", str(psm), "-c", "tessedit_create_tsv=1"]

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.run(
        cmd,
        input=buf.getvalue(),
        capture_output=True,
        timeout=180,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
    if proc.returncode != 0 or not proc.stdout:
        return []
    return _parse_tsv(proc.stdout.decode("utf-8", "replace"), options.min_conf)


def _parse_tsv(data: str, min_conf: float) -> list[Word]:
    words: list[Word] = []
    for row in data.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12:
            continue
        try:
            level = int(parts[0])
        except ValueError:
            continue
        if level != 5:
            continue
        text = parts[11].strip()
        if not text:
            continue
        try:
            conf = float(parts[10])
            left, top, width, height = (float(parts[i]) for i in (6, 7, 8, 9))
        except ValueError:
            continue
        if conf < min_conf:
            continue
        words.append(
            Word(
                text=text,
                x0=left,
                y0=top,
                x1=left + width,
                y1=top + height,
                conf=conf,
                block=int(parts[2]),
                par=int(parts[3]),
                line=int(parts[4]),
            )
        )
    return words


# --------------------------------------------------------------------------
# Dò bảng trên ảnh
# --------------------------------------------------------------------------


def _runs(mask: np.ndarray, min_len: int) -> tuple[np.ndarray, np.ndarray]:
    padded = np.concatenate(([0], mask.view(np.int8), [0]))
    diff = np.diff(padded)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    keep = (ends - starts) >= min_len
    return starts[keep], ends[keep]


def image_segments(dark: np.ndarray) -> tuple[list, list]:
    """Trả về segment ngang/dọc theo pixel."""
    h, w = dark.shape
    min_h = max(80, int(0.04 * w))
    min_v = max(80, int(0.035 * h))

    hs: list[tuple[float, float, float]] = []
    vs: list[tuple[float, float, float]] = []

    for y in range(h):
        starts, ends = _runs(dark[y], min_h)
        for a, b in zip(starts, ends):
            hs.append((float(y), float(a), float(b)))

    for x in range(w):
        starts, ends = _runs(dark[:, x], min_v)
        for a, b in zip(starts, ends):
            vs.append((float(x), float(a), float(b)))

    return hs, vs


def detect_image_grids(dark: np.ndarray, dpi: int, page_h: float, page_w: float) -> list[RawGrid]:
    hs_px, vs_px = image_segments(dark)
    if len(hs_px) < 4 or len(vs_px) < 2:
        return []

    s = 72.0 / dpi
    hs_pt = [(page_h - y * s, x0 * s, x1 * s) for y, x0, x1 in hs_px]
    vs_pt = [(x * s, page_h - y1 * s, page_h - y0 * s) for x, y0, y1 in vs_px]

    grids = find_grids(hs_pt, vs_pt)
    page_area = page_w * page_h
    out: list[RawGrid] = []
    for g in grids:
        bbox = g.bbox
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if g.n_cols < 2:
            continue
        if area > 0.6 * page_area or (bbox[3] - bbox[1]) > 0.8 * page_h:
            continue  # khung viền trang trí, không phải bảng
        if (bbox[2] - bbox[0]) < 60 or (bbox[3] - bbox[1]) < 20:
            continue
        out.append(g)
    return out


# --------------------------------------------------------------------------
# Dựng IDM
# --------------------------------------------------------------------------


def extract(pdf_path: str, options: OcrOptions, workdir: str) -> Document:
    import time

    import pypdfium2 as pdfium

    os.makedirs(workdir, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    doc = Document(source=pdf_path)
    doc.meta["engine"] = "ocr"
    doc.meta["ocr_lang"] = options.lang
    doc.meta["ocr_dpi"] = options.dpi

    start = (options.pages[0] - 1) if options.pages else 0
    end = options.pages[1] if options.pages else len(pdf)
    doc.meta["page_start"] = start + 1
    uid_counter = [0]
    t0 = time.time()
    total = min(end, len(pdf)) - start

    for page_index in range(start, min(end, len(pdf))):
        if options.cancel and options.cancel():
            doc.meta["cancelled"] = True
            break
        page = pdf[page_index]
        pw, ph = page.get_size()
        bitmap = page.render(scale=options.dpi / 72.0)
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()

        if options.debug:
            image.save(os.path.join(workdir, f"p{page_index + 1:03d}_scan.png"))

        blocks = _ocr_page(image, page_index, pw, ph, options, workdir, uid_counter)
        blocks.sort(key=lambda b: -b.bbox[3])
        doc.pages.append(Page(width=pw, height=ph, blocks=blocks))
        if options.progress:
            options.progress(page_index + 1 - start, total, "page")

    pdf.close()
    doc.meta["ocr_seconds"] = round(time.time() - t0, 2)

    resolve_styles(doc)
    detect_toc(doc)
    detect_headers_footers(doc)
    resolve_layout(doc)
    return doc


def _ocr_page(
    image: Image.Image,
    page_index: int,
    pw: float,
    ph: float,
    options: OcrOptions,
    workdir: str,
    uid_counter: list[int],
) -> list[Block]:
    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    arr = np.asarray(gray, dtype=np.uint8)
    dpi = options.dpi

    grids = detect_image_grids(arr < 170, dpi, ph, pw) if options.detect_tables else []

    # che vùng bảng để OCR phần thân không bị rối
    body = arr.copy()
    for g in grids:
        b = _grid_to_px(g.bbox, dpi, ph)
        body[max(0, b[1] - 2) : b[3] + 2, max(0, b[0] - 2) : b[2] + 2] = 255

    body_img = Image.fromarray(body)
    words = _run_tesseract(body_img, options, psm=3)

    blocks: list[Block] = []
    body_lines = _words_to_lines(words, dpi, ph, uid_counter, options)
    blocks.extend(_lines_to_blocks(body_lines))

    for g in grids:
        b = _grid_to_px(g.bbox, dpi, ph)
        pad = 2
        crop = Image.fromarray(arr[max(0, b[1] - pad) : b[3] + pad, max(0, b[0] - pad) : b[2] + pad])
        offset = (max(0, b[0] - pad), max(0, b[1] - pad))
        table_words = _run_tesseract(crop, options, psm=6)
        for w in table_words:
            w.x0 += offset[0]
            w.x1 += offset[0]
            w.y0 += offset[1]
            w.y1 += offset[1]
        table = _table_from_words(g, table_words, dpi, ph, options, pw * ph)
        if table:
            blocks.append(Block(kind="table", bbox=g.bbox, table=table))

    return blocks


def _grid_to_px(bbox: BBox, dpi: int, page_h: float) -> tuple[int, int, int, int]:
    s = dpi / 72.0
    x0, y0, x1, y1 = bbox
    return (int(x0 * s), int((page_h - y1) * s), int(x1 * s), int((page_h - y0) * s))

_TOC_NUM_RE = re.compile(r"^\d{1,3}$")
_TOC_HEAD_RE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*[\.\)]|[IVXLC]{1,4}\.)\s")


def _split_line_words(ws: list[Word], dpi: int) -> list[list[Word]]:
    """Tách 1 dòng OCR bị gộp nhiều mục lục (khoảng trắng lớn sau số trang)."""
    if len(ws) < 2:
        return [ws]
    gap_min = 22.0 * dpi / 72.0
    segs: list[list[Word]] = [[ws[0]]]
    for prev, cur in zip(ws, ws[1:]):
        gap = cur.x0 - prev.x1
        if gap >= gap_min and (_TOC_NUM_RE.match(prev.text) or _TOC_HEAD_RE.match(cur.text)):
            segs.append([cur])
        else:
            segs[-1].append(cur)
    return segs


def _strip_toc_number(ws: list[Word], dpi: int) -> tuple[list[Word], str | None]:
    """Bỏ số trang cuối dòng nếu nó cách phần tiêu đề một khoảng lớn (dot leader)."""
    if len(ws) < 2:
        return ws, None
    last = ws[-1]
    if not _TOC_NUM_RE.match(last.text):
        return ws, None
    if last.x0 - ws[-2].x1 < 15.0 * dpi / 72.0:
        return ws, None
    return ws[:-1], last.text


def _words_to_lines(
    words: list[Word], dpi: int, page_h: float, uid_counter: list[int], options: OcrOptions
) -> list[TextLine]:
    if not words:
        return []
    s = 72.0 / dpi
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for w in words:
        grouped.setdefault((w.block, w.par, w.line), []).append(w)

    lines: list[TextLine] = []
    for key in sorted(grouped):
        ws = sorted(grouped[key], key=lambda w: w.x0)
        for seg in _split_line_words(ws, dpi):
            seg, toc_page = _strip_toc_number(seg, dpi)
            line = _build_line(seg, key, s, page_h, uid_counter, options, dpi)
            if line is None:
                continue
            line.toc_page = toc_page
            lines.append(line)
    return lines


def _build_line(
    ws: list[Word],
    key: tuple[int, int, int],
    s: float,
    page_h: float,
    uid_counter: list[int],
    options: OcrOptions,
    dpi: int,
) -> TextLine | None:
    chars: list[Char] = []
    prev_x1 = None
    for i, w in enumerate(ws):
        size = round(max(4.0, (w.y1 - w.y0) * s * options.size_factor), 1)
        if i and prev_x1 is not None:
            chars.append(
                Char(
                    ch=" ",
                    bbox=(prev_x1 * s, page_h - w.y1 * s, w.x0 * s, page_h - w.y0 * s),
                    font=options.body_font,
                    raw_font=options.body_font,
                    size=size,
                    bold=False,
                    italic=False,
                    color=(0, 0, 0),
                    uid=-1,
                )
            )
        uid_counter[0] += 1
        chars.append(
            Char(
                ch=w.text,
                bbox=(w.x0 * s, page_h - w.y1 * s, w.x1 * s, page_h - w.y0 * s),
                font=options.body_font,
                raw_font=options.body_font,
                size=size,
                bold=False,
                italic=False,
                color=(0, 0, 0),
                uid=uid_counter[0],
            )
        )
        prev_x1 = w.x1

    if not chars:
        return None
    bbox = (
        min(c.x0 for c in chars),
        min(c.y0 for c in chars),
        max(c.x1 for c in chars),
        max(c.y1 for c in chars),
    )
    text = "".join(c.ch for c in chars).strip()
    if not text:
        return None
    line = TextLine(chars=chars, bbox=bbox, text=text)
    line.par_key = key[:2]
    return line


def _lines_to_blocks(lines: list[TextLine]) -> list[Block]:
    if not lines:
        return []

    gaps = [a.y0 - b.y1 for a, b in zip(lines, lines[1:]) if 0 <= a.y0 - b.y1 <= 60]
    med_gap = median(gaps) if gaps else 4.0
    sizes = sorted(l.size for l in lines)
    med_size = sizes[len(sizes) // 2]
    thr = med_gap + max(3.0, 0.28 * med_size)

    paragraphs: list[list[TextLine]] = []
    current: list[TextLine] = []
    prev_key = None
    for line in lines:
        key = getattr(line, "par_key", None)
        if not current:
            current = [line]
            prev_key = key
            continue
        prev = current[-1]
        gap = prev.y0 - line.y1
        new_par = (
            key != prev_key
            or gap > thr
            or abs(prev.size - line.size) > 1.0
            or abs(prev.x0 - line.x0) > 8
        )
        if new_par:
            paragraphs.append(current)
            current = [line]
        else:
            current.append(line)
        prev_key = key
    if current:
        paragraphs.append(current)

    return blocks_from_paragraphs(paragraphs, lines)


def _table_from_words(
    grid: RawGrid,
    words: list[Word],
    dpi: int,
    page_h: float,
    options: OcrOptions,
    page_area: float = 0.0,
) -> Table | None:
    if not words:
        return None
    s = 72.0 / dpi

    cells: list[list[Cell]] = [[Cell(covered=True) for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for (r, c), (rs, cs) in grid.spans.items():
        cells[r][c] = Cell(rowspan=rs, colspan=cs)

    per_cell: dict[tuple[int, int], list[Word]] = {}
    for w in words:
        x = w.cx * s
        y = page_h - w.cy * s
        anchor = grid.anchor_at(x, y)
        if anchor is None:
            continue
        per_cell.setdefault(anchor, []).append(w)

    text_cells = 0
    for anchor, ws in per_cell.items():
        cell = cells[anchor[0]][anchor[1]]
        cell.lines = _cell_lines(ws, dpi, page_h, options)
        if cell.text:
            text_cells += 1

    if text_cells == 0:
        return None
    if page_area and not table_is_plausible(grid, text_cells, page_area):
        return None

    table = Table(n_rows=grid.n_rows, n_cols=grid.n_cols, col_widths=grid.col_widths, cells=cells)
    if _ocr_header(cells):
        table.header_rows = 1
    return table


def _cell_lines(ws: list[Word], dpi: int, page_h: float, options: OcrOptions) -> list[list[Span]]:
    s = 72.0 / dpi
    ws = sorted(ws, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    rows: list[list[Word]] = []
    for w in ws:
        if rows and abs(rows[-1][0].cy - w.cy) <= max(3.0, 0.45 * (w.y1 - w.y0)):
            rows[-1].append(w)
        else:
            rows.append([w])

    out: list[list[Span]] = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        size = round(max(4.0, median([(w.y1 - w.y0) for w in row]) * s * options.size_factor), 1)
        text = " ".join(w.text for w in row)
        if text.strip():
            out.append([Span(text=text.strip(), font=options.body_font, size=size)])
    return out


def _ocr_header(cells: list[list[Cell]]) -> bool:
    if len(cells) < 2:
        return False
    first = [c for c in cells[0] if not c.covered and c.text]
    if not first:
        return False
    letters = [ch for c in first for ch in c.text if ch.isalpha()]
    if not letters:
        return False
    caps = sum(1 for ch in letters if ch.isupper()) / len(letters)
    return caps > 0.7


def cleanup_footer_text(text: str) -> tuple[str, bool]:
    """Tách tiền tố footer khỏi số trang. VD 'Trang 9' -> ('Trang ', True)."""
    m = _FOOTER_RE.match(text.strip())
    if not m:
        return text.strip(), False
    return m.group("prefix").strip(), True
