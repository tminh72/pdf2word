"""Chấm điểm chất lượng: CER/WER so với text tham chiếu."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .idm import Document, doc_text


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip().lower()


def cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate ~ 1 - similarity (sau khi chuẩn hoá)."""
    ref = normalize(reference).replace("\n", " ")
    hyp = normalize(hypothesis).replace("\n", " ")
    if not ref:
        return 0.0 if not hyp else 1.0
    return max(0.0, 1.0 - SequenceMatcher(None, ref, hyp, autojunk=False).ratio())


def docx_text(path: str) -> str:
    """Text theo đúng thứ tự tài liệu (paragraph và table xen kẽ)."""
    from docx import Document as Docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    dx = Docx(path)
    parts: list[str] = []

    for child in dx.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, dx).text
            if text.strip():
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, dx)
            # duyệt đúng các <w:tc> (bỏ ô continuation của vMerge) để không lặp ô gộp
            from docx.table import _Cell

            for tr in table._tbl.tr_lst:
                for tc in tr.tc_lst:
                    tcPr = tc.tcPr
                    if tcPr is not None:
                        vm = tcPr.find(qn("w:vMerge"))
                        if vm is not None and vm.get(qn("w:val")) != "restart":
                            continue
                    cell = _Cell(tc, table)
                    if cell.text.strip():
                        parts.append(cell.text.strip())
    return "\n".join(parts)


def compare(required_text: str, produced_text: str) -> dict[str, float]:
    ref = normalize(required_text)
    hyp = normalize(produced_text)
    ref_words = ref.split()
    hyp_words = hyp.split()
    return {
        "cer": cer(ref, hyp),
        "wer": 1.0 - SequenceMatcher(None, ref_words, hyp_words, autojunk=False).ratio()
        if ref_words
        else 0.0,
        "ref_chars": float(len(ref)),
        "hyp_chars": float(len(hyp)),
    }


def idm_text(doc: Document) -> str:
    return doc_text(doc)
