"""Cho phép chạy ``python -m benchmark``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
