"""CLI: pdf2word convert / inspect / profile / eval."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

from . import __version__


def _parse_pages(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    if "-" in value:
        a, b = value.split("-", 1)
        return int(a), int(b)
    n = int(value)
    return n, n


def cmd_convert(args: argparse.Namespace) -> int:
    from .service import ConvertRequest, convert_file, default_output
    from .eval import docx_text, normalize

    pdf_path = os.path.abspath(args.input)
    if not os.path.exists(pdf_path):
        print(f"Khong tim thay file: {pdf_path}", file=sys.stderr)
        return 2

    request = ConvertRequest(
        input=pdf_path,
        output=os.path.abspath(args.output) if args.output else default_output(pdf_path),
        engine="deepdoc",
        pages=_parse_pages(args.pages),
        dpi_images=args.dpi,
        ocr_dpi=args.ocr_dpi,
        lang=args.lang,
        deepdoc_model=args.deepdoc_model,
        images=not args.no_images,
        tables=not args.no_tables,
        page_breaks=not args.no_page_breaks,
        keep_page_numbers=args.keep_page_numbers,
        polish=not args.no_polish,
        font=args.font,
        debug=args.verbose,
        assets_dir=args.assets_dir,
    )

    result = convert_file(request)
    doc = result.document
    meta = result.meta
    out_path = result.output
    print(
        f"[done] {out_path}\n"
        f"       {result.pages} trang [deepdoc] | {result.headings} heading, "
        f"{result.toc} muc luc, {result.tables} bang, {result.images} anh | "
        f"extract {result.seconds - float(meta.get('write_seconds', 0)):.2f}s, "
        f"write {float(meta.get('write_seconds', 0)):.2f}s"
    )
    if meta.get("header_text"):
        print(f"       header: {meta['header_text'][:70]!r}")
    if meta.get("footer_text"):
        print(
            f"       footer: {meta['footer_text'][:70]!r} "
            f"(offset trang: {meta.get('footer_page_offset', 0)})"
        )

    if args.verbose:
        polish = meta.get("polish") or {}
        if polish:
            print(
                f"[debug] chuan hoa: {polish.get('spell_fixes', 0)} chinh tai, "
                f"{polish.get('space_fixes', 0)} khoang trang, "
                f"{polish.get('font_fixes', 0)} font, "
                f"{polish.get('dropped_blocks', 0)} block rong"
            )
        text = normalize(docx_text(out_path))
        print(f"[debug] {len(text)} ky tu trong DOCX")

    if args.dump_idm and doc is not None:
        with open(args.dump_idm, "w", encoding="utf-8") as f:
            json.dump(doc.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"[debug] IDM -> {args.dump_idm}")

    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    print("Che do deepdoc khong can profile digital/scan.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    from .pipeline import PipelineOptions, convert_pdf

    workdir = tempfile.mkdtemp(prefix="pdf2word_inspect_")
    try:
        doc = convert_pdf(
            os.path.abspath(args.input),
            PipelineOptions(
                engine="deepdoc",
                pages=_parse_pages(args.pages),
                lang=args.lang,
                deepdoc_model_dir=args.deepdoc_model,
                debug=False,
            ),
            workdir,
        )
        data = doc.to_dict()
        out = args.output
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"IDM -> {out}")
        else:
            print(text[: args.limit])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval import compare, docx_text

    with open(args.reference, encoding="utf-8") as f:
        ref = f.read()
    hyp = docx_text(os.path.abspath(args.docx))
    stats = compare(ref, hyp)
    print(f"CER: {stats['cer'] * 100:.2f}%  |  WER: {stats['wer'] * 100:.2f}%")
    print(f"ref {int(stats['ref_chars'])} ky tu, hyp {int(stats['hyp_chars'])} ky tu")
    return 0 if stats["cer"] <= args.max_cer else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf2word", description="Chuyen PDF sang DOCX (offline, CPU).")
    p.add_argument("--version", action="version", version=f"pdf2word {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="PDF -> DOCX")
    c.add_argument("input")
    c.add_argument("-o", "--output")
    c.add_argument("--dpi", type=int, default=200, help="DPI khi render anh (mac dinh 200)")
    c.add_argument("--ocr-dpi", type=int, default=300, help="DPI khi render trang de OCR")
    c.add_argument("--lang", default="vie+eng", help="Ngon ngu Tesseract, VD vie+eng")
    c.add_argument("--deepdoc-model", help="Thu muc model DeepDoc (chua det.onnx, tsr.onnx, ...)")
    c.add_argument("--pages", help="VD: 1-5 hoac 3")
    c.add_argument("--no-images", action="store_true")
    c.add_argument("--no-tables", action="store_true")
    c.add_argument("--no-page-breaks", action="store_true")
    c.add_argument("--keep-page-numbers", action="store_true", help="Khong bo so trang o le")
    c.add_argument("--no-polish", action="store_true", help="Khong chuan hoa chinh tai/khoang trang/font")
    c.add_argument(
        "--font",
        help="Ep font cho toan bo DOCX (VD 'Times New Roman', 'Arial'); mac dinh giu font goc",
    )
    c.add_argument("--assets-dir", help="Giu anh tach ra trong thu muc nay")
    c.add_argument("--dump-idm", help="Xuat IDM JSON de debug")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_convert)

    ins = sub.add_parser("inspect", help="Dump IDM JSON")
    ins.add_argument("input")
    ins.add_argument("-o", "--output")
    ins.add_argument("--pages")
    ins.add_argument("--lang", default="vie+eng")
    ins.add_argument("--deepdoc-model", help="Thu muc model DeepDoc")
    ins.add_argument("--limit", type=int, default=4000)
    ins.set_defaults(func=cmd_inspect)

    ev = sub.add_parser("eval", help="Cham CER/WER cua DOCX voi text tham chieu")
    ev.add_argument("docx")
    ev.add_argument("reference")
    ev.add_argument("--max-cer", type=float, default=0.15)
    ev.set_defaults(func=cmd_eval)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Da huy.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
