"""Đọc và kiểm tra dataset benchmark dạng manifest.

Cấu trúc một dataset::

    vi_core/
      manifest.json
      pdfs/*.pdf
      refs/*.txt        # text tham chiếu (đã bao gồm text trong bảng)
      refs/*.json       # heading / toc / header / footer / bảng ground truth

`manifest.json`::

    {
      "name": "vi_core",
      "version": "1.0",
      "language": "vi",
      "docs": [
        {
          "id": "bao_cao_tai_chinh",
          "pdf": "pdfs/bao_cao_tai_chinh.pdf",
          "reference": "refs/bao_cao_tai_chinh.txt",
          "truth": "refs/bao_cao_tai_chinh.json",
          "kind": "digital",
          "pages": 3,
          "tags": ["report", "tables", "toc"]
        }
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import ExpectedDoc


@dataclass
class BenchmarkDataset:
    name: str
    path: Path
    docs: list[ExpectedDoc] = field(default_factory=list)
    version: str = "1.0"
    description: str = ""
    license: str = "CC0-1.0"
    language: str = "vi"
    manifest_path: Path | None = None

    def doc(self, doc_id: str) -> ExpectedDoc:
        for item in self.docs:
            if item.id == doc_id:
                return item
        raise KeyError(f"Không có tài liệu '{doc_id}' trong dataset {self.name}")

    def by_tags(self, tags: list[str]) -> list[ExpectedDoc]:
        wanted = {t.strip().lower() for t in tags if t.strip()}
        if not wanted:
            return list(self.docs)
        return [d for d in self.docs if wanted & {t.lower() for t in d.tags}]


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON gốc phải là object")
    return data


def _resolve(root: Path, value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise ValueError("giá trị phải là chuỗi hoặc danh sách chuỗi")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("phần tử phải là chuỗi")
        if item.strip():
            out.append(item.strip())
    return out


def _tables(value) -> list[list[list[str]]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tables phải là danh sách bảng")
    out: list[list[list[str]]] = []
    for table in value:
        if not isinstance(table, list) or not all(isinstance(row, list) for row in table):
            raise ValueError("mỗi bảng phải là danh sách hàng, mỗi hàng là danh sách ô")
        rows: list[list[str]] = []
        for row in table:
            cells = []
            for cell in row:
                if not isinstance(cell, str):
                    raise ValueError("ô bảng phải là chuỗi")
                cells.append(cell.strip())
            rows.append(cells)
        out.append(rows)
    return out


def load_dataset(path: str | Path) -> BenchmarkDataset:
    """Đọc manifest từ file hoặc thư mục dataset."""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "manifest.json"
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Không thấy manifest: {manifest_path}")

    data = _read_json(manifest_path)
    root = manifest_path.parent

    name = str(data.get("name") or root.name)
    version = str(data.get("version") or "1.0")
    docs: list[ExpectedDoc] = []
    seen: set[str] = set()

    raw_docs = data.get("docs")
    if not isinstance(raw_docs, list) or not raw_docs:
        raise ValueError(f"{manifest_path}: 'docs' phải là danh sách không rỗng")

    for raw in raw_docs:
        if not isinstance(raw, dict):
            raise ValueError(f"{manifest_path}: mỗi doc phải là object")
        doc_id = str(raw.get("id") or "").strip()
        if not doc_id:
            raise ValueError(f"{manifest_path}: có doc thiếu 'id'")
        if doc_id in seen:
            raise ValueError(f"{manifest_path}: id bị trùng: {doc_id}")
        seen.add(doc_id)

        pdf_rel = raw.get("pdf")
        if not pdf_rel:
            raise ValueError(f"{manifest_path}: doc '{doc_id}' thiếu 'pdf'")
        pdf = _resolve(root, str(pdf_rel))

        truth_path = _resolve(root, raw.get("truth"))
        truth: dict = _read_json(Path(truth_path)) if truth_path and Path(truth_path).is_file() else {}

        text = ""
        reference = _resolve(root, raw.get("reference"))
        if reference and Path(reference).is_file():
            text = Path(reference).read_text(encoding="utf-8")
        elif isinstance(truth.get("text"), str):
            text = truth["text"]

        # Cho phép khai báo cấu trúc trực tiếp trong manifest hoặc trong truth JSON.
        def pick(key: str, default):
            return raw[key] if key in raw else truth.get(key, default)

        try:
            headings = _string_list(pick("headings", []))
            toc = _string_list(pick("toc", []))
            header = _string_list(pick("header", []))
            footer = _string_list(pick("footer", []))
            tables = _tables(pick("tables", []))
        except ValueError as exc:
            raise ValueError(f"{manifest_path}: doc '{doc_id}': {exc}") from exc

        pages = pick("pages", 0)
        try:
            pages = int(pages or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{manifest_path}: doc '{doc_id}': pages phải là số") from None

        docs.append(
            ExpectedDoc(
                id=doc_id,
                pdf=pdf,
                text=text,
                kind=str(pick("kind", "digital") or "digital"),
                pages=pages,
                headings=headings,
                toc=toc,
                header=header,
                footer=footer,
                tables=tables,
                tags=_string_list(raw.get("tags", [])),
            )
        )

    return BenchmarkDataset(
        name=name,
        path=root,
        docs=docs,
        version=version,
        description=str(data.get("description") or ""),
        license=str(data.get("license") or "CC0-1.0"),
        language=str(data.get("language") or "vi"),
        manifest_path=manifest_path,
    )


def validate_dataset(dataset: BenchmarkDataset) -> list[str]:
    """Trả về danh sách lỗi (rỗng = hợp lệ)."""
    errors: list[str] = []
    if not dataset.docs:
        errors.append("Dataset không có tài liệu nào.")
    for doc in dataset.docs:
        prefix = f"[{doc.id}]"
        if not Path(doc.pdf).is_file():
            errors.append(f"{prefix} không thấy PDF: {doc.pdf}")
        if doc.kind not in ("digital", "scanned", "mixed"):
            errors.append(f"{prefix} kind không hợp lệ: {doc.kind}")
        if doc.pages <= 0:
            errors.append(f"{prefix} pages phải > 0")
        if not doc.text.strip():
            errors.append(f"{prefix} thiếu text tham chiếu")
        if doc.kind in ("scanned", "mixed") and not doc.text.strip():
            errors.append(f"{prefix} tài liệu scan cần text tham chiếu để chấm OCR")
        for i, table in enumerate(doc.tables):
            if not table:
                errors.append(f"{prefix} bảng #{i + 1} rỗng")
                continue
            widths = {len(row) for row in table}
            if len(widths) > 1:
                errors.append(f"{prefix} bảng #{i + 1} có số cột không đều: {sorted(widths)}")
        if doc.toc and not any(ch.isdigit() for ch in doc.text):
            errors.append(f"{prefix} có TOC nhưng text tham chiếu không có số trang")
    return errors
