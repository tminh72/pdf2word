"""Luồng nền cho app desktop: chuyển đổi hàng đợi và render DOCX."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading

from PySide6.QtCore import QThread, Signal

from ..service import ConvertRequest, ConvertResult, convert_file


def find_soffice() -> str | None:
    """Tìm LibreOffice để render DOCX → PDF cho khung xem trước."""
    candidates = [
        os.environ.get("PDF2WORD_SOFFICE"),
        shutil.which("soffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


class ConvertWorker(QThread):
    """Chuyển đổi lần lượt từng file trong hàng đợi."""

    file_started = Signal(int, str)
    file_progress = Signal(int, int, int)  # row, done, total
    file_done = Signal(int, object)  # row, ConvertResult
    file_failed = Signal(int, str)
    file_cancelled = Signal(int)
    queue_progress = Signal(int, int)
    all_done = Signal()

    def __init__(self, jobs: list[tuple[int, ConvertRequest]], parent=None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def is_stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:  # noqa: D401 - Qt entry point
        total = len(self.jobs)
        for n, (row, request) in enumerate(self.jobs):
            if self._stop.is_set():
                break
            self.file_started.emit(row, os.path.basename(request.input))

            def progress(done: int, pages: int, _stage: str, row: int = row) -> None:
                self.file_progress.emit(row, done, pages)

            try:
                result: ConvertResult = convert_file(
                    request, progress=progress, cancel=self._stop.is_set
                )
            except Exception as exc:  # noqa: BLE001 - hiển thị mọi lỗi cho người dùng
                self.file_failed.emit(row, str(exc))
            else:
                if result.cancelled:
                    self.file_cancelled.emit(row)
                else:
                    self.file_done.emit(row, result)
            self.queue_progress.emit(n + 1, total)
        self.all_done.emit()


class SofficeWorker(QThread):
    """Render một DOCX sang PDF bằng LibreOffice headless."""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, docx_path: str, parent=None) -> None:
        super().__init__(parent)
        self.docx_path = docx_path
        self._dir = tempfile.mkdtemp(prefix="pdf2word_preview_")
        self.out_dir = self._dir
        self._proc: subprocess.Popen | None = None

    def run(self) -> None:  # noqa: D401 - Qt entry point
        exe = find_soffice()
        if not exe:
            self.failed.emit("Không tìm thấy LibreOffice.")
            return
        try:
            self._proc = subprocess.Popen(
                [
                    exe,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    self._dir,
                    self.docx_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._proc.communicate(timeout=120)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Render DOCX thất bại: {exc}")
            return
        stem = os.path.splitext(os.path.basename(self.docx_path))[0]
        pdf_path = os.path.join(self._dir, stem + ".pdf")
        if os.path.exists(pdf_path):
            self.finished_ok.emit(pdf_path)
        else:
            self.failed.emit("LibreOffice không tạo được PDF.")

    def cleanup(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)
