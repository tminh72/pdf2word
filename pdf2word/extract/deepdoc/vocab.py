"""Từ vựng VietOCR dùng cho decoder ONNX.

Decoder trả về 233 lớp: 0=<pad>, 1=<sos>, 2=<eos>, 3=<mask>, 4..232 là 229
ký tự tiếng Việt/ASCII trong ``models/deepdoc/vocab.txt``. Thứ tự này lấy từ
``vietocr/config/base.yml`` của upstream.
"""

from __future__ import annotations

from pathlib import Path

PAD, SOS, EOS, MASK = 0, 1, 2, 3


class VietVocab:
    def __init__(self, chars: str):
        if not chars:
            raise ValueError("Vocab rỗng")
        self.chars = chars
        self.c2i = {c: i + 4 for i, c in enumerate(chars)}
        self.i2c = {i + 4: c for i, c in enumerate(chars)}

    @classmethod
    def from_file(cls, path: str | Path) -> "VietVocab":
        text = Path(path).read_text(encoding="utf-8")
        # File có thể kết thúc bằng newline do editor; chỉ bỏ newline cuối.
        return cls(text.rstrip("\r\n"))

    def __len__(self) -> int:
        return len(self.chars) + 4

    def decode(self, ids: list[int] | tuple[int, ...]) -> str:
        ids = list(ids)
        start = 1 if SOS in ids else 0
        end = ids.index(EOS) if EOS in ids else len(ids)
        return "".join(self.i2c[i] for i in ids[start:end] if i >= 4)
