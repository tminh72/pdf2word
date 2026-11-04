"""Chuyển artifact của pipeline ngoài (Markdown / TXT / HTML) sang DOCX.

Mục đích không phải dựng lại bố cục đẹp, mà tạo DOCX tương đương về nội dung
để dùng chung bộ metric với pipeline built-in (text, heading, bảng).
"""

from __future__ import annotations

import html
import os
import re
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor


def _set_font(target, name: str) -> None:
    target.font.name = name
    try:
        rpr = target.element.get_or_add_rPr()
        fonts = rpr.get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), name)
    except Exception:  # noqa: BLE001 - DOCX lạ vẫn nên ghi được
        pass


def _set_lang(target, lang: str = "vi-VN") -> None:
    try:
        rpr = target.element.get_or_add_rPr()
        el = rpr.find(qn("w:lang"))
        if el is None:
            el = OxmlElement("w:lang")
            rpr.append(el)
        el.set(qn("w:val"), lang)
        el.set(qn("w:eastAsia"), lang)
    except Exception:  # noqa: BLE001
        pass


@dataclass
class _Block:
    kind: str  # heading | paragraph | list | table
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    ordered: bool = False


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*\S)\s*$")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_FENCE_RE = re.compile(r"^\s*```")


def _clean_inline(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    # Bỏ dấu nhấn mạnh đơn, tránh đụng dấu * trong biểu thức.
    text = re.sub(r"(?<!\w)[*_](\S.*?\S)[*_](?!\w)", r"\1", text)
    return html.unescape(text).strip()


def _split_md_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    # Tách theo | chưa escape; đủ dùng cho output của các tool phổ biến.
    return [_clean_inline(cell) for cell in line.split("|")]


def markdown_blocks(text: str) -> list[_Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[_Block] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        nonlocal para
        if para:
            joined = " ".join(p.strip() for p in para if p.strip())
            if joined:
                blocks.append(_Block("paragraph", _clean_inline(joined)))
            para = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if _FENCE_RE.match(line):
            flush()
            i += 1
            code: list[str] = []
            while i < len(lines) and not _FENCE_RE.match(lines[i]):
                code.append(lines[i])
                i += 1
            if code:
                for chunk in code:
                    if chunk.strip():
                        blocks.append(_Block("paragraph", chunk.rstrip()))
            i += 1
            continue

        if not stripped:
            flush()
            i += 1
            continue

        m = _HEADING_RE.match(line)
        if m:
            flush()
            blocks.append(_Block("heading", _clean_inline(m.group(2)), level=len(m.group(1))))
            i += 1
            continue

        # Bảng pipe: dòng hiện tại + dòng phân cách + các dòng dữ liệu.
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            flush()
            rows: list[list[str]] = [_split_md_row(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_md_row(lines[i]))
                i += 1
            width = max((len(r) for r in rows), default=0)
            rows = [r + [""] * (width - len(r)) for r in rows]
            blocks.append(_Block("table", rows=rows))
            continue

        m = _ORDERED_RE.match(line)
        if m:
            flush()
            blocks.append(_Block("list", _clean_inline(m.group(2)), ordered=True))
            i += 1
            continue

        m = _BULLET_RE.match(line)
        if m:
            flush()
            blocks.append(_Block("list", _clean_inline(m.group(1)), ordered=False))
            i += 1
            continue

        if stripped.startswith(">"):
            flush()
            blocks.append(_Block("paragraph", _clean_inline(stripped.lstrip("> ").strip())))
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush()
    return blocks


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "head", "meta", "link", "noscript", "svg"}
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "li", "blockquote", "pre"}
_TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th"}


class _HtmlBlocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._para: list[str] = []
        self._heading: str | None = None
        self._heading_level = 0
        self._skip_depth = 0
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_tag = ""

    def _flush(self) -> None:
        if self._para:
            text = " ".join(part.strip() for part in self._para if part.strip())
            if text:
                self.blocks.append(_Block("paragraph", _clean_inline(text)))
            self._para = []

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(_clean_inline(" ".join(self._cell)))
        self._cell = None
        self._cell_tag = ""

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            if any(c for c in self._row):
                self._rows.append(self._row)
        self._row = None

    def _close_table(self) -> None:
        self._close_row()
        if self._rows:
            width = max(len(r) for r in self._rows)
            rows = [r + [""] * (width - len(r)) for r in self._rows]
            self.blocks.append(_Block("table", rows=rows))
        self._rows = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _TABLE_TAGS:
            if not self._table_depth:
                self._flush()
            self._table_depth += 1
            if tag == "tr":
                self._close_row()
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._close_cell()
                self._cell = []
                self._cell_tag = tag
            return
        if self._table_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        elif tag in ("br", "hr"):
            self._flush()
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._heading = ""
            self._heading_level = int(tag[1])

    def handle_endtag(self, tag: str) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _TABLE_TAGS:
            if tag == "tr":
                self._close_row()
            elif tag in ("td", "th"):
                self._close_cell()
            elif tag == "table":
                self._close_table()
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth:
            return
        if re.fullmatch(r"h[1-6]", tag) and self._heading is not None:
            text = _clean_inline(self._heading)
            if text:
                self.blocks.append(_Block("heading", text, level=self._heading_level))
            self._heading = None
            self._heading_level = 0
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
            return
        if self._table_depth:
            return
        if self._heading is not None:
            self._heading += data
        elif data.strip():
            self._para.append(data)


def html_blocks(text: str) -> list[_Block]:
    parser = _HtmlBlocks()
    parser.feed(text)
    parser.close()
    parser._flush()  # noqa: SLF001 - đóng block cuối
    return parser.blocks


# --------------------------------------------------------------------------
# TXT
# --------------------------------------------------------------------------


def text_blocks(text: str) -> list[_Block]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[_Block] = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # TXT do một số tool xuất ra có thể vẫn còn heading markdown.
        for line in chunk.split("\n"):
            m = _HEADING_RE.match(line)
            if m and len(chunk.split("\n")) <= 12:
                blocks.append(_Block("heading", _clean_inline(m.group(2)), level=len(m.group(1))))
            else:
                blocks.append(_Block("paragraph", _clean_inline(line)))
    return blocks


# --------------------------------------------------------------------------
# Ghi DOCX
# --------------------------------------------------------------------------


def write_blocks(blocks: list[_Block], dest: str) -> str:
    docx = DocxDocument()
    styles = docx.styles
    normal = styles["Normal"]
    normal.font.size = Pt(11)
    _set_font(normal, "Times New Roman")
    _set_lang(normal)
    normal.paragraph_format.space_after = Pt(3)

    for level in range(1, 5):
        try:
            style = styles[f"Heading {level}"]
        except KeyError:
            continue
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        _set_font(style, "Times New Roman")
        _set_lang(style)

    for block in blocks:
        if block.kind == "heading":
            docx.add_heading(block.text, level=max(1, min(4, block.level or 1)))
        elif block.kind == "table":
            if not block.rows:
                continue
            n_cols = max(len(row) for row in block.rows)
            table = docx.add_table(rows=len(block.rows), cols=n_cols)
            table.style = "Table Grid"
            for r, row in enumerate(block.rows):
                for c in range(n_cols):
                    value = row[c] if c < len(row) else ""
                    cell = table.cell(r, c)
                    cell.text = value
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(10)
        elif block.kind == "list":
            style = "List Number" if block.ordered else "List Bullet"
            try:
                docx.add_paragraph(block.text, style=style)
            except KeyError:
                docx.add_paragraph(block.text)
        else:
            docx.add_paragraph(block.text)

    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    docx.save(dest)
    return dest


def convert_artifact(src: str, dest: str) -> str:
    """Chuyển .md/.markdown/.txt/.html/.htm sang DOCX; .docx được copy."""
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"Không thấy artifact: {src}")
    suffix = src_path.suffix.lower()
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".docx":
        if src_path.resolve() != dest_path.resolve():
            shutil.copy2(src_path, dest_path)
        return str(dest_path)

    text = src_path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".md", ".markdown"):
        blocks = markdown_blocks(text)
    elif suffix in (".html", ".htm"):
        blocks = html_blocks(text)
    elif suffix in (".txt", ".text"):
        blocks = text_blocks(text)
    else:
        raise ValueError(f"Không hỗ trợ artifact {suffix}; cần .docx/.md/.txt/.html")
    return write_blocks(blocks, str(dest_path))
