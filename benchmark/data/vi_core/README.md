# Dataset `vi_core` v1.0

Dataset tiếng Việt tổng hợp cho benchmark PDF→DOCX: báo cáo tài chính, thông báo scan, báo cáo nhiều bảng và đơn hành chính. Ground truth do pdf2word biên soạn, không chứa dữ liệu khách hàng.

- Ngôn ngữ: `vi`
- Giấy phép: `CC0-1.0`
- Số tài liệu: 4

## Nội dung

| ID | Kiểu | Trang | Tags |
|---|---|---:|---|
| `bao_cao_tai_chinh` | digital | 3 | digital, report, tables, toc, headings |
| `thong_bao_tuyen_sinh_scan` | scanned | 3 | scanned, ocr, header-footer, report |
| `bao_cao_bang` | digital | 3 | digital, report, tables, headings |
| `don_de_nghi` | digital | 1 | digital, form, tables, administrative |

## Cấu trúc

```
vi_core/
  manifest.json
  pdfs/*.pdf
  refs/*.txt   # text tham chiếu, đúng thứ tự nội dung
  refs/*.json  # heading, TOC, header/footer và bảng ground truth
```

## Tạo lại

```powershell
.\.venv\Scripts\python.exe -m benchmark make-data --force
```

Dữ liệu được sinh bằng ReportLab với font Arial (có dấu tiếng Việt). Bản scan được render từ PDF nguồn ở 200 DPI rồi nhúng ảnh vào PDF mới.

## Lưu ý

- Đây là dữ liệu tổng hợp, không phải tài liệu thật của khách hàng.
- Text tham chiếu không chứa header/footer của bản scan; các phần đó được chấm riêng.
- Bảng ground truth giữ đúng số hàng/cột; ô bị gộp để trống ở vị trí bị chiếm.
- Có thể thêm tài liệu mới bằng cách tạo PDF + refs và khai báo trong `manifest.json`.
