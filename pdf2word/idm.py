"""IDM - Intermediate Document Model.

Mọi engine (digital extractor, OCR, fallback) đều phải đổ về cấu trúc này.
Renderer DOCX chỉ đọc IDM, không biết gì về PDF.

Toạ độ: đơn vị point (1/72 inch), gốc dưới-trái (giống hệ toạ độ PDF).
Khi ghi DOCX sẽ đổi sang gốc trên-trái nếu cần.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1)
Color = tuple[int, int, int]

BlockKind = Literal[
    "paragraph", "heading", "title", "list", "table", "image", "toc", "header", "footer"
]
BlockAlign = Literal["left", "center", "right", "justify"]


@dataclass
class Span:
    """Một đoạn text liền mạch có cùng style."""

    text: str
    font: str = "Times New Roman"
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: Color = (0, 0, 0)

    @property
    def is_plain(self) -> bool:
        return not (self.bold or self.italic or self.underline) and self.color == (0, 0, 0)


@dataclass
class Cell:
    """Một ô của bảng. lines = danh sách dòng, mỗi dòng là danh sách span."""

    lines: list[list[Span]] = field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    covered: bool = False  # True nếu ô bị ô khác merge đè lên
    align: BlockAlign = "left"

    @property
    def text(self) -> str:
        return "\n".join("".join(s.text for s in line) for line in self.lines).strip()


@dataclass
class Table:
    n_rows: int
    n_cols: int
    col_widths: list[float]  # point
    cells: list[list[Cell]]
    header_rows: int = 0


@dataclass
class ImageRef:
    path: str  # file ảnh đã tách (png/jpg)
    width: float  # point, kích thước hiển thị
    height: float


@dataclass
class Block:
    kind: BlockKind = "paragraph"
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    spans: list[Span] = field(default_factory=list)
    level: int = 0  # heading level 1..9
    align: BlockAlign = "left"
    indent: float = 0.0  # thụt lề trái (point)
    first_line_indent: float = 0.0
    gap_before: float = 0.0  # khoảng trống phía trên (point)
    ordered: bool = False  # list có đánh số
    marker: str = ""  # "1." / "•" ...
    table: Table | None = None
    image: ImageRef | None = None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    def dominant_size(self) -> float:
        if not self.spans:
            return 0.0
        counts: dict[float, int] = {}
        for s in self.spans:
            counts[round(s.size, 1)] = counts.get(round(s.size, 1), 0) + len(s.text)
        return max(counts, key=counts.get)

    def bold_ratio(self) -> float:
        total = sum(len(s.text) for s in self.spans) or 1
        bold = sum(len(s.text) for s in self.spans if s.bold)
        return bold / total


@dataclass
class Page:
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    rotation: int = 0


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    # Thông số dàn trang suy ra từ nội dung, dùng cho renderer
    margin_left: float = 56.7
    margin_right: float = 56.7
    margin_top: float = 56.7
    margin_bottom: float = 56.7
    body_size: float = 11.0
    body_font: str = "Times New Roman"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def iter_blocks(doc: Document):
    for page in doc.pages:
        yield from page.blocks


def doc_text(doc: Document, include_tables: bool = True) -> str:
    """Trích text thuần để chấm điểm / debug."""
    out: list[str] = []
    for block in iter_blocks(doc):
        if block.kind == "table":
            if include_tables and block.table:
                for row in block.table.cells:
                    for cell in row:
                        if not cell.covered:
                            t = cell.text.replace("\n", " ")
                            if t:
                                out.append(t)
            continue
        if block.kind == "image":
            continue
        if block.text.strip():
            out.append(block.text)
    return "\n".join(out)
