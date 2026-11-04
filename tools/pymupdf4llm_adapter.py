"""Adapter mẫu cho pipeline ngoài: PDF → Markdown bằng PyMuPDF4LLM.

Chỉ dùng khi bạn muốn thử một model/tool khác ngoài pdf2word. Cài trước:

    pip install pymupdf4llm

Sau đó bật pipeline ``pymupdf4llm`` trong
``benchmark/configs/external.example.json`` (hoặc copy config và sửa đường dẫn).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Dùng: python tools/pymupdf4llm_adapter.py <input.pdf> <output.md>", file=sys.stderr)
        return 2
    try:
        import pymupdf4llm
    except ImportError:
        print("Thiếu pymupdf4llm. Cài bằng: pip install pymupdf4llm", file=sys.stderr)
        return 3

    source = Path(argv[1])
    dest = Path(argv[2])
    if not source.is_file():
        print(f"Không thấy PDF: {source}", file=sys.stderr)
        return 4
    markdown = pymupdf4llm.to_markdown(str(source))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
