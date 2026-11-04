"""Dựng dòng và đoạn từ character của pdfminer.

Luồng: LTChar -> Char -> TextLine -> reading order (XY-cut) -> paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pdfminer.layout import LTAnno, LTChar

from ..idm import BBox, Color, Span
from .fonts import normalize_font_name, pdf_color_to_rgb

_SPACE_GAP_RATIO = 0.28

_BULLET_RE = re.compile(r"^\s*([\u2022\u25aa\u25e6\u25cf\u25cb\u2023\u2219\u00b7\*\u2013\u2014\-])\s+")
_NUMBER_RE = re.compile(r"^\s*((?:\d{1,3})|(?:[a-zA-Z])|(?:[ivxlcdmIVXLCDM]{1,5}))[\.\)]\s+")


@dataclass
class Char:
    ch: str
    bbox: BBox
    font: str
    raw_font: str
    size: float
    bold: bool
    italic: bool
    color: Color
    uid: int

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class TextLine:
    chars: list[Char] = field(default_factory=list)
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    text: str = ""
    list_marker: bool = False  # dòng này là item của list (đã tách marker)
    par_key: tuple | None = None  # khoá đoạn do engine cung cấp (OCR: block + par)
    toc_page: str | None = None  # số trang nếu dòng là mục lục (tiêu đề ... trang)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def size(self) -> float:
        if not self.chars:
            return 0.0
        sizes = sorted(c.size for c in self.chars if c.ch.strip())
        if not sizes:
            return self.chars[0].size
        return sizes[len(sizes) // 2]

    @property
    def bold(self) -> bool:
        visible = [c for c in self.chars if c.ch.strip()]
        if not visible:
            return False
        return sum(1 for c in visible if c.bold) / len(visible) > 0.6


def _char_from_lt(c: LTChar, index: int) -> Char:
    text = c.get_text()
    gs = getattr(c, "graphicstate", None)
    ncolor = getattr(gs, "ncolor", None) if gs is not None else None
    raw = getattr(c, "fontname", "") or ""
    return Char(
        ch=text,
        bbox=tuple(c.bbox),  # type: ignore[arg-type]
        font=normalize_font_name(raw),
        raw_font=raw,
        size=round(float(c.size), 1),
        bold=("bold" in raw.lower()) or ("black" in raw.lower()) or ("heavy" in raw.lower()),
        italic=("italic" in raw.lower()) or ("oblique" in raw.lower()),
        color=pdf_color_to_rgb(ncolor),
        uid=index,
    )


def make_line(chars: list[Char]) -> TextLine | None:
    """Chèn space theo khoảng trống hình học rồi dựng TextLine."""
    chars = [c for c in chars if c.ch != "\n"]
    if not chars:
        return None

    with_spaces: list[Char] = []
    for c in chars:
        if with_spaces:
            prev = with_spaces[-1]
            gap = c.x0 - prev.x1
            need = (
                gap > _SPACE_GAP_RATIO * max(c.size, 1.0)
                and not prev.ch.isspace()
                and not c.ch.isspace()
            )
            if need:
                with_spaces.append(
                    Char(
                        ch=" ",
                        bbox=(prev.x1, c.bbox[1], prev.x1 + gap, c.bbox[3]),
                        font=c.font,
                        raw_font=c.raw_font,
                        size=c.size,
                        bold=False,
                        italic=False,
                        color=c.color,
                        uid=-1,
                    )
                )
        with_spaces.append(c)

    text = "".join(c.ch for c in with_spaces).strip()
    if not text:
        return None

    vis = [c for c in with_spaces if not c.ch.isspace()] or with_spaces
    bbox = (
        min(c.x0 for c in vis),
        min(c.y0 for c in vis),
        max(c.x1 for c in vis),
        max(c.y1 for c in vis),
    )
    return TextLine(chars=with_spaces, bbox=bbox, text=text)


def build_lines(lt_lines, excluded_uids: set[int], counter: list[int]) -> list[TextLine]:
    """Chuyển các LTTextLine của pdfminer thành TextLine của mình."""
    out: list[TextLine] = []
    for lt in lt_lines:
        chars: list[Char] = []
        for child in lt:
            if isinstance(child, LTChar):
                idx = counter[0]
                counter[0] += 1
                if idx in excluded_uids:
                    continue
                chars.append(_char_from_lt(child, idx))
            # LTAnno: pdfminer tự thêm space, ta dựng lại theo khoảng cách
        line = make_line(chars)
        if line:
            out.append(line)
    return out


def strip_line(line: TextLine, used_uids: set[int]) -> TextLine | None:
    """Bỏ các char đã thuộc bảng rồi dựng lại dòng."""
    chars = [c for c in line.chars if c.uid < 0 or c.uid not in used_uids]
    return make_line(chars)


def chars_to_spans(chars: list[Char]) -> list[Span]:
    spans: list[Span] = []
    last_key = None
    for c in chars:
        key = (c.font, c.size, c.bold, c.italic, c.color)
        if spans and key == last_key:
            spans[-1].text += c.ch
        else:
            spans.append(
                Span(
                    text=c.ch,
                    font=c.font,
                    size=c.size,
                    bold=c.bold,
                    italic=c.italic,
                    color=c.color,
                )
            )
            last_key = key
    # gộp khoảng trắng thừa
    for s in spans:
        s.text = re.sub(r"[ \t]+", " ", s.text)
    spans = [s for s in spans if s.text]
    if spans:
        spans[0].text = spans[0].text.lstrip()
        spans[-1].text = spans[-1].text.rstrip()
    return [s for s in spans if s.text]


# --------------------------------------------------------------------------
# Reading order: XY-cut
# --------------------------------------------------------------------------


def _max_gap(intervals: list[tuple[float, float]], min_width: float) -> float | None:
    """Trả về tâm của khoảng trống lớn nhất giữa các interval, nếu đủ rộng."""
    if len(intervals) < 2:
        return None
    intervals = sorted(intervals)
    best = None
    best_w = min_width
    cur_end = intervals[0][1]
    for start, end in intervals[1:]:
        if start - cur_end > best_w:
            best_w = start - cur_end
            best = (cur_end + start) / 2
        cur_end = max(cur_end, end)
    return best


def xy_cut(lines: list[TextLine], depth: int = 0) -> list[TextLine]:
    """Sắp thứ tự đọc: cắt dọc (cột) trước, rồi cắt ngang (khối)."""
    if len(lines) <= 1 or depth > 6:
        return sorted(lines, key=lambda l: (-l.cy, l.x0))

    vcut = _max_gap([(l.x0, l.x1) for l in lines], min_width=18.0)
    if vcut is not None:
        left = [l for l in lines if (l.x0 + l.x1) / 2 < vcut]
        right = [l for l in lines if (l.x0 + l.x1) / 2 >= vcut]
        if left and right and min(len(left), len(right)) >= 2:
            return xy_cut(left, depth + 1) + xy_cut(right, depth + 1)

    hcut = _max_gap([(l.y0, l.y1) for l in lines], min_width=14.0)
    if hcut is not None:
        top = [l for l in lines if (l.y0 + l.y1) / 2 > hcut]
        bottom = [l for l in lines if (l.y0 + l.y1) / 2 <= hcut]
        if top and bottom:
            return xy_cut(top, depth + 1) + xy_cut(bottom, depth + 1)

    return sorted(lines, key=lambda l: (-l.cy, l.x0))


# --------------------------------------------------------------------------
# Paragraph grouping
# --------------------------------------------------------------------------


def detect_marker(text: str) -> tuple[bool, bool, str]:
    """(is_list, ordered, marker)"""
    m = _BULLET_RE.match(text)
    if m:
        return True, False, m.group(1)
    m = _NUMBER_RE.match(text)
    if m:
        return True, True, m.group(1) + "."
    return False, False, ""


_TERMINAL = ".!?:;"


_ITEM_RE = re.compile(r"^\(cid:\d+\)$|^[\u2022\u25aa\u25e6\u25cf\u25cb\u2023\u2219\u00b7\*\-\u2013\u2014]$")


def is_marker_only(text: str) -> bool:
    """Dòng chỉ chứa bullet glyph (reportlab hay dùng '(cid:127)')."""
    return bool(_ITEM_RE.match(text.strip()))


def group_paragraphs(lines: list[TextLine], gap_threshold: float | None = None) -> list[list[TextLine]]:
    """Gom dòng thành đoạn dựa trên canh lề, khoảng cách dọc, style."""
    paragraphs: list[list[TextLine]] = []
    current: list[TextLine] = []

    def flush():
        nonlocal current
        if current:
            paragraphs.append(current)
            current = []

    max_width = max((l.width for l in lines), default=1.0)

    for line in lines:
        if not current:
            current = [line]
            continue

        prev = current[-1]
        same_size = abs(prev.size - line.size) <= 0.6
        same_bold = prev.bold == line.bold
        gap = prev.y0 - line.y1  # dương = khoảng trống giữa 2 dòng
        line_h = max(prev.height, line.height, 1.0)

        left_diff = abs(prev.x0 - line.x0)
        prev_is_wide = prev.width >= 0.82 * max_width
        ends_sentence = bool(prev.text) and prev.text.rstrip()[-1] in _TERMINAL

        thr = gap_threshold if gap_threshold is not None else 0.45 * line.size
        raw_list, _, _ = detect_marker(line.text)
        # marker kiểu "III." nằm giữa câu không được coi là list
        is_list = raw_list and (ends_sentence or gap > thr or left_diff > 6.0)

        new_para = False
        if line.list_marker:
            new_para = True
        elif not same_size or not same_bold:
            new_para = True
        elif gap > thr or gap < -0.8 * line_h:
            new_para = True
        elif is_list:
            new_para = True
        elif left_diff > 6.0 and not prev_is_wide:
            new_para = True
        elif ends_sentence and left_diff > 2.0:
            new_para = True
        elif prev.width < 0.3 * max_width and gap > max(0.45 * line_h, thr - 1):
            new_para = True

        if new_para:
            flush()
            current = [line]
        else:
            current.append(line)

    flush()
    return paragraphs
