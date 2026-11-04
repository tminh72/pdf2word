"""Phân loại PDF: có text layer (digital) hay scan (cần OCR)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PdfProfile:
    pages: int
    digital_pages: int
    mean_chars: float
    kind: str  # "digital" | "scanned" | "mixed"


def profile_pdf(path: str, min_chars_per_page: int = 80, digital_ratio: float = 0.6) -> PdfProfile:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    counts: list[int] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                counts.append(tp.count_chars())
                tp.close()
            finally:
                page.close()
    finally:
        pdf.close()

    if not counts:
        return PdfProfile(pages=0, digital_pages=0, mean_chars=0.0, kind="scanned")

    digital = sum(1 for c in counts if c >= min_chars_per_page)
    mean = sum(counts) / len(counts)
    ratio = digital / len(counts)
    if ratio >= digital_ratio:
        kind = "digital"
    elif digital == 0:
        kind = "scanned"
    else:
        kind = "mixed"
    return PdfProfile(pages=len(counts), digital_pages=digital, mean_chars=mean, kind=kind)
