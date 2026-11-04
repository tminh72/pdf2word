"""Renderer IDM -> DOCX (python-docx)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from docx import Document as DocxDocument
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from ..idm import Block, Document, Span, Table
from ..extract.fonts import color_is_black

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


@dataclass
class WriteOptions:
    page_breaks: bool = True
    embed_images: bool = True
    table_font_ratio: float = 0.95


def write_docx(doc: Document, out_path: str, options: WriteOptions | None = None) -> str:
    options = options or WriteOptions()
    dx = DocxDocument()

    _setup_styles(dx, doc)
    _setup_page(dx, doc)
    _setup_header_footer(dx, doc)

    total_pages = len(doc.pages)
    for page_index, page in enumerate(doc.pages):
        if page_index > 0 and options.page_breaks:
            dx.add_page_break()
        for block in page.blocks:
            _write_block(dx, block, doc, options)

    props = dx.core_properties
    props.title = os.path.splitext(os.path.basename(doc.source))[0] if doc.source else "pdf2word"
    props.comments = "Converted by pdf2word"

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    dx.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


def _write_toc(dx: DocxDocument, block: Block, doc: Document, options: WriteOptions) -> None:
    """Dòng mục lục: tab phải + leader chấm + số trang."""
    usable = doc.pages[0].width - doc.margin_left - doc.margin_right if doc.pages else 480
    para = dx.add_paragraph()
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(2)
    para.paragraph_format.left_indent = Pt(max(0.0, block.indent))
    para.paragraph_format.tab_stops.add_tab_stop(
        Pt(usable - max(0.0, block.indent)), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS
    )
    _add_runs(para, block.spans, doc, options)
    tail = para.add_run("\t" + block.marker)
    size = block.spans[0].size if block.spans else doc.body_size
    tail.font.size = Pt(size)
    _set_font(tail, doc.body_font)


def _set_font(style_or_run, font_name: str) -> None:
    style_or_run.font.name = font_name
    try:
        rpr = style_or_run.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)
    except Exception:
        pass


def _set_lang(style_or_run, lang: str = "vi-VN") -> None:
    """Gắn ngôn ngữ soạn thảo để Word bật kiểm tra chính tả tiếng Việt."""
    try:
        rpr = style_or_run.element.get_or_add_rPr()
        el = rpr.find(qn("w:lang"))
        if el is None:
            el = OxmlElement("w:lang")
            rpr.append(el)
        el.set(qn("w:val"), lang)
        el.set(qn("w:eastAsia"), lang)
    except Exception:
        pass


def _heading_sizes(doc: Document) -> dict[int, float]:
    sizes: dict[int, float] = {}
    for page in doc.pages:
        for block in page.blocks:
            if block.kind == "heading" and block.level:
                sizes[block.level] = max(sizes.get(block.level, 0.0), block.dominant_size())
    body = doc.body_size
    for level in range(1, 5):
        sizes.setdefault(level, round(body * (1.5 if level == 1 else 1.28 if level == 2 else 1.12), 1))
    return sizes


def _setup_styles(dx: DocxDocument, doc: Document) -> None:
    styles = dx.styles
    normal = styles["Normal"]
    normal.font.size = Pt(doc.body_size)
    _set_font(normal, doc.body_font)
    _set_lang(normal)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.15

    sizes = _heading_sizes(doc)
    for level in range(1, 5):
        name = f"Heading {level}"
        try:
            st = styles[name]
        except KeyError:
            continue
        st.font.size = Pt(sizes[level])
        st.font.bold = True
        st.font.italic = False
        st.font.color.rgb = RGBColor(0, 0, 0)
        _set_font(st, doc.body_font)
        _set_lang(st)
        st.paragraph_format.space_before = Pt(10 if level <= 2 else 6)
        st.paragraph_format.space_after = Pt(4)

    try:
        title = styles["Title"]
        title.font.size = Pt(sizes[1] * 1.15)
        title.font.color.rgb = RGBColor(0, 0, 0)
        _set_font(title, doc.body_font)
        _set_lang(title)
    except KeyError:
        pass


def _setup_page(dx: DocxDocument, doc: Document) -> None:
    section = dx.sections[0]
    if doc.pages:
        first = doc.pages[0]
        section.page_width = Pt(first.width)
        section.page_height = Pt(first.height)
    section.left_margin = Pt(doc.margin_left)
    section.right_margin = Pt(doc.margin_right)
    section.top_margin = Pt(doc.margin_top)
    section.bottom_margin = Pt(doc.margin_bottom)


_FOOTER_NUM_RE = re.compile(r"^(?P<prefix>.*?)(?P<num>\d{1,4})\s*$")


def _setup_header_footer(dx: DocxDocument, doc: Document) -> None:
    header_text = doc.meta.get("header_text")
    footer_text = doc.meta.get("footer_text")
    if not header_text and not footer_text:
        return

    for section in dx.sections:
        if header_text:
            section.header.is_linked_to_previous = False
            p = section.header.paragraphs[0]
            p.alignment = _ALIGN.get(doc.meta.get("header_align", "left"), WD_ALIGN_PARAGRAPH.LEFT)
            run = p.add_run(header_text)
            run.font.size = Pt(float(doc.meta.get("header_size") or doc.body_size))
            _set_font(run, doc.body_font)

        if footer_text:
            section.footer.is_linked_to_previous = False
            p = section.footer.paragraphs[0]
            p.alignment = _ALIGN.get(doc.meta.get("footer_align", "left"), WD_ALIGN_PARAGRAPH.LEFT)
            size = float(doc.meta.get("footer_size") or doc.body_size)

            m = _FOOTER_NUM_RE.match(footer_text.strip())
            if m and doc.meta.get("footer_has_page_number"):
                prefix = m.group("prefix").strip()
                if prefix:
                    run = p.add_run(prefix + " ")
                    run.font.size = Pt(size)
                    _set_font(run, doc.body_font)
                _add_page_field(p, int(doc.meta.get("footer_page_offset") or 0), size, doc.body_font)
            else:
                run = p.add_run(footer_text)
                run.font.size = Pt(size)
                _set_font(run, doc.body_font)


def _add_page_field(paragraph, offset: int, size: float, font: str) -> None:
    """Chèn field PAGE (kèm bù trừ nếu PDF đánh số lệch so với trang thật)."""
    run = paragraph.add_run()
    run.font.size = Pt(size)
    _set_font(run, font)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" = PAGE - {offset} " if offset else " PAGE "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


def _write_block(dx: DocxDocument, block: Block, doc: Document, options: WriteOptions) -> None:
    if block.kind in ("header", "footer"):
        return  # đã đưa vào header/footer của section
    if block.kind == "image":
        _write_image(dx, block, doc, options)
        return
    if block.kind == "table":
        if block.table:
            _write_table(dx, block.table, doc, options)
        return
    if block.kind == "toc":
        _write_toc(dx, block, doc, options)
        return

    if block.kind in ("heading", "title"):
        style = "Title" if block.kind == "title" else f"Heading {min(max(block.level, 1), 4)}"
    else:
        style = "Normal"

    try:
        para = dx.add_paragraph(style=style)
    except KeyError:
        para = dx.add_paragraph()

    pf = para.paragraph_format
    para.alignment = _ALIGN.get(block.align, WD_ALIGN_PARAGRAPH.LEFT)
    if block.indent:
        pf.left_indent = Pt(block.indent)
    if block.first_line_indent:
        pf.first_line_indent = Pt(block.first_line_indent)
    if block.kind == "list":
        pf.left_indent = Pt(block.indent + 18)
        pf.first_line_indent = Pt(-18)
        prefix = ""
        body = block.text.lstrip()
        if block.marker == "\u2022" and not body.startswith(("\u2022", "-", "*", "+")):
            prefix = "\u2022  "
        if prefix and block.spans:
            run = para.add_run(prefix)
            first = block.spans[0]
            run.font.size = Pt(max(1.0, first.size))
            _set_font(run, first.font or doc.body_font)
    if block.kind not in ("heading", "title"):
        gap = min(block.gap_before, 18.0)
        if gap > 2:
            pf.space_before = Pt(round(gap * 0.6, 1))

    _add_runs(para, block.spans, doc, options)


def _add_runs(para, spans: list[Span], doc: Document, options: WriteOptions, size_ratio: float = 1.0) -> None:
    if not spans:
        return
    for span in spans:
        text = span.text
        if not text:
            continue
        run = para.add_run(text)
        font = span.font or doc.body_font
        run.font.size = Pt(max(1.0, span.size * size_ratio))
        _set_font(run, font)
        run.bold = span.bold
        run.italic = span.italic
        if span.underline:
            run.underline = True
        if not color_is_black(span.color):
            run.font.color.rgb = RGBColor(*span.color)


def _write_image(dx: DocxDocument, block: Block, doc: Document, options: WriteOptions) -> None:
    if not options.embed_images or not block.image:
        return
    img = block.image
    if not os.path.exists(img.path):
        return

    usable = doc.pages[0].width - doc.margin_left - doc.margin_right if doc.pages else 480
    width_pt = min(img.width, usable)
    para = dx.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    run = para.add_run()
    try:
        run.add_picture(img.path, width=Pt(width_pt))
    except Exception:
        pass


def _write_table(dx: DocxDocument, table: Table, doc: Document, options: WriteOptions) -> None:
    if table.n_rows < 1 or table.n_cols < 1:
        return

    usable = doc.pages[0].width - doc.margin_left - doc.margin_right if doc.pages else 480
    widths = list(table.col_widths)
    total = sum(widths) or usable
    scale = min(1.0, usable / total)
    widths = [w * scale for w in widths]

    t = dx.add_table(rows=table.n_rows, cols=table.n_cols)
    try:
        t.style = "Table Grid"
    except KeyError:
        pass
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    # fixed layout để Word tôn trọng độ rộng cột
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)

    for j, w in enumerate(widths):
        t.columns[j].width = Pt(w)

    # merge trước
    for r in range(table.n_rows):
        for c in range(table.n_cols):
            cell = table.cells[r][c]
            if cell.covered:
                continue
            if cell.rowspan > 1 or cell.colspan > 1:
                r_end = min(r + cell.rowspan - 1, table.n_rows - 1)
                c_end = min(c + cell.colspan - 1, table.n_cols - 1)
                try:
                    t.cell(r, c).merge(t.cell(r_end, c_end))
                except Exception:
                    pass

    # điền nội dung
    for r in range(table.n_rows):
        for c in range(table.n_cols):
            cell = table.cells[r][c]
            if cell.covered:
                continue
            tc = t.cell(r, c)
            span_w = sum(widths[c : c + cell.colspan]) or widths[c]
            tc.width = Pt(span_w)
            tc.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

            existing = list(tc.paragraphs)
            if not cell.lines:
                p = existing[0] if existing else tc.add_paragraph()
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                if cell.align in _ALIGN:
                    p.alignment = _ALIGN[cell.align]
                continue

            while len(existing) < len(cell.lines):
                existing.append(tc.add_paragraph())
            # xoá paragraph thừa
            for extra in existing[len(cell.lines) :]:
                extra._element.getparent().remove(extra._element)

            for i, line in enumerate(cell.lines):
                p = existing[i]
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                if cell.align in _ALIGN:
                    p.alignment = _ALIGN[cell.align]
                _add_runs(p, line, doc, options, size_ratio=options.table_font_ratio)

    if table.header_rows:
        for r in range(min(table.header_rows, table.n_rows)):
            tr = t.rows[r]._tr
            trPr = tr.get_or_add_trPr()
            header = OxmlElement("w:tblHeader")
            header.set(qn("w:val"), "true")
            trPr.append(header)
