"""Test hậu kỳ: chuẩn hoá dấu thanh, khoảng trắng, font và từ điển sửa lỗi."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pdf2word.idm import Block, Cell, Document, Page, Span, Table  # noqa: E402
from pdf2word.polish import (  # noqa: E402
    PolishOptions,
    PolishStats,
    clean_font_name,
    clean_text,
    load_spellfix,
    normalize_vietnamese_tone,
    polish_document,
)


class ToneTest(unittest.TestCase):
    def test_old_style_to_new_style(self) -> None:
        cases = {
            "hoà": "hòa",
            "thuỷ": "thủy",
            "khoẻ": "khỏe",
            "tuỳ": "tùy",
            "uỷ": "ủy",
        }
        for wrong, right in cases.items():
            self.assertEqual(normalize_vietnamese_tone(wrong), right, wrong)

    def test_misplaced_tone_from_ocr(self) -> None:
        cases = {
            "đựơc": "được",
            "ngừơi": "người",
        }
        for wrong, right in cases.items():
            self.assertEqual(normalize_vietnamese_tone(wrong), right, wrong)

    def test_valid_words_unchanged(self) -> None:
        for word in (
            "hòa",
            "quả",
            "gia",
            "già",
            "gì",
            "toán",
            "ngoài",
            "cửa",
            "của",
            "nguyễn",
            "quyển",
            "khuỷu",
            "cứu",
            "mía",
            "múa",
        ):
            self.assertEqual(normalize_vietnamese_tone(word), word, word)

    def test_uppercase_preserved(self) -> None:
        self.assertEqual(normalize_vietnamese_tone("HOÀ"), "HÒA")


class TextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = PolishStats()
        self.options = PolishOptions()
        self.fixes = load_spellfix()

    def clean(self, text: str) -> str:
        return clean_text(text, self.options, self.fixes, self.stats)

    def test_spaces_and_punctuation(self) -> None:
        self.assertEqual(self.clean("  Xin   chào ,thế giới . "), "Xin chào, thế giới.")
        self.assertEqual(self.clean("mục 1,5 điểm"), "mục 1,5 điểm")
        self.assertEqual(self.clean("Lưu ý:điều kiện"), "Lưu ý: điều kiện")

    def test_ligature_and_nbsp(self) -> None:
        self.assertEqual(self.clean("ﬁle\u00a0mẫu"), "file mẫu")

    def test_tone_in_text(self) -> None:
        self.assertEqual(self.clean("Hoà bình thựơng"), "Hòa bình thương")

    def test_stats_counted(self) -> None:
        self.clean("  a   b ,c ")
        self.assertGreater(self.stats.space_fixes, 0)


class FontTest(unittest.TestCase):
    def test_subset_and_alias(self) -> None:
        self.assertEqual(clean_font_name("ABCDEF+TimesNewRomanPSMT"), "Times New Roman")
        self.assertEqual(clean_font_name("ArialMT"), "Arial")
        self.assertEqual(clean_font_name("Calibri-Bold"), "Calibri")
        self.assertEqual(clean_font_name(""), "")


class DocumentPolishTest(unittest.TestCase):
    def test_polish_document(self) -> None:
        span = Span(text=" Ngừơi   dùng ,dựng bảng ", font="ABCDEF+ArialMT", size=12.26)
        block = Block(kind="paragraph", spans=[span])
        empty = Block(kind="paragraph", spans=[Span(text="   ")])
        cell = Cell(lines=[[Span(text=" hoà bình ", font="TimesNewRomanPSMT")]])
        table = Table(
            n_rows=1,
            n_cols=1,
            col_widths=[100.0],
            cells=[[cell]],
        )
        doc = Document(
            pages=[Page(width=600, height=800, blocks=[block, empty, Block(kind="table", table=table)])],
            body_font="ABCDEF+TimesNewRomanPSMT",
            meta={"header_text": " ĐẦU   TRANG "},
        )

        stats = polish_document(doc, PolishOptions())

        self.assertEqual(doc.pages[0].blocks[0].text, "Người dùng, dựng bảng")
        self.assertEqual(len(doc.pages[0].blocks), 2)  # block rỗng bị bỏ
        self.assertEqual(doc.pages[0].blocks[1].table.cells[0][0].text, "hòa bình")
        self.assertEqual(doc.meta["header_text"], "ĐẦU TRANG")
        self.assertEqual(doc.body_font, "Times New Roman")
        self.assertEqual(span.font, "Arial")
        self.assertAlmostEqual(span.size, 12.5)
        self.assertGreater(stats["spell_fixes"], 0)

    def test_font_override(self) -> None:
        span = Span(text="Nội dung", font="Arial", size=11)
        doc = Document(
            pages=[Page(width=600, height=800, blocks=[Block(kind="paragraph", spans=[span])])],
            body_font="Arial",
        )
        polish_document(doc, PolishOptions(font="Times New Roman"))
        self.assertEqual(span.font, "Times New Roman")
        self.assertEqual(doc.body_font, "Times New Roman")

    def test_span_boundary_spaces_not_lost(self) -> None:
        # PDF tách câu thành nhiều span; space ở đầu span phải được giữ.
        spans = [
            Span(text="doanh thu"),
            Span(text=" thuần"),
            Span(text=" đạt 128,4"),
            Span(text=" tỷ"),
            Span(text=" đồng"),
            Span(text=". Ban lãnh đạo"),
        ]
        doc = Document(
            pages=[Page(width=600, height=800, blocks=[Block(kind="paragraph", spans=spans)])],
            body_font="Times New Roman",
        )
        polish_document(doc, PolishOptions())
        text = doc.pages[0].blocks[0].text
        self.assertEqual(text, "doanh thu thuần đạt 128,4 tỷ đồng. Ban lãnh đạo")
        self.assertNotIn("tỷđồng", text)
        self.assertNotIn("phẩm.Ban", text)

    def test_line_font_and_size_unified(self) -> None:
        spans = [
            Span(text="Trong quý III", font="Arial", size=11.0),
            Span(text=" công ty", font="Times New Roman", size=15.0),
            Span(text=" tăng trưởng", font="Arial", size=11.04),
        ]
        doc = Document(
            pages=[Page(width=600, height=800, blocks=[Block(kind="paragraph", spans=spans)])],
            body_font="Arial",
        )
        polish_document(doc, PolishOptions())
        self.assertEqual(spans[0].font, "Arial")
        self.assertEqual(spans[1].font, "Arial")  # font đồng nhất theo dòng
        self.assertAlmostEqual(spans[0].size, spans[2].size)
        self.assertAlmostEqual(spans[1].size, 15.0)  # giữ cỡ khác biệt lớn

    def test_spellfix_dictionary(self) -> None:
        fixes = load_spellfix()
        self.assertTrue(fixes, "models/spellfix.txt phải tồn tại và có mục")
        stats = PolishStats()
        text = clean_text("thưong mại", PolishOptions(), fixes, stats)
        self.assertEqual(text, "thương mại")


if __name__ == "__main__":
    unittest.main()
