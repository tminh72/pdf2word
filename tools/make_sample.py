"""Sinh PDF mẫu để test: báo cáo có bảng, heading, bullet, ảnh, 2 cột.

Chạy: python tools/make_sample.py
Yêu cầu: reportlab, Pillow (chỉ dùng khi phát triển).
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "sample")
REPORT_SRC = os.path.join(OUT_DIR, "report.pdf")
FONTS = "C:/Windows/Fonts"

REF: list[str] = []


def _register_fonts() -> tuple[str, str, str, str]:
    reg = os.path.join(FONTS, "arial.ttf")
    bold = os.path.join(FONTS, "arialbd.ttf")
    italic = os.path.join(FONTS, "ariali.ttf")
    bolditalic = os.path.join(FONTS, "arialbi.ttf")
    pdfmetrics.registerFont(TTFont("Sample", reg))
    pdfmetrics.registerFont(TTFont("Sample-Bold", bold))
    pdfmetrics.registerFont(TTFont("Sample-Italic", italic))
    pdfmetrics.registerFont(TTFont("Sample-BoldItalic", bolditalic))
    pdfmetrics.registerFontFamily("Sample", normal="Sample", bold="Sample-Bold", italic="Sample-Italic", boldItalic="Sample-BoldItalic")
    return "Sample", "Sample-Bold", "Sample-Italic", "Sample-BoldItalic"


def _make_chart(path: str) -> None:
    img = Image.new("RGB", (620, 320), "white")
    d = ImageDraw.Draw(img)
    ax_x0, ax_y0, ax_x1, ax_y1 = 60, 260, 590, 40
    d.line([(ax_x0, ax_y0), (ax_x1, ax_y0)], fill="black", width=2)
    d.line([(ax_x0, ax_y0), (ax_x0, ax_y1)], fill="black", width=2)
    bars = [120, 180, 150, 230, 200, 260]
    labels = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
    step = (ax_x1 - ax_x0 - 30) / len(bars)
    for i, v in enumerate(bars):
        x = ax_x0 + 18 + i * step
        d.rectangle([x, ax_y0 - v, x + step * 0.6, ax_y0], fill="#3b82f6")
        d.text((x, ax_y0 + 8), labels[i], fill="black")
    img.save(path)


def build_excel_style() -> None:
    """Bảng vẽ bằng rect tô mỏng (kiểu Excel export), không dùng line stroke."""
    from reportlab.pdfgen import canvas

    path = os.path.join(OUT_DIR, "excel_style.pdf")
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(60, 780, "BANG CHI PHI THANG 10")

    x0, y0 = 60, 640
    col_w = [140, 100, 100]
    rows = [
        ["Khoan muc", "Ke hoach", "Thuc te"],
        ["Nguyen vat lieu", "120", "128"],
        ["Nhan cong", "45", "44"],
        ["Van chuyen", "18", "21"],
    ]
    row_h = 24
    height = row_h * len(rows)

    # text
    for r, row in enumerate(rows):
        cy = y0 + height - (r + 1) * row_h + 7
        cx = x0
        for ci, value in enumerate(col_w):
            c.drawString(cx + 6, cy, row[ci])
            cx += value

    # duong ke = rect to mong
    def hline(x, y, w):
        c.rect(x, y, w, 0.7, stroke=0, fill=1)

    def vline(x, y, h):
        c.rect(x, y, 0.7, h, stroke=0, fill=1)

    total_w = sum(col_w)
    for r in range(len(rows) + 1):
        hline(x0, y0 + r * row_h, total_w)
    cx = x0
    for w in col_w:
        vline(cx, y0, height)
        cx += w
    vline(x0 + total_w, y0, height)

    c.showPage()
    c.save()

    with open(os.path.join(OUT_DIR, "excel_reference.txt"), "w", encoding="utf-8") as f:
        f.write("BANG CHI PHI THANG 10\n")
        for row in rows:
            f.write("\t".join(row) + "\n")
    print("Created:", path)


def build() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    normal, bold, italic, _ = _register_fonts()
    chart = os.path.join(OUT_DIR, "chart.png")
    _make_chart(chart)

    styles = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=18, leading=24, alignment=TA_CENTER, spaceAfter=8),
        "subtitle": ParagraphStyle("subtitle", fontName=normal, fontSize=11.5, leading=16, alignment=TA_CENTER, spaceAfter=18),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=14, leading=19, spaceBefore=14, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12, leading=17, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=normal, fontSize=11, leading=16.5, alignment=TA_JUSTIFY, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", fontName=normal, fontSize=11, leading=16.5, leftIndent=22, bulletIndent=6, spaceAfter=3),
        "caption": ParagraphStyle("caption", fontName=italic, fontSize=9.5, leading=13, alignment=TA_CENTER, spaceBefore=4),
    }

    story: list = []
    story.append(Paragraph("BÁO CÁO TÀI CHÍNH QUÝ III/2026", styles["title"]))
    REF.append("BÁO CÁO TÀI CHÍNH QUÝ III/2026")
    story.append(Paragraph("Công ty TNHH Thương mại Mẫu Việt Nam", styles["subtitle"]))
    REF.append("Công ty TNHH Thương mại Mẫu Việt Nam")

    story.append(Paragraph("1. Tổng quan hoạt động", styles["h1"]))
    REF.append("1. Tổng quan hoạt động")
    intro = (
        "Trong quý III năm 2026, công ty duy trì đà tăng trưởng ổn định với doanh thu thuần "
        "đạt 128,4 tỷ đồng, tăng 12,6% so với cùng kỳ năm trước. Lợi nhuận sau thuế ghi nhận "
        "9,7 tỷ đồng nhờ tối ưu chi phí vận hành và cơ cấu lại danh mục sản phẩm."
    )
    story.append(Paragraph(intro, styles["body"]))
    REF.append(intro)

    para2 = (
        "Ban lãnh đạo đánh giá mức tăng trưởng này phù hợp với kế hoạch đề ra. Tuy nhiên, "
        "áp lực tỷ giá và chi phí nguyên vật liệu vẫn là rủi ro cần theo dõi trong quý tới."
    )
    story.append(Paragraph(para2, styles["body"]))
    REF.append(para2)

    story.append(Paragraph("2. Kết quả kinh doanh theo tháng", styles["h1"]))
    REF.append("2. Kết quả kinh doanh theo tháng")

    header = ["Tháng", "Doanh thu (tỷ đồng)", "Chi phí (tỷ đồng)", "Lợi nhuận (tỷ đồng)", "Ghi chú"]
    rows = [
        ["Tháng 7", "41,2", "36,1", "3,1", ""],
        ["Tháng 8", "43,5", "37,8", "3,4", ""],
        ["Tháng 9", "43,7", "38,2", "3,2", "Số liệu tạm tính"],
    ]
    data = [header] + rows
    table = Table(data, colWidths=[60, 105, 95, 110, 78], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")),
                ("FONTNAME", (0, 0), (-1, 0), bold),
                ("FONTNAME", (0, 1), (-1, -1), normal),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ALIGN", (1, 1), (3, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    for row in data:
        REF.append("\t".join(cell for cell in row if cell))

    story.append(Spacer(1, 10))
    story.append(Paragraph("3. Cơ cấu chi phí", styles["h1"]))
    REF.append("3. Cơ cấu chi phí")
    story.append(RLImage(chart, width=420, height=217))
    story.append(Paragraph("Hình 1: Doanh thu theo quý", styles["caption"]))
    REF.append("Hình 1: Doanh thu theo quý")

    story.append(Paragraph("3.1. Các khoản chi phí trọng yếu", styles["h2"]))
    REF.append("3.1. Các khoản chi phí trọng yếu")
    bullets = [
        ("Chi phí nguyên vật liệu", "chiếm 54,2% tổng chi phí, tăng nhẹ so với quý trước."),
        ("Chi phí nhân công", "chiếm 18,7% và đã được kiểm soát nhờ tự động hoá."),
        ("Chi phí bán hàng", "chiếm 12,1%, tập trung vào kênh phân phối hiện đại."),
    ]
    for b, detail in bullets:
        story.append(Paragraph(f"<b>{b}</b> {detail}", styles["bullet"], bulletText="\u2022"))
        REF.append(f"• {b} {detail}")

    story.append(Spacer(1, 8))
    story.append(Paragraph("3.2. Bảng cân đối rút gọn", styles["h2"]))
    REF.append("3.2. Bảng cân đối rút gọn")
    bal = Table(
        [
            ["Chỉ tiêu", "Đầu kỳ", "Cuối kỳ", "Chênh lệch"],
            ["Tài sản ngắn hạn", "96,3", "104,8", "+8,5"],
            ["Tài sản dài hạn", "71,2", "74,1", "+2,9"],
            ["Tổng tài sản", "167,5", "178,9", "+11,4"],
        ],
        colWidths=[160, 90, 90, 90],
    )
    bal.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EFEFEF")),
                ("FONTNAME", (0, 0), (-1, 0), bold),
                ("FONTNAME", (0, 1), (-1, -1), normal),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(bal)
    for row in bal._cellvalues:
        REF.append("\t".join(row))

    story.append(Spacer(1, 8))
    story.append(Paragraph("3.3. Bảng tổng hợp theo vùng (có ô gộp)", styles["h2"]))
    REF.append("3.3. Bảng tổng hợp theo vùng (có ô gộp)")
    merged_data = [
        ["Vùng", "Doanh thu", "Chi phí", "Ghi chú"],
        ["Miền Bắc", "42,1", "36,0", "Ghi chú chung"],
        ["", "", "ổn định", ""],
        ["Miền Nam", "48,6", "41,2", "Mở rộng"],
        ["Tổng cộng", "90,7", "77,2", ""],
    ]
    merged = Table(merged_data, colWidths=[90, 90, 90, 150])
    merged.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2EFDA")),
                ("FONTNAME", (0, 0), (-1, 0), bold),
                ("FONTNAME", (0, 1), (-1, -1), normal),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("SPAN", (0, 1), (0, 2)),
                ("SPAN", (3, 1), (3, 2)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(merged)
    for row in merged_data:
        REF.append("\t".join(cell for cell in row if cell))

    story.append(PageBreak())
    story.append(Paragraph("4. Kế hoạch quý IV", styles["h1"]))
    REF.append("4. Kế hoạch quý IV")
    plan = (
        "Công ty đặt mục tiêu doanh thu 140 tỷ đồng cho quý IV, tương đương mức tăng 9% so với "
        "quý III. Trọng tâm là mở rộng kênh bán hàng trực tuyến và hoàn thiện hệ thống quản trị "
        "tồn kho theo thời gian thực."
    )
    story.append(Paragraph(plan, styles["body"]))
    REF.append(plan)

    doc = SimpleDocTemplate(
        os.path.join(OUT_DIR, "report.pdf"),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="Báo cáo tài chính mẫu",
    )
    doc.build(story)

    with open(os.path.join(OUT_DIR, "reference.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(REF))
    print("Created:", os.path.join(OUT_DIR, "report.pdf"))


def build_scanned() -> None:
    """PDF scan giả lập: render 1 trang báo cáo thành ảnh rồi nhét vào PDF mới."""
    import pypdfium2 as pdfium
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    src = pdfium.PdfDocument(REPORT_SRC)
    page = src[0]
    bitmap = page.render(scale=150 / 72.0)
    img_path = os.path.join(OUT_DIR, "_scan_page.png")
    bitmap.to_pil().save(img_path)
    bitmap.to_pil().close()
    bitmap.close()
    page.close()
    src.close()

    path = os.path.join(OUT_DIR, "scanned.pdf")
    c = canvas.Canvas(path, pagesize=A4)
    c.drawImage(img_path, 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    os.remove(img_path)
    print("Created:", path)


if __name__ == "__main__":
    build()
    build_excel_style()
    build_scanned()
