"""Smoke test cho app desktop PySide6.

Chạy offscreen để không bật cửa sổ thật. Test tự bỏ qua nếu chưa cài PySide6
hoặc chưa có PDF mẫu.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SAMPLE_PDF = os.path.join(ROOT, "sample", "report.pdf")


def _gui_ready() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return os.path.exists(SAMPLE_PDF)


@unittest.skipUnless(_gui_ready(), "cần PySide6 và sample/report.pdf")
class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from pdf2word.gui.app import MainWindow

        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()

    def test_add_and_preview_pdf(self) -> None:
        self.window.add_paths([SAMPLE_PDF])
        self.app.processEvents()

        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.rows[0]["status"], "Chờ")
        self.assertEqual(os.path.basename(self.window.rows[0]["input"]), "report.pdf")

        self.window.table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(self.window.pdf_preview._path, os.path.abspath(SAMPLE_PDF))
        self.assertIsNotNone(self.window.pdf_preview._doc)
        self.assertIn("/", self.window.pdf_preview.page_label.text())

    def test_add_deduplicates_and_expands_folder(self) -> None:
        folder = os.path.dirname(SAMPLE_PDF)
        self.window.add_paths([SAMPLE_PDF, folder])
        self.app.processEvents()

        inputs = [row["input"] for row in self.window.rows]
        self.assertEqual(len(inputs), len(set(os.path.normcase(p) for p in inputs)))
        self.assertGreaterEqual(len(inputs), 2)

    def test_parse_pages(self) -> None:
        from pdf2word.gui.app import parse_pages

        self.assertIsNone(parse_pages(""))
        self.assertEqual(parse_pages("3"), (3, 3))
        self.assertEqual(parse_pages("1-5"), (1, 5))
        with self.assertRaises(ValueError):
            parse_pages("5-1")
        with self.assertRaises(ValueError):
            parse_pages("0")

    def test_docx_preview_after_conversion(self) -> None:
        from pdf2word.service import ConvertRequest, convert_file

        out_dir = tempfile.mkdtemp(prefix="pdf2word_gui_test_")
        out_path = os.path.join(out_dir, "report.docx")
        result = convert_file(ConvertRequest(input=SAMPLE_PDF, output=out_path, pages=(1, 1)))
        self.assertTrue(os.path.exists(result.output))

        self.window.docx_preview.load(result.output)
        self.app.processEvents()
        self.assertEqual(self.window.docx_preview._path, out_path)
        self.assertIn("BẢNG", self.window.docx_preview.text_view.toPlainText().upper())


if __name__ == "__main__":
    unittest.main()
