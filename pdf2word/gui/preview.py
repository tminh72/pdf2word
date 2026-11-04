"""Widget xem trước PDF và DOCX."""

from __future__ import annotations

import html
import os
import shutil

import pypdfium2 as pdfium
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .worker import SofficeWorker, find_soffice


class PdfPreview(QWidget):
    """Xem PDF bằng pypdfium2, hien thi tat cac trang doc xuong."""

    ZOOMS = [("Vua chieu rong", 0.0), ("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc = None
        self._path: str | None = None
        self._zoom = 0.0
        self._page_widgets: list = []

        self.zoom_box = QComboBox()
        for label, value in self.ZOOMS:
            self.zoom_box.addItem(label, value)
        self.zoom_box.currentIndexChanged.connect(self._on_zoom)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.addStretch(1)
        bar.addWidget(QLabel("Phong to:"))
        bar.addWidget(self.zoom_box)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 8, 8, 8)
        self.container_layout.setSpacing(12)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)
        self.scroll.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.scroll, 1)

        self._set_controls(False)

    def load(self, path: str) -> None:
        self.clear()
        if not path or not os.path.exists(path):
            self._show_message("Khong tim thay file PDF")
            return
        try:
            self._doc = pdfium.PdfDocument(path)
        except Exception as exc:
            self._show_message("Khong mo duoc PDF:\n" + str(exc))
            return
        self._path = path
        self._set_controls(True)
        self._render_all()

    def clear(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
        self._path = None
        self._zoom = 0.0
        self._clear_pages()
        self._show_message("Chua chon file PDF")
        self._set_controls(False)

    def _show_message(self, text: str) -> None:
        for w in self._page_widgets:
            w.setPixmap(QPixmap())
            w.setText(text)
        if not self._page_widgets:
            lbl = QLabel(text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#888; padding:24px;")
            self.container_layout.addWidget(lbl)
            self._page_widgets.append(lbl)

    def _clear_pages(self) -> None:
        for w in self._page_widgets:
            self.container_layout.removeWidget(w)
            w.deleteLater()
        self._page_widgets.clear()

    def _set_controls(self, enabled: bool) -> None:
        self.zoom_box.setEnabled(enabled)

    def _on_zoom(self) -> None:
        self._zoom = float(self.zoom_box.currentData() or 0.0)
        self._render_all()

    def _render_all(self) -> None:
        if self._doc is None:
            return
        try:
            self._clear_pages()
            total = len(self._doc)
            viewport_w = max(200, self.scroll.viewport().width() - 12)

            for i in range(total):
                page = self._doc[i]
                width_pt, height_pt = page.get_size()
                if self._zoom > 0:
                    scale = self._zoom
                else:
                    scale = max(0.1, min(4.0, viewport_w / max(width_pt, 1.0)))

                bitmap = page.render(scale=scale)
                pil: Image.Image = bitmap.to_pil().convert("RGB")
                data = pil.tobytes("raw", "RGB")
                image = QImage(
                    data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888
                ).copy()
                pix = QPixmap.fromImage(image)

                lbl = QLabel()
                lbl.setPixmap(pix)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("border:1px solid #ddd; margin:4px 0;")
                self.container_layout.addWidget(lbl)
                self._page_widgets.append(lbl)

                pil.close()
                bitmap.close()
                page.close()

            self.container_layout.addStretch(1)
        except Exception as exc:
            self._show_message("Khong render duoc PDF:\n" + str(exc))


def _iter_block_items(doc):
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def docx_to_html(path: str) -> str:
    """Chuyen DOCX thanh HTML don gian de xem nhanh khi khong co LibreOffice.
    Them duong gach ranh giua cac trang."""
    from docx import Document as DocxDocument
    from docx.table import Table

    doc = DocxDocument(path)
    parts = [
        "<style>"
        "body{font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;line-height:1.4;}"
        "h1{font-size:17pt}h2{font-size:14pt}h3{font-size:12pt}"
        "table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #bbb;padding:3px 5px}"
        ".page-break{border-top:1px solid #999;margin-top:14px;padding-top:8px;page-break-before:always;}"
        ".page-first{margin-top:0;border-top:none;}"
        "</style>"
    ]
    first = True
    for block in _iter_block_items(doc):
        if isinstance(block, Table):
            rows = []
            for row in block.rows:
                cells = "".join(
                    f"<td>{html.escape(cell.text).replace(chr(10), '<br>')}</td>" for cell in row.cells
                )
                rows.append(f"<tr>{cells}</tr>")
            parts.append("<table>" + "".join(rows) + "</table>")
            continue
        text = block.text.strip()
        if not text:
            continue
        style = (block.style.name or "").lower() if block.style is not None else ""
        escaped = html.escape(text)
        if style.startswith("title"):
            parts.append(f"<h1>{escaped}</h1>")
        elif "heading" in style:
            level = 2
            digits = "".join(ch for ch in style if ch.isdigit())
            if digits:
                level = min(4, int(digits) + 1)
            parts.append(f"<h{level}>{escaped}</h{level}>")
        else:
            cls = "page-first" if first else "page-break"
            first = False
            parts.append(f'<p class="{cls}">{escaped}</p>')
    return "\n".join(parts)

class DocxPreview(QWidget):
    """Xem DOCX: ban van ban, hoac ban trang in qua LibreOffice neu co."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._worker = None
        self._gen = 0
        self._rendered_dirs: list = []
        self.soffice = find_soffice()

        self.text_view = QTextBrowser()
        self.text_view.setOpenExternalLinks(False)
        self.page_view = PdfPreview()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_view)
        self.stack.addWidget(self.page_view)

        self.text_btn = QPushButton("Van ban")
        self.page_btn = QPushButton("Trang in")
        self.text_btn.setCheckable(True)
        self.page_btn.setCheckable(True)
        self.text_btn.setChecked(True)
        self.text_btn.clicked.connect(lambda: self._show(0))
        self.page_btn.clicked.connect(lambda: self._show(1))
        self.page_btn.setEnabled(bool(self.soffice))

        self.status = QLabel("Chua co ket qua DOCX")
        self.status.setStyleSheet("color:#777; padding:2px 6px;")

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.addWidget(self.text_btn)
        bar.addWidget(self.page_btn)
        bar.addStretch(1)
        bar.addWidget(self.status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.stack, 1)

        if not self.soffice:
            self.status.setText("Chua cai LibreOffice — chi xem duoc dang van ban")

    def load(self, path: str) -> None:
        if not path or not os.path.exists(path):
            self.clear("Chua co ket qua DOCX")
            return
        if path == self._path and self.page_view._path:
            return
        self._path = path
        self._gen += 1
        gen = self._gen

        self.text_view.setHtml(docx_to_html(path))
        self._show(0)
        self.status.setText(os.path.basename(path))

        if self.soffice:
            self._show(0)
            self.status.setText("Dang render ban trang in…")
            worker = SofficeWorker(path, self)
            worker.finished_ok.connect(lambda pdf, gen=gen: self._on_rendered(pdf, gen))
            worker.failed.connect(lambda msg, gen=gen: self._on_render_failed(msg, gen))
            worker.finished.connect(worker.deleteLater)
            self._rendered_dirs.append(worker.out_dir)
            self._worker = worker
            worker.start()

    def clear(self, message: str = "Chua chon file DOCX") -> None:
        self._gen += 1
        self._path = None
        self.page_view.clear()
        self.text_view.clear()
        self.stack.setCurrentIndex(0)
        self.status.setText(message)
        self._cleanup_rendered()

    def _cleanup_rendered(self) -> None:
        for folder in self._rendered_dirs:
            shutil.rmtree(folder, ignore_errors=True)
        self._rendered_dirs.clear()

    def closeEvent(self, event) -> None:
        self.page_view.clear()
        self._cleanup_rendered()
        super().closeEvent(event)

    def _show(self, index: int) -> None:
        if index == 1 and not self.soffice:
            return
        self.stack.setCurrentIndex(index)
        self.text_btn.setChecked(index == 0)
        self.page_btn.setChecked(index == 1)

    def _on_rendered(self, pdf_path: str, gen: int) -> None:
        if gen != self._gen:
            return
        self.page_view.load(pdf_path)
        self._show(1)
        self.status.setText("Ban trang in (LibreOffice)")

    def _on_render_failed(self, message: str, gen: int) -> None:
        if gen != self._gen:
            return
        self.status.setText(message)
        self._show(0)
