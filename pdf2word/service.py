"""Dich vu chuyen doi dung chung cho CLI va app desktop.

Gom mot lan chay convert (doc PDF -> IDM -> ghi DOCX) ke thong ke, tien trinh
va huy giua chuen.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable

from .docx.writer import WriteOptions, write_docx
from .idm import Document
from .pipeline import PipelineOptions, convert_pdf
from .polish import PolishOptions, polish_document

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class ConvertRequest:
    input: str
    output: str
    engine: str = "deepdoc"  # chi con deepdoc
    pages: tuple[int, int] | None = None
    dpi_images: int = 200
    ocr_dpi: int = 300
    lang: str = "vie+eng"
    deepdoc_model: str | None = None
    images: bool = True
    tables: bool = True
    page_breaks: bool = True
    keep_page_numbers: bool = False
    polish: bool = True
    font: str | None = None  # None = giu font goc (da chuan hoa ten)
    debug: bool = False
    assets_dir: str | None = None


@dataclass
class ConvertResult:
    input: str
    output: str
    pages: int = 0
    engine: str = "deepdoc"
    headings: int = 0
    toc: int = 0
    tables: int = 0
    images: int = 0
    seconds: float = 0.0
    cancelled: bool = False
    meta: dict = field(default_factory=dict)
    document: Document | None = None


def default_output(pdf_path: str) -> str:
    base, _ = os.path.splitext(os.path.abspath(pdf_path))
    return base + ".docx"


def convert_file(
    request: ConvertRequest,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> ConvertResult:
    """Chay mot file. Tra ve ConvertResult; nem ngoai le neu loi."""
    pdf_path = os.path.abspath(request.input)
    out_path = os.path.abspath(request.output)

    options = PipelineOptions(
        engine=request.engine,
        dpi_images=request.dpi_images,
        dpi_scan=request.ocr_dpi,
        lang=request.lang,
        deepdoc_model_dir=request.deepdoc_model,
        extract_images=request.images,
        extract_tables=request.tables,
        pages=request.pages,
        page_breaks=request.page_breaks,
        skip_page_numbers=not request.keep_page_numbers,
        debug=request.debug,
        progress=progress,
        cancel=cancel,
    )

    if request.assets_dir:
        workdir = os.path.abspath(request.assets_dir)
        os.makedirs(workdir, exist_ok=True)
        cleanup = False
    else:
        workdir = tempfile.mkdtemp(prefix="pdf2word_")
        cleanup = True

    t0 = time.time()
    try:
        doc = convert_pdf(pdf_path, options, workdir)
        t1 = time.time()

        if request.polish:
            polish_document(doc, PolishOptions(font=request.font))

        result = ConvertResult(
            input=pdf_path,
            output=out_path,
            pages=len(doc.pages),
            engine=doc.meta.get("engine", "deepdoc"),
            headings=sum(1 for p in doc.pages for b in p.blocks if b.kind in ("heading", "title")),
            toc=sum(1 for p in doc.pages for b in p.blocks if b.kind == "toc"),
            tables=sum(1 for p in doc.pages for b in p.blocks if b.kind == "table"),
            images=sum(1 for p in doc.pages for b in p.blocks if b.kind == "image"),
            cancelled=bool(doc.meta.get("cancelled")),
            meta=dict(doc.meta),
            document=doc,
        )

        if not result.cancelled:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            write_docx(doc, out_path, WriteOptions(page_breaks=request.page_breaks))
            result.seconds = time.time() - t0
            result.meta["write_seconds"] = round(time.time() - t1, 2)
        return result
    finally:
        if cleanup:
            shutil.rmtree(workdir, ignore_errors=True)


def zip_outputs(paths: list[str], zip_path: str) -> str:
    """Nen cac file DOCX thanh mot ZIP (giu ten file, xu ly trung ten)."""
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            if not os.path.exists(path):
                continue
            name = os.path.basename(path)
            stem, ext = os.path.splitext(name)
            n = 1
            while name in used:
                n += 1
                name = f"{stem} ({n}){ext}"
            used.add(name)
            zf.write(path, name)
    return zip_path