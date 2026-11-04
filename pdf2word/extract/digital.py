"""Extractor cho PDF có text layer (digital).

- Text/style: pdfminer.six (MIT)
- Render ảnh/figure: pypdfium2 (Apache-2.0)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTCurve, LTFigure, LTImage, LTTextContainer, LTTextLine

from ..idm import BBox, Block, Document, ImageRef, Page
from .postprocess import blocks_from_paragraphs, detect_toc, resolve_layout, resolve_styles
from .text import TextLine, build_lines, group_paragraphs, is_marker_only, strip_line, xy_cut
from .tables import detect_tables

_PAGE_NUM_RE = re.compile(
    r"^\s*(?:trang|page|p\.)?\s*\d{1,4}\s*(?:/|of)\s*\d{1,4}\s*$|^\s*(?:trang|page|p\.)?\s*\d{1,4}\s*$",
    re.IGNORECASE,
)


@dataclass
class ExtractOptions:
    dpi: int = 200
    extract_images: bool = True
    extract_tables: bool = True
    min_image_side: float = 36.0
    page_breaks: bool = True
    skip_page_numbers: bool = True
    pages: tuple[int, int] | None = None  # 1-based, inclusive
    debug: bool = False
    progress: Callable[[int, int, str], None] | None = None
    cancel: Callable[[], bool] | None = None


@dataclass
class _PageObjects:
    lines: list[LTTextLine] = field(default_factory=list)
    curves: list[LTCurve] = field(default_factory=list)
    images: list[LTImage] = field(default_factory=list)
    figures: list[LTFigure] = field(default_factory=list)


def _walk(obj, acc: _PageObjects) -> None:
    if isinstance(obj, LTTextLine):
        acc.lines.append(obj)
        return
    if isinstance(obj, LTCurve):
        acc.curves.append(obj)
    if isinstance(obj, LTImage):
        acc.images.append(obj)
    if isinstance(obj, LTFigure):
        acc.figures.append(obj)
    if hasattr(obj, "__iter__"):
        for child in obj:
            _walk(child, acc)


def _figure_vector_info(fig: LTFigure) -> tuple[int, int]:
    """(số curve, số ký tự text) bên trong figure."""
    curves = chars = 0
    stack = list(fig)
    while stack:
        o = stack.pop()
        if isinstance(o, LTTextLine):
            chars += len(o.get_text().strip())
            continue
        if isinstance(o, LTCurve):
            curves += 1
        if hasattr(o, "__iter__"):
            stack.extend(list(o))
    return curves, chars


def render_region(pdf, page_index: int, bbox: BBox, out_path: str, dpi: int) -> bool:
    """Render vùng bbox (toạ độ PDF, gốc dưới-trái) ra PNG.

    pypdfium2 nhận crop = (trái, dưới, phải, trên) là *phần cắt bỏ*.
    """
    try:
        page = pdf[page_index]
        pw, ph = page.get_size()
        x0, y0, x1, y1 = bbox
        x0 = max(0.0, min(x0, pw))
        x1 = max(0.0, min(x1, pw))
        y0 = max(0.0, min(y0, ph))
        y1 = max(0.0, min(y1, ph))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        bitmap = page.render(scale=dpi / 72.0, crop=(x0, y0, pw - x1, ph - y1))
        pil = bitmap.to_pil()
        pil.save(out_path)
        pil.close()
        bitmap.close()
        return True
    except Exception:
        return False


def extract(pdf_path: str, options: ExtractOptions, workdir: str) -> Document:
    import pypdfium2 as pdfium

    os.makedirs(workdir, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    doc = Document(source=pdf_path)
    doc.meta["engine"] = "digital"
    counter = [0]
    img_seq = 0

    laparams = LAParams(
        line_overlap=0.5,
        char_margin=2.0,
        line_margin=0.5,
        word_margin=0.1,
        boxes_flow=None,
        detect_vertical=False,
        all_texts=True,
    )

    start = (options.pages[0] - 1) if options.pages else 0
    end = options.pages[1] if options.pages else None
    total = (end if end is not None else len(pdf)) - start

    for page_index, ltpage in enumerate(extract_pages(pdf_path, laparams=laparams)):
        if options.cancel and options.cancel():
            doc.meta["cancelled"] = True
            break
        if page_index < start or (end is not None and page_index >= end):
            continue

        pw = float(ltpage.width)
        ph = float(ltpage.height)
        acc = _PageObjects()
        _walk(ltpage, acc)

        lines = build_lines(acc.lines, set(), counter)
        lines = [l for l in lines if not _is_page_number(l, ph, options)]

        blocks: list[Block] = []
        used_uids: set[int] = set()
        table_boxes: list[BBox] = []

        if options.extract_tables:
            for hit in detect_tables(lines, acc.curves, pw, ph):
                used_uids |= hit.used_uids
                table_boxes.append(hit.bbox)
                blocks.append(Block(kind="table", bbox=hit.bbox, table=hit.table))

        remaining = [sl for line in lines if (sl := strip_line(line, used_uids))]
        remaining = [l for l in remaining if l.text.strip()]

        blocks.extend(_paragraph_blocks(remaining, pw, ph))

        if options.extract_images:
            image_blocks, img_seq = _extract_images(
                pdf, page_index, acc, table_boxes, workdir, img_seq, options, pw, ph
            )
            blocks.extend(image_blocks)

        blocks.sort(key=lambda b: -b.bbox[3])
        doc.pages.append(Page(width=pw, height=ph, blocks=blocks, rotation=getattr(ltpage, "rotate", 0)))
        if options.progress:
            options.progress(page_index + 1 - start, total, "page")

    pdf.close()
    resolve_styles(doc)
    detect_toc(doc)
    resolve_layout(doc)
    return doc


def _is_page_number(line: TextLine, page_h: float, options: ExtractOptions) -> bool:
    if not options.skip_page_numbers:
        return False
    cy = line.cy
    in_margin = cy < page_h * 0.07 or cy > page_h * 0.93
    return bool(in_margin and _PAGE_NUM_RE.match(line.text))


def _paragraph_blocks(lines: list[TextLine], pw: float, ph: float) -> list[Block]:
    # Dòng chỉ chứa bullet glyph -> gắn cờ list cho dòng nội dung cùng baseline
    markers = [l for l in lines if is_marker_only(l.text)]
    content = [l for l in lines if not is_marker_only(l.text)]
    for marker in markers:
        candidates = [
            l for l in content if abs(l.cy - marker.cy) <= max(3.0, 0.4 * l.size) and l.x0 >= marker.x0 - 1
        ]
        if candidates:
            nearest = min(candidates, key=lambda l: (abs(l.cy - marker.cy), l.x0 - marker.x0))
            nearest.list_marker = True

    filtered: list[TextLine] = xy_cut(content)
    if not filtered:
        return []

    # Ngưỡng ngắt đoạn thích nghi theo khoảng cách dòng thực tế của trang
    gaps = [a.y0 - b.y1 for a, b in zip(filtered, filtered[1:]) if 0 <= a.y0 - b.y1 <= 60]
    gap_threshold = None
    if gaps:
        gaps.sort()
        median_gap = gaps[len(gaps) // 2]
        sizes = sorted(l.size for l in filtered) or [11.0]
        median_size = sizes[len(sizes) // 2]
        gap_threshold = median_gap + max(3.0, 0.28 * median_size)

    paras = group_paragraphs(filtered, gap_threshold)
    return blocks_from_paragraphs(paras, filtered)


def _extract_images(
    pdf,
    page_index: int,
    acc: _PageObjects,
    table_boxes: list[BBox],
    workdir: str,
    seq: int,
    options: ExtractOptions,
    pw: float,
    ph: float,
) -> tuple[list[Block], int]:
    blocks: list[Block] = []
    page_area = pw * ph
    boxes: list[BBox] = []

    for img in acc.images:
        b: BBox = tuple(img.bbox)  # type: ignore[assignment]
        w, h = b[2] - b[0], b[3] - b[1]
        if w < options.min_image_side or h < options.min_image_side:
            continue
        if w * h > 0.8 * page_area:
            continue  # ảnh nền full trang
        boxes.append(b)

    for fig in acc.figures:
        b: BBox = tuple(fig.bbox)  # type: ignore[assignment]
        w, h = b[2] - b[0], b[3] - b[1]
        if w < 60 or h < 40:
            continue
        curves, chars = _figure_vector_info(fig)
        if curves < 2 or chars > 60:
            continue
        boxes.append(b)

    for b in _dedupe_boxes(boxes):
        if any(_overlap_ratio(b, t) > 0.3 for t in table_boxes):
            continue
        seq += 1
        path = os.path.join(workdir, f"p{page_index + 1:03d}_img{seq:03d}.png")
        if not render_region(pdf, page_index, b, path, options.dpi):
            continue
        blocks.append(
            Block(kind="image", bbox=b, image=ImageRef(path=path, width=b[2] - b[0], height=b[3] - b[1]))
        )
    return blocks, seq


def _dedupe_boxes(boxes: list[BBox]) -> list[BBox]:
    out: list[BBox] = []
    for b in sorted(boxes, key=lambda x: -((x[2] - x[0]) * (x[3] - x[1]))):
        if any(_overlap_ratio(b, o) > 0.85 for o in out):
            continue
        out.append(b)
    return out


def _overlap_ratio(a: BBox, b: BBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = (a[2] - a[0]) * (a[3] - a[1])
    return inter / area if area else 0.0
