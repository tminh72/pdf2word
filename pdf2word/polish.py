"""Hậu kỳ tài liệu: chuẩn hoá chính tả tiếng Việt, khoảng trắng và font.

Chạy trên IDM trước khi ghi DOCX, nên áp dụng cho cả engine digital và OCR.

Phần "chính tả" ở đây là chuẩn hoá **chính tả/đặt dấu thanh**:
- đưa kiểu đặt dấu cũ về kiểu mới (hoà → hòa, thuỷ → thủy, khoẻ → khỏe);
- sửa dấu thanh/thanh điệu đặt sai vị trí do OCR hoặc font lỗi
  (đựơc → được, ngừơi → người, thựơng → thương);
- áp dụng từ điển sửa lỗi tuỳ chọn trong ``models/spellfix.txt``.

Ngoài ra: gộp khoảng trắng thừa, bỏ space trước dấu câu, thêm space sau dấu câu,
đổi ligature (ﬁ → fi), làm sạch tên font kiểu PostScript, chuẩn hoá cỡ chữ.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass

from .idm import Block, Cell, Document, Span, Table

_TONE_MARKS = {"\u0300", "\u0301", "\u0303", "\u0309", "\u0323"}
_QUALITY_MARKS = {"\u0302", "\u0306", "\u031b"}  # circumflex, breve, horn
_VOWELS = set("aeiouyAEIOUY")

_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "\u00ad": "",
    }
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES_RE = re.compile(r"[ \t\r\f\v]+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?%\)\]\}»”])")
_SPACE_AFTER_PUNCT_RE = re.compile(r"([,;:])(?=[^\s\d/\\])")
_MULTI_PUNCT_RE = re.compile(r"([,;:])\1+")

_FONT_ALIASES = {
    "arialmt": "Arial",
    "arial": "Arial",
    "helvetica": "Arial",
    "helveticaneue": "Arial",
    "timesnewromanpsmt": "Times New Roman",
    "timesnewroman": "Times New Roman",
    "times": "Times New Roman",
    "timenewroman": "Times New Roman",
    "calibri": "Calibri",
    "cambria": "Cambria",
    "cambriae": "Cambria",
    "cambriamath": "Cambria Math",
    "segoeui": "Segoe UI",
    "segoeuilight": "Segoe UI Light",
    "tahoma": "Tahoma",
    "verdana": "Verdana",
    "couriernew": "Courier New",
    "courier": "Courier New",
    "georgia": "Georgia",
    "palatino": "Palatino Linotype",
    "palatinolinotype": "Palatino Linotype",
    "garamond": "Garamond",
    "consolas": "Consolas",
}

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")


@dataclass
class PolishOptions:
    spell: bool = True
    normalize_fonts: bool = True
    normalize_space: bool = True
    font: str | None = None  # None = giữ font gốc (đã làm sạch)
    snap_sizes: bool = True


@dataclass
class PolishStats:
    tokens: int = 0
    spell_fixes: int = 0
    space_fixes: int = 0
    font_fixes: int = 0
    size_fixes: int = 0
    dropped_blocks: int = 0

    def as_dict(self) -> dict:
        return {
            "tokens": self.tokens,
            "spell_fixes": self.spell_fixes,
            "space_fixes": self.space_fixes,
            "font_fixes": self.font_fixes,
            "size_fixes": self.size_fixes,
            "dropped_blocks": self.dropped_blocks,
        }


# --------------------------------------------------------------------------
# Chuẩn hoá tiếng Việt
# --------------------------------------------------------------------------


def _units(text: str) -> list[list[str]]:
    """Tách chuỗi NFD thành các "đơn vị": ký tự gốc + dấu kết hợp theo sau."""
    out: list[list[str]] = []
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.category(ch).startswith("L"):
            out.append([ch])
        elif out:
            out[-1].append(ch)
    return out


def _recompose(units: list[list[str]]) -> str:
    return unicodedata.normalize("NFC", "".join("".join(u) for u in units))


def normalize_vietnamese_tone(token: str) -> str:
    """Đặt lại dấu thanh về vị trí chuẩn (kiểu mới) cho một âm tiết.

    Chỉ xử lý token là một âm tiết (các nguyên âm liền nhau). Token khác
    được trả về nguyên vẹn.
    """
    if not token:
        return token

    nfd = unicodedata.normalize("NFD", token)
    for ch in nfd:
        if ch in _TONE_MARKS or ch in _QUALITY_MARKS:
            continue
        if not unicodedata.category(ch).startswith("L"):
            return token  # có dấu câu/số/ký tự lạ: không phải một âm tiết thuần

    units = _units(nfd)
    if not units:
        return token

    bases = [u[0] for u in units]
    lower = [b.lower() for b in bases]

    # "qu" và "gi" ở đầu từ: u/i thuộc phần phụ âm đầu, không phải nguyên âm.
    onset_skip: set[int] = set()
    if len(bases) >= 3 and lower[0] == "q" and lower[1] == "u" and lower[2] in "aeiouy":
        onset_skip.add(1)
    if len(bases) >= 3 and lower[0] == "g" and lower[1] == "i" and lower[2] in "aeiouy":
        onset_skip.add(1)

    vowel_units = [i for i, b in enumerate(bases) if b in _VOWELS and i not in onset_skip]
    if not vowel_units:
        return token

    # Một âm tiết chỉ có một cụm nguyên âm liền nhau.
    if vowel_units != list(range(vowel_units[0], vowel_units[-1] + 1)):
        return token

    tone = next((ch for u in units for ch in u if ch in _TONE_MARKS), None)
    if tone is None:
        return token

    quality_units = [i for i in vowel_units if any(ch in _QUALITY_MARKS for ch in units[i])]

    if quality_units:
        target = quality_units[-1]
    else:
        n = len(vowel_units)
        if n == 1:
            target = vowel_units[0]
        elif n >= 3:
            target = vowel_units[1]
        else:
            has_coda = any(b not in _VOWELS for b in bases[vowel_units[-1] + 1 :])
            target = vowel_units[1] if has_coda else vowel_units[0]

    # Bỏ mọi dấu thanh rồi gắn lại vào đúng nguyên âm.
    for unit in units:
        unit[:] = [ch for ch in unit if ch not in _TONE_MARKS]
    units[target].append(tone)
    return _recompose(units)


# --------------------------------------------------------------------------
# Từ điển sửa lỗi tuỳ chọn
# --------------------------------------------------------------------------


def _default_spellfix() -> str | None:
    env = os.environ.get("PDF2WORD_SPELLFIX")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "models", "spellfix.txt")),
        os.path.abspath(os.path.join(here, "..", "..", "models", "spellfix.txt")),
    ]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "models", "spellfix.txt"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "models", "spellfix.txt"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_spellfix(path: str | None = None) -> dict[str, str]:
    """Đọc file sửa lỗi, mỗi dòng ``sai => đúng`` (bỏ qua dòng #)."""
    path = path or _default_spellfix()
    if not path or not os.path.isfile(path):
        return {}
    fixes: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=>" in line:
                    wrong, right = line.split("=>", 1)
                elif "=" in line:
                    wrong, right = line.split("=", 1)
                else:
                    continue
                wrong, right = wrong.strip(), right.strip()
                if wrong and right:
                    fixes[wrong.lower()] = right
    except OSError:
        return {}
    return fixes


def _match_case(source: str, target: str) -> str:
    if source.isupper():
        return target.upper()
    if source[:1].isupper():
        return target[:1].upper() + target[1:]
    return target


def _apply_dictionary(token: str, fixes: dict[str, str]) -> str:
    if not fixes:
        return token
    target = fixes.get(token.lower())
    return _match_case(token, target) if target else token


# --------------------------------------------------------------------------
# Khoảng trắng / dấu câu
# --------------------------------------------------------------------------


def clean_text(
    text: str,
    options: PolishOptions,
    fixes: dict[str, str],
    stats: PolishStats,
    strip_edges: bool = True,
) -> str:
    if not text:
        return text
    text = text.translate(_LIGATURES)
    text = _CONTROL_RE.sub("", text)
    text = unicodedata.normalize("NFC", text)

    if options.normalize_space:
        cleaned = _SPACES_RE.sub(" ", text)
        cleaned = _MULTI_PUNCT_RE.sub(r"\1", cleaned)
        cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
        cleaned = _SPACE_AFTER_PUNCT_RE.sub(r"\1 ", cleaned)
        if strip_edges:
            cleaned = cleaned.strip()
        if cleaned != text:
            stats.space_fixes += 1
        text = cleaned

    if options.spell:
        pieces = re.split(r"(\s+)", text)
        changed = False
        for i, piece in enumerate(pieces):
            if not piece or piece.isspace():
                continue
            stats.tokens += 1
            if any(ch.isdigit() for ch in piece):
                continue
            fixed = _apply_dictionary(piece, fixes)
            fixed = normalize_vietnamese_tone(fixed)
            if fixed != piece:
                pieces[i] = fixed
                stats.spell_fixes += 1
                changed = True
        if changed:
            text = "".join(pieces)

    return text


# --------------------------------------------------------------------------
# Font / cỡ chữ
# --------------------------------------------------------------------------


def clean_font_name(name: str | None) -> str:
    if not name:
        return ""
    name = _SUBSET_RE.sub("", name.strip())
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    if key in _FONT_ALIASES:
        return _FONT_ALIASES[key]
    low = name.lower()
    if "times" in low or "tnr" in low:
        return "Times New Roman"
    if "arial" in low or "helvetica" in low:
        return "Arial"
    if "calibri" in low:
        return "Calibri"
    if "cambria" in low:
        return "Cambria"
    if "courier" in low:
        return "Courier New"
    if "segoe" in low:
        return "Segoe UI"
    return name


def _fix_span(span: Span, doc: Document, options: PolishOptions, stats: PolishStats) -> None:
    default_font = options.font or doc.body_font or "Times New Roman"
    if options.normalize_fonts or options.font:
        font = options.font or clean_font_name(span.font) or default_font
        if font != span.font:
            span.font = font
            stats.font_fixes += 1
    if options.snap_sizes:
        size = span.size if span.size and span.size > 0 else doc.body_size
        size = max(6.0, min(96.0, round(size * 2) / 2))
        if abs(size - span.size) > 1e-6:
            span.size = size
            stats.size_fixes += 1


_SYMBOL_FONT_HINTS = ("symbol", "wingdings", "webdings", "emoji", "math", "dingbat")


def _is_symbol_font(name: str) -> bool:
    low = (name or "").lower()
    return any(hint in low for hint in _SYMBOL_FONT_HINTS)


def _unify_line(spans: list[Span], options: PolishOptions, stats: PolishStats) -> None:
    """Làm font/cỡ chữ đồng nhất trong một dòng (giữ bold/italic và superscript)."""
    candidates = [s for s in spans if s.text.strip() and not _is_symbol_font(s.font)]
    if not candidates:
        return

    # Cỡ chữ chủ đạo theo số ký tự; snap các span gần nhau về cùng cỡ.
    size_weight: dict[float, int] = {}
    for span in candidates:
        key = round(span.size, 1)
        size_weight[key] = size_weight.get(key, 0) + len(span.text)
    dominant_size = max(size_weight, key=size_weight.get)
    tolerance = max(1.0, dominant_size * 0.12)

    if options.snap_sizes:
        for span in candidates:
            if abs(span.size - dominant_size) <= tolerance and abs(span.size - dominant_size) > 1e-6:
                span.size = dominant_size
                stats.size_fixes += 1

    if not (options.normalize_fonts or options.font):
        return

    font_weight: dict[str, int] = {}
    for span in candidates:
        font_weight[span.font] = font_weight.get(span.font, 0) + len(span.text)
    dominant_font = max(font_weight, key=font_weight.get)
    for span in candidates:
        if span.font != dominant_font:
            span.font = dominant_font
            stats.font_fixes += 1


def _fix_spans(
    spans: list[Span],
    doc: Document,
    options: PolishOptions,
    fixes: dict[str, str],
    stats: PolishStats,
) -> list[Span]:
    kept: list[Span] = []
    for span in spans:
        # Giữ space ở đầu/cuối span: PDF hay tách 1 câu thành nhiều span,
        # strip ở đây sẽ làm dính chữ ("tỷ" + " đồng" -> "tỷđồng").
        span.text = clean_text(span.text, options, fixes, stats, strip_edges=False)
        if not span.text:
            continue
        _fix_span(span, doc, options, stats)
        kept.append(span)

    if not kept:
        return []

    # Chuẩn hoá space tại ranh giới các span.
    for i in range(1, len(kept)):
        prev, cur = kept[i - 1], kept[i]
        if prev.text.endswith(" ") and cur.text.startswith(" "):
            cur.text = cur.text.lstrip(" ")
        elif not prev.text.endswith((" ", "\t")) and not cur.text.startswith((" ", "\t")):
            tail = prev.text.rstrip()
            head = cur.text.lstrip()
            if tail and head and (
                tail[-1] in ",;:" or (tail[-1] == "." and head[:1].isupper())
            ):
                prev.text = tail + " "

    kept = [span for span in kept if span.text]

    # Bỏ space thừa ở đầu/cuối cả dòng, không đụng space giữa các span.
    if kept:
        kept[0].text = kept[0].text.lstrip(" ")
        kept[-1].text = kept[-1].text.rstrip(" ")
        kept = [span for span in kept if span.text]

    if kept:
        _unify_line(kept, options, stats)
    return kept


def _fix_cell(cell: Cell, doc: Document, options: PolishOptions, fixes: dict[str, str], stats: PolishStats) -> None:
    lines: list[list[Span]] = []
    for line in cell.lines:
        spans = _fix_spans(line, doc, options, fixes, stats)
        if spans:
            lines.append(spans)
    cell.lines = lines


def _fix_table(
    table: Table, doc: Document, options: PolishOptions, fixes: dict[str, str], stats: PolishStats
) -> None:
    for row in table.cells:
        for cell in row:
            if not cell.covered:
                _fix_cell(cell, doc, options, fixes, stats)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def polish_document(
    doc: Document,
    options: PolishOptions | None = None,
    spellfix_path: str | None = None,
) -> dict:
    """Chuẩn hoá tại chỗ; trả về thống kê để ghi log/meta."""
    options = options or PolishOptions()
    stats = PolishStats()
    fixes = load_spellfix(spellfix_path) if options.spell else {}

    if options.font:
        doc.body_font = options.font
    elif options.normalize_fonts:
        doc.body_font = clean_font_name(doc.body_font) or "Times New Roman"

    for key in ("header_text", "footer_text"):
        value = doc.meta.get(key)
        if isinstance(value, str) and value:
            doc.meta[key] = clean_text(value, options, fixes, stats)

    for page in doc.pages:
        kept_blocks: list[Block] = []
        for block in page.blocks:
            if block.kind == "table" and block.table:
                _fix_table(block.table, doc, options, fixes, stats)
                kept_blocks.append(block)
                continue
            if block.kind == "image":
                kept_blocks.append(block)
                continue
            block.spans = _fix_spans(block.spans, doc, options, fixes, stats)
            if block.text.strip():
                kept_blocks.append(block)
            else:
                stats.dropped_blocks += 1
        page.blocks = kept_blocks

    result = stats.as_dict()
    doc.meta["polish"] = result
    return result
