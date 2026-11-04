"""Chấm điểm DOCX của một pipeline so với ground truth.

Các nhóm metric:
- text: CER / WER trên toàn bộ text (dùng lại pdf2word.eval).
- tables: số bảng, độ giống bảng (khớp ô + kích thước).
- headings: F1 của heading theo style Word.
- toc: F1 của các dòng mục lục (title + số trang).
- header_footer: có đủ header/footer mong đợi hay không.
- composite: điểm tổng hợp có trọng số (bỏ qua metric không áp dụng).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pdf2word.eval import docx_text, normalize

DEFAULT_WEIGHTS = {
    "text": 0.45,
    "tables": 0.25,
    "headings": 0.10,
    "toc": 0.10,
    "header_footer": 0.10,
}


@dataclass
class ExpectedDoc:
    id: str
    pdf: str
    text: str = ""
    kind: str = "digital"
    pages: int = 0
    headings: list[str] = field(default_factory=list)
    toc: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    footer: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Đọc cấu trúc DOCX
# --------------------------------------------------------------------------


def _table_grid(table) -> list[list[str]]:
    """Bảng Word -> ma trận đầy đủ; ô merge bị chiếm chỗ là chuỗi rỗng."""
    from docx.oxml.ns import qn
    from docx.table import _Cell

    grid: list[list[str]] = []
    for tr in table._tbl.tr_lst:
        row: list[str] = []
        for tc in tr.tc_lst:
            span = 1
            tc_pr = tc.tcPr
            if tc_pr is not None:
                gs = tc_pr.find(qn("w:gridSpan"))
                if gs is not None:
                    try:
                        span = max(1, int(gs.get(qn("w:val")) or 1))
                    except ValueError:
                        span = 1
            text = _Cell(tc, table).text.strip()
            row.append(text)
            row.extend([""] * (span - 1))
        grid.append(row)
    if grid:
        width = max(len(r) for r in grid)
        for row in grid:
            row.extend([""] * (width - len(row)))
    return grid


def docx_tables(path: str) -> list[list[list[str]]]:
    from docx import Document as Docx

    doc = Docx(path)
    return [_table_grid(t) for t in doc.tables]


def docx_headings(path: str) -> list[str]:
    from docx import Document as Docx

    doc = Docx(path)
    out: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name or "").lower() if para.style is not None else ""
        if para.text.strip() and (style.startswith("heading") or style.startswith("title")):
            out.append(para.text.strip())
    return out


_TOC_RE = re.compile(r"^(?P<title>.+?)\t+(?P<page>\d{1,4})\s*$")


def docx_toc(path: str) -> list[str]:
    """Các dòng mục lục do writer tạo: 'Tiêu đề<TAB>số trang'."""
    from docx import Document as Docx

    doc = Docx(path)
    out: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        m = _TOC_RE.match(text)
        if m:
            title = m.group("title").strip().rstrip(".")
            if title:
                out.append(title)
    return out


def docx_header_footer(path: str) -> tuple[str, str]:
    from docx import Document as Docx

    doc = Docx(path)
    headers: list[str] = []
    footers: list[str] = []
    for section in doc.sections:
        headers.extend(p.text.strip() for p in section.header.paragraphs if p.text.strip())
        footers.extend(p.text.strip() for p in section.footer.paragraphs if p.text.strip())
    return "\n".join(headers), "\n".join(footers)


# --------------------------------------------------------------------------
# Metric
# --------------------------------------------------------------------------


def _fold(text: str) -> str:
    """So khớp cấu trúc không phân biệt hoa/thường và dấu tiếng Việt."""
    text = unicodedata.normalize("NFD", normalize(text))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _f1(expected: list[str], actual: list[str], key=None) -> float:
    key = key or normalize
    exp = Counter(key(x) for x in expected if x and x.strip())
    act = Counter(key(x) for x in actual if x and x.strip())
    if not exp and not act:
        return 1.0
    if not exp or not act:
        return 0.0
    overlap = sum((exp & act).values())
    precision = overlap / sum(act.values())
    recall = overlap / sum(exp.values())
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _cell_equal(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


def table_similarity(gt: list[list[str]], actual: list[list[str]]) -> float:
    """Độ giống 0..1: khớp ô / số ô lớn hơn (phạt thiếu/thừa hàng/cột)."""
    if not gt:
        return 1.0
    gt_cells = sum(len(r) for r in gt)
    act_cells = sum(len(r) for r in actual)
    if not actual:
        return 0.0
    matches = 0
    for r in range(min(len(gt), len(actual))):
        for c in range(min(len(gt[r]), len(actual[r]))):
            if _cell_equal(gt[r][c], actual[r][c]):
                matches += 1
    return matches / max(gt_cells, act_cells)


def tables_score(
    expected: list[list[list[str]]], actual: list[list[list[str]]]
) -> tuple[float | None, float | None]:
    """Trả về (similarity, count_f1); None nếu tài liệu không có bảng kỳ vọng."""
    if not expected:
        return None, None
    if not actual:
        return 0.0, 0.0

    used: set[int] = set()
    similarities: list[float] = []
    matched = 0
    for gt in expected:
        best_score = 0.0
        best_idx = -1
        for idx, act in enumerate(actual):
            if idx in used:
                continue
            score = table_similarity(gt, act)
            if score > best_score:
                best_score = score
                best_idx = idx
        similarities.append(best_score)
        if best_idx >= 0:
            used.add(best_idx)
        if best_score >= 0.5:
            matched += 1

    similarity = sum(similarities) / len(expected)
    precision = matched / len(actual) if actual else 0.0
    recall = matched / len(expected)
    count_f1 = (
        2 * precision * recall / (precision + recall) if precision + recall else 0.0
    )
    return similarity, count_f1


def header_footer_score(expected_h: list[str], expected_f: list[str], actual: tuple[str, str]) -> float | None:
    if not expected_h and not expected_f:
        return None
    actual_h, actual_f = _fold(actual[0]), _fold(actual[1])
    checks: list[bool] = []
    checks.extend(_fold(x) in actual_h for x in expected_h if x.strip())
    checks.extend(_fold(x) in actual_f for x in expected_f if x.strip())
    if not checks:
        return None
    return sum(checks) / len(checks)


def composite(metrics: dict, weights: dict[str, float] | None = None) -> float | None:
    weights = weights or DEFAULT_WEIGHTS
    parts: list[tuple[float, float]] = []
    mapping = {
        "text": 1.0 - float(metrics.get("cer") or 0.0),
        "tables": metrics.get("tables"),
        "headings": metrics.get("headings_f1"),
        "toc": metrics.get("toc_f1"),
        "header_footer": metrics.get("header_footer"),
    }
    for key, value in mapping.items():
        if value is None:
            continue
        parts.append((weights.get(key, 0.0), max(0.0, min(1.0, float(value)))))
    total = sum(w for w, _ in parts)
    if not total:
        return None
    return sum(w * v for w, v in parts) / total


_TOC_LEADER_RE = re.compile(r"(?:[.\u2026\u00b7\u2024]\s*){3,}")


def text_for_metric(text: str) -> str:
    """Chuẩn hoá text để chấm: bỏ dấu chấm dẫn mục lục, không phạt tab leader."""
    text = normalize(text)
    text = _TOC_LEADER_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_docx(docx_path: str, expected: ExpectedDoc, seconds: float = 0.0) -> dict:
    """Chấm một DOCX. Trả về dict metric (có thể chứa None nếu không áp dụng)."""
    from pdf2word.eval import compare

    text = docx_text(docx_path)
    cmp = compare(text_for_metric(expected.text), text_for_metric(text))
    tables = docx_tables(docx_path)
    table_sim, table_count_f1 = tables_score(expected.tables, tables)
    headings = docx_headings(docx_path)
    toc = docx_toc(docx_path)
    hf = header_footer_score(expected.header, expected.footer, docx_header_footer(docx_path))

    metrics: dict = {
        "cer": cmp["cer"],
        "wer": cmp["wer"],
        "ref_chars": int(cmp["ref_chars"]),
        "hyp_chars": int(cmp["hyp_chars"]),
        "tables": table_sim,
        "table_count_f1": table_count_f1,
        "tables_expected": len(expected.tables),
        "tables_actual": len(tables),
        "headings_f1": _f1(expected.headings, headings, key=_fold) if expected.headings else None,
        "headings_expected": len(expected.headings),
        "headings_actual": len(headings),
        "toc_f1": _f1(expected.toc, toc, key=_fold) if expected.toc else None,
        "toc_expected": len(expected.toc),
        "toc_actual": len(toc),
        "header_footer": hf,
        "seconds": round(seconds, 3),
        "pages": expected.pages,
        "pages_per_second": round(expected.pages / seconds, 3) if seconds > 0 else None,
        "docx_bytes": os.path.getsize(docx_path) if os.path.exists(docx_path) else 0,
    }
    metrics["composite"] = composite(metrics)
    return metrics


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()
