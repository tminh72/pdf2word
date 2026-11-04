"""Adapter cho các pipeline có thể đem ra benchmark.

Mỗi pipeline chỉ cần làm một việc: nhận PDF, trả về một artifact
(``.docx``/``.md``/``.txt``/``.html``). Runner sẽ chuyển artifact về DOCX,
chấm bằng cùng bộ metric và ghi thời gian chạy.

Hai loại adapter:

* ``builtin``  – dùng chính package pdf2word (auto / digital / ocr).
* ``external`` – gọi command line của model/tool khác (Marker, Docling,
  MinerU, ...), cấu hình bằng template command trong JSON.
"""

from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metrics import ExpectedDoc

ARTIFACT_EXTS = {".docx", ".md", ".markdown", ".txt", ".text", ".html", ".htm"}


class PipelineError(RuntimeError):
    """Lỗi khi chạy pipeline (được runner ghi vào kết quả, không làm sập cả run)."""


@dataclass
class PipelineOutput:
    artifact: str
    seconds: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pipeline:
    name: str
    type: str = "builtin"  # builtin | external
    description: str = ""
    enabled: bool = True

    # builtin
    engine: str = "auto"
    polish: bool = True
    font: str | None = None
    tesseract: str | None = None
    tessdata: str | None = None

    # external
    command: list[str] = field(default_factory=list)
    output: str = ""
    artifact: str = "docx"
    artifact_glob: str = ""
    timeout: float = 1800.0
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""

    def convert(self, doc: ExpectedDoc, out_dir: str) -> PipelineOutput:
        if not self.enabled:
            raise PipelineError(f"Pipeline '{self.name}' đang tắt trong cấu hình")
        if self.type == "builtin":
            return self._convert_builtin(doc, out_dir)
        if self.type == "external":
            return self._convert_external(doc, out_dir)
        raise PipelineError(f"Loại pipeline không hỗ trợ: {self.type}")

    # ------------------------------------------------------------------
    # Built-in pdf2word
    # ------------------------------------------------------------------
    def _convert_builtin(self, doc: ExpectedDoc, out_dir: str) -> PipelineOutput:
        from pdf2word.service import ConvertRequest, convert_file

        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{doc.id}.docx")
        request = ConvertRequest(
            input=doc.pdf,
            output=out_path,
            engine=self.engine,
            polish=self.polish,
            font=self.font,
            tesseract=self.tesseract,
            tessdata=self.tessdata,
            images=True,
            tables=True,
        )
        started = time.perf_counter()
        result = convert_file(request)
        seconds = time.perf_counter() - started
        if result.cancelled or not os.path.isfile(out_path):
            raise PipelineError("pdf2word không tạo được DOCX (bị huỷ?)")
        return PipelineOutput(
            artifact=out_path,
            seconds=seconds,
            meta={
                "engine": result.engine,
                "pages": result.pages,
                "tables": result.tables,
                "headings": result.headings,
                "toc": result.toc,
                "images": result.images,
                "polish": result.meta.get("polish", {}),
                "pipeline_seconds": round(result.seconds, 3),
            },
        )

    # ------------------------------------------------------------------
    # External command
    # ------------------------------------------------------------------
    def _render(self, template: str, doc: ExpectedDoc, out_dir: str, output: str) -> str:
        values = {
            "input": doc.pdf,
            "output": output,
            "outdir": out_dir,
            "stem": doc.id,
            "doc_id": doc.id,
            "lang": "vi",
        }
        try:
            return template.format(**values)
        except KeyError as exc:
            raise PipelineError(f"Template command có biến lạ: {exc}") from exc

    def _convert_external(self, doc: ExpectedDoc, out_dir: str) -> PipelineOutput:
        if not self.command:
            raise PipelineError(f"Pipeline '{self.name}' thiếu 'command'")
        os.makedirs(out_dir, exist_ok=True)
        ext = self.artifact if self.artifact.startswith(".") else f".{self.artifact}"
        output = self._render(self.output or f"{doc.id}{ext}", doc, out_dir, "")
        if not os.path.isabs(output):
            output = os.path.join(out_dir, output)

        args = [self._render(arg, doc, out_dir, output) for arg in self.command]
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "NO_COLOR": "1",
                "PDF2WORD_BENCH_DOC": doc.id,
                "PDF2WORD_BENCH_INPUT": doc.pdf,
                "PDF2WORD_BENCH_OUTPUT": output,
                "PDF2WORD_BENCH_OUTDIR": out_dir,
            }
        )
        env.update({k: str(v) for k, v in self.env.items()})

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        startinfo = None
        if os.name == "nt":
            startinfo = subprocess.STARTUPINFO()
            startinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startinfo.wShowWindow = subprocess.SW_HIDE

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                args,
                cwd=self.cwd or out_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                creationflags=creationflags,
                startupinfo=startinfo,
            )
        except FileNotFoundError as exc:
            raise PipelineError(f"Không tìm thấy lệnh '{args[0]}': {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"Quá thời gian {self.timeout:g}s") from exc
        seconds = time.perf_counter() - started

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
            raise PipelineError(f"Lệnh trả mã {proc.returncode}. {tail}")

        artifact = self._find_artifact(output, out_dir)
        return PipelineOutput(
            artifact=artifact,
            seconds=seconds,
            meta={
                "command": args,
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-1000:],
                "stderr_tail": (proc.stderr or "")[-1000:],
            },
        )

    def _find_artifact(self, output: str, out_dir: str) -> str:
        if output and os.path.isfile(output):
            return output
        patterns: list[str] = []
        if self.artifact_glob:
            patterns.append(self.artifact_glob)
        ext = self.artifact if self.artifact.startswith(".") else f".{self.artifact}"
        patterns.extend([f"*{ext}", f"**/*{ext}"])
        for pattern in patterns:
            matches = [
                path
                for path in glob.glob(os.path.join(out_dir, pattern), recursive=True)
                if os.path.isfile(path) and Path(path).suffix.lower() in ARTIFACT_EXTS
            ]
            if matches:
                return max(matches, key=os.path.getmtime)
        raise PipelineError(
            f"Pipeline không tạo artifact. Mong đợi '{output}' hoặc '*{ext}' trong {out_dir}"
        )


