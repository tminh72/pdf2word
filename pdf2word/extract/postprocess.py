"""Hậu xử lý dùng chung cho mọi engine (digital & OCR).

- Gom dòng thành đoạn chuẩn (indent, align, list marker).
- Suy ra heading / title / TOC.
- Phát hiện header/footer lặp lại và tách khỏi nội dung.
- Suy ra lề trang.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..idm import BBox, Block, Document, Span
from .text import TextLine, chars_to_spans, detect_marker

_NUM_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\.\)]\s+\S")
_NUM_HEADING_LOOSE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\.\)]?\s+\S")
_DOTTED_TOC = re.compile(r"^(?P<title>.*?)\s*(?:[\.\u2026\u00b7\u2024]\s*){3,}\s*(?P<page>\d{1,3})\s*$")
# Nhiều dòng mục lục có thể bị gom thành một block (PDF ít khoảng cách dòng).
_DOTTED_TOC_ANY = re.compile(
    r"(?P<title>[^\n]+?)\s*(?:[\.\u2026\u00b7\u2024]\s*){3,}\s*(?P<page>\d{1,3})(?=\s|$)"
)
_TERMINALS = (".", "!", "?", ":", ";")


def merge_line_spans(lines: list[TextLine]) -> list[Span]:
    """Nối các dòng trong 1 đoạn thành span; xử lý ngắt từ bằng hyphen."""
    spans: list[Span] = []
    for line in lines:
        line_spans = chars_to_spans(line.chars)
        if not line_spans:
            continue
        if spans:
            prev_text = spans[-1].text
            if prev_text.endswith("-") and len(prev_text) > 1 and not line.text[:1].isupper():
                spans[-1].text = prev_text[:-1]
                line_spans[0].text = line_spans[0].text.lstrip()
            else:
                line_spans[0].text = " " + line_spans[0].text.lstrip()
        spans.extend(line_spans)
    return spans


def _align(bbox: BBox, page_lines: list[TextLine]) -> str:
    if not page_lines:
        return "left"
    page_left = min(l.x0 for l in page_lines)
    page_right = max(l.x1 for l in page_lines)
    page_w = max(page_right - page_left, 1.0)
    width = bbox[2] - bbox[0]
    center = (bbox[0] + bbox[2]) / 2
    if width < 0.75 * page_w and abs(center - (page_left + page_right) / 2) < 10:
        return "center"
    if width < 0.6 * page_w and abs(bbox[2] - page_right) < 4 and (bbox[0] - page_left) > 30:
        return "right"
    return "left"


def blocks_from_paragraphs(paras: list[list[TextLine]], page_lines: list[TextLine]) -> list[Block]:
    blocks: list[Block] = []
    content_left = min((l.x0 for l in page_lines), default=0.0)

    for para in paras:
        spans = merge_line_spans(para)
        if not spans:
            continue
        text = "".join(s.text for s in spans).strip()
        if not text:
            continue
        lefts = [l.x0 for l in para]
        body_left = min(lefts[1:]) if len(lefts) > 1 else lefts[0]
        first_indent = max(0.0, lefts[0] - body_left) if len(lefts) > 1 else 0.0
        bbox = (
            min(l.x0 for l in para),
            min(l.y0 for l in para),
            max(l.x1 for l in para),
            max(l.y1 for l in para),
        )
        block = Block(
            kind="paragraph",
            bbox=bbox,
            spans=spans,
            indent=min(72.0, max(0.0, body_left - content_left)),
            first_line_indent=first_indent,
        )
        if block.indent > 36:
            block.indent = 0.0
            block.first_line_indent = 0.0

        if len(para) == 1 and para[0].toc_page:
            block.kind = "toc"
            block.marker = para[0].toc_page
            block.align = "left"
            blocks.append(block)
            continue

        body = text.lstrip()
        if body.startswith(("- ", "• ", "+ ", "* ")):
            block.kind = "list"
            block.marker = body[0]
        elif any(l.list_marker for l in para):
            block.kind = "list"
            block.marker = "\u2022"
        else:
            is_list, is_ordered, marker = detect_marker(text)
            if is_list:
                block.marker = marker
                block.ordered = is_ordered

        block.align = _align(bbox, page_lines)
        blocks.append(block)
    return blocks


def detect_toc(doc: Document) -> None:
    """Biến các dòng 'tiêu đề ........ số trang' thành block TOC (tab leader)."""
    for page in doc.pages:
        index = 0
        while index < len(page.blocks):
            block = page.blocks[index]
            if block.kind in ("table", "image", "toc"):
                index += 1
                continue
            text = block.text.strip()
            m = _DOTTED_TOC.match(text)
            if m:
                title = m.group("title").strip()
                # Nếu "title" còn chứa một mục lục khác thì đây là block gộp nhiều dòng.
                if 3 <= len(title) <= 200 and not _DOTTED_TOC_ANY.search(title):
                    block.kind = "toc"
                    block.marker = m.group("page")
                    block.spans = [Span(
                        text=title,
                        font=block.spans[0].font if block.spans else "Times New Roman",
                        size=block.spans[0].size if block.spans else doc.body_size,
                    )]
                    block.align = "left"
                    index += 1
                    continue

            # Một số PDF gom cả khối mục lục vào một block; tách lại từng dòng.
            entries = [
                (hit.group("title").strip(), hit.group("page"))
                for hit in _DOTTED_TOC_ANY.finditer(text)
            ]
            entries = [(title, page_no) for title, page_no in entries if 3 <= len(title) <= 200]
            if len(entries) < 2:
                index += 1
                continue
            font = block.spans[0].font if block.spans else "Times New Roman"
            size = block.spans[0].size if block.spans else doc.body_size
            new_blocks = [
                Block(
                    kind="toc",
                    bbox=block.bbox,
                    spans=[Span(text=title, font=font, size=size)],
                    marker=page_no,
                    align="left",
                )
                for title, page_no in entries
            ]
            page.blocks[index : index + 1] = new_blocks
            index += len(new_blocks)


def detect_headers_footers(doc: Document) -> None:
    """Tìm dòng lặp lại ở mép trên/dưới -> header/footer Word; bỏ khỏi nội dung."""
    n_pages = len(doc.pages)
    if n_pages < 3:
        return

    threshold = max(3, int(0.4 * n_pages))
    candidates: dict[str, dict] = {}
    page_start = int(doc.meta.get("page_start", 1))

    for page_index, page in enumerate(doc.pages):
        seen: set[str] = set()
        for block in page.blocks:
            if block.kind in ("table", "image"):
                continue
            cy = (block.bbox[1] + block.bbox[3]) / 2
            if cy >= page.height * 0.88:
                zone = "header"
            elif cy <= page.height * 0.12:
                zone = "footer"
            else:
                continue
            key = _normalize_margin_text(block.text)
            if not key or key in seen:
                continue
            seen.add(key)
            entry = candidates.setdefault(
                key,
                {
                    "count": 0,
                    "zone": zone,
                    "text": block.text.strip(),
                    "align": block.align,
                    "size": block.spans[0].size if block.spans else doc.body_size,
                    "numbers": [],
                },
            )
            entry["count"] += 1
            num = re.search(r"(\d{1,4})\s*$", block.text.strip())
            if num:
                entry["numbers"].append((page_start + page_index, int(num.group(1))))

    best: dict[str, dict | None] = {"header": None, "footer": None}
    for entry in candidates.values():
        if entry["count"] < threshold:
            continue
        zone = entry["zone"]
        if best[zone] is None or entry["count"] > best[zone]["count"]:
            best[zone] = entry

    for page in doc.pages:
        for block in page.blocks:
            if block.kind in ("table", "image"):
                continue
            cy = (block.bbox[1] + block.bbox[3]) / 2
            zone = "header" if cy >= page.height * 0.88 else "footer" if cy <= page.height * 0.12 else None
            if not zone or not best[zone]:
                continue
            if _normalize_margin_text(block.text) == _normalize_margin_text(best[zone]["text"]):
                block.kind = zone  # writer bỏ qua, chỉ dùng meta

    if best["header"]:
        doc.meta["header_text"] = best["header"]["text"]
        doc.meta["header_size"] = round(float(best["header"]["size"]), 1)
        doc.meta["header_align"] = best["header"]["align"]
    if best["footer"]:
        footer = best["footer"]["text"]
        doc.meta["footer_text"] = footer
        doc.meta["footer_size"] = round(float(best["footer"]["size"]), 1)
        doc.meta["footer_align"] = best["footer"]["align"]
        doc.meta["footer_has_page_number"] = bool(re.search(r"\d", footer))
        numbers = best["footer"]["numbers"]
        if len(numbers) >= 3:
            offsets = sorted(idx - num for idx, num in numbers)
            doc.meta["footer_page_offset"] = offsets[len(offsets) // 2]


def _block_size(doc: Document, kind: str) -> float:
    for page in doc.pages:
        for block in page.blocks:
            if block.kind == kind and block.spans:
                return round(block.spans[0].size, 1)
    return doc.body_size


def _normalize_margin_text(text: str) -> str:
    """Chuẩn hoá để so khớp header/footer dù OCR lệch dấu tiếng Việt."""
    t = text.strip().lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("\u0111", "d")
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^a-z# ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) >= 4 else ""


def resolve_styles(doc: Document) -> None:
    sizes: Counter[float] = Counter()
    fonts: Counter[str] = Counter()
    for page in doc.pages:
        for block in page.blocks:
            if block.kind in ("table", "image"):
                continue
            for span in block.spans:
                n = len(span.text.strip())
                if n:
                    sizes[round(span.size, 1)] += n
                    fonts[span.font] += n

    body_size = sizes.most_common(1)[0][0] if sizes else 11.0
    body_font = fonts.most_common(1)[0][0] if fonts else "Times New Roman"
    doc.body_size = body_size
    doc.body_font = body_font
    is_deepdoc = doc.meta.get("engine") == "deepdoc"
    is_ocr = doc.meta.get("engine") == "ocr" or is_deepdoc

    if is_deepdoc:
        # Chiều cao hộp OCR thay đổi theo dấu tiếng Việt giữa các trang; gom
        # các cỡ quanh cỡ thân về đúng một cỡ để không thành heading giả.
        for page in doc.pages:
            for block in page.blocks:
                if block.kind in ("table", "image", "toc"):
                    continue
                size = block.dominant_size()
                if body_size and 0.78 * body_size <= size <= 1.25 * body_size:
                    for span in block.spans:
                        span.size = body_size

    for page in doc.pages:
        prev_bottom: float | None = None
        for block in page.blocks:
            if prev_bottom is not None:
                block.gap_before = max(0.0, prev_bottom - block.bbox[3])
            prev_bottom = block.bbox[1] if prev_bottom is None else min(prev_bottom, block.bbox[1])

    for page in doc.pages:
        prev_text = ""
        for block in page.blocks:
            if block.kind in ("table", "image", "toc"):
                prev_text = ""
                continue
            text = block.text.strip()
            if not text:
                continue
            size = block.dominant_size()
            ratio = size / body_size if body_size else 1.0
            bold_ratio = block.bold_ratio()
            letters = [c for c in text if c.isalpha()]
            caps = sum(1 for c in letters if c.isupper()) / len(letters) if letters else 0.0

            num_level = 0
            m = _NUM_HEADING.match(text)
            if m:
                num_level = len(m.group(1).split("."))
            elif is_deepdoc:
                m = _NUM_HEADING_LOOSE.match(text)
                if m:
                    num_level = len(m.group(1).split("."))

            level = 0
            if len(text) <= 220:
                if num_level and len(text) <= 140:
                    heading_like = (
                        bold_ratio > 0.55
                        or ratio >= 1.04
                        or (is_ocr and len(text) <= 90 and not text.endswith(_TERMINALS))
                    )
                    if heading_like:
                        level = min(num_level, 4)
                if level == 0 and caps > 0.9 and len(text) <= 100 and len(letters) >= 4:
                    if block.align == "center" or block.gap_before > 0.6 * size:
                        level = 1
                if level == 0 and ratio >= 1.5:
                    level = 1
                elif level == 0 and ratio >= 1.2:
                    level = 2
                elif level == 0 and ratio >= 1.07:
                    level = 3
                elif level == 0 and (
                    bold_ratio > 0.75
                    and len(text) <= 90
                    and not text.endswith((".", ",", ";", ":"))
                    and block.gap_before > 0.3 * size
                ):
                    level = 3

            if level:
                block.kind = "heading"
                block.level = level
                block.marker = ""
            elif block.kind == "paragraph" and block.marker:
                is_list = (
                    block.indent >= 6
                    or block.gap_before >= 0.5 * size
                    or prev_text.endswith(_TERMINALS)
                )
                if is_list:
                    block.kind = "list"
                else:
                    block.marker = ""
            prev_text = text

    if doc.pages:
        _resolve_cover_page(doc)


def _resolve_cover_page(doc: Document) -> None:
    """Trang bìa: chỉ dòng lớn nhất được căn giữa mới là title."""
    page = doc.pages[0]
    text_blocks = [
        b for b in page.blocks if b.kind not in ("table", "image", "header", "footer", "toc")
    ]
    centered = [b for b in text_blocks if b.align == "center" and b.spans]
    mostly_centered = len(centered) >= max(2, int(0.5 * len(text_blocks)))
    if not text_blocks or len(text_blocks) > 12 or not mostly_centered:
        for block in text_blocks[:4]:
            if block.kind == "heading" and block.level == 1 and block.align == "center":
                block.kind = "title"
                break
        return

    for block in text_blocks:
        if block.kind in ("heading", "title"):
            block.kind = "paragraph"
            block.level = 0
    biggest = max(centered, key=lambda b: (b.dominant_size(), len(b.text)))
    biggest.kind = "title"
    biggest.level = 1


def resolve_layout(doc: Document) -> None:
    page_left: list[float] = []
    page_right: list[float] = []
    page_top: list[float] = []
    page_bottom: list[float] = []

    for page in doc.pages:
        lefts, rights, tops, bottoms = [], [], [], []
        for block in page.blocks:
            if block.kind in ("header", "footer"):
                continue
            if block.kind == "image" and block.bbox[3] - block.bbox[1] > page.height * 0.7:
                continue
            lefts.append(block.bbox[0])
            rights.append(page.width - block.bbox[2])
            tops.append(page.height - block.bbox[3])
            bottoms.append(block.bbox[1])
        if not lefts:
            continue
        page_left.append(min(lefts))
        page_right.append(min(rights))
        page_top.append(min(tops))
        page_bottom.append(min(bottoms))

    if not page_left:
        return

    doc.margin_left = _clamp(min(page_left), 18, 120)
    doc.margin_right = _clamp(min(page_right), 18, 120)
    doc.margin_top = _clamp(min(page_top), 18, 120)
    doc.margin_bottom = _clamp(min(page_bottom), 18, 120)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
