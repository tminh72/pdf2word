"""Test OCR: tách mục lục, dò bảng, và chạy end-to-end trên PDF scan.

Các test cần Tesseract sẽ tự bỏ qua nếu máy không có tesseract/tessdata.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2word.extract.ocr import (  # noqa: E402
    Word,
    _split_line_words,
    _strip_toc_number,
    default_tessdata,
    find_tesseract,
)
from pdf2word.pipeline import PipelineOptions, convert_pdf  # noqa: E402

SAMPLE_DIR = os.path.join(ROOT, "sample")
SCANNED_PDF = os.path.join(SAMPLE_DIR, "scanned.pdf")
REAL_PDF = os.path.join(SAMPLE_DIR, "real", "sample.pdf")


def _ensure_scanned() -> None:
    if os.path.exists(SCANNED_PDF):
        return
    from tools.make_sample import build, build_scanned

    build()
    build_scanned()


def _tesseract_ready() -> bool:
    try:
        find_tesseract()
    except RuntimeError:
        return False
    tessdata = default_tessdata()
    return bool(tessdata and os.path.exists(os.path.join(tessdata, "vie.traineddata")))


def _w(text: str, x0: float, x1: float) -> Word:
    return Word(text=text, x0=x0, y0=0, x1=x1, y1=10, conf=90, block=1, par=1, line=1)


class TocLineTest(unittest.TestCase):
    """Không cần Tesseract: kiểm tra tách dòng mục lục từ toạ độ word."""

    def test_split_merged_entries(self) -> None:
        words = [
            _w("Vật", 10, 30),
            _w("tư", 36, 50),
            _w("5", 400, 408),
            _w("2.1.", 540, 565),
            _w("Thép", 570, 600),
            _w("5", 710, 718),
        ]
        segs = _split_line_words(words, dpi=300)
        self.assertEqual(len(segs), 2)
        self.assertEqual([w.text for w in segs[0]], ["Vật", "tư", "5"])
        self.assertEqual([w.text for w in segs[1]], ["2.1.", "Thép", "5"])

    def test_strip_page_number(self) -> None:
        words = [_w("Chương", 10, 60), _w("I", 66, 72), _w("3", 400, 408)]
        rest, page = _strip_toc_number(words, dpi=300)
        self.assertEqual(page, "3")
        self.assertEqual([w.text for w in rest], ["Chương", "I"])

    def test_normal_spacing_is_not_toc(self) -> None:
        words = [_w("Năm", 10, 40), _w("2026", 46, 80)]
        rest, page = _strip_toc_number(words, dpi=300)
        self.assertIsNone(page)
        self.assertEqual(len(rest), 2)


@unittest.skipUnless(_tesseract_ready(), "cần Tesseract + models/tessdata/vie")
class OcrPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_scanned()
        cls.tmp = tempfile.mkdtemp(prefix="pdf2word_ocr_test_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _convert(self, pdf: str, name: str, pages=None):
        return convert_pdf(
            pdf,
            PipelineOptions(engine="ocr", dpi_scan=300, pages=pages),
            os.path.join(self.tmp, name),
        )

    def test_scanned_page_has_text(self) -> None:
        doc = self._convert(SCANNED_PDF, "scan")
        self.assertEqual(len(doc.pages), 1)
        text = " ".join(b.text for b in doc.pages[0].blocks if b.kind != "image")
        self.assertGreater(len(text.strip()), 50, "OCR không trả về chữ")

    @unittest.skipUnless(os.path.exists(REAL_PDF), "thiếu sample/real/sample.pdf")
    def test_real_table_is_reconstructed(self) -> None:
        doc = self._convert(REAL_PDF, "real_p6", pages=(6, 6))
        tables = [b for p in doc.pages for b in p.blocks if b.kind == "table"]
        self.assertEqual(len(tables), 1, "phải nhận đúng 1 bảng ở trang 6")
        table = tables[0].table
        assert table is not None
        self.assertEqual((table.n_rows, table.n_cols), (6, 3))
        self.assertEqual(table.header_rows, 1)
        header = " ".join(
            span.text for cell in table.cells[0] for line in cell.lines for span in line
        )
        self.assertIn("QUY", header.upper())


if __name__ == "__main__":
    unittest.main()
