"""CLI benchmark: ``python -m benchmark ...``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dataset import load_dataset, validate_dataset
from .runner import run_benchmark

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "benchmark" / "data" / "vi_core" / "manifest.json"
DEFAULT_CONFIG = ROOT / "benchmark" / "configs" / "builtin.json"
DEFAULT_OUT = ROOT / "benchmark" / "results"


def _split_csv(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out or None


def cmd_run(args: argparse.Namespace) -> int:
    def progress(step: int, total: int, pipeline: str, doc: str) -> None:
        print(f"[{step}/{total}] {pipeline} × {doc}", flush=True)

    weights = None
    if args.weights:
        try:
            weights = json.loads(args.weights)
        except json.JSONDecodeError as exc:
            print(f"Lỗi --weights: {exc}", file=sys.stderr)
            return 2
        if not isinstance(weights, dict):
            print("Lỗi --weights: phải là JSON object", file=sys.stderr)
            return 2

    try:
        result = run_benchmark(
            dataset=args.data,
            config=args.config,
            out_dir=args.out,
            run_id=args.run_id,
            doc_ids=_split_csv(args.docs),
            tags=_split_csv(args.tags),
            limit=args.limit,
            force=args.force,
            reuse=args.reuse,
            weights=weights,
            pipeline_names=_split_csv(args.pipeline),
            progress=progress,
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2

    aggregates = result["aggregates"]
    print("\nTổng hợp:")
    for name, agg in aggregates.items():
        comp = "—" if agg["composite"] is None else f"{agg['composite']:.3f}"
        print(
            f"  {name:24s} {agg['docs_ok']}/{agg['docs_total']} docs · "
            f"composite {comp} · CER {_pct(agg['cer'])} · {_sec(agg['seconds_per_doc'])}"
        )
    print(f"\nBáo cáo: {result['run']['report_md']}")
    print(f"JSON:    {result['run']['results_json']}")
    print(f"CSV:     {result['run']['summary_csv']}")
    total_errors = sum(a["docs_error"] for a in aggregates.values())
    if total_errors:
        print(f"Có {total_errors} lượt chạy lỗi; xem report.md.")
    return 0 if any(a["docs_ok"] for a in aggregates.values()) else 1


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _sec(value) -> str:
    return "—" if value is None else f"{value:.2f}s/doc"


def cmd_list(args: argparse.Namespace) -> int:
    try:
        dataset = load_dataset(args.data)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    print(f"Dataset: {dataset.name} v{dataset.version} ({dataset.language})")
    if dataset.description:
        print(dataset.description)
    print(f"{'ID':28s} {'Kiểu':8s} {'Trang':>5s}  Tags")
    for doc in dataset.docs:
        print(f"{doc.id:28s} {doc.kind:8s} {doc.pages:5d}  {', '.join(doc.tags)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        dataset = load_dataset(args.data)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    errors = validate_dataset(dataset)
    if errors:
        print(f"Dataset KHÔNG hợp lệ ({len(errors)} lỗi):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        f"OK: {dataset.name} v{dataset.version} · {len(dataset.docs)} tài liệu · "
        f"{sum(d.pages for d in dataset.docs)} trang"
    )
    return 0


def cmd_make_data(args: argparse.Namespace) -> int:
    from .make_data import build_dataset

    try:
        build_dataset(args.out, force=args.force)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    print(f"Đã tạo dataset tại: {Path(args.out).resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmark",
        description="Benchmark chuyển PDF tiếng Việt sang DOCX (offline, CPU).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Chạy benchmark và xuất báo cáo")
    run.add_argument("--data", default=str(DEFAULT_DATA), help="Manifest hoặc thư mục dataset")
    run.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config pipeline JSON")
    run.add_argument("--out", default=str(DEFAULT_OUT), help="Thư mục kết quả")
    run.add_argument("--run-id", help="Tên lần chạy (mặc định theo thời gian)")
    run.add_argument("--docs", action="append", help="ID tài liệu, phân tách bằng dấu phẩy")
    run.add_argument("--tags", action="append", help="Lọc theo tag, phân tách bằng dấu phẩy")
    run.add_argument("--pipeline", action="append", help="Tên pipeline cần chạy")
    run.add_argument("--limit", type=int, help="Giới hạn số tài liệu")
    run.add_argument("--reuse", action="store_true", help="Tái dùng DOCX đã có")
    run.add_argument("--force", action="store_true", help="Chạy lại, ghi đè kết quả cũ")
    run.add_argument("--weights", help="JSON trọng số composite, ví dụ '{\"text\":0.5}'")
    run.set_defaults(func=cmd_run)

    ls = sub.add_parser("list", help="Liệt kê tài liệu trong dataset")
    ls.add_argument("--data", default=str(DEFAULT_DATA))
    ls.set_defaults(func=cmd_list)

    val = sub.add_parser("validate", help="Kiểm tra dataset")
    val.add_argument("--data", default=str(DEFAULT_DATA))
    val.set_defaults(func=cmd_validate)

    mk = sub.add_parser("make-data", help="Sinh lại dataset tiếng Việt mẫu")
    mk.add_argument("--out", default=str(DEFAULT_DATA.parent))
    mk.add_argument("--force", action="store_true", help="Ghi đè dataset đã có")
    mk.set_defaults(func=cmd_make_data)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
