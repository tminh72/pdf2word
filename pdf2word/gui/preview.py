"""Widget xem trước PDF và DOCX."""

from __future__ import annotations

import html
import os
import shutil

import pypdfium2 as pdfium
from PIL import Image
from PySide6.QtCore import Qt, QTimer
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
    """Xem PDF bằng pypdfium2, không cần thư viện ngoài."""

    ZOOMS = [("Vừa chiều rộng", 0.0), ("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc = None
        self._path: str | None = None
        self._page = 0
        self._zoom = 0.0

        self.prev_btn = QToolButton()
        self.prev_btn.setText("‹")
        self.prev_btn.setToolTip("Trang trước")
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn = QToolButton()
        self.next_btn.setText("›")
        self.next_btn.setToolTip("Trang sau")
        self.next_btn.clicked.connect(lambda: self._step(1))

        self.page_label = QLabel("—")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(70)

        self.zoom_box = QComboBox()
        for label, value in self.ZOOMS:
            self.zoom_box.addItem(label, value)
        self.zoom_box.currentIndexChanged.connect(self._on_zoom)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.page_label)
        bar.addWidget(self.next_btn)
        bar.addStretch(1)
        bar.addWidget(self.zoom_box)

        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setText("Chưa chọn file PDF")
        self.canvas.setStyleSheet("color:#888; padding:24px;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)

        self._set_controls(False)

    # ------------------------------------------------------------------
    def load(self, path: str) -> None:
        self.clear()
        if not path or not os.path.exists(path):
            self.canvas.setText("Không tìm thấy file PDF")
            return
        try:
            self._doc = pdfium.PdfDocument(path)
        except Exception as exc:  # noqa: BLE001
            self.canvas.setText(f"Không mở được PDF:\n{exc}")
            return
        self._path = path
        self._page = 0
        self._set_controls(True)
        self._render()

    def clear(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
        self._path = None
        self._page = 0
        self.canvas.setPixmap(QPixmap())
        self.canvas.setText("Chưa chọn file PDF")
        self.page_label.setText("—")
        self._set_controls(False)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt
        super().resizeEvent(event)
        if self._zoom == 0.0:
            self._timer.start()

    # ------------------------------------------------------------------
    def _set_controls(self, enabled: bool) -> None:
        self.prev_btn.setEnabled(enabled)
        self.next_btn.setEnabled(enabled)
        self.zoom_box.setEnabled(enabled)

    def _step(self, delta: int) -> None:
        if not self._doc:
            return
        self._page = max(0, min(len(self._doc) - 1, self._page + delta))
        self._render()

    def _on_zoom(self) -> None:
        self._zoom = float(self.zoom_box.currentData() or 0.0)
        self._render()

    def _render(self) -> None:
        if self._doc is None:
            return
        try:
            page = self._doc[self._page]
            width_pt, _height_pt = page.get_size()
            if self._zoom > 0:
                scale = self._zoom
            else:
                viewport = max(200, self.scroll.viewport().width() - 12)
                scale = max(0.1, min(4.0, viewport / max(width_pt, 1.0)))

            bitmap = page.render(scale=scale)
            pil: Image.Image = bitmap.to_pil().convert("RGB")
            data = pil.tobytes("raw", "RGB")
            image = QImage(
                data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888
            ).copy()
            self.canvas.setPixmap(QPixmap.fromImage(image))
            self.canvas.setText("")
            self.page_label.setText(f"{self._page + 1} / {len(self._doc)}")
            pil.close()
            bitmap.close()
            page.close()
        except Exception as exc:  # noqa: BLE001
            self.canvas.setText(f"Không render được trang:\n{exc}")


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
    """Chuyển DOCX thành HTML đơn giản để xem nhanh khi không có LibreOffice."""
    from docx import Document as DocxDocument
    from docx.table import Table

    doc = DocxDocument(path)
    parts: list[str] = [
        "<style>body{font-family:'Segoe UI',Arial,sans-serif;font-size:10pt;}"
        "h1{font-size:17pt}h2{font-size:14pt}h3{font-size:12pt}"
        "table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #bbb;padding:3px 5px}</style>"
    ]
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
            parts.append(f"<p>{escaped}</p>")
    return "\n".join(parts)


class DocxPreview(QWidget):
    """Xem DOCX: bản văn bản, hoặc bản trang in qua LibreOffice nếu có."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._worker: SofficeWorker | None = None
        self._gen = 0
        self._rendered_dirs: list[str] = []
        self.soffice = find_soffice()

        self.text_view = QTextBrowser()
        self.text_view.setOpenExternalLinks(False)
        self.page_view = PdfPreview()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_view)
        self.stack.addWidget(self.page_view)

        self.text_btn = QPushButton("Văn bản")
        self.page_btn = QPushButton("Trang in")
        self.text_btn.setCheckable(True)
        self.page_btn.setCheckable(True)
        self.text_btn.setChecked(True)
        self.text_btn.clicked.connect(lambda: self._show(0))
        self.page_btn.clicked.connect(lambda: self._show(1))
        self.page_btn.setEnabled(bool(self.soffice))

        self.status = QLabel("Chưa có kết quả DOCX")
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
            self.status.setText("Chưa cài LibreOffice — chỉ xem được dạng văn bản")

    # ------------------------------------------------------------------
    def load(self, path: str) -> None:
        if not path or not os.path.exists(path):
            self.clear("Chưa có kết quả DOCX")
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
            self.status.setText("Đang render bản trang in…")
            worker = SofficeWorker(path, self)
            worker.finished_ok.connect(lambda pdf, gen=gen: self._on_rendered(pdf, gen))
            worker.failed.connect(lambda msg, gen=gen: self._on_render_failed(msg, gen))
            worker.finished.connect(worker.deleteLater)
            self._rendered_dirs.append(worker.out_dir)
            self._worker = worker
            worker.start()

    def clear(self, message: str = "Chưa chọn file DOCX") -> None:
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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        self.page_view.clear()
        self._cleanup_rendered()
        super().closeEvent(event)

    # ------------------------------------------------------------------
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
        self.status.setText("Bản trang in (LibreOffice)")

    def _on_render_failed(self, message: str, gen: int) -> None:
        if gen != self._gen:
            return
        self.status.setText(message)
        self._show(0)
