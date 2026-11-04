"""Tiện ích font: chuẩn hoá tên font, suy ra bold/italic, đổi màu PDF sang RGB."""

from __future__ import annotations

import re

from ..idm import Color

_BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demibold", "extrabold", "demi")
_ITALIC_HINTS = ("italic", "oblique", "kursiv")

# Tên font phổ biến -> font hệ thống trên Windows
_FAMILIES: list[tuple[str, str]] = [
    ("timesnewroman", "Times New Roman"),
    ("times", "Times New Roman"),
    ("arial", "Arial"),
    ("helvetica", "Arial"),
    ("calibri", "Calibri"),
    ("cambria", "Cambria"),
    ("carlito", "Calibri"),
    ("caladea", "Cambria"),
    ("tahoma", "Tahoma"),
    ("verdana", "Verdana"),
    ("segoe", "Segoe UI"),
    ("georgia", "Georgia"),
    ("courier", "Courier New"),
    ("consolas", "Consolas"),
    ("garamond", "Garamond"),
    ("palatino", "Palatino Linotype"),
    ("bookantiqua", "Book Antiqua"),
    ("roboto", "Roboto"),
    ("opensans", "Open Sans"),
    ("notosans", "Noto Sans"),
    ("notoserif", "Noto Serif"),
    ("dejavu", "DejaVu Sans"),
    ("liberation", "Liberation Serif"),
]


def normalize_font_name(raw: str) -> str:
    """'ABCDEF+Arial-BoldMT' -> 'Arial'."""
    if not raw:
        return "Times New Roman"
    name = raw.split("+")[-1]
    low = re.sub(r"[^a-z]", "", name.lower())

    # bỏ hậu tố style: -Bold, -Italic, -BoldItalic, BoldMT, PSMT...
    core = re.split(r"[-_,]", name)[0]

    if "timesnewromanpsmt" in low or "timesnewroman" in low:
        return "Times New Roman"
    for key, friendly in _FAMILIES:
        if key in low:
            return friendly

    core = re.sub(r"(?i)(ps)?(mt|ms)$", "", core).strip()
    core = re.sub(r"(?i)[-_]?(bold|italic|oblique|black|heavy|semibold)+$", "", core).strip()
    core = re.sub(r"(?i)(bold|italic|oblique|black|heavy)$", "", core).strip()
    return core or "Times New Roman"


def is_bold(raw: str) -> bool:
    low = raw.lower()
    return any(h in low for h in _BOLD_HINTS)


def is_italic(raw: str) -> bool:
    low = raw.lower()
    return any(h in low for h in _ITALIC_HINTS)


def pdf_color_to_rgb(ncolor) -> Color:
    """ncolor của pdfminer: float (gray) hoặc tuple 1/3/4 thành phần trong [0,1]."""
    try:
        if ncolor is None:
            return (0, 0, 0)
        if isinstance(ncolor, (int, float)):
            v = max(0, min(255, int(round(float(ncolor) * 255))))
            return (v, v, v)
        comps = [max(0.0, min(1.0, float(c))) for c in ncolor]
        if len(comps) == 1:
            v = int(round(comps[0] * 255))
            return (v, v, v)
        if len(comps) == 3:
            return tuple(int(round(c * 255)) for c in comps)  # type: ignore[return-value]
        if len(comps) == 4:
            c, m, y, k = comps
            return (
                int(round(255 * (1 - c) * (1 - k))),
                int(round(255 * (1 - m) * (1 - k))),
                int(round(255 * (1 - y) * (1 - k))),
            )
    except Exception:
        pass
    return (0, 0, 0)


def color_is_black(color: Color) -> bool:
    return color[0] < 24 and color[1] < 24 and color[2] < 24
