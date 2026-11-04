"""Dieu phoi engine: chi giu DeepDoc + VietOCR ONNX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .extract.deepdoc import DeepDocOptions, extract as extract_deepdoc
from .idm import Document

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class PipelineOptions:
    engine: str = "deepdoc"  # chi con deepdoc
    dpi_images: int = 200
    dpi_scan: int = 300
    lang: str = "vie+eng"
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
    return _deepdoc(pdf_path, options, workdir)


def _deepdoc(pdf_path: str, options: PipelineOptions, workdir: str) -> Document:
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