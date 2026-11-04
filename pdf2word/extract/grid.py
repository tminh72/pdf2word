"""Hình học bảng dùng chung cho cả PDF số và PDF scan.

Đầu vào là các segment ngang/dọc đã trích từ vector (pdfminer) hoặc từ ảnh
(numpy). Đầu ra là lưới ô kèm thông tin merge (rowspan/colspan).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..idm import BBox

TOL = 2.0


def merge_segments(
    segs: list[tuple[float, float, float]], tol: float = TOL
) -> list[tuple[float, list[tuple[float, float]]]]:
    """Gom segment cùng toạ độ (y với ngang, x với dọc) và nối các đoạn rời."""
    if not segs:
        return []
    segs = sorted(segs)
    groups: list[list] = []
    for pos, a0, a1 in segs:
        if groups:
            g = groups[-1]
            if abs(g[0] / g[1] - pos) <= tol:
                g[0] += pos
                g[1] += 1
                g[2].append((a0, a1))
                continue
        groups.append([pos, 1, [(a0, a1)]])

    out: list[tuple[float, list[tuple[float, float]]]] = []
    for total, count, ivs in groups:
        pos = total / count
        ivs.sort()
        merged: list[list[float]] = []
        for a0, a1 in ivs:
            if merged and a0 <= merged[-1][1] + 3.0:
                merged[-1][1] = max(merged[-1][1], a1)
            else:
                merged.append([a0, a1])
        keep = [(a0, a1) for a0, a1 in merged if a1 - a0 >= 3]
        if keep:
            out.append((pos, keep))
    return out


def cluster(vals: list[float], tol: float = 3.0) -> list[float]:
    vals = sorted(vals)
    groups: list[list[float]] = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def v_covers(vs, x: float, y_bot: float, y_top: float, tol: float = 3.5) -> bool:
    for sx, sy0, sy1 in vs:
        if abs(sx - x) <= tol + 1.5 and sy0 <= y_bot + 4 and sy1 >= y_top - 4:
            return True
    return False


def h_covers(hs, y: float, x0: float, x1: float, tol: float = 3.5) -> bool:
    for sy, sx0, sx1 in hs:
        if abs(sy - y) <= tol + 1.5 and sx0 <= x0 + 4 and sx1 >= x1 - 4:
            return True
    return False


def index_at(bounds: list[float], value: float, descending: bool) -> int | None:
    if descending:
        for i in range(len(bounds) - 1):
            if bounds[i] >= value >= bounds[i + 1]:
                return i
        return None
    for i in range(len(bounds) - 1):
        if bounds[i] <= value <= bounds[i + 1]:
            return i
    return None


@dataclass
class RawGrid:
    n_rows: int
    n_cols: int
    rows: list[float]  # y giảm dần
    cols: list[float]  # x tăng dần
    spans: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    anchor_of: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    covered: set[tuple[int, int]] = field(default_factory=set)

    @property
    def bbox(self) -> BBox:
        return (self.cols[0], self.rows[-1], self.cols[-1], self.rows[0])

    @property
    def col_widths(self) -> list[float]:
        return [self.cols[i + 1] - self.cols[i] for i in range(self.n_cols)]

    def anchor_at(self, x: float, y: float) -> tuple[int, int] | None:
        r = index_at(self.rows, y, descending=True)
        c = index_at(self.cols, x, descending=False)
        if r is None or c is None:
            return None
        return self.anchor_of.get((r, c))


def find_grids(
    hsegs: list[tuple[float, float, float]],
    vsegs: list[tuple[float, float, float]],
    max_band_height: float = 900.0,
    max_cells: int = 400,
) -> list[RawGrid]:
    """Tìm các bảng trong một trang từ segment ngang/dọc thô."""
    if len(hsegs) < 4 or len(vsegs) < 2:
        return []

    merged_h = merge_segments(hsegs)
    merged_v = merge_segments(vsegs)
    rows = sorted(merged_h, key=lambda r: -r[0])
    vflat = [(pos, a0, a1) for pos, ivs in merged_v for a0, a1 in ivs]
    hflat = [(pos, a0, a1) for pos, ivs in merged_h for a0, a1 in ivs]

    grids: list[RawGrid] = []
    i = 0
    while i < len(rows) - 1:
        band = [rows[i]]
        j = i + 1
        while j < len(rows):
            gap = band[-1][0] - rows[j][0]
            if gap <= 0.5 or gap > max_band_height:
                break
            prev_ivs = [iv for r in band[-2:] for iv in r[1]] if len(band) > 1 else band[-1][1]
            ivs = prev_ivs + rows[j][1]
            lo = min(a0 for a0, _ in ivs)
            hi = max(a1 for _, a1 in ivs)
            if not any(
                lo - 3 <= x <= hi + 3 and a0 <= rows[j][0] + 4 and a1 >= band[-1][0] - 4
                for x, a0, a1 in vflat
            ):
                break
            band.append(rows[j])
            j += 1

        if len(band) >= 2:
            grid = _grid_from_band(band, vflat, hflat, max_cells)
            if grid:
                grids.append(grid)
                i = j
                continue
        i += 1

    grids.sort(key=lambda g: (g.bbox[2] - g.bbox[0]) * (g.bbox[3] - g.bbox[1]), reverse=True)
    kept: list[RawGrid] = []
    for grid in grids:
        if any(_iou(grid.bbox, k.bbox) > 0.55 for k in kept):
            continue
        kept.append(grid)
    return kept


def _grid_from_band(band, vflat, hflat, max_cells: int) -> RawGrid | None:
    row_clustered = sorted(cluster([r[0] for r in band], tol=4.0), reverse=True)
    row_ys = [round(v, 2) for v in row_clustered]
    y_top = row_ys[0]
    y_bot = row_ys[-1]
    if y_top - y_bot < 8:
        return None

    band_h = y_top - y_bot

    xs: list[float] = []
    for x, a0, a1 in vflat:
        overlap = min(a1, y_top) - max(a0, y_bot)
        if overlap >= 0.6 * band_h - 3:
            xs.append(x)

    # Điểm đầu/cuối của đường ngang chỉ được coi là biên cột nếu lặp lại
    # ở nhiều dòng (tránh mảnh vụn do chữ che đường kẻ trên bản scan).
    hits: dict[float, int] = {}
    for _, ivs in band:
        row_seen: set[float] = set()
        for a0, a1 in ivs:
            for v in (a0, a1):
                key = round(v, 1)
                if key in row_seen:
                    continue
                row_seen.add(key)
                hits[key] = hits.get(key, 0) + 1
    min_hits = max(2, int(0.25 * len(band)))
    xs.extend([v for v, n in hits.items() if n >= min_hits])

    x_min, x_max = min(xs), max(xs)
    cols = [c for c in cluster(xs, tol=3.0) if x_min - 3 <= c <= x_max + 3]
    if len(cols) < 2 or len(row_ys) < 2:
        return None

    n_r, n_c = len(row_ys) - 1, len(cols) - 1
    if n_r * n_c > max_cells:
        return None

    parent = list(range(n_r * n_c))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in range(n_r):
        for c in range(n_c - 1):
            if not v_covers(vflat, cols[c + 1], row_ys[r + 1], row_ys[r]):
                union(r * n_c + c, r * n_c + c + 1)
    for r in range(n_r - 1):
        for c in range(n_c):
            if not h_covers(hflat, row_ys[r + 1], cols[c], cols[c + 1]):
                union(r * n_c + c, (r + 1) * n_c + c)

    groups: dict[int, list[int]] = {}
    for idx in range(n_r * n_c):
        groups.setdefault(find(idx), []).append(idx)

    grid = RawGrid(n_rows=n_r, n_cols=n_c, rows=row_ys, cols=cols)
    for root, idxs in groups.items():
        rs = [i // n_c for i in idxs]
        cs = [i % n_c for i in idxs]
        r0, r1 = min(rs), max(rs)
        c0, c1 = min(cs), max(cs)
        anchor = (r0, c0)
        grid.spans[anchor] = (r1 - r0 + 1, c1 - c0 + 1)
        for i in idxs:
            grid.anchor_of[(i // n_c, i % n_c)] = anchor
            if (i // n_c, i % n_c) != anchor:
                grid.covered.add((i // n_c, i % n_c))
    return grid


def table_is_plausible(grid: RawGrid, text_cells: int, page_area: float) -> bool:
    """Loại lưới giả: khung trang trí, con dấu, ô quá hẹp, bảng rỗng.

    `text_cells` là số ô thực sự có chữ; `page_area` là diện tích trang (pt²).
    """
    if grid.n_rows < 1 or grid.n_cols < 2:
        return False
    widths = grid.col_widths
    if not widths or min(widths) < 8:
        return False
    n_spans = len(grid.spans)
    if text_cells < max(2, int(0.3 * n_spans)):
        return False
    b = grid.bbox
    area = (b[2] - b[0]) * (b[3] - b[1])
    if page_area > 0 and area > 0.6 * page_area:
        return False
    if n_spans == 1:
        rs, cs = next(iter(grid.spans.values()))
        if rs >= grid.n_rows and cs >= grid.n_cols:
            return False
    return True


def _iou(a: BBox, b: BBox) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(area_a + area_b - inter, 1e-6)