def _list_of_strings(value, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value, posix=False)
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"'{field_name}' phải là chuỗi hoặc danh sách chuỗi")
    return list(value)


def pipeline_from_dict(raw: dict) -> Pipeline:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Mỗi pipeline phải có 'name'")
    kind = str(raw.get("type") or "builtin").strip().lower()
    if kind not in ("builtin", "external"):
        raise ValueError(f"Pipeline '{name}': type phải là 'builtin' hoặc 'external'")

    kwargs: dict[str, Any] = {
        "name": name,
        "type": kind,
        "description": str(raw.get("description") or ""),
        "enabled": bool(raw.get("enabled", True)),
    }
    if kind == "builtin":
        engine = str(raw.get("engine") or "auto").lower()
        if engine not in ("auto", "digital", "ocr", "deepdoc"):
            raise ValueError(f"Pipeline '{name}': engine phải là auto/digital/ocr/deepdoc")
        kwargs.update(
            engine=engine,
            polish=bool(raw.get("polish", True)),
            font=raw.get("font") or None,
            tesseract=raw.get("tesseract") or None,
            tessdata=raw.get("tessdata") or None,
        )
    else:
        command = _list_of_strings(raw.get("command"), f"{name}.command")
        if not command:
            raise ValueError(f"Pipeline '{name}': thiếu 'command'")
        artifact = str(raw.get("artifact") or "docx").lower().lstrip(".")
        if f".{artifact}" not in ARTIFACT_EXTS:
            raise ValueError(
                f"Pipeline '{name}': artifact phải là docx/md/txt/html, nhận '{artifact}'"
            )
        env = raw.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError(f"Pipeline '{name}': env phải là object")
        kwargs.update(
            command=command,
            output=str(raw.get("output") or ""),
            artifact=artifact,
            artifact_glob=str(raw.get("artifact_glob") or ""),
            timeout=float(raw.get("timeout") or 1800),
            env={str(k): str(v) for k, v in env.items()},
            cwd=str(raw.get("cwd") or ""),
        )
    return Pipeline(**kwargs)


def load_pipelines(config: str | Path | None = None) -> list[Pipeline]:
    """Đọc cấu hình pipeline. Không truyền config -> 3 pipeline built-in."""
    if config is None:
        return [
            Pipeline(name="pdf2word-auto", engine="auto", description="pdf2word tự chọn digital/OCR"),
            Pipeline(name="pdf2word-digital", engine="digital", description="pdf2word chỉ PDF số"),
            Pipeline(name="pdf2word-ocr", engine="ocr", description="pdf2word OCR toàn bộ"),
        ]

    path = Path(config).resolve()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    raw_list = data.get("pipelines") if isinstance(data, dict) else data
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError(f"{path}: 'pipelines' phải là danh sách không rỗng")

    pipelines: list[Pipeline] = []
    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: mỗi pipeline phải là object")
        pipeline = pipeline_from_dict(raw)
        if pipeline.name in seen:
            raise ValueError(f"{path}: tên pipeline bị trùng: {pipeline.name}")
        seen.add(pipeline.name)
        pipelines.append(pipeline)
    return pipelines
