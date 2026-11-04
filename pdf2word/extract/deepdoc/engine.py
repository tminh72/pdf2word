"""Engine OCR DeepDoc + VietOCR ONNX, chạy hoàn toàn trên CPU.

Luồng một trang:
  render DPI -> layout.onnx dò vùng bảng (kể cả bảng không kẻ)
  -> dò bảng có đường kẻ (grid.py) -> det.onnx tìm dòng chữ
  -> VietOCR ONNX nhận dạng từng dòng -> dòng ngoài bảng gom thành đoạn
  -> vùng bảng cắt ra chạy tsr.onnx để lấy hàng/cột/header -> gán chữ vào ô

Lưu ý license: ``layout.onnx`` là AGPL-3.0 (DeepDoc/RAGFlow). Khi phân phối
ứng dụng kèm model này, toàn bộ tác phẩm kết hợp phải tuân thủ AGPL-3.0.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from statistics import median
from typing import Callable

import numpy as np
import cv2
from PIL import Image, ImageOps

from ...idm import BBox, Block, Cell, Document, Page, Span, Table
from ..grid import RawGrid, index_at, table_is_plausible
from ..postprocess import (
    blocks_from_paragraphs,
    detect_headers_footers,
    detect_toc,
    resolve_layout,
    resolve_styles,
)
from ..text import Char, TextLine, group_paragraphs, xy_cut
from .models import Detector, LayoutDetector, ModelPaths, Recognizer, Tsr, find_model_dir
from .table_grid import grid_from_rules, grid_from_tsr


@dataclass
class DeepDocOptions:
    dpi: int = 300
    detect_limit: int = 1600
    detect_tables: bool = True
    detect_layout: bool = True
    pages: tuple[int, int] | None = None
    page_breaks: bool = True
    body_font: str = "Times New Roman"
    size_factor: float = 1.2
    model_dir: str | None = None
    batch_size: int = 12
    debug: bool = False
    progress: Callable[[int, int, str], None] | None = None
    cancel: Callable[[], bool] | None = None


@dataclass
class OcrLine:
    text: str
    bbox: BBox  # point, gốc dưới-trái
    height: float  # point (chiều cao hộp chữ)
    bbox_px: tuple[float, float, float, float]
    quad: np.ndarray
    toc_page: str | None = None

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def extract(pdf_path: str, options: DeepDocOptions, workdir: str) -> Document:
    import pypdfium2 as pdfium

    os.makedirs(workdir, exist_ok=True)
    model_dir = find_model_dir(options.model_dir)
    paths = ModelPaths(model_dir)
    detector = Detector(paths.det, limit_side_len=options.detect_limit)
    recognizer = Recognizer(paths, batch_size=options.batch_size)
    tsr = Tsr(paths.tsr) if options.detect_tables else None
    layout = None
    if options.detect_tables and options.detect_layout and os.path.isfile(paths.layout):
        layout = LayoutDetector(paths.layout)

    pdf = pdfium.PdfDocument(pdf_path)
    doc = Document(source=pdf_path)
    doc.meta["engine"] = "deepdoc"
    doc.meta["deepdoc_model_dir"] = model_dir
    doc.meta["deepdoc_dpi"] = options.dpi
    doc.meta["deepdoc_detect_limit"] = options.detect_limit
    doc.meta["deepdoc_layout"] = layout is not None

    start = (options.pages[0] - 1) if options.pages else 0
    end = options.pages[1] if options.pages else len(pdf)
    doc.meta["page_start"] = start + 1
    total = min(end, len(pdf)) - start
    uid_counter = [0]
    t0 = time.time()

    for page_index in range(start, min(end, len(pdf))):
        if options.cancel and options.cancel():
            doc.meta["cancelled"] = True
            break
        page = pdf[page_index]
        pw, ph = page.get_size()
        bitmap = page.render(scale=options.dpi / 72.0)
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()

        if options.debug:
            image.save(os.path.join(workdir, f"p{page_index + 1:03d}_deepdoc.png"))

        blocks = _page_blocks(
            image,
            page_index,
            pw,
            ph,
            options,
            detector,
            recognizer,
            tsr,
            layout,
            workdir,
            uid_counter,
        )
        blocks.sort(key=lambda b: -b.bbox[3])
        doc.pages.append(Page(width=pw, height=ph, blocks=blocks))
        if options.progress:
            options.progress(page_index + 1 - start, total, "page")

    pdf.close()
    doc.meta["deepdoc_seconds"] = round(time.time() - t0, 2)

    resolve_styles(doc)
    detect_toc(doc)
    detect_headers_footers(doc)
    resolve_layout(doc)
    return doc


def _page_blocks(
    image: Image.Image,
    page_index: int,
    pw: float,
    ph: float,
    options: DeepDocOptions,
    detector: Detector,
    recognizer: Recognizer,
    tsr: Tsr | None,
    layout: LayoutDetector | None,
    workdir: str,
    uid_counter: list[int],
) -> list[Block]:
    from ..ocr import _grid_to_px, detect_image_grids

    rgb = np.asarray(image)
    bgr = rgb[:, :, ::-1].copy()
    dpi = options.dpi
    scale = 72.0 / dpi

    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    dark = np.asarray(gray, dtype=np.uint8) < 170
    grids = detect_image_grids(dark, dpi, ph, pw) if options.detect_tables else []
    regions = _table_regions(grids, layout, bgr, ph, scale)

    table_boxes = [region.bbox for region in regions]

    quads = detector.detect(bgr)
    texts = recognizer.recognize(bgr, quads)
    lines: list[OcrLine] = []
    for quad, text in zip(quads, texts):
        if not text:
            continue
        px0 = float(quad[:, 0].min())
        px1 = float(quad[:, 0].max())
        py0 = float(quad[:, 1].min())
        py1 = float(quad[:, 1].max())
        x0 = px0 * scale
        x1 = px1 * scale
        y0 = ph - py1 * scale
        y1 = ph - py0 * scale
        lines.append(
            OcrLine(
                text=text,
                bbox=(x0, y0, x1, y1),
                height=y1 - y0,
                bbox_px=(px0, py0, px1, py1),
                quad=quad,
            )
        )

    _stabilize_sizes(lines)
    lines, toc_lines = _reconstruct_toc(lines, bgr, ph, pw, dpi, recognizer)

    body_lines: list[OcrLine] = []
    table_lines: dict[int, list[OcrLine]] = {}
    for line in lines:
        owner = _table_index(line.cx, line.cy, table_boxes)
        if owner is None:
            body_lines.append(line)
        else:
            table_lines.setdefault(owner, []).append(line)

    blocks: list[Block] = []
    for line in toc_lines:
        tl = _text_line(line, options, uid_counter)
        if tl is not None:
            tl.toc_page = line.toc_page
            blocks.extend(blocks_from_paragraphs([[tl]], [tl]))

    text_lines = [_text_line(line, options, uid_counter) for line in body_lines]
    text_lines = [line for line in text_lines if line is not None]
    if text_lines:
        text_lines = xy_cut(text_lines)
        paragraphs = group_paragraphs(text_lines)
        blocks.extend(blocks_from_paragraphs(paragraphs, text_lines))

    fallback_lines: list[OcrLine] = []
    for index, region in enumerate(regions):
        crop_px = _grid_to_px(region.bbox, dpi, ph)
        pad = 2
        cx0 = max(0, crop_px[0] - pad)
        cy0 = max(0, crop_px[1] - pad)
        cx1 = min(bgr.shape[1], crop_px[2] + pad)
        cy1 = min(bgr.shape[0], crop_px[3] + pad)
        crop = bgr[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            continue
        if options.debug:
            Image.fromarray(crop[:, :, ::-1]).save(
                os.path.join(workdir, f"p{page_index + 1:03d}_table{index}.png")
            )

        grid = None
        header_rows = 0
        if region.rule_grid is not None:
            grid, header_rows = grid_from_rules(region.rule_grid)
        if tsr is not None:
            dets = tsr.detect(crop)
            built = grid_from_tsr(dets, (cx0, cy0), ph, dpi)
            if built is not None:
                grid, header_rows = built
                # Bảng TSR có thể hẹp hơn vùng dò được: vẫn giữ chữ trong
                # vùng bảng bằng cách nới biên.
                grid.cols[0] = min(grid.cols[0], region.bbox[0])
                grid.cols[-1] = max(grid.cols[-1], region.bbox[2])
                grid.rows[0] = max(grid.rows[0], region.bbox[3])
                grid.rows[-1] = min(grid.rows[-1], region.bbox[1])

        owned = table_lines.get(index, [])
        if grid is None:
            fallback_lines.extend(owned)
            continue
        grid = _trim_grid_to_lines(grid, owned)
        table = _build_table(
            grid,
            header_rows,
            owned,
            options,
            grid.bbox,
            pw * ph,
        )
        if table is None:
            fallback_lines.extend(owned)
            continue
        blocks.append(Block(kind="table", bbox=grid.bbox, table=table))

    if fallback_lines:
        # Bảng dò được nhưng không dựng được lưới: trả chữ về phần thân thay
        # vì làm mất nội dung.
        fb = [_text_line(line, options, uid_counter) for line in fallback_lines]
        fb = [line for line in fb if line is not None]
        if fb:
            blocks.extend(blocks_from_paragraphs(group_paragraphs(fb), fb))

    return blocks


@dataclass
class TableRegion:
    bbox: BBox
    rule_grid: RawGrid | None = None
    source: str = "rules"


def _table_regions(
    grids: list[RawGrid],
    layout: LayoutDetector | None,
    bgr: np.ndarray,
    page_h: float,
    scale: float,
) -> list[TableRegion]:
    """Gộp bảng dò bằng đường kẻ và bảng dò bằng model layout (kể cả không kẻ)."""
    regions = [TableRegion(bbox=g.bbox, rule_grid=g) for g in grids]
    if layout is None:
        return regions

    for det in layout.detect(bgr):
        if det["label"] != "table" or det["score"] < 0.35:
            continue
        px0, py0, px1, py1 = det["bbox"]
        bbox: BBox = (
            px0 * scale,
            page_h - py1 * scale,
            px1 * scale,
            page_h - py0 * scale,
        )
        if (bbox[2] - bbox[0]) < 40 or (bbox[3] - bbox[1]) < 20:
            continue
        if any(_overlap_ratio(bbox, r.bbox) >= 0.5 for r in regions):
            continue
        regions.append(TableRegion(bbox=bbox, rule_grid=None, source="layout"))
    return regions


def _overlap_ratio(a: BBox, b: BBox) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(area_a, area_b)


def _stabilize_sizes(lines: list[OcrLine]) -> None:
    """Gom cỡ chữ thân về trung vị để nhiễu chiều cao hộp không thành heading."""
    heights = [line.height for line in lines if line.height > 1]
    if not heights:
        return
    median_h = median(heights)
    if median_h <= 0:
        return
    for line in lines:
        if 0.85 * median_h <= line.height <= 1.18 * median_h:
            line.height = median_h


def _reconstruct_toc(
    lines: list[OcrLine],
    image_bgr: np.ndarray,
    page_h: float,
    page_w: float,
    dpi: int,
    recognizer: Recognizer,
) -> tuple[list[OcrLine], list[OcrLine]]:
    """Gộp 'tiêu đề ... số trang' và bỏ mẩu dấu chấm dẫn bị OCR thành chữ."""
    if not lines:
        return [], []
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    kept = [line for line in lines if not _is_dot_leader(gray, line, page_h, dpi)]

    rows: list[list[OcrLine]] = []
    for line in sorted(kept, key=lambda ln: -ln.cy):
        for row in rows:
            ref = row[0]
            if abs(ref.cy - line.cy) <= 0.45 * max(ref.height, line.height):
                row.append(line)
                break
        else:
            rows.append([line])

    out: list[OcrLine] = []
    toc: list[OcrLine] = []
    for row in rows:
        row.sort(key=lambda ln: ln.bbox[0])
        if len(row) >= 2:
            title, number = row[0], row[-1]
            gap = number.bbox[0] - title.bbox[2]
            width = number.bbox[2] - number.bbox[0]
            if (
                number is not title
                and gap >= 25
                and width <= 45
                and number.bbox[0] > page_w * 0.35
                and _has_dot_leader(gray, title, number, page_h, dpi)
            ):
                digits = recognizer.recognize_numeric(image_bgr, number.quad)
                title.toc_page = digits or "1"
                row = [title]
        if not row[0].toc_page and _leader_to_margin(
            gray, row[0], page_w, page_h, dpi
        ):
            row[0].toc_page = "1"
        if row[0].toc_page:
            toc.append(row[0])
        else:
            out.extend(row)
    return out, toc


def _region_of(gray: np.ndarray, bbox_px: tuple[float, float, float, float]) -> np.ndarray:
    x0, y0, x1, y1 = (int(round(v)) for v in bbox_px)
    h, w = gray.shape[:2]
    x0, x1 = max(0, min(x0, w)), max(0, min(x1, w))
    y0, y1 = max(0, min(y0, h)), max(0, min(y1, h))
    return gray[y0:y1, x0:x1]


def _dot_components(region: np.ndarray) -> bool:
    if region.size == 0 or region.shape[1] < 12:
        return False
    dark = (region < 175).astype(np.uint8)
    coverage = float(dark.mean())
    if coverage < 0.008 or coverage > 0.4:
        return False
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, 8)
    if count - 1 < 5:
        return False
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    limit = max(3.0, 0.42 * region.shape[0])
    # Dấu chấm dẫn đều nhỏ; nếu có bất kỳ nét chữ cao nào thì không phải.
    return int(heights.max()) <= limit


def _is_dot_leader(
    gray: np.ndarray, line: OcrLine, page_h: float, dpi: int
) -> bool:
    scale = dpi / 72.0
    x0, x1 = line.bbox_px[0], line.bbox_px[2]
    y0 = (page_h - line.bbox[3]) * scale
    y1 = (page_h - line.bbox[1]) * scale
    if x1 - x0 < 22 or y1 - y0 < 4:
        return False
    return _dot_components(_region_of(gray, (x0, y0, x1, y1)))


def _has_dot_leader(
    gray: np.ndarray,
    title: OcrLine,
    number: OcrLine,
    page_h: float,
    dpi: int,
) -> bool:
    scale = dpi / 72.0
    x0 = (title.bbox[2] + 2) * scale
    x1 = (number.bbox[0] - 2) * scale
    y0 = (page_h - min(title.bbox[3], number.bbox[3])) * scale
    y1 = (page_h - max(title.bbox[1], number.bbox[1])) * scale
    if x1 - x0 < 10:
        return False
    return _dot_components(_region_of(gray, (x0, y0, x1, y1)))


def _leader_to_margin(
    gray: np.ndarray, line: OcrLine, page_w: float, page_h: float, dpi: int
) -> bool:
    """Mục lục không có hộp số trang (det bỏ sót): nhìn dấu chấm tới lề phải."""
    if line.bbox[2] > page_w * 0.88:
        return False
    scale = dpi / 72.0
    x0 = (line.bbox[2] + 2) * scale
    # Dừng trước vùng số trang để không nhầm chữ số (nét cao) với dấu chấm.
    x1 = min(page_w * 0.82, page_w - 55) * scale
    if x1 - x0 < 40 * scale:
        return False
    y0 = (page_h - line.bbox[3]) * scale
    y1 = (page_h - line.bbox[1]) * scale
    pad = max(3.0, 0.35 * (y1 - y0))
    return _dot_components(_region_of(gray, (x0, y0 - pad, x1, y1 + pad)))


def _table_index(cx: float, cy: float, boxes: list[BBox]) -> int | None:
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
            return i
    return None


def _text_line(line: OcrLine, options: DeepDocOptions, uid_counter: list[int]) -> TextLine | None:
    text = line.text.strip()
    if not text:
        return None
    size = round(max(4.0, line.height * options.size_factor), 1)
    uid_counter[0] += 1
    char = Char(
        ch=text,
        bbox=line.bbox,
        font=options.body_font,
        raw_font=options.body_font,
        size=size,
        bold=False,
        italic=False,
        color=(0, 0, 0),
        uid=uid_counter[0],
    )
    return TextLine(chars=[char], bbox=line.bbox, text=text)


def _build_table(
    grid: RawGrid,
    header_rows: int,
    lines: list[OcrLine],
    options: DeepDocOptions,
    bbox: BBox,
    page_area: float,
) -> Table | None:
    if not lines:
        return None
    cells: list[list[Cell]] = [
        [Cell(covered=True) for _ in range(grid.n_cols)] for _ in range(grid.n_rows)
    ]
    for (r, c), (rs, cs) in grid.spans.items():
        if 0 <= r < grid.n_rows and 0 <= c < grid.n_cols:
            cells[r][c] = Cell(rowspan=rs, colspan=cs)

    per_cell: dict[tuple[int, int], list[OcrLine]] = {}
    for line in lines:
        anchor = grid.anchor_at(line.cx, line.cy)
        if anchor is None:
            anchor = _clamped_anchor(grid, line.cx, line.cy)
        if anchor is None:
            continue
        per_cell.setdefault(anchor, []).append(line)

    text_cells = 0
    for anchor, cell_lines in per_cell.items():
        cell = cells[anchor[0]][anchor[1]]
        cell_lines.sort(key=lambda ln: -ln.cy)
        cell.lines = [
            [
                Span(
                    text=ln.text,
                    font=options.body_font,
                    size=round(max(4.0, ln.height * options.size_factor), 1),
                )
            ]
            for ln in cell_lines
            if ln.text.strip()
        ]
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
    if header_rows <= 0:
        header_rows = _guess_header_rows(cells)
    table.header_rows = max(0, min(header_rows, grid.n_rows))
    return table


def _trim_grid_to_lines(grid: RawGrid, lines: list[OcrLine]) -> RawGrid:
    """Bỏ hàng/cột trống ở rìa do TSR dự đoán thừa.

    Bảng không kẻ thường bị model thêm một hàng/cột rỗng; cắt rìa giúp bảng
    khớp kích thước thật mà không đụng tới các cột rỗng ở giữa.
    """
    if not lines:
        return grid
    used_rows: set[int] = set()
    used_cols: set[int] = set()
    for line in lines:
        r = index_at(grid.rows, line.cy, descending=True)
        c = index_at(grid.cols, line.cx, descending=False)
        if r is not None:
            used_rows.add(r)
        if c is not None:
            used_cols.add(c)
    if not used_rows or not used_cols:
        return grid
    r0, r1 = min(used_rows), max(used_rows)
    c0, c1 = min(used_cols), max(used_cols)
    if r0 == 0 and r1 == grid.n_rows - 1 and c0 == 0 and c1 == grid.n_cols - 1:
        return grid

    n_rows = r1 - r0 + 1
    n_cols = c1 - c0 + 1
    trimmed = RawGrid(
        n_rows=n_rows,
        n_cols=n_cols,
        rows=grid.rows[r0 : r1 + 2],
        cols=grid.cols[c0 : c1 + 2],
    )
    for (r, c), (rs, cs) in grid.spans.items():
        rr1, cc1 = r + rs - 1, c + cs - 1
        sr0, sr1 = max(r, r0), min(rr1, r1)
        sc0, sc1 = max(c, c0), min(cc1, c1)
        if sr1 < sr0 or sc1 < sc0:
            continue
        anchor = (sr0 - r0, sc0 - c0)
        span = (sr1 - sr0 + 1, sc1 - sc0 + 1)
        trimmed.spans[anchor] = span
        for rr in range(anchor[0], anchor[0] + span[0]):
            for cc in range(anchor[1], anchor[1] + span[1]):
                trimmed.anchor_of[(rr, cc)] = anchor
                if (rr, cc) != anchor:
                    trimmed.covered.add((rr, cc))
    return trimmed


def _clamped_anchor(grid: RawGrid, x: float, y: float) -> tuple[int, int] | None:
    x = min(max(x, grid.cols[0]), grid.cols[-1])
    y = min(max(y, grid.rows[-1]), grid.rows[0])
    return grid.anchor_at(x, y)


def _looks_like_header(cells: list[list[Cell]]) -> bool:
    if len(cells) < 2:
        return False
    first = [c for c in cells[0] if not c.covered and c.text]
    if not first:
        return False
    letters = [ch for c in first for ch in c.text if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) > 0.7


def _guess_header_rows(cells: list[list[Cell]]) -> int:
    """Suy header khi TSR không trả ``table column header``.

    Hai tín hiệu: dòng đầu viết IN HOA, hoặc dòng đầu toàn nhãn chữ trong khi
    dòng kế tiếp có nhiều ô số (dữ liệu).
    """
    if len(cells) < 2:
        return 0
    first = [c.text.strip() for c in cells[0] if not c.covered and c.text.strip()]
    if not first:
        return 0
    if _looks_like_header(cells):
        return 1

    second = [c.text.strip() for c in cells[1] if not c.covered and c.text.strip()]
    if not second:
        return 0

    def numeric_ratio(values: list[str]) -> float:
        return sum(1 for v in values if any(ch.isdigit() for ch in v)) / len(values)

    if numeric_ratio(first) <= 0.34 and numeric_ratio(second) >= 0.5:
        return 1
    return 0




def detect_dot_leaders(
    image_bgr: np.ndarray, dpi: int = 300
) -> list[dict]:
    """Quet toan bo trang, tra ve cac vung dau cham leader (bbox, confidence).

    Dung de phat hien muc luc co dau cham chuyen dong (dot leader) khi
    det.onnx bo qua cac ky tu dac biet.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    dark = (gray < 175).astype(np.uint8)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, 8)

    dots = []
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        if 2 <= cw <= 14 and 2 <= ch <= 14 and 2 <= area <= 100:
            cx = _centroids[i][0]
            cy = _centroids[i][1]
            dots.append((cx, cy, cw, ch, area))

    if not dots:
        return []

    # Sap xep theo y, sau do x
    dots.sort(key=lambda d: (d[1], d[0]))

    # Gom cac dot cung hang (khoang cach y nho)
    lines: list[list[tuple]] = []
    current = [dots[0]]
    for d in dots[1:]:
        if abs(d[1] - current[-1][1]) <= max(3.0, 0.45 * current[-1][3]):
            current.append(d)
        else:
            lines.append(current)
            current = [d]
    if current:
        lines.append(current)

    # Chi nhan hang co du nhieu dot (>= 8)
    result: list[dict] = []
    scale = dpi / 72.0
    for line_dots in lines:
        if len(line_dots) < 8:
            continue
        xs = [d[0] for d in line_dots]
        ys = [d[1] for d in line_dots]
        ws = [d[2] for d in line_dots]
        hs = [d[3] for d in line_dots]
        bx0 = int(min(xs) - 2)
        by0 = int(min(ys) - 2)
        bx1 = int(max(xs) + max(ws) + 2)
        by1 = int(max(ys) + max(hs) + 2)
        bx0 = max(0, bx0); by0 = max(0, by0)
        bx1 = min(w, bx1); by1 = min(h, by1)
        result.append(
            {
                "bbox_px": [bx0, by0, bx1, by1],
                "bbox_pt": [
                    bx0 / scale,
                    (h - by1) / scale,
                    bx1 / scale,
                    (h - by0) / scale,
                ],
                "dot_count": len(line_dots),
                "confidence": round(min(1.0, len(line_dots) / 25.0), 3),
            }
        )
    return result


__all__ = ["DeepDocOptions", "extract", "detect_dot_leaders"]
