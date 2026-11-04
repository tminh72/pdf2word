"""App desktop pdf2word (PySide6): kéo thả, hàng đợi, xem trước PDF ↔ DOCX."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..service import ConvertRequest, ConvertResult, zip_outputs
from .preview import DocxPreview, PdfPreview
from .worker import ConvertWorker, find_soffice

APP_NAME = "pdf2word"
ORG_NAME = "pdf2word"
COL_NAME, COL_PAGES, COL_STATUS, COL_RESULT, COL_DELETE = range(5)


def parse_pages(value: str) -> tuple[int, int] | None:
    value = value.strip()
    if not value:
        return None
    if "-" in value:
        a, b = value.split("-", 1)
        start, end = int(a), int(b)
        if start < 1 or end < start:
            raise ValueError("Khoảng trang không hợp lệ (ví dụ đúng: 1-5).")
        return start, end
    n = int(value)
    if n < 1:
        raise ValueError("Số trang phải ≥ 1.")
    return n, n


def collect_pdfs(paths: list[str]) -> list[str]:
    """Mở rộng thư mục thành danh sách PDF, bỏ trùng, giữ thứ tự."""
    out: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            out.append(os.path.abspath(path))

    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for name in sorted(files):
                    if name.lower().endswith(".pdf"):
                        add(os.path.join(root, name))
        elif path.lower().endswith(".pdf") and os.path.exists(path):
            add(path)
    return out


class PdfTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels(["File PDF", "Trang", "Trạng thái", "Kết quả", "Xóa"])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setAcceptDrops(True)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        self.setColumnWidth(COL_NAME, 240)
        self.setColumnWidth(COL_PAGES, 52)
        self.setColumnWidth(COL_STATUS, 120)
        self.setColumnWidth(COL_DELETE, 50)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self.worker: ConvertWorker | None = None
        self.settings = QSettings(ORG_NAME, APP_NAME)

        self.setWindowTitle("pdf2word — Chuyển PDF sang Word (offline)")
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        self._build_actions()
        self._build_ui()
        self._restore_settings()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _icon(self, pixmap: QStyle.StandardPixmap):
        return self.style().standardIcon(pixmap)

    def _build_actions(self) -> None:
        self.act_add = QAction("Thêm file PDF…", self)
        self.act_add.setShortcut(QKeySequence.Open)
        self.act_add.setIcon(self._icon(QStyle.SP_DialogOpenButton))
        self.act_add.triggered.connect(self.add_files)

        self.act_add_dir = QAction("Thêm thư mục…", self)
        self.act_add_dir.setIcon(self._icon(QStyle.SP_DirOpenIcon))
        self.act_add_dir.triggered.connect(self.add_folder)

        self.act_remove = QAction("Xoá khỏi danh sách", self)
        self.act_remove.setShortcut(QKeySequence.Delete)
        self.act_remove.setIcon(self._icon(QStyle.SP_TrashIcon))
        self.act_remove.triggered.connect(self.remove_selected)

        self.act_clear = QAction("Xoá tất cả", self)
        self.act_clear.setIcon(self._icon(QStyle.SP_DialogResetButton))
        self.act_clear.triggered.connect(self.clear_rows)

        self.act_convert = QAction("Chuyển đổi", self)
        self.act_convert.setShortcut("Ctrl+Return")
        self.act_convert.setIcon(self._icon(QStyle.SP_MediaPlay))
        self.act_convert.triggered.connect(self.start_conversion)

        self.act_stop = QAction("Dừng", self)
        self.act_stop.setShortcut("Esc")
        self.act_stop.setIcon(self._icon(QStyle.SP_MediaStop))
        self.act_stop.triggered.connect(self.stop_conversion)
        self.act_stop.setEnabled(False)

        act_zip = QAction("Đóng gói ZIP…", self)
        act_zip.setIcon(self._icon(QStyle.SP_DialogSaveButton))
        act_zip.triggered.connect(self.make_zip)

        act_open = QAction("Mở thư mục kết quả", self)
        act_open.setIcon(self._icon(QStyle.SP_DirIcon))
        act_open.triggered.connect(self.open_output_folder)

        act_about = QAction("Giới thiệu", self)
        act_about.setIcon(self._icon(QStyle.SP_MessageBoxInformation))
        act_about.triggered.connect(self.show_about)

        act_quit = QAction("Thoát", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.setIcon(self._icon(QStyle.SP_DialogCloseButton))
        act_quit.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("&Tệp")
        file_menu.addAction(self.act_add)
        file_menu.addAction(self.act_add_dir)
        file_menu.addAction(self.act_remove)
        file_menu.addAction(self.act_clear)
        file_menu.addSeparator()
        file_menu.addAction(self.act_convert)
        file_menu.addAction(self.act_stop)
        file_menu.addSeparator()
        file_menu.addAction(act_zip)
        file_menu.addAction(act_open)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        help_menu = self.menuBar().addMenu("&Trợ giúp")
        help_menu.addAction(act_about)

        toolbar = self.addToolBar("Chính")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.addAction(self.act_add)
        toolbar.addAction(self.act_add_dir)
        toolbar.addAction(self.act_remove)
        toolbar.addSeparator()
        toolbar.addAction(self.act_convert)
        toolbar.addAction(self.act_stop)
        toolbar.addSeparator()
        toolbar.addAction(act_zip)
        self.toolbar = toolbar
        self.act_zip = act_zip
        self.act_open = act_open

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QPushButton#primaryButton {
                background:#1565c0; color:#fff; font-weight:600;
                border:none; border-radius:5px; padding:9px 18px;
            }
            QPushButton#primaryButton:hover { background:#0d47a1; }
            QPushButton#primaryButton:pressed { background:#0a3d91; }
            QPushButton#primaryButton:disabled { background:#b0bec5; color:#eceff1; }
            QPushButton#dangerButton {
                background:#fff; color:#c62828; font-weight:600;
                border:1px solid #c62828; border-radius:5px; padding:8px 16px;
            }
            QPushButton#dangerButton:hover { background:#ffebee; }
            QPushButton#dangerButton:disabled { color:#bdbdbd; border-color:#e0e0e0; }
            QPushButton#secondaryButton {
                background:#f5f5f5; color:#222; border:1px solid #c9c9c9;
                border-radius:5px; padding:8px 14px;
            }
            QPushButton#secondaryButton:hover { background:#ececec; }
            QPushButton#secondaryButton:disabled { color:#aaa; }
            QPushButton#deleteButton {
                background:#fff; color:#c62828; font-weight:600;
                border:1px solid #c62828; border-radius:3px; padding:4px 8px;
            }
            QPushButton#deleteButton:hover { background:#ffebee; }
            QLabel#fileLabel { color:#444; padding:2px 2px 0 2px; }
            QProgressBar { border:1px solid #d0d0d0; border-radius:4px; background:#f0f0f0; }
            QProgressBar::chunk { background:#1565c0; border-radius:3px; }
            """
        )

        self.table = PdfTable()
        self.table.files_dropped.connect(self.add_paths)
        self.table.itemSelectionChanged.connect(self._on_selection)

        self.file_label = QLabel("Chưa chọn file")
        self.file_label.setObjectName("fileLabel")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_label.setWordWrap(False)

        self.btn_add = QPushButton("Thêm PDF")
        self.btn_add.setObjectName("secondaryButton")
        self.btn_add.setIcon(self._icon(QStyle.SP_DialogOpenButton))
        self.btn_add.setIconSize(QSize(17, 17))
        self.btn_add.setToolTip("Thêm một hoặc nhiều file PDF (Ctrl+O)")
        self.btn_add.clicked.connect(self.add_files)

        self.btn_add_dir = QPushButton("Thêm thư mục")
        self.btn_add_dir.setObjectName("secondaryButton")
        self.btn_add_dir.setIcon(self._icon(QStyle.SP_DirOpenIcon))
        self.btn_add_dir.setIconSize(QSize(17, 17))
        self.btn_add_dir.setToolTip("Thêm toàn bộ PDF trong một thư mục")
        self.btn_add_dir.clicked.connect(self.add_folder)

        self.btn_convert = QPushButton("Chuyển đổi")
        self.btn_convert.setObjectName("primaryButton")
        self.btn_convert.setIcon(self._icon(QStyle.SP_MediaPlay))
        self.btn_convert.setIconSize(QSize(18, 18))
        self.btn_convert.setToolTip("Bắt đầu chuyển đổi các file đang chờ (Ctrl+Enter)")
        self.btn_convert.setMinimumHeight(38)
        self.btn_convert.clicked.connect(self.start_conversion)

        self.btn_stop = QPushButton("Dừng")
        self.btn_stop.setObjectName("dangerButton")
        self.btn_stop.setIcon(self._icon(QStyle.SP_MediaStop))
        self.btn_stop.setIconSize(QSize(16, 16))
        self.btn_stop.setToolTip("Dừng sau trang đang xử lý (Esc)")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_conversion)

        self.btn_zip = QPushButton("Đóng gói ZIP")
        self.btn_zip.setObjectName("secondaryButton")
        self.btn_zip.setIcon(self._icon(QStyle.SP_DialogSaveButton))
        self.btn_zip.setIconSize(QSize(17, 17))
        self.btn_zip.setToolTip("Gom các file DOCX đã xong vào một ZIP")
        self.btn_zip.clicked.connect(self.make_zip)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 4)
        action_row.addWidget(self.btn_add)
        action_row.addWidget(self.btn_add_dir)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_convert)
        action_row.addWidget(self.btn_stop)
        action_row.addWidget(self.btn_zip)

        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setValue(0)
        self.overall.setTextVisible(False)
        self.overall.setFixedHeight(10)
        self.overall_label = QLabel("Sẵn sàng")
        self.overall_label.setStyleSheet("color:#555;")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.addWidget(self._options_box())
        left_layout.addLayout(action_row)
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(self.file_label)
        bottom = QHBoxLayout()
        bottom.addWidget(self.overall_label, 1)
        bottom.addWidget(self.overall, 0)
        left_layout.addLayout(bottom)

        self.pdf_preview = PdfPreview()
        self.docx_preview = DocxPreview()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.pdf_preview, "PDF gốc")
        self.tabs.addTab(self.docx_preview, "DOCX kết quả")
        self.tabs.addTab(self.log, "Nhật ký")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([620, 660])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage(
            "Kéo & thả PDF vào danh sách, chọn tuỳ chọn rồi bấm Chuyển đổi."
        )

    def _options_box(self) -> QGroupBox:
        box = QGroupBox("Tuỳ chọn chuyển đổi")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        # Chỉ giữ engine deepdoc (mặc định), không hiển thị trên UI
        self.engine = "deepdoc"

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText("VD 1-5, để trống = tất cả")

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(150, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(300)
        self.dpi_spin.setSuffix(" DPI")

        self.lang_box = QComboBox()
        self.lang_box.setEditable(True)
        self.lang_box.addItems(["vie+eng", "vie", "eng"])

        self.font_box = QComboBox()
        self.font_box.addItem("Giữ font gốc (đã chuẩn hoá)", None)
        for name in ("Times New Roman", "Arial", "Calibri", "Cambria", "Tahoma", "Segoe UI"):
            self.font_box.addItem(name, name)
        self.font_box.setToolTip(
            "Ép font cho toàn bộ DOCX. 'Giữ font gốc' chỉ làm sạch tên font, không đổi kiểu."
        )

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Để trống = cùng thư mục với PDF")
        browse = QPushButton("Chọn…")
        browse.clicked.connect(self.choose_output_dir)
        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(browse)
        out_widget = QWidget()
        out_widget.setLayout(out_row)

        # Các tùy chọn bảng/ảnh/ngắt trang/số trang: đặt mặc định, không hiện UI
        self.extract_tables = True
        self.extract_images = True
        self.page_breaks = True
        self.keep_page_numbers = False

        form.addRow("Trang:", self.pages_edit)
        form.addRow("OCR DPI:", self.dpi_spin)
        form.addRow("Ngôn ngữ:", self.lang_box)
        form.addRow("Font chữ:", self.font_box)
        form.addRow("Thư mục ra:", out_widget)
        return box

    # ------------------------------------------------------------------
    # Hàng đợi
    # ------------------------------------------------------------------
    def action_open_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn PDF", "", "PDF (*.pdf)")
        self.add_paths(files)

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn PDF", "", "PDF (*.pdf)")
        self.add_paths(files)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa PDF")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths: list[str]) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        pdfs = collect_pdfs(paths)
        known = {os.path.normcase(row["input"]) for row in self.rows}
        added = 0
        first_added = -1
        for path in pdfs:
            if os.path.normcase(path) in known:
                continue
            known.add(os.path.normcase(path))
            row = len(self.rows)
            self.rows.append(
                {"input": path, "output": "", "status": "Chờ", "result": None, "progress": 0.0}
            )
            self.table.insertRow(row)
            self.table.setItem(row, COL_NAME, QTableWidgetItem(os.path.basename(path)))
            self.table.item(row, COL_NAME).setToolTip(path)
            self.table.setItem(row, COL_PAGES, QTableWidgetItem("—"))
            self.table.setItem(row, COL_STATUS, QTableWidgetItem("Chờ"))
            self.table.setItem(row, COL_RESULT, QTableWidgetItem(""))
            btn_delete = QPushButton("Xóa")
            btn_delete.setObjectName("deleteButton")
            btn_delete.clicked.connect(lambda checked, r=row: self.delete_row(r))
            self.table.setCellWidget(row, COL_DELETE, btn_delete)
            if first_added < 0:
                first_added = row
            added += 1
        if added:
            self._log(f"Đã thêm {added} file.")
            self.statusBar().showMessage(f"Đã thêm {added} file PDF.")
            self.table.selectRow(first_added)
            self.table.scrollToItem(self.table.item(first_added, COL_NAME))
            self.tabs.setCurrentIndex(0)
        elif pdfs:
            self.statusBar().showMessage("Các file này đã có trong danh sách.")

    def delete_row(self, row: int) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.table.removeRow(row)
        self.rows.pop(row)
        self._refresh_previews()

    def remove_selected(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows:
            return
        for row in reversed(rows):
            self.table.removeRow(row)
            self.rows.pop(row)
        self._refresh_previews()

    def clear_rows(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.table.setRowCount(0)
        self.rows.clear()
        self._refresh_previews()
        self._update_overall(0.0, "Sẵn sàng")

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục kết quả", self.out_edit.text().strip() or ""
        )
        if folder:
            self.out_edit.setText(folder)

    # ------------------------------------------------------------------
    # Chuyển đổi
    # ------------------------------------------------------------------
    def start_conversion(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.rows:
            QMessageBox.information(self, "Chưa có file", "Hãy thêm ít nhất một file PDF.")
            return

        engine = self.engine  # luôn "deepdoc"
        try:
            pages = parse_pages(self.pages_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Trang không hợp lệ", str(exc))
            return

        # Kiểm tra model deepdoc
        try:
            from ..extract.deepdoc import find_model_dir

            model_dir = find_model_dir()
            missing = [
                name
                for name in (
                    "cnn.onnx",
                    "encoder.onnx",
                    "decoder.onnx",
                    "tsr.onnx",
                    "layout.onnx",
                    "vocab.txt",
                )
                if not os.path.isfile(os.path.join(model_dir, name))
            ]
            if missing:
                raise RuntimeError("Thiếu model: " + ", ".join(missing))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Chưa sẵn sàng DeepDoc",
                "Không tìm thấy model DeepDoc hoặc thiếu onnxruntime/opencv.

"
                f"Chi tiết: {exc}

"
                "Cài: pip install -e .[deepdoc] và đặt model vào models/deepdoc.",
            )
            return

        out_dir = self.out_edit.text().strip()
        if out_dir and not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as exc:
                QMessageBox.critical(self, "Lỗi thư mục", f"Không tạo được thư mục:
{exc}")
                return

        jobs: list[tuple[int, ConvertRequest]] = []
        used_outputs: set[str] = set()
        font = self.font_box.currentData()
        polish = True  # chuẩn hoá chính tả luôn bật
        for row_index, row in enumerate(self.rows):
            output = self._output_for(row["input"], out_dir, used_outputs)
            used_outputs.add(os.path.normcase(output))
            row["output"] = output
            row["status"] = "Chờ"
            row["progress"] = 0.0
            row["result"] = None
            self._set_cell(row_index, COL_STATUS, "Chờ")
            self._set_cell(row_index, COL_RESULT, "")
            self._set_cell(row_index, COL_PAGES, "—")
            jobs.append(
                (
                    row_index,
                    ConvertRequest(
                        input=row["input"],
                        output=output,
                        engine=engine,
                        pages=pages,
                        ocr_dpi=self.dpi_spin.value(),
                        lang=self.lang_box.currentText().strip() or "vie+eng",
                        images=self.extract_images,
                        tables=self.extract_tables,
                        page_breaks=self.page_breaks,
                        keep_page_numbers=self.keep_page_numbers,
                        polish=polish,
                        font=font,
                    ),
                )
            )

        self._set_running(True)
        self._update_overall(0.0, "Đang chuyển đổi…")
        extra = []
        if polish:
            extra.append("chuẩn hoá chính tả")
        if font:
            extra.append(f"font {font}")
        suffix = f" — {', '.join(extra)}" if extra else ""
        self._log(f"Bắt đầu chuyển đổi {len(jobs)} file (chế độ deepdoc){suffix}.")

        worker = ConvertWorker(jobs, self)
        worker.file_started.connect(self._on_file_started)
        worker.file_progress.connect(self._on_file_progress)
        worker.file_done.connect(self._on_file_done)
        worker.file_failed.connect(self._on_file_failed)
        worker.file_cancelled.connect(self._on_file_cancelled)
        worker.queue_progress.connect(self._on_queue_progress)
        worker.all_done.connect(self._on_all_done)
        self.worker = worker
        worker.start()

    def stop_conversion(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.act_stop.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.overall_label.setText("Đang dừng sau trang hiện tại…")
            self._log("Yêu cầu dừng: sẽ dừng sau trang đang xử lý.")

    def _output_for(self, pdf_path: str, out_dir: str, used: set[str]) -> str:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        folder = out_dir or os.path.dirname(pdf_path)
        candidate = os.path.join(folder, stem + ".docx")
        n = 1
        while os.path.normcase(candidate) in used:
            n += 1
            candidate = os.path.join(folder, f"{stem} ({n}).docx")
        return candidate

    def _set_running(self, running: bool) -> None:
        self.act_convert.setEnabled(not running)
        self.act_stop.setEnabled(running)
        self.btn_convert.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_zip.setEnabled(not running)
        for widget in (
            self.act_add,
            self.act_add_dir,
            self.act_remove,
            self.act_clear,
            self.btn_add,
            self.btn_add_dir,
            self.pages_edit,
            self.dpi_spin,
            self.lang_box,
            self.font_box,
            self.out_edit,
        ):
            widget.setEnabled(not running)

    # ------------------------------------------------------------------
    # Sự kiện worker
    # ------------------------------------------------------------------
    def _on_file_started(self, row: int, name: str) -> None:
        self._set_cell(row, COL_STATUS, "Đang xử lý…")
        self.statusBar().showMessage(f"Đang chuyển đổi: {name}")
        self.table.selectRow(row)

    def _on_file_progress(self, row: int, done: int, total: int) -> None:
        pct = int(100 * done / max(total, 1))
        self._set_cell(row, COL_STATUS, f"Trang {done}/{total} ({pct}%)")
        if 0 <= row < len(self.rows):
            self.rows[row]["progress"] = done / max(total, 1)

    def _on_queue_progress(self, done: int, total: int) -> None:
        overall = sum(row["progress"] for row in self.rows) / max(len(self.rows), 1)
        self._update_overall(overall, f"Đã xong {done}/{total} file")

    def _on_file_done(self, row: int, result: ConvertResult) -> None:
        if not (0 <= row < len(self.rows)):
            return
        self.rows[row]["result"] = result
        self.rows[row]["status"] = "Xong"
        self.rows[row]["progress"] = 1.0
        self._set_cell(row, COL_PAGES, str(result.pages))
        self._set_cell(row, COL_STATUS, "Xong")
        summary = (
            f"{result.pages} trang · {result.tables} bảng · "
            f"{result.headings} heading · {result.seconds:.1f}s"
        )
        self._set_cell(row, COL_RESULT, summary)
        item = self.table.item(row, COL_RESULT)
        if item is not None:
            item.setToolTip(f"{result.output}\n{summary}")
        polish = result.meta.get("polish") or {}
        polish_note = ""
        if polish.get("spell_fixes") or polish.get("font_fixes"):
            polish_note = (
                f" · chuẩn hoá {polish.get('spell_fixes', 0)} lỗi chính tả, "
                f"{polish.get('font_fixes', 0)} font"
            )
        self._log(f"[xong] {os.path.basename(result.input)} → {result.output} ({summary}){polish_note}")
        if self.table.currentRow() == row:
            self.docx_preview.load(result.output)

    def _on_file_failed(self, row: int, message: str) -> None:
        if 0 <= row < len(self.rows):
            self.rows[row]["status"] = "Lỗi"
            self.rows[row]["progress"] = 1.0
            self._set_cell(row, COL_STATUS, "Lỗi")
            self._set_cell(row, COL_RESULT, message[:120])
            self._log(f"[lỗi] {os.path.basename(self.rows[row]['input'])}: {message}")
        self.statusBar().showMessage("Có file bị lỗi — xem cột Kết quả/Nhật ký.")

    def _on_file_cancelled(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            self.rows[row]["status"] = "Đã huỷ"
            self._set_cell(row, COL_STATUS, "Đã huỷ")
        self._log("Đã huỷ file đang xử lý.")

    def _on_all_done(self) -> None:
        self._set_running(False)
        self.worker = None
        done = sum(1 for row in self.rows if row["status"] == "Xong")
        failed = sum(1 for row in self.rows if row["status"] == "Lỗi")
        self._update_overall(
            sum(row["progress"] for row in self.rows) / max(len(self.rows), 1),
            f"Hoàn tất: {done} xong, {failed} lỗi",
        )
        self._log(f"Kết thúc: {done} file thành công, {failed} file lỗi.")
        self.statusBar().showMessage(f"Hoàn tất: {done} xong, {failed} lỗi.")
        self._refresh_previews()

    # ------------------------------------------------------------------
    # Xem trước
    # ------------------------------------------------------------------
    def _on_selection(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.rows)):
            self._refresh_previews()
            return
        path = self.rows[row]["input"]
        self.file_label.setText(path)
        self.file_label.setToolTip(path)
        self.pdf_preview.load(path)
        result = self.rows[row].get("result")
        if result is not None and os.path.exists(result.output):
            self.docx_preview.load(result.output)
        else:
            self.docx_preview.clear("Chưa có kết quả DOCX cho file này")

    def _refresh_previews(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            self._on_selection()
        else:
            self.file_label.setText("Chưa chọn file")
            self.file_label.setToolTip("")
            self.pdf_preview.clear()
            self.docx_preview.clear()

    # ------------------------------------------------------------------
    # Tiện ích
    # ------------------------------------------------------------------
    def _set_cell(self, row: int, column: int, text: str) -> None:
        item = self.table.item(row, column)
        if item is None:
            self.table.setItem(row, column, QTableWidgetItem(text))
        else:
            item.setText(text)

    def _update_overall(self, fraction: float, label: str) -> None:
        self.overall.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.overall_label.setText(label)

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def make_zip(self) -> None:
        outputs = [
            row["result"].output
            for row in self.rows
            if row.get("result") is not None and os.path.exists(row["result"].output)
        ]
        if not outputs:
            QMessageBox.information(self, "Chưa có kết quả", "Hãy chuyển đổi ít nhất một file trước.")
            return
        default = os.path.join(
            self.out_edit.text().strip() or os.path.dirname(outputs[0]), "ket-qua-pdf2word.zip"
        )
        path, _ = QFileDialog.getSaveFileName(self, "Lưu ZIP", default, "ZIP (*.zip)")
        if not path:
            return
        try:
            zip_outputs(outputs, path)
        except Exception as exc:
            QMessageBox.critical(self, "Lỗi ZIP", str(exc))
            return
        self._log(f"Đã đóng gói {len(outputs)} file → {path}")
        self.statusBar().showMessage(f"Đã tạo ZIP: {path}")

    def open_output_folder(self) -> None:
        outputs = [
            row["result"].output
            for row in self.rows
            if row.get("result") is not None and os.path.exists(row["result"].output)
        ]
        if outputs:
            folder = os.path.dirname(outputs[0])
            if sys.platform == "win32":
                import subprocess

                subprocess.Popen(
                    ["explorer", "/select,", outputs[0]],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
        else:
            folder = self.out_edit.text().strip()
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.information(self, "Chưa có thư mục", "Chưa có thư mục kết quả để mở.")

    def show_about(self) -> None:
        soffice = find_soffice()
        QMessageBox.about(
            self,
            "Giới thiệu pdf2word",
            f"<b>pdf2word {__version__}</b><br>"
            "Chuyển PDF sang Word, chạy offline trên CPU.<br><br>"
            "• Engine: DeepDoc + VietOCR ONNX (CPU)<br>"
            f"• Xem trước DOCX: {'LibreOffice' if soffice else 'dạng văn bản (chưa có LibreOffice)'}<br><br>"
            "Không gửi dữ liệu ra ngoài.",
        )

    # ------------------------------------------------------------------
    # Cấu hình / vòng đời
    # ------------------------------------------------------------------
    def _restore_settings(self) -> None:
        s = self.settings
        self.pages_edit.setText(s.value("pages", "") or "")
        self.dpi_spin.setValue(int(s.value("dpi", 300)))
        self.lang_box.setCurrentText(s.value("lang", "vie+eng") or "vie+eng")
        font = s.value("font")
        idx = self.font_box.findData(font if font else None)
        if idx >= 0:
            self.font_box.setCurrentIndex(idx)
        self.out_edit.setText(s.value("out_dir", "") or "")
        geometry = s.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("pages", self.pages_edit.text())
        s.setValue("dpi", self.dpi_spin.value())
        s.setValue("lang", self.lang_box.currentText())
        s.setValue("font", self.font_box.currentData() or "")
        s.setValue("out_dir", self.out_edit.text())
        s.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Đang chuyển đổi",
                "Đang chuyển đổi. Dừng và thoát?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(8000)
        self._save_settings()
        self.pdf_preview.clear()
        self.docx_preview.clear()
        super().closeEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName("pdf2word")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise System.exit(main())
