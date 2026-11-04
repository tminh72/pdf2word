"""Điều phối engine: chọn digital hay OCR cho từng tài liệu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .extract.digital import ExtractOptions, extract as extract_digital
from .extract.ocr import OcrOptions, extract as extract_ocr
from .extract.router import profile_pdf
from .idm import Document

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class PipelineOptions:
    engine: str = "auto"  # auto | digital | ocr | deepdoc
    dpi_images: int = 200
    dpi_scan: int = 300
    lang: str = "vie+eng"
    tesseract_cmd: str | None = None
    tessdata_dir: str | None = None
    deepdoc_model_dir: str | None = None
    extract_tables: bool = True
    extract_images: bool = True
    pages: tuple[int, int] | None = None
    page_breaks: bool = True
    skip_page_numbers: bool = True
    debug: bool = False
    progress: ProgressFn | None = None
    cancel: CancelFn | None = None


def convert_pdf(pdf_path: str, options: PipelineOptions, workdir: str) -> Document:
    if options.engine == "digital":
        doc = _digital(pdf_path, options, workdir)
    elif options.engine == "ocr":
        doc = _ocr(pdf_path, options, workdir)
    elif options.engine == "deepdoc":
        doc = _deepdoc(pdf_path, options, workdir)
    else:
        profile = profile_pdf(pdf_path)
        if profile.kind == "digital":
            doc = _digital(pdf_path, options, workdir)
        else:
            # scan hoặc mixed: OCR toàn bộ để không mất trang ảnh
            doc = _ocr(pdf_path, options, workdir)
        doc.meta["profile"] = profile.kind

    doc.meta.setdefault("engine", "digital")
    return doc


def _digital(pdf_path: str, options: PipelineOptions, workdir: str) -> Document:
    return extract_digital(
        pdf_path,
        ExtractOptions(
            dpi=options.dpi_images,
            extract_images=options.extract_images,
            extract_tables=options.extract_tables,
            page_breaks=options.page_breaks,
            skip_page_numbers=options.skip_page_numbers,
            pages=options.pages,
            debug=options.debug,
            progress=options.progress,
            cancel=options.cancel,
        ),
        workdir,
    )


def _ocr(pdf_path: str, options: PipelineOptions, workdir: str) -> Document:
    return extract_ocr(
        pdf_path,
        OcrOptions(
            dpi=options.dpi_scan,
            lang=options.lang,
            tesseract_cmd=options.tesseract_cmd,
            tessdata_dir=options.tessdata_dir,
            detect_tables=options.extract_tables,
            pages=options.pages,
            page_breaks=options.page_breaks,
            debug=options.debug,
            progress=options.progress,
            cancel=options.cancel,
        ),
        workdir,
    )


def _deepdoc(pdf_path: str, options: PipelineOptions, workdir: str) -> Document:
    # Import muộn để auto/digital/ocr không cần onnxruntime/opencv.
    from .extract.deepdoc import DeepDocOptions, extract as extract_deepdoc

    return extract_deepdoc(
        pdf_path,
        DeepDocOptions(
            dpi=options.dpi_scan,
            detect_tables=options.extract_tables,
            pages=options.pages,
            page_breaks=options.page_breaks,
            model_dir=options.deepdoc_model_dir,
            debug=options.debug,
            progress=options.progress,
            cancel=options.cancel,
        ),
        workdir,
    )
