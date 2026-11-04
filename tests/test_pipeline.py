"""Smoke test end-to-end trên PDF mẫu.

Chạy: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2word.docx.writer import WriteOptions, write_docx  # noqa: E402
from pdf2word.eval import compare, docx_text  # noqa: E402
from pdf2word.extract.digital import ExtractOptions, extract  # noqa: E402
from pdf2word.extract.router import profile_pdf  # noqa: E402

SAMPLE_DIR = os.path.join(ROOT, "sample")
REPORT_PDF = os.path.join(SAMPLE_DIR, "report.pdf")
EXCEL_PDF = os.path.join(SAMPLE_DIR, "excel_style.pdf")
SCANNED_PDF = os.path.join(SAMPLE_DIR, "scanned.pdf")


def _ensure_samples() -> None:
    if all(os.path.exists(p) for p in (REPORT_PDF, EXCEL_PDF, SCANNED_PDF)):
        return
    from tools.make_sample import build, build_excel_style, build_scanned

    build()
    build_excel_style()
    build_scanned()


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_samples()
        cls.tmp = tempfile.mkdtemp(prefix="pdf2word_test_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _convert(self, pdf: str, name: str):
        workdir = os.path.join(self.tmp, name + "_assets")
        doc = extract(pdf, ExtractOptions(dpi=150), workdir)
        out = os.path.join(self.tmp, name + ".docx")
        write_docx(doc, out, WriteOptions())
        return doc, out

    def test_profile(self) -> None:
        profile = profile_pdf(REPORT_PDF)
        self.assertEqual(profile.pages, 3)
        self.assertEqual(profile.kind, "digital")

    def test_report_structure(self) -> None:
        doc, _ = self._convert(REPORT_PDF, "report")

        self.assertEqual(len(doc.pages), 3)
        tables = [b for p in doc.pages for b in p.blocks if b.kind == "table"]
        self.assertEqual(len(tables), 3, "phải nhận đủ 3 bảng")

        merged = [t for t in tables if t.table and any(c.rowspan > 1 for row in t.table.cells for c in row)]
        self.assertTrue(merged, "bảng có ô gộp phải được merge rowspan")

        images = [b for p in doc.pages for b in p.blocks if b.kind == "image"]
        self.assertGreaterEqual(len(images), 1)

        kinds = {b.kind for p in doc.pages for b in p.blocks}
        self.assertIn("title", kinds)
        self.assertIn("heading", kinds)
        self.assertIn("list", kinds)

        headings = [b for p in doc.pages for b in p.blocks if b.kind == "heading"]
        self.assertTrue(any(h.level == 1 for h in headings))
        self.assertTrue(any(h.level == 2 for h in headings))

    def test_report_text_accuracy(self) -> None:
        _, out = self._convert(REPORT_PDF, "report_text")
        with open(os.path.join(SAMPLE_DIR, "reference.txt"), encoding="utf-8") as f:
            ref = f.read()
        stats = compare(ref, docx_text(out))
        self.assertLess(stats["cer"], 0.02, f"CER quá cao: {stats}")
        self.assertLess(stats["wer"], 0.02, f"WER quá cao: {stats}")

    def test_excel_style_table(self) -> None:
        doc, out = self._convert(EXCEL_PDF, "excel")
        tables = [b for p in doc.pages for b in p.blocks if b.kind == "table"]
        self.assertEqual(len(tables), 1)
        table = tables[0].table
        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual((table.n_rows, table.n_cols), (4, 3))
        with open(os.path.join(SAMPLE_DIR, "excel_reference.txt"), encoding="utf-8") as f:
            ref = f.read()
        stats = compare(ref, docx_text(out))
        self.assertLess(stats["cer"], 0.02, f"CER quá cao: {stats}")

    def test_scanned_is_detected(self) -> None:
        profile = profile_pdf(SCANNED_PDF)
        self.assertEqual(profile.kind, "scanned")
        # Phase 1 không crash, chỉ trả về trang rỗng (OCR ở Phase 3)
        doc = extract(SCANNED_PDF, ExtractOptions(), os.path.join(self.tmp, "scan_assets"))
        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(doc.pages[0].blocks, [])

    def test_page_range(self) -> None:
        doc = extract(REPORT_PDF, ExtractOptions(pages=(2, 2)), os.path.join(self.tmp, "page2"))
        self.assertEqual(len(doc.pages), 1)
        self.assertEqual(doc.pages[0].width, doc.pages[0].width)


if __name__ == "__main__":
    unittest.main()
