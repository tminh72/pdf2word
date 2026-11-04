"""Sinh dataset tiếng Việt chuẩn cho benchmark.

Dataset là dữ liệu **tổng hợp, tự biên soạn**, không lấy từ khách hàng hay
nguồn bên ngoài. Mỗi tài liệu có:

* PDF sinh bằng ReportLab (font Arial có dấu tiếng Việt);
* text tham chiếu chính xác theo đúng thứ tự nội dung;
* ground truth heading / TOC / header-footer / bảng.

Bản scan được tạo bằng cách render PDF nguồn thành ảnh 200 DPI rồi nhúng vào
PDF mới, mô phỏng tài liệu scan sạch.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIR = Path("C:/Windows/Fonts")
FONT_CANDIDATES = {
    "normal": ["arial.ttf", "segoeui.ttf", "tahoma.ttf"],
    "bold": ["arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf"],
    "italic": ["ariali.ttf", "segoeuii.ttf", "tahoma.ttf"],
}

DATASET_NAME = "vi_core"
DATASET_VERSION = "1.0"


def _register_fonts() -> tuple[str, str, str]:
    names: list[str] = []
    for key in ("normal", "bold", "italic"):
        chosen: Path | None = None
        for filename in FONT_CANDIDATES[key]:
            path = FONT_DIR / filename
            if path.is_file():
                chosen = path
                break
        if chosen is None:
            raise FileNotFoundError(
                f"Không tìm thấy font {key} trong {FONT_DIR}; cần Arial/Segoe UI/Tahoma."
            )
        name = f"Bench{key.title()}"
        try:
            pdfmetrics.registerFont(TTFont(name, str(chosen)))
        except Exception:  # noqa: BLE001 - đã đăng ký ở lần gọi trước
            pass
        names.append(name)
    return names[0], names[1], names[2]


@dataclass
class Flow:
    normal: str
    bold: str
    italic: str
    story: list = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    toc: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    styles: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.styles = {
            "title": ParagraphStyle(
                "title",
                fontName=self.bold,
                fontSize=18,
                leading=24,
                alignment=TA_CENTER,
                spaceAfter=8,
            ),
            "subtitle": ParagraphStyle(
                "subtitle",
                fontName=self.normal,
                fontSize=11.5,
                leading=16,
                alignment=TA_CENTER,
                spaceAfter=14,
            ),
            "h1": ParagraphStyle(
                "h1", fontName=self.bold, fontSize=14, leading=19, spaceBefore=12, spaceAfter=7
            ),
            "h2": ParagraphStyle(
                "h2", fontName=self.bold, fontSize=12, leading=17, spaceBefore=9, spaceAfter=5
            ),
            "body": ParagraphStyle(
                "body",
                fontName=self.normal,
                fontSize=11,
                leading=16.5,
                alignment=TA_JUSTIFY,
                spaceAfter=6,
            ),
            "left": ParagraphStyle(
                "left", fontName=self.normal, fontSize=11, leading=16.5, alignment=TA_LEFT,
                spaceAfter=4,
            ),
            "bullet": ParagraphStyle(
                "bullet",
                fontName=self.normal,
                fontSize=11,
                leading=16.5,
                leftIndent=18,
                spaceAfter=3,
            ),
            "small": ParagraphStyle(
                "small",
                fontName=self.italic,
                fontSize=9.5,
                leading=13,
                alignment=TA_LEFT,
                spaceAfter=4,
            ),
            "right": ParagraphStyle(
                "right", fontName=self.normal, fontSize=11, leading=16.5, alignment=TA_RIGHT,
                spaceAfter=2,
            ),
        }

    def title(self, text: str) -> None:
        self.story.append(Paragraph(escape(text), self.styles["title"]))
        self.lines.append(text)
        self.headings.append(text)

    def subtitle(self, text: str) -> None:
        self.story.append(Paragraph(escape(text), self.styles["subtitle"]))
        self.lines.append(text)

    def heading(self, text: str, level: int = 1) -> None:
        style = "h1" if level <= 1 else "h2"
        self.story.append(Paragraph(escape(text), self.styles[style]))
        self.lines.append(text)
        self.headings.append(text)

    def paragraph(self, text: str, style: str = "body") -> None:
        self.story.append(Paragraph(escape(text), self.styles[style]))
        self.lines.append(text)

    def bullet(self, text: str) -> None:
        self.story.append(Paragraph("• " + escape(text), self.styles["bullet"]))
        self.lines.append("• " + text)

    def toc_line(self, title: str, page: int) -> None:
        line = f"{title} {'.' * max(8, 58 - len(title))} {page}"
        self.story.append(Paragraph(escape(line), self.styles["left"]))
        self.lines.append(line)
        self.toc.append(title)

    def table(
        self,
        rows: list[list[str]],
        widths: list[float],
        spans: list[tuple[int, int, int, int]] | None = None,
        font_size: float = 9,
        align_right_from: int | None = None,
    ) -> None:
        data = [[Paragraph(escape(cell), ParagraphStyle(
            "cell", fontName=self.normal, fontSize=font_size, leading=font_size + 2.5,
            alignment=TA_LEFT,
        )) for cell in row] for row in rows]
        table = Table(data, colWidths=widths, repeatRows=1)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        if spans:
            style.extend(("SPAN", (c0, r0), (c1, r1)) for r0, c0, r1, c1 in spans)
        if align_right_from is not None:
            style.append(("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"))
        table.setStyle(TableStyle(style))
        self.story.append(table)
        self.story.append(Spacer(1, 7))
        self.tables.append([list(row) for row in rows])
        for row in rows:
            text = "\t".join(cell for cell in row if cell)
            if text:
                self.lines.append(text)

    def spacer(self, points: float = 8) -> None:
        self.story.append(Spacer(1, points))

    def page_break(self) -> None:
        self.story.append(PageBreak())


@dataclass
class BuiltDoc:
    id: str
    pdf: Path
    reference: Path
    truth: Path
    kind: str
    pages: int
    tags: list[str]
    text: str
    headings: list[str]
    toc: list[str]
    header: list[str]
    footer: list[str]
    tables: list[list[list[str]]]


def _build_story(flow: Flow, source: Path, *, title: str, header: str = "", footer: str = "") -> int:
    normal, bold, _ = _register_fonts()

    def on_page(canvas, doc):  # noqa: ANN001
        if header:
            canvas.saveState()
            canvas.setFont(bold, 8.5)
            canvas.setFillColor(colors.HexColor("#333333"))
            canvas.drawString(20 * mm, A4[1] - 17 * mm, header)
            canvas.restoreState()
        if footer:
            canvas.saveState()
            canvas.setFont(normal, 8.5)
            canvas.setFillColor(colors.HexColor("#555555"))
            canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"{footer} {canvas.getPageNumber()}")
            canvas.restoreState()

    doc = SimpleDocTemplate(
        str(source),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=27 * mm,
        bottomMargin=23 * mm,
        title=title,
        author="pdf2word benchmark",
    )
    doc.build(flow.story, onFirstPage=on_page, onLaterPages=on_page)
    return doc.page


def _write_doc(
    root: Path,
    doc_id: str,
    flow: Flow,
    source: Path,
    *,
    kind: str,
    tags: list[str],
    header: list[str] | None = None,
    footer: list[str] | None = None,
    title: str,
    pages: int,
) -> BuiltDoc:
    refs = root / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    reference = refs / f"{doc_id}.txt"
    truth = refs / f"{doc_id}.json"
    text = "\n".join(flow.lines).strip() + "\n"
    reference.write_text(text, encoding="utf-8")
    truth.write_text(
        json.dumps(
            {
                "headings": flow.headings,
                "toc": flow.toc,
                "header": header or [],
                "footer": footer or [],
                "tables": flow.tables,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    pdf_path = root / "pdfs" / f"{doc_id}.pdf"
    return BuiltDoc(
        id=doc_id,
        pdf=pdf_path,
        reference=reference,
        truth=truth,
        kind=kind,
        pages=pages,
        tags=tags,
        text=text,
        headings=flow.headings,
        toc=flow.toc,
        header=header or [],
        footer=footer or [],
        tables=flow.tables,
    )


# --------------------------------------------------------------------------
# 1. Báo cáo tài chính (digital, heading + TOC + 2 bảng)
# --------------------------------------------------------------------------


def _doc_financial(root: Path) -> BuiltDoc:
    flow = Flow(*_register_fonts())
    flow.title("BÁO CÁO TÀI CHÍNH QUÝ III/2026")
    flow.subtitle("Công ty TNHH Thương mại Mẫu Việt Nam")
    # "MỤC LỤC" là nhãn trang bìa; vẫn được tính là heading cấp 1 khi xuất DOCX.
    flow.paragraph("MỤC LỤC", "subtitle")
    flow.headings.append("MỤC LỤC")
    flow.toc_line("1. Tổng quan hoạt động", 2)
    flow.toc_line("2. Kết quả kinh doanh theo tháng", 2)
    flow.toc_line("3. Cơ cấu chi phí", 2)
    flow.toc_line("4. Kế hoạch quý IV", 3)
    flow.toc_line("5. Kết luận", 3)
    flow.page_break()

    flow.heading("1. Tổng quan hoạt động", 1)
    flow.paragraph(
        "Trong quý III năm 2026, công ty duy trì đà tăng trưởng ổn định với doanh thu thuần "
        "đạt 128,4 tỷ đồng, tăng 12,6% so với cùng kỳ năm trước. Lợi nhuận sau thuế ghi nhận "
        "9,7 tỷ đồng nhờ tối ưu chi phí vận hành và cơ cấu lại danh mục sản phẩm."
    )
    flow.paragraph(
        "Ban lãnh đạo đánh giá mức tăng trưởng này phù hợp với kế hoạch đề ra. Tuy nhiên, "
        "áp lực tỷ giá và chi phí nguyên vật liệu vẫn là rủi ro cần theo dõi trong quý tới."
    )
    flow.heading("2. Kết quả kinh doanh theo tháng", 1)
    flow.table(
        [
            ["Tháng", "Doanh thu (tỷ đồng)", "Chi phí (tỷ đồng)", "Lợi nhuận (tỷ đồng)", "Ghi chú"],
            ["Tháng 7", "41,2", "36,1", "5,1", ""],
            ["Tháng 8", "43,5", "37,8", "5,7", ""],
            ["Tháng 9", "43,7", "38,2", "5,5", "Số liệu tạm tính"],
            ["Cộng quý", "128,4", "112,1", "16,3", ""],
        ],
        [70, 100, 88, 105, 105],
        align_right_from=1,
    )
    flow.heading("3. Cơ cấu chi phí", 1)
    flow.paragraph(
        "Chi phí nguyên vật liệu chiếm 54,2% tổng chi phí, tăng nhẹ so với quý trước. "
        "Chi phí nhân công chiếm 18,7% và đã được kiểm soát nhờ tự động hoá quy trình."
    )
    flow.table(
        [
            ["Chỉ tiêu", "Đầu kỳ", "Cuối kỳ", "Chênh lệch"],
            ["Tài sản ngắn hạn", "96,3", "104,8", "+8,5"],
            ["Tài sản dài hạn", "71,2", "74,1", "+2,9"],
            ["Tổng tài sản", "167,5", "178,9", "+11,4"],
        ],
        [160, 100, 100, 100],
        align_right_from=1,
    )
    flow.page_break()

    flow.heading("4. Kế hoạch quý IV", 1)
    flow.paragraph(
        "Công ty đặt mục tiêu doanh thu 140 tỷ đồng cho quý IV, tương đương mức tăng 9% so với "
        "quý III. Trọng tâm là mở rộng kênh bán hàng trực tuyến và hoàn thiện hệ thống quản trị "
        "tồn kho theo thời gian thực."
    )
    flow.heading("4.1. Các ưu tiên chính", 2)
    flow.bullet("Hoàn thiện báo cáo quản trị theo tuần cho ban lãnh đạo.")
    flow.bullet("Giảm thời gian xử lý đơn hàng xuống dưới 24 giờ.")
    flow.bullet("Đào tạo nhân sự mới về quy trình kiểm soát chi phí.")
    flow.heading("5. Kết luận", 1)
    flow.paragraph(
        "Kết quả quý III cho thấy mô hình kinh doanh tiếp tục ổn định. Công ty cần theo dõi sát "
        "biến động tỷ giá và duy trì kỷ luật chi phí để hoàn thành kế hoạch cả năm."
    )
    flow.spacer(12)
    flow.paragraph("Người lập báo cáo: Phòng Tài chính - Kế toán", "small")

    source = root / "pdfs" / "bao_cao_tai_chinh.pdf"
    pages = _build_story(flow, source, title="Báo cáo tài chính quý III/2026")
    return _write_doc(
        root,
        "bao_cao_tai_chinh",
        flow,
        source,
        kind="digital",
        tags=["digital", "report", "tables", "toc", "headings"],
        title="Báo cáo tài chính",
        pages=pages,
    )


# --------------------------------------------------------------------------
# 2. Thông báo tuyển sinh (scan, header/footer)
# --------------------------------------------------------------------------


def _doc_scanned_source(root: Path) -> tuple[Flow, Path, str, str]:
    flow = Flow(*_register_fonts())
    flow.title("THÔNG BÁO TUYỂN SINH NĂM 2026")
    flow.subtitle("Trường Cao đẳng Kỹ thuật Mẫu")
    flow.paragraph(
        "Trường Cao đẳng Kỹ thuật Mẫu thông báo tuyển sinh các ngành kỹ thuật và kinh tế "
        "trong năm 2026. Hồ sơ được tiếp nhận trực tiếp tại phòng tuyển sinh hoặc gửi qua "
        "đường bưu chính."
    )
    flow.heading("1. Ngành đào tạo và chỉ tiêu", 1)
    flow.paragraph(
        "Năm 2026, nhà trường tuyển sinh bốn ngành chính với tổng chỉ tiêu 880 sinh viên. "
        "Thí sinh có thể đăng ký xét tuyển bằng học bạ hoặc kết quả kỳ thi tốt nghiệp."
    )
    flow.page_break()

    flow.heading("2. Điều kiện và hồ sơ dự tuyển", 1)
    flow.paragraph(
        "Thí sinh tốt nghiệp trung học phổ thông hoặc tương đương có đủ điều kiện đăng ký. "
        "Hồ sơ gồm phiếu đăng ký, bản sao học bạ, giấy khai sinh và ảnh thẻ ba nhân bốn."
    )
    flow.bullet("Phiếu đăng ký xét tuyển có xác nhận của nhà trường.")
    flow.bullet("Bản sao công chứng học bạ trung học phổ thông.")
    flow.bullet("Giấy khai sinh và căn cước công dân còn hiệu lực.")
    flow.paragraph(
        "Thời gian nhận hồ sơ từ ngày 05 tháng 5 đến hết ngày 30 tháng 8 năm 2026. "
        "Lệ phí xét tuyển là 50.000 đồng mỗi hồ sơ."
    )
    flow.page_break()

    flow.heading("3. Hình thức xét tuyển", 1)
    flow.paragraph(
        "Nhà trường xét tuyển theo tổng điểm ba môn thuộc tổ hợp đăng ký, ưu tiên thí sinh "
        "có chứng chỉ ngoại ngữ hoặc thành tích học tập xuất sắc. Kết quả được công bố trên "
        "cổng thông tin điện tử của trường."
    )
    flow.heading("4. Liên hệ", 1)
    flow.paragraph(
        "Mọi thắc mắc vui lòng liên hệ Phòng Tuyển sinh, số điện thoại 0236 000 0000, "
        "trong giờ hành chính từ thứ hai đến thứ sáu."
    )
    flow.spacer(20)
    flow.paragraph("Ban Tuyển sinh", "right")
    flow.paragraph("Trường Cao đẳng Kỹ thuật Mẫu", "right")

    source = root / "pdfs" / "_nguon_scan.pdf"
    header = "TRƯỜNG CAO ĐẲNG KỸ THUẬT MẪU"
    footer = "Trang"
    pages = _build_story(flow, source, title="Thông báo tuyển sinh 2026", header=header, footer=footer)
    return flow, source, header, footer


def _make_scanned(src_pdf: Path, dest_pdf: Path) -> int:
    import pypdfium2 as pdfium
    from PIL import Image
    from reportlab.pdfgen import canvas

    tmp = Path(tempfile.mkdtemp(prefix="pdf2word_scan_"))
    try:
        pdf = pdfium.PdfDocument(str(src_pdf))
        images: list[Path] = []
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=200 / 72.0)
            image_path = tmp / f"page_{index + 1}.png"
            image = bitmap.to_pil().convert("L")
            image.save(image_path, "PNG", optimize=True)
            image.close()
            bitmap.close()
            page.close()
            images.append(image_path)
        pdf.close()

        c = canvas.Canvas(str(dest_pdf), pagesize=A4)
        c.setTitle("Thông báo tuyển sinh 2026 - bản scan")
        for image_path in images:
            c.drawImage(str(image_path), 0, 0, width=A4[0], height=A4[1])
            c.showPage()
        c.save()
        return len(images)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _doc_scanned(root: Path) -> BuiltDoc:
    flow, source, header, footer = _doc_scanned_source(root)
    dest = root / "pdfs" / "thong_bao_tuyen_sinh_scan.pdf"
    pages = _make_scanned(source, dest)
    source.unlink(missing_ok=True)
    return _write_doc(
        root,
        "thong_bao_tuyen_sinh_scan",
        flow,
        dest,
        kind="scanned",
        tags=["scanned", "ocr", "header-footer", "report"],
        header=[header],
        footer=[footer],
        title="Thông báo tuyển sinh (scan)",
        pages=pages,
    )


# --------------------------------------------------------------------------
# 3. Báo cáo nhiều bảng (digital, bảng gộp ô)
# --------------------------------------------------------------------------


def _doc_tables(root: Path) -> BuiltDoc:
    flow = Flow(*_register_fonts())
    flow.title("BÁO CÁO TỔNG HỢP KINH DOANH 6 THÁNG")
    flow.subtitle("Công ty Cổ phần Dữ liệu Mẫu")
    flow.paragraph(
        "Báo cáo tổng hợp kết quả kinh doanh sáu tháng đầu năm 2026 theo khu vực, "
        "nhóm sản phẩm và kênh bán hàng. Số liệu đã được rà soát bởi phòng kế toán."
    )
    flow.heading("1. Kết quả theo tháng", 1)
    flow.table(
        [
            ["Tháng", "Doanh thu", "Chi phí", "Lợi nhuận", "Tỷ lệ hoàn thành", "Ghi chú"],
            ["Tháng 1", "112,5", "96,8", "15,7", "98%", ""],
            ["Tháng 2", "105,2", "91,4", "13,8", "94%", "Tết Nguyên đán"],
            ["Tháng 3", "121,8", "101,2", "20,6", "107%", ""],
            ["Tháng 4", "118,4", "99,7", "18,7", "104%", ""],
            ["Tháng 5", "124,1", "103,5", "20,6", "109%", "Cao điểm"],
            ["Tháng 6", "129,7", "107,8", "21,9", "114%", ""],
            ["Cộng", "711,7", "600,4", "111,3", "104%", ""],
        ],
        [55, 72, 72, 78, 95, 100],
        align_right_from=1,
    )
    flow.page_break()

    flow.heading("2. Kết quả theo khu vực", 1)
    flow.paragraph(
        "Khu vực miền Nam tiếp tục đóng góp tỷ trọng lớn nhất với 48,6% doanh thu. "
        "Khu vực miền Bắc tăng trưởng 8,4% so với cùng kỳ nhờ mở rộng hệ thống phân phối."
    )
    flow.table(
        [
            ["Nhóm", "Chỉ tiêu", "Quý I", "Quý II", "Tăng trưởng"],
            ["Doanh thu", "Miền Bắc", "40,2", "42,1", "4,7%"],
            ["", "Miền Nam", "45,1", "48,6", "7,8%"],
            ["Tổng", "Toàn quốc", "85,3", "90,7", "6,3%"],
        ],
        [90, 150, 80, 80, 90],
        spans=[(1, 0, 2, 0)],
        align_right_from=2,
    )
    flow.heading("3. Cơ cấu theo kênh bán hàng", 1)
    flow.table(
        [
            ["Kênh", "Doanh thu", "Tỷ trọng", "Chi phí bán hàng"],
            ["Cửa hàng truyền thống", "356,2", "50,1%", "28,4"],
            ["Đại lý phân phối", "188,4", "26,5%", "12,1"],
            ["Bán hàng trực tuyến", "142,6", "20,0%", "16,8"],
            ["Kênh khác", "24,5", "3,4%", "2,2"],
        ],
        [170, 100, 100, 120],
        align_right_from=1,
    )
    flow.page_break()

    flow.heading("4. Tổng hợp chỉ tiêu tài chính", 1)
    flow.table(
        [
            ["Chỉ tiêu", "Thực hiện", "Kế hoạch", "Chênh lệch"],
            ["Doanh thu thuần", "711,7", "690,0", "+21,7"],
            ["Giá vốn hàng bán", "600,4", "590,0", "+10,4"],
            ["Lợi nhuận gộp", "111,3", "100,0", "+11,3"],
            ["Chi phí quản lý", "34,2", "33,0", "+1,2"],
            ["Lợi nhuận sau thuế", "61,7", "53,6", "+8,1"],
        ],
        [160, 100, 100, 110],
        align_right_from=1,
    )
    flow.heading("5. Nhận xét", 1)
    flow.paragraph(
        "Sáu tháng đầu năm ghi nhận mức tăng trưởng 6,3% so với cùng kỳ. Tỷ lệ hoàn thành "
        "kế hoạch đạt 104%, trong đó nhóm sản phẩm chủ lực đóng góp 62% doanh thu."
    )
    flow.bullet("Duy trì tốc độ tăng trưởng ở kênh trực tuyến.")
    flow.bullet("Kiểm soát chi phí bán hàng dưới 9% doanh thu.")
    flow.bullet("Hoàn thiện báo cáo quản trị theo tuần từ quý III.")
    flow.spacer(12)
    flow.paragraph("Người lập: Phòng Kế hoạch - Tài chính", "small")

    source = root / "pdfs" / "bao_cao_bang.pdf"
    pages = _build_story(flow, source, title="Báo cáo tổng hợp kinh doanh 6 tháng")
    return _write_doc(
        root,
        "bao_cao_bang",
        flow,
        source,
        kind="digital",
        tags=["digital", "report", "tables", "headings"],
        title="Báo cáo nhiều bảng",
        pages=pages,
    )


# --------------------------------------------------------------------------
# 4. Mẫu đơn hành chính (digital, form + bảng)
# --------------------------------------------------------------------------


def _doc_form(root: Path) -> BuiltDoc:
    flow = Flow(*_register_fonts())
    flow.title("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    flow.subtitle("Độc lập - Tự do - Hạnh phúc")
    flow.paragraph("-----------------------------", "subtitle")
    flow.title("ĐƠN ĐỀ NGHỊ XÁC NHẬN THÔNG TIN")
    flow.paragraph("Kính gửi: Phòng Hành chính - Nhân sự", "left")
    flow.paragraph(
        "Tôi tên là Nguyễn Văn Mẫu, sinh ngày 15 tháng 8 năm 1995, đề nghị xác nhận "
        "các thông tin cá nhân và quá trình công tác dưới đây."
    )
    flow.table(
        [
            ["Họ và tên", "Nguyễn Văn Mẫu"],
            ["Ngày sinh", "15/08/1995"],
            ["Số căn cước công dân", "001095000000"],
            ["Chức vụ", "Chuyên viên dữ liệu"],
            ["Bộ phận", "Phòng Công nghệ thông tin"],
            ["Địa chỉ thường trú", "Số 12 phố Mẫu, phường Trung Hòa, Hà Nội"],
        ],
        [150, 320],
    )
    flow.paragraph("Danh mục giấy tờ kèm theo:", "left")
    flow.table(
        [
            ["STT", "Tên giấy tờ", "Số lượng"],
            ["1", "Bản sao căn cước công dân", "01"],
            ["2", "Giấy xác nhận công tác", "01"],
            ["3", "Bản sao hợp đồng lao động", "01"],
        ],
        [45, 340, 85],
    )
    flow.paragraph(
        "Tôi cam đoan các thông tin trên là đúng sự thật và chịu trách nhiệm trước pháp luật "
        "về nội dung đã khai."
    )
    flow.spacer(10)
    flow.paragraph("Hà Nội, ngày 20 tháng 10 năm 2026", "right")
    flow.paragraph("Người làm đơn", "right")
    flow.paragraph("(Ký và ghi rõ họ tên)", "right")
    flow.spacer(24)
    flow.paragraph("Nguyễn Văn Mẫu", "right")

    source = root / "pdfs" / "don_de_nghi.pdf"
    pages = _build_story(flow, source, title="Đơn đề nghị xác nhận thông tin")
    return _write_doc(
        root,
        "don_de_nghi",
        flow,
        source,
        kind="digital",
        tags=["digital", "form", "tables", "administrative"],
        title="Đơn hành chính",
        pages=pages,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_dataset(out: str | Path, force: bool = False) -> BenchmarkDataset:
    root = Path(out).resolve()
    manifest = root / "manifest.json"
    if manifest.exists() and not force:
        raise FileExistsError(f"Dataset đã tồn tại: {manifest} (dùng --force để ghi đè)")

    for sub in ("pdfs", "refs"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    docs = [
        _doc_financial(root),
        _doc_scanned(root),
        _doc_tables(root),
        _doc_form(root),
    ]

    manifest_data = {
        "name": DATASET_NAME,
        "version": DATASET_VERSION,
        "language": "vi",
        "license": "CC0-1.0",
        "description": (
            "Dataset tiếng Việt tổng hợp cho benchmark PDF→DOCX: báo cáo tài chính, "
            "thông báo scan, báo cáo nhiều bảng và đơn hành chính. Ground truth do "
            "pdf2word biên soạn, không chứa dữ liệu khách hàng."
        ),
        "docs": [
            {
                "id": doc.id,
                "pdf": f"pdfs/{doc.pdf.name}",
                "reference": f"refs/{doc.reference.name}",
                "truth": f"refs/{doc.truth.name}",
                "kind": doc.kind,
                "pages": doc.pages,
                "tags": doc.tags,
            }
            for doc in docs
        ],
    }
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_readme(root, manifest_data, docs)
    return _load_for_return(manifest)


def _load_for_return(manifest: Path):
    from .dataset import load_dataset

    return load_dataset(manifest)


def _write_readme(root: Path, manifest: dict, docs: list[BuiltDoc]) -> None:
    lines = [
        f"# Dataset `{manifest['name']}` v{manifest['version']}",
        "",
        manifest["description"],
        "",
        f"- Ngôn ngữ: `{manifest['language']}`",
        f"- Giấy phép: `{manifest['license']}`",
        f"- Số tài liệu: {len(docs)}",
        "",
        "## Nội dung",
        "",
        "| ID | Kiểu | Trang | Tags |",
        "|---|---|---:|---|",
    ]
    for doc in docs:
        lines.append(f"| `{doc.id}` | {doc.kind} | {doc.pages} | {', '.join(doc.tags)} |")
    lines.extend(
        [
            "",
            "## Cấu trúc",
            "",
            "```",
            "vi_core/",
            "  manifest.json",
            "  pdfs/*.pdf",
            "  refs/*.txt   # text tham chiếu, đúng thứ tự nội dung",
            "  refs/*.json  # heading, TOC, header/footer và bảng ground truth",
            "```",
            "",
            "## Tạo lại",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe -m benchmark make-data --force",
            "```",
            "",
            "Dữ liệu được sinh bằng ReportLab với font Arial (có dấu tiếng Việt). "
            "Bản scan được render từ PDF nguồn ở 200 DPI rồi nhúng ảnh vào PDF mới.",
            "",
            "## Lưu ý",
            "",
            "- Đây là dữ liệu tổng hợp, không phải tài liệu thật của khách hàng.",
            "- Text tham chiếu không chứa header/footer của bản scan; các phần đó được chấm riêng.",
            "- Bảng ground truth giữ đúng số hàng/cột; ô bị gộp để trống ở vị trí bị chiếm.",
            "- Có thể thêm tài liệu mới bằng cách tạo PDF + refs và khai báo trong `manifest.json`.",
            "",
        ]
    )
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sinh dataset benchmark tiếng Việt")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "data" / "vi_core"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    dataset = build_dataset(args.out, force=args.force)
    print(f"Đã tạo {len(dataset.docs)} tài liệu tại {dataset.path}")
