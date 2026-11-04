"""Dựng lưới ô bảng từ kết quả TSR (tsr.onnx).

TSR trả về các hộp ``table row`` / ``table column`` / ``table column header``
và ``table spanning cell``. Module này đổi chúng thành ``RawGrid`` (cùng dạng
với lưới dò từ đường kẻ) để phần dựng bảng dùng chung một đường.
"""

from __future__ import annotations

from .models import TSR_LABELS  # noqa: F401  (giữ import cho tài liệu)
from ..grid import RawGrid


def grid_from_tsr(
    dets: list[dict],
    crop_origin: tuple[int, int],
    page_h: float,
    dpi: int,
) -> tuple[RawGrid, int] | None:
    """Chuyển detection TSR (toạ độ crop, gốc trên-trái) sang RawGrid (point)."""
    scale = 72.0 / dpi
    ox, oy = crop_origin

    def to_idm(box: list[float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = box
        return (
            (ox + x0) * scale,
            page_h - (oy + y1) * scale,
            (ox + x1) * scale,
            page_h - (oy + y0) * scale,
        )

    rows = [to_idm(d["bbox"]) for d in dets if d["label"] == "table row"]
    headers = [
        to_idm(d["bbox"])
        for d in dets
        if d["label"] in ("table column header", "table projected row header")
    ]
    columns = [to_idm(d["bbox"]) for d in dets if d["label"] == "table column"]
    spans = [
        to_idm(d["bbox"])
        for d in dets
        if d["label"] in ("table spanning cell", "table projected row header")
    ]

    if len(rows) < 1:
        rows = list(headers)
    row_bands = _merge_bands(rows)
    col_bands = _merge_columns(columns)
    if not row_bands or len(col_bands) < 2:
        return None

    row_edges = _row_edges(row_bands)
    col_edges = _col_edges(col_bands)
    if row_edges is None or col_edges is None:
        return None

    n_rows = len(row_edges) - 1
    n_cols = len(col_edges) - 1

    grid = RawGrid(n_rows=n_rows, n_cols=n_cols, rows=row_edges, cols=col_edges)
    for r in range(n_rows):
        for c in range(n_cols):
            grid.spans[(r, c)] = (1, 1)
            grid.anchor_of[(r, c)] = (r, c)

    # Ô gộp: xác định các hàng/cột bị hộp spanning cell phủ.
    for box in spans:
        covered_rows = [
            r
            for r in range(n_rows)
            if _inside((row_edges[r] + row_edges[r + 1]) / 2, box[1], box[3])
        ]
        covered_cols = [
            c
            for c in range(n_cols)
            if _inside((col_edges[c] + col_edges[c + 1]) / 2, box[0], box[2])
        ]
        if len(covered_rows) < 2 and len(covered_cols) < 2:
            continue
        if not covered_rows or not covered_cols:
            continue
        r0, r1 = min(covered_rows), max(covered_rows)
        c0, c1 = min(covered_cols), max(covered_cols)
        anchor = (r0, c0)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid.spans.pop((r, c), None)
                grid.anchor_of[(r, c)] = anchor
                if (r, c) != anchor:
                    grid.covered.add((r, c))
        grid.spans[anchor] = (r1 - r0 + 1, c1 - c0 + 1)

    header_rows = 0
    for r in range(n_rows):
        center = (row_edges[r] + row_edges[r + 1]) / 2
        if any(_inside(center, box[1], box[3]) for box in headers):
            header_rows += 1
        elif header_rows:
            break
    return grid, header_rows


def grid_from_rules(raw: RawGrid) -> tuple[RawGrid, int]:
    """Lưới dò từ đường kẻ đã có sẵn anchor; header suy sau khi có chữ."""
    for r in range(raw.n_rows):
        for c in range(raw.n_cols):
            if (r, c) not in raw.anchor_of:
                raw.anchor_of[(r, c)] = (r, c)
                raw.spans.setdefault((r, c), (1, 1))
    return raw, 0


def _inside(value: float, lo: float, hi: float) -> bool:
    return lo - 2 <= value <= hi + 2


def _merge_bands(rects: list[tuple[float, float, float, float]]):
    rects = sorted(rects, key=lambda r: r[3], reverse=True)
    merged: list[list[float]] = []
    for x0, y0, x1, y1 in rects:
        # Chỉ gộp khi hai hộp chồng lên nhau đáng kể; hai hàng kề nhau (chạm
        # biên hoặc chồng vài point do model) phải được giữ riêng.
        if merged:
            m = merged[-1]
            overlap = y1 - m[1]
            min_h = min(m[3] - m[1], y1 - y0)
            if overlap > max(2.0, 0.3 * min_h):
                m[0] = min(m[0], x0)
                m[1] = min(m[1], y0)
                m[2] = max(m[2], x1)
                m[3] = max(m[3], y1)
                continue
        merged.append([x0, y0, x1, y1])
    return merged


def _merge_columns(rects: list[tuple[float, float, float, float]]):
    rects = sorted(rects, key=lambda r: r[0])
    merged: list[list[float]] = []
    for x0, y0, x1, y1 in rects:
        if merged:
            m = merged[-1]
            overlap = m[2] - x0
            min_w = min(m[2] - m[0], x1 - x0)
            if overlap > max(2.0, 0.3 * min_w):
                m[0] = min(m[0], x0)
                m[1] = min(m[1], y0)
                m[2] = max(m[2], x1)
                m[3] = max(m[3], y1)
                continue
        merged.append([x0, y0, x1, y1])
    return merged


def _row_edges(bands: list[list[float]]) -> list[float] | None:
    edges = [bands[0][3]]
    for prev, cur in zip(bands, bands[1:]):
        edges.append((prev[1] + cur[3]) / 2)
    edges.append(bands[-1][1])
    for a, b in zip(edges, edges[1:]):
        if a - b < 1.0:
            return None
    return edges


def _col_edges(bands: list[list[float]]) -> list[float] | None:
    edges = [bands[0][0]]
    for prev, cur in zip(bands, bands[1:]):
        edges.append((prev[2] + cur[0]) / 2)
    edges.append(bands[-1][2])
    for a, b in zip(edges, edges[1:]):
        if b - a < 1.0:
            return None
    return edges


__all__ = ["grid_from_rules", "grid_from_tsr"]
