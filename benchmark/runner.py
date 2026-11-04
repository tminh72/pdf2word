"""Chạy benchmark: dataset × pipeline -> artifact -> metric -> báo cáo."""

from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .dataset import BenchmarkDataset, load_dataset, validate_dataset
from .markdown import convert_artifact
from .metrics import DEFAULT_WEIGHTS, ExpectedDoc, score_docx
from .pipelines import Pipeline, PipelineError, load_pipelines

ProgressFn = Callable[[int, int, str, str], None]


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _md_cell(value, digits: int = 3) -> str:
    text = _fmt(value, digits)
    return text.replace("|", "\\|").replace("\n", " ")


def environment_info() -> dict:
    import pdf2word

    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "pdf2word": getattr(pdf2word, "__version__", "?"),
        "cpu_count": os.cpu_count(),
    }
    try:
        from pdf2word.extract.ocr import find_tesseract

        info["tesseract"] = find_tesseract()
    except Exception:  # noqa: BLE001 - không có Tesseract vẫn chạy được PDF số
        info["tesseract"] = shutil.which("tesseract")
    soffice = os.environ.get("PDF2WORD_SOFFICE") or shutil.which("soffice")
    if not soffice:
        for candidate in (
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ):
            if os.path.exists(candidate):
                soffice = candidate
                break
    info["libreoffice"] = soffice or None
    if info.get("tesseract"):
        try:
            import subprocess

            proc = subprocess.run(
                [info["tesseract"], "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            first = (proc.stdout or proc.stderr or "").splitlines()
            info["tesseract_version"] = first[0].strip() if first else None
        except Exception:  # noqa: BLE001
            info["tesseract_version"] = None
    return info


def _select_docs(
    dataset: BenchmarkDataset,
    doc_ids: list[str] | None,
    tags: list[str] | None,
    limit: int | None,
) -> list[ExpectedDoc]:
    docs = list(dataset.docs)
    if doc_ids:
        wanted = [x.strip() for x in doc_ids if x.strip()]
        missing = [x for x in wanted if x not in {d.id for d in docs}]
        if missing:
            raise ValueError(f"Không có tài liệu: {', '.join(missing)}")
        order = {doc_id: i for i, doc_id in enumerate(wanted)}
        docs = sorted((d for d in docs if d.id in order), key=lambda d: order[d.id])
    if tags:
        wanted_tags = {t.strip().lower() for t in tags if t.strip()}
        docs = [d for d in docs if wanted_tags & {t.lower() for t in d.tags}]
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit phải > 0")
        docs = docs[:limit]
    return docs


def _aggregate(rows: list[dict], pipelines: list[Pipeline]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for pipeline in pipelines:
        items = [r for r in rows if r["pipeline"] == pipeline.name]
        ok = [r for r in items if r["status"] == "ok"]
        metrics = [r["metrics"] for r in ok]
        composites = [m["composite"] for m in metrics if m.get("composite") is not None]
        seconds = sum(float(r.get("seconds") or 0.0) for r in ok)
        pages = sum(int(m.get("pages") or 0) for m in metrics)
        out[pipeline.name] = {
            "enabled": pipeline.enabled,
            "type": pipeline.type,
            "description": pipeline.description,
            "docs_total": len(items),
            "docs_ok": len(ok),
            "docs_error": len(items) - len(ok),
            "composite": _mean(composites),
            "cer": _mean([m["cer"] for m in metrics if m.get("cer") is not None]),
            "wer": _mean([m["wer"] for m in metrics if m.get("wer") is not None]),
            "tables": _mean([m["tables"] for m in metrics if m.get("tables") is not None]),
            "headings_f1": _mean(
                [m["headings_f1"] for m in metrics if m.get("headings_f1") is not None]
            ),
            "toc_f1": _mean([m["toc_f1"] for m in metrics if m.get("toc_f1") is not None]),
            "header_footer": _mean(
                [m["header_footer"] for m in metrics if m.get("header_footer") is not None]
            ),
            "seconds": round(seconds, 3),
            "seconds_per_doc": round(seconds / len(ok), 3) if ok else None,
            "pages_per_second": round(pages / seconds, 3) if seconds > 0 else None,
        }
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "pipeline",
        "doc",
        "kind",
        "status",
        "composite",
        "cer",
        "wer",
        "tables",
        "headings_f1",
        "toc_f1",
        "header_footer",
        "seconds",
        "pages",
        "docx_bytes",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            m = row.get("metrics") or {}
            writer.writerow(
                {
                    "pipeline": row["pipeline"],
                    "doc": row["doc"],
                    "kind": row.get("kind", ""),
                    "status": row["status"],
                    "composite": m.get("composite"),
                    "cer": m.get("cer"),
                    "wer": m.get("wer"),
                    "tables": m.get("tables"),
                    "headings_f1": m.get("headings_f1"),
                    "toc_f1": m.get("toc_f1"),
                    "header_footer": m.get("header_footer"),
                    "seconds": row.get("seconds"),
                    "pages": m.get("pages"),
                    "docx_bytes": m.get("docx_bytes"),
                    "error": row.get("error") or "",
                }
            )


def _write_report(path: Path, result: dict) -> None:
    run = result["run"]
    dataset = result["dataset"]
    aggregates = result["aggregates"]
    rows = result["rows"]

    lines: list[str] = []
    lines.append(f"# Benchmark PDF → DOCX: {dataset['name']}")
    lines.append("")
    lines.append(f"- Run: `{run['id']}` — {run['created_at']}")
    lines.append(f"- Dataset: **{dataset['name']}** v{dataset['version']} "
                 f"({len(dataset['docs'])} tài liệu, ngôn ngữ `{dataset['language']}`)")
    lines.append(
        f"- Môi trường: Python {run['environment']['python']} · "
        f"{run['environment']['platform']} · pdf2word {run['environment']['pdf2word']}"
    )
    if run["environment"].get("tesseract"):
        lines.append(f"- Tesseract: {run['environment'].get('tesseract_version') or run['environment']['tesseract']}")
    lines.append("")
    lines.append("## Tổng hợp theo pipeline")
    lines.append("")
    lines.append(
        "| Pipeline | Docs OK | Composite ↑ | CER ↓ | WER ↓ | Bảng ↑ | Heading F1 ↑ | "
        "TOC F1 ↑ | Header/Footer ↑ | s/doc ↓ |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, agg in aggregates.items():
        lines.append(
            "| {name} | {ok}/{total} | {comp} | {cer} | {wer} | {tables} | {head} | "
            "{toc} | {hf} | {spd} |".format(
                name=name,
                ok=agg["docs_ok"],
                total=agg["docs_total"],
                comp=_md_cell(agg["composite"]),
                cer=_md_cell(agg["cer"]),
                wer=_md_cell(agg["wer"]),
                tables=_md_cell(agg["tables"]),
                head=_md_cell(agg["headings_f1"]),
                toc=_md_cell(agg["toc_f1"]),
                hf=_md_cell(agg["header_footer"]),
                spd=_md_cell(agg["seconds_per_doc"], 2),
            )
        )
    lines.append("")
    lines.append(
        "Composite = trung bình có trọng số của text (1-CER), bảng, heading, TOC, "
        "header/footer; metric không áp dụng cho tài liệu được bỏ qua. "
        "Điểm càng cao càng tốt; CER/WER càng thấp càng tốt."
    )
    lines.append("")

    for name, agg in aggregates.items():
        lines.append(f"## {name}")
        if agg["description"]:
            lines.append("")
            lines.append(agg["description"])
        lines.append("")
        lines.append("| Tài liệu | Loại | CER | WER | Bảng | Heading F1 | TOC F1 | H/F | Composite | Giây |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in [r for r in rows if r["pipeline"] == name]:
            if row["status"] != "ok":
                lines.append(
                    f"| {row['doc']} | {row.get('kind', '')} | — | — | — | — | — | — | "
                    f"**LỖI** | — |"
                )
                continue
            m = row["metrics"]
            lines.append(
                "| {doc} | {kind} | {cer} | {wer} | {tables} | {head} | {toc} | {hf} | "
                "{comp} | {sec} |".format(
                    doc=row["doc"],
                    kind=row.get("kind", ""),
                    cer=_md_cell(m.get("cer")),
                    wer=_md_cell(m.get("wer")),
                    tables=_md_cell(m.get("tables")),
                    head=_md_cell(m.get("headings_f1")),
                    toc=_md_cell(m.get("toc_f1")),
                    hf=_md_cell(m.get("header_footer")),
                    comp=_md_cell(m.get("composite")),
                    sec=_md_cell(row.get("seconds"), 2),
                )
            )
        lines.append("")
        errors = [r for r in rows if r["pipeline"] == name and r["status"] != "ok"]
        if errors:
            lines.append("**Lỗi:**")
            lines.append("")
            for row in errors:
                lines.append(f"- `{row['doc']}`: {row.get('error')}")
            lines.append("")

    lines.append("## Ghi chú")
    lines.append("")
    lines.append("- Dataset là dữ liệu tổng hợp, không phải dữ liệu khách hàng thật.")
    lines.append("- Điểm bảng dựa trên cấu trúc ô/hàng/cột, không chấm định dạng (màu, viền).")
    lines.append("- Pipeline ngoài cần được cài đặt sẵn và khai báo trong config; xem `benchmark/configs/`.")
    lines.append("")
    lines.append(f"Kết quả thô: `{run['results_json']}` · CSV: `{run['summary_csv']}`")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def run_benchmark(
    dataset: str | Path | BenchmarkDataset,
    config: str | Path | None = None,
    out_dir: str | Path = "benchmark/results",
    run_id: str | None = None,
    doc_ids: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    reuse: bool = False,
    weights: dict[str, float] | None = None,
    pipeline_names: list[str] | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """Chạy benchmark và trả về dict kết quả (đồng thời ghi JSON/MD/CSV)."""
    dataset_obj = dataset if isinstance(dataset, BenchmarkDataset) else load_dataset(dataset)
    errors = validate_dataset(dataset_obj)
    if errors:
        raise ValueError("Dataset không hợp lệ:\n" + "\n".join(f"- {e}" for e in errors))

    pipelines = load_pipelines(config)
    if pipeline_names:
        wanted = [x.strip() for x in pipeline_names if x.strip()]
        missing = [x for x in wanted if x not in {p.name for p in pipelines}]
        if missing:
            raise ValueError(f"Không có pipeline: {', '.join(missing)}")
        order = {name: i for i, name in enumerate(wanted)}
        pipelines = sorted((p for p in pipelines if p.name in order), key=lambda p: order[p.name])
    enabled = [p for p in pipelines if p.enabled]
    if not enabled:
        raise ValueError("Không có pipeline nào được bật trong config")
    docs = _select_docs(dataset_obj, doc_ids, tags, limit)
    if not docs:
        raise ValueError("Không có tài liệu nào khớp bộ lọc")

    run_id = run_id or _now_id()
    run_dir = Path(out_dir).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    rows: list[dict] = []
    total = len(enabled) * len(docs)
    step = 0
    started_at = datetime.now(timezone.utc).isoformat()

    for pipeline in enabled:
        pipe_dir = artifacts_dir / pipeline.name
        pipe_dir.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            step += 1
            if progress:
                progress(step, total, pipeline.name, doc.id)

            target = pipe_dir / f"{doc.id}.docx"
            sidecar = pipe_dir / f"{doc.id}.json"
            row: dict = {
                "pipeline": pipeline.name,
                "doc": doc.id,
                "kind": doc.kind,
                "status": "ok",
                "artifact": str(target),
                "raw_artifact": "",
                "seconds": None,
                "metrics": None,
                "meta": {},
                "error": None,
            }
            try:
                if reuse and not force and target.is_file():
                    meta: dict = {}
                    seconds = 0.0
                    raw = ""
                    if sidecar.is_file():
                        try:
                            saved = json.loads(sidecar.read_text(encoding="utf-8"))
                            seconds = float(saved.get("seconds") or 0.0)
                            meta = saved.get("meta") or {}
                            raw = str(saved.get("raw_artifact") or "")
                        except (OSError, ValueError):
                            pass
                    row["seconds"] = seconds
                    row["meta"] = meta
                    row["raw_artifact"] = raw
                    row["reused"] = True
                else:
                    if target.exists():
                        target.unlink()
                    if sidecar.exists():
                        sidecar.unlink()
                    started = time.perf_counter()
                    output = pipeline.convert(doc, str(pipe_dir))
                    raw_artifact = output.artifact
                    if Path(raw_artifact).suffix.lower() != ".docx":
                        convert_artifact(raw_artifact, str(target))
                    elif Path(raw_artifact).resolve() != target.resolve():
                        shutil.copy2(raw_artifact, target)
                    elapsed = time.perf_counter() - started
                    row["seconds"] = round(elapsed, 3)
                    row["raw_artifact"] = str(raw_artifact)
                    row["meta"] = output.meta
                    row["reused"] = False
                    sidecar.write_text(
                        json.dumps(
                            {
                                "seconds": row["seconds"],
                                "raw_artifact": row["raw_artifact"],
                                "meta": output.meta,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

                if not target.is_file():
                    raise PipelineError(f"Không có DOCX để chấm: {target}")
                metrics = score_docx(
                    str(target),
                    doc,
                    seconds=float(row["seconds"] or 0.0),
                )
                if weights:
                    from .metrics import composite

                    metrics["composite"] = composite(metrics, weights)
                row["metrics"] = metrics
            except Exception as exc:  # noqa: BLE001 - một pipeline lỗi không dừng cả run
                row["status"] = "error"
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)

    aggregates = _aggregate(rows, enabled)

    result: dict = {
        "run": {
            "id": run_id,
            "created_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "out_dir": str(run_dir),
            "config": str(config) if config else None,
            "environment": environment_info(),
            "weights": weights or DEFAULT_WEIGHTS,
        },
        "dataset": {
            "name": dataset_obj.name,
            "version": dataset_obj.version,
            "path": str(dataset_obj.path),
            "license": dataset_obj.license,
            "language": dataset_obj.language,
            "docs": [
                {
                    "id": d.id,
                    "pdf": d.pdf,
                    "kind": d.kind,
                    "pages": d.pages,
                    "tags": d.tags,
                }
                for d in docs
            ],
        },
        "pipelines": [
            {
                "name": p.name,
                "type": p.type,
                "description": p.description,
                "engine": p.engine,
                "polish": p.polish,
                "command": p.command,
            }
            for p in enabled
        ],
        "aggregates": aggregates,
        "rows": rows,
    }

    json_path = run_dir / "results.json"
    csv_path = run_dir / "summary.csv"
    md_path = run_dir / "report.md"
    result["run"]["results_json"] = str(json_path)
    result["run"]["summary_csv"] = str(csv_path)
    result["run"]["report_md"] = str(md_path)

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    _write_report(md_path, result)
    return result
