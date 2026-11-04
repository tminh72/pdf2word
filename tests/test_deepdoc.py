"""Test engine DeepDoc: vocab, dựng lưới bảng và smoke test model (nếu có)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2word.extract.deepdoc.table_grid import grid_from_tsr  # noqa: E402
from pdf2word.extract.deepdoc.vocab import VietVocab  # noqa: E402
from pdf2word.extract.deepdoc.engine import TableRegion, _overlap_ratio, _table_regions  # noqa: E402
from pdf2word.extract.grid import RawGrid  # noqa: E402

MODEL_DIR = os.path.join(ROOT, "models", "deepdoc")
SCAN = os.path.join(ROOT, "benchmark", "data", "vi_core", "pdfs", "thong_bao_tuyen_sinh_scan.pdf")


class VocabTest(unittest.TestCase):
    def test_decode_strips_special_tokens(self) -> None:
        vocab = VietVocab("abc")
        self.assertEqual(len(vocab), 7)
        self.assertEqual(vocab.decode([1, 4, 5, 6, 2]), "abc")
        self.assertEqual(vocab.decode([4, 5, 2, 6]), "ab")

    def test_bundled_vocab_size(self) -> None:
        path = os.path.join(MODEL_DIR, "vocab.txt")
        if not os.path.isfile(path):
            self.skipTest("chưa có vocab.txt")
        vocab = VietVocab.from_file(path)
        self.assertEqual(len(vocab), 233)


class TableGridTest(unittest.TestCase):
    def test_rows_columns_header_and_span(self) -> None:
        # Toạ độ crop (gốc trên-trái), dpi=72 nên 1px = 1pt.
        dets = [
            {"label": "table column", "bbox": [0, 0, 100, 150]},
            {"label": "table column", "bbox": [100, 0, 200, 150]},
            {"label": "table row", "bbox": [0, 0, 200, 50]},
            {"label": "table row", "bbox": [0, 50, 200, 100]},
            {"label": "table row", "bbox": [0, 100, 200, 150]},
            {"label": "table column header", "bbox": [0, 0, 200, 50]},
        ]
        built = grid_from_tsr(dets, (0, 0), page_h=800.0, dpi=72)
        self.assertIsNotNone(built)
        grid, header_rows = built  # type: ignore[misc]
        self.assertEqual((grid.n_rows, grid.n_cols), (3, 2))
        self.assertEqual(header_rows, 1)
        self.assertEqual(grid.rows, [800.0, 750.0, 700.0, 650.0])
        self.assertEqual(grid.cols, [0.0, 100.0, 200.0])
        # Ô hàng 0, cột 0 nằm ở góc trên-trái: y trong (750, 800).
        self.assertEqual(grid.anchor_at(50, 775), (0, 0))
        self.assertEqual(grid.anchor_at(150, 675), (2, 1))

    def test_spanning_cell(self) -> None:
        dets = [
            {"label": "table column", "bbox": [0, 0, 100, 100]},
            {"label": "table column", "bbox": [100, 0, 200, 100]},
            {"label": "table row", "bbox": [0, 0, 200, 50]},
            {"label": "table row", "bbox": [0, 50, 200, 100]},
            {"label": "table spanning cell", "bbox": [0, 0, 200, 50]},
        ]
        built = grid_from_tsr(dets, (0, 0), page_h=500.0, dpi=72)
        self.assertIsNotNone(built)
        grid, _ = built  # type: ignore[misc]
        self.assertEqual(grid.spans[(0, 0)], (1, 2))
        self.assertIn((0, 1), grid.covered)
        self.assertEqual(grid.anchor_at(150, 475), (0, 0))


class LayoutRegionsTest(unittest.TestCase):
    def test_layout_table_added_and_overlap_merged(self) -> None:
        ruled = RawGrid(n_rows=2, n_cols=2, rows=[800, 750, 700], cols=[0, 100, 200])

        class FakeLayout:
            def detect(self, _image):
                return [
                    {"label": "table", "score": 0.9, "bbox": [0, 0, 200, 100]},
                    {"label": "table", "score": 0.9, "bbox": [0, 200, 200, 300]},
                    {"label": "table", "score": 0.9, "bbox": [0, 400, 10, 410]},
                    {"label": "plain text", "score": 0.9, "bbox": [0, 500, 200, 600]},
                ]

        regions = _table_regions(
            [ruled], FakeLayout(), np.zeros((900, 300, 3), dtype=np.uint8), 800.0, 1.0
        )
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].source, "rules")
        self.assertEqual(regions[1].source, "layout")
        self.assertEqual(regions[1].bbox, (0.0, 500.0, 200.0, 600.0))
        self.assertAlmostEqual(_overlap_ratio((0, 0, 10, 10), (0, 0, 20, 20)), 1.0)


def _deepdoc_ready() -> bool:
    if not os.path.isfile(os.path.join(MODEL_DIR, "det.onnx")) or not os.path.isfile(SCAN):
        return False
    try:
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class DeepDocSmokeTest(unittest.TestCase):
    @unittest.skipUnless(_deepdoc_ready(), "thiếu model hoặc onnxruntime/opencv")
    def test_extract_first_page(self) -> None:
        from pdf2word.extract.deepdoc import DeepDocOptions, extract

        with tempfile.TemporaryDirectory(prefix="pdf2word_deepdoc_") as workdir:
            doc = extract(SCAN, DeepDocOptions(pages=(1, 1)), workdir)
        self.assertEqual(doc.meta["engine"], "deepdoc")
        if os.path.isfile(os.path.join(MODEL_DIR, "layout.onnx")):
            self.assertTrue(doc.meta["deepdoc_layout"])
        self.assertEqual(len(doc.pages), 1)
        text = " ".join(b.text for b in doc.pages[0].blocks)
        self.assertIn("TUY", text.upper())
        self.assertTrue(any(b.spans for b in doc.pages[0].blocks))


if __name__ == "__main__":
    unittest.main()
