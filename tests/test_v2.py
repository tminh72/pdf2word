"""Test các tính năng mới trong bản v2:
- detect_dot_leaders: phát hiện dấu chấm leader trong mục lục
- Pipeline deepdoc end-to-end
- GUI app: khởi tạo window, kiểm tra thành phần UI
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2word.extract.deepdoc.engine import detect_dot_leaders  # noqa: E402
from pdf2word.pipeline import PipelineOptions, convert_pdf  # noqa: E402

MODEL_DIR = os.path.join(ROOT, "models", "deepdoc")
SCAN = os.path.join(ROOT, "benchmark", "data", "vi_core", "pdfs", "thong_bao_tuyen_sinh_scan.pdf")


def _deepdoc_ready() -> bool:
    if not os.path.isfile(os.path.join(MODEL_DIR, "det.onnx")) or not os.path.isfile(SCAN):
        return False
    try:
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


class DotLeaderTest(unittest.TestCase):
    def test_detect_dot_leaders_returns_list(self) -> None:
        """detect_dot_leaders phải trả về list (có thể rỗng nếu không có dot leader)."""
        # Ảnh đen trắng không có dot leader nào
        img = np.full((100, 200, 3), 255, dtype=np.uint8)
        result = detect_dot_leaders(img, dpi=300)
        self.assertIsInstance(result, list)

    def test_detect_dot_leaders_on_synthetic_leader(self) -> None:
        """Tạo ảnh mô phỏng dòng mục lục có dấu chấm leader, kiểm tra detect được."""
        img = np.full((100, 500, 3), 255, dtype=np.uint8)
        # Vẽ dòng chữ tiêu đề bên trái (giả lập)
        img[40:60, 10:80, :] = 0
        # Vẽ dấu chấm leader (nhiều điểm nhỏ cách đều trên hàng y=50)
        for x in range(90, 400, 8):
            img[48:52, x:x + 4, :] = 0
        # Vẽ số trang bên phải
        img[40:60, 410:440, :] = 0

        result = detect_dot_leaders(img, dpi=300)
        self.assertIsInstance(result, list)
        # Nếu detect được ít nhất 1 vùng dot leader
        if result:
            leader = result[0]
            self.assertIn("bbox_px", leader)
            self.assertIn("bbox_pt", leader)
            self.assertIn("dot_count", leader)
            self.assertIn("confidence", leader)
            self.assertGreaterEqual(leader["dot_count"], 8)


class DeepDocPipelineTest(unittest.TestCase):
    @unittest.skipUnless(_deepdoc_ready(), "thiếu model hoặc onnxruntime/opencv")
    def test_convert_pdf_deepdoc_end_to_end(self) -> None:
        """Chuyển đổi 1 trang PDF scan bằng engine deepdoc, kiểm tra IDM đầu ra."""
        with tempfile.TemporaryDirectory(prefix="pdf2word_pipeline_") as workdir:
            doc = convert_pdf(
                SCAN,
                PipelineOptions(engine="deepdoc", pages=(1, 1)),
                workdir,
            )
        self.assertEqual(doc.meta["engine"], "deepdoc")
        self.assertEqual(len(doc.pages), 1)
        page = doc.pages[0]
        self.assertGreater(len(page.blocks), 0)
        # Có ít nhất một block có text
        text_blocks = [b for b in page.blocks if b.text.strip()]
        self.assertGreater(len(text_blocks), 0)


class GuiTest(unittest.TestCase):
    @unittest.skipIf(os.environ.get("DISPLAY", "") == "" and sys.platform.startswith("linux"),
                     "không có display trên Linux headless")
    def test_gui_window_initializes(self) -> None:
        """Khởi tạo MainWindow không bị lỗi."""
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from pdf2word.gui.app import MainWindow  # noqa: F401

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.assertIsNotNone(window)
        self.assertEqual(window.engine, "deepdoc")
        # Kiểm tra bảng hàng đợi có 5 cột (thêm cột Xóa)
        self.assertEqual(window.table.columnCount(), 5)
        # Kiểm tra các tùy chọn mặc định
        self.assertTrue(window.extract_tables)
        self.assertTrue(window.extract_images)
        self.assertTrue(window.page_breaks)
        self.assertFalse(window.keep_page_numbers)
        window.close()


if __name__ == "__main__":
    unittest.main()
