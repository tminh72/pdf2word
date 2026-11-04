"""Nhận diện bảng trong PDF số dựa trên đường kẻ (ruling lines).

Hai kiểu vẽ bảng phổ biến:
  1. Word/LibreOffice: mỗi cạnh là 1 line hoặc rect stroke rỗng.
  2. Excel export: mỗi cạnh là 1 rect tô mỏng (fill, cao ~0.5-2pt).

Hình học (grid, merge ô) nằm ở `grid.py`, dùng chung với OCR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..idm import BBox, Cell, Table
from .grid import TOL, RawGrid, find_grids, table_is_plausible
from .text import Char, TextLine, chars_to_spans


@dataclass
class TableHit:
    table: Table
    bbox: BBox
    used_uids: set[int] = field(default_factory=set)


def extract_segments(curves) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Trả về (horizontals, verticals) dạng (y, x0, x1) và (x, y0, y1)."""
    hs: list[tuple[float, float, float]] = []
    vs: list[tuple[float, float, float]] = []

    for c in curves:
        x0, y0, x1, y1 = c.bbox
        w, h = x1 - x0, y1 - y0
        stroke = bool(getattr(c, "stroke", False))
        fill = bool(getattr(c, "fill", False))
        pts = getattr(c, "pts", None) or []

        if stroke and len(pts) == 2:  # LTLine
            (px0, py0), (px1, py1) = pts
            if abs(py1 - py0) <= TOL and abs(px1 - px0) >= 3:
                hs.append(((py0 + py1) / 2, min(px0, px1), max(px0, px1)))
                continue
            if abs(px1 - px0) <= TOL and abs(py1 - py0) >= 3:
                vs.append(((px0 + px1) / 2, min(py0, py1), max(py0, py1)))
                continue

        if h <= TOL and w >= 3 and (stroke or fill):  # rect tô mỏng
            hs.append(((y0 + y1) / 2, x0, x1))
            continue
        if w <= TOL and h >= 3 and (stroke or fill):
            vs.append(((x0 + x1) / 2, y0, y1))
            continue

        if stroke and w >= 3 and h >= 3:  # rect stroke rỗng -> 4 cạnh
            hs.append((y0, x0, x1))
            hs.append((y1, x0, x1))
            vs.append((x0, y0, y1))
            vs.append((x1, y0, y1))

    return hs, vs


def detect_tables(lines: list[TextLine], curves, page_w: float, page_h: float) -> list[TableHit]:
    hs_raw, vs_raw = extract_segments(curves)
    grids = find_grids(hs_raw, vs_raw)
    hits: list[TableHit] = []
    for grid in grids:
        hit = table_from_grid(grid, lines, page_w * page_h)
        if hit:
            hits.append(hit)
    return hits


def table_from_grid(grid: RawGrid, lines: list[TextLine], page_area: float = 0.0) -> TableHit | None:
    """Dựng Table từ RawGrid + gán ký tự vào ô."""
    bbox = grid.bbox
    if grid.n_cols < 2 or (bbox[2] - bbox[0]) < 40 or (bbox[3] - bbox[1]) < 12:
        return None

    cells: list[list[Cell]] = [[Cell(covered=True) for _ in range(grid.n_cols)] for _ in range(grid.n_rows)]
    for (r, c), (rs, cs) in grid.spans.items():
        cells[r][c] = Cell(rowspan=rs, colspan=cs)

    used: set[int] = set()
    per_cell: dict[tuple[int, int], list[Char]] = {}

    for line in lines:
        cx = (line.bbox[0] + line.bbox[2]) / 2
        cy = (line.bbox[1] + line.bbox[3]) / 2
        if not (bbox[0] - 2 <= cx <= bbox[2] + 2 and bbox[1] - 2 <= cy <= bbox[3] + 2):
            continue
        for ch in line.chars:
            if not (bbox[0] - 1 <= ch.cx <= bbox[2] + 1 and bbox[1] - 1 <= ch.cy <= bbox[3] + 1):
                continue
            anchor = grid.anchor_at(ch.cx, ch.cy)
            if anchor is None:
                continue
            per_cell.setdefault(anchor, []).append(ch)
            if ch.uid >= 0:
                used.add(ch.uid)

    text_cells = 0
    for anchor, chars in per_cell.items():
        cell = cells[anchor[0]][anchor[1]]
        cell.lines = chars_to_lines(chars)
        if cell.text:
            text_cells += 1

    if text_cells == 0:
        return None
    if page_area and not table_is_plausible(grid, text_cells, page_area):
        return None

    table = Table(
        n_rows=grid.n_rows,
        n_cols=grid.n_cols,
        col_widths=grid.col_widths,
        cells=cells,
    )
    if looks_like_header(cells):
        table.header_rows = 1
    return TableHit(table=table, bbox=bbox, used_uids=used)


def chars_to_lines(chars: list[Char]) -> list[list]:
    if not chars:
        return []
    chars.sort(key=lambda c: (-c.cy, c.x0))
    rows: list[list[Char]] = []
    for ch in chars:
        if rows and abs(rows[-1][0].cy - ch.cy) <= max(2.0, 0.35 * ch.size):
            rows[-1].append(ch)
        else:
            rows.append([ch])
    out = []
    for row in rows:
        row.sort(key=lambda c: c.x0)
        spans = chars_to_spans(row)
        if spans:
            out.append(spans)
    return out


def looks_like_header(cells: list[list[Cell]]) -> bool:
    if len(cells) < 2:
        return False
    first = [c for c in cells[0] if not c.covered and c.text]
    rest = [c for row in cells[1:] for c in row if not c.covered and c.text]
    if not first or not rest:
        return False
    bold_first = _bold_ratio(first)
    bold_rest = _bold_ratio(rest)
    return bold_first > 0.7 and bold_first > bold_rest + 0.2


def _bold_ratio(cells: list[Cell]) -> float:
    total = bold = 0
    for cell in cells:
        for line in cell.lines:
            for span in line:
                total += len(span.text)
                if span.bold:
                    bold += len(span.text)
    return bold / total if total else 0.0
