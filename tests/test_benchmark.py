"""Test benchmark: dataset, metric, chuyển artifact và runner smoke."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from benchmark.dataset import load_dataset, validate_dataset  # noqa: E402
from benchmark.make_data import build_dataset  # noqa: E402
from benchmark.markdown import convert_artifact  # noqa: E402
from benchmark.metrics import (  # noqa: E402
    _fold,
    docx_headings,
    docx_tables,
    table_similarity,
    text_for_metric,
)
from benchmark.pipelines import Pipeline  # noqa: E402
from benchmark.runner import run_benchmark  # noqa: E402
from pdf2word.eval import docx_text  # noqa: E402
from pdf2word.idm import Block, Document, Page, Span  # noqa: E402
from pdf2word.extract.postprocess import detect_toc  # noqa: E402

DATA = os.path.join(ROOT, "benchmark", "data", "vi_core", "manifest.json")


class DatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.exists(DATA):
            build_dataset(os.path.dirname(DATA), force=True)
        cls.dataset = load_dataset(DATA)

    def test_dataset_valid_and_has_all_kinds(self) -> None:
        self.assertEqual(validate_dataset(self.dataset), [])
        self.assertGreaterEqual(len(self.dataset.docs), 4)
        kinds = {d.kind for d in self.dataset.docs}
        self.assertIn("digital", kinds)
        self.assertIn("scanned", kinds)
        self.assertTrue(all(d.text.strip() for d in self.dataset.docs))

    def test_duplicate_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "x",
                        "docs": [
                            {"id": "a", "pdf": "a.pdf"},
                            {"id": "a", "pdf": "b.pdf"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_dataset(manifest)


class MarkdownTest(unittest.TestCase):
    def test_markdown_table_heading_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "x.md"
            dest = Path(tmp) / "x.docx"
            src.write_text(
                "# Tiêu đề

Đoạn văn **quan trọng**.

"
                "| Chỉ tiêu | Giá trị |
|---|---|
| Doanh thu | 128,4 |
| Chi phí | 112,1 |

"
                "- Mục một
- Mục hai
",
                encoding="utf-8",
            )
            convert_artifact(str(src), str(dest))
            text = docx_text(str(dest))
            self.assertIn("Tiêu đề", text)
            self.assertIn("quan trọng", text)
            self.assertIn("Doanh thu", text)
            self.assertEqual(docx_tables(str(dest))[0], [["Chỉ tiêu", "Giá trị"], ["Doanh thu", "128,4"], ["Chi phí", "112,1"]])
            self.assertIn("Tiêu đề", docx_headings(str(dest)))

    def test_html_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "x.html"
            dest = Path(tmp) / "x.docx"
            src.write_text(
                "<h2>Mục lục</h2><p>Nội dung <b>đậm</b>.</p>"
                "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
                encoding="utf-8",
            )
            convert_artifact(str(src), str(dest))
            self.assertIn("Nội dung đậm", docx_text(str(dest)))
            self.assertEqual(docx_tables(str(dest))[0], [["A", "B"], ["1", "2"]])


class MetricTest(unittest.TestCase):
    def test_table_similarity(self) -> None:
        gt = [["A", "B"], ["1", "2"]]
        self.assertEqual(table_similarity(gt, gt), 1.0)
        self.assertAlmostEqual(table_similarity(gt, [["A", "B"], ["1", "x"]]), 0.75)
        self.assertEqual(table_similarity(gt, []), 0.0)

    def test_fold_and_toc_leader(self) -> None:
        self.assertEqual(_fold("TRƯỜNG CAO ĐẲNG"), _fold("truong cao dang"))
        self.assertEqual(
            text_for_metric("1. Mục lục........ 3"),
            "1. mục lục 3",
        )

    def test_detect_toc_split_merged_block(self) -> None:
        merged = (
            "1. Tổng quan................ 2 "
            "2. Kế hoạch................. 3 "
            "3. Kết luận................. 4"
        )
        doc = Document(
            pages=[Page(width=600, height=800, blocks=[Block(kind="paragraph", spans=[Span(text=merged)])])],
            body_size=11,
        )
        detect_toc(doc)
        blocks = doc.pages[0].blocks
        self.assertEqual([b.kind for b in blocks], ["toc", "toc", "toc"])
        self.assertEqual([b.marker for b in blocks], ["2", "3", "4"])
        self.assertEqual(blocks[0].text, "1. Tổng quan")


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not os.path.exists(DATA):
            build_dataset(os.path.dirname(DATA), force=True)
        cls.dataset = load_dataset(DATA)

    @unittest.skip("v2 chỉ có pipeline deepdoc, test này cần các pipeline khác")
    def test_digital_runner_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_benchmark(
                self.dataset,
                out_dir=tmp,
                run_id="test",
                doc_ids=["don_de_nghi"],
                pipeline_names=["pdf2word-deepdoc"],
                force=True,
            )
            row = result["rows"][0]
            self.assertEqual(row["status"], "ok", row.get("error"))
            self.assertGreater(row["metrics"]["composite"], 0.9)
            self.assertTrue(Path(row["artifact"]).is_file())
            for name in ("results.json", "report.md", "summary.csv"):
                self.assertTrue((Path(tmp) / "test" / name).is_file())

    def test_external_pipeline_and_artifact_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "adapter.py"
            script.write_text(
                "import pathlib, sys
"
                "pathlib.Path(sys.argv[1]).write_text('# Tiêu đề\n\nNội dung thử nghiệm.\n', encoding='utf-8')
",
                encoding="utf-8",
            )
            pipeline = Pipeline(
                name="external-test",
                type="external",
                command=[sys.executable, str(script), "{output}"],
                artifact="md",
                timeout=60,
            )
            doc = self.dataset.doc("don_de_nghi")
            out = Path(tmp) / "out"
            output = pipeline.convert(doc, str(out))
            self.assertTrue(output.artifact.endswith(".md"))
            dest = Path(tmp) / "converted.docx"
            convert_artifact(output.artifact, str(dest))
            self.assertIn("Nội dung thử nghiệm", docx_text(str(dest)))
            self.assertIn("Tiêu đề", docx_headings(str(dest)))


if __name__ == "__main__":
    unittest.main()
