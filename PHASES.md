# Lộ trình phát triển (đã chốt theo bối cảnh: desktop offline, CPU, nội bộ, báo cáo có bảng)

## Ràng buộc đã chốt
- Chạy offline hoàn toàn, không gọi cloud.
- Máy văn phòng **không GPU** → OCR phải là loại CPU-friendly (Tesseract/PP-OCR mobile).
- Dùng nội bộ → ưu tiên license permissive nhưng vẫn tránh GPL/AGPL để sau này khỏi phải viết lại.
- Trọng tâm nghiệp vụ: **báo cáo có bảng** (báo cáo tài chính, báo cáo tháng, biểu mẫu).

## Nguyên tắc kiến trúc
1. Mọi engine (digital / OCR / fallback) đều đổ về **IDM** (Intermediate Document Model).
2. Renderer DOCX chỉ đọc IDM → đổi engine không phá renderer.
3. Mọi thay đổi pipeline phải chạy qua bộ benchmark (`benchmark/`).

---

## Phase 1 — Nền tảng + PDF số → DOCX  ⟵ HOÀN THÀNH
Mục tiêu: báo cáo xuất Word "dùng được ngay", chạy offline, không phụ thuộc OCR.

- [x] Repo skeleton, IDM schema (page/block/span/table/cell).
- [x] Router nhận biết PDF có text layer hay không.
- [x] Extractor digital: pypdfium2 (render ảnh) + pdfminer.six (chữ, font, màu, toạ độ).
- [x] Dựng dòng → đoạn, nhận diện heading, bullet/numbering, canh lề, thụt đầu dòng.
- [x] **Bảng kẻ khung**: dò đường kẻ ngang/dọc, dựng grid, merge cell (rowspan/colspan).
      Hỗ trợ cả 2 kiểu vẽ phổ biến: line mảnh (Word) và rect tô mỏng (Excel export).
- [x] Ảnh raster + figure vector (chart) → PNG 200 DPI.
- [x] DOCX writer: style thật (Normal/Heading/Title), bảng Word thật, header row lặp lại.
- [x] CLI: `convert`, `inspect` (dump IDM JSON), `eval` (CER vs text tham chiếu).
- [x] Bộ sinh PDF mẫu + smoke test tự động.

**Giới hạn đã biết của Phase 1:** chưa OCR, chưa xử lý header/footer thành header Word,
chưa nhận bảng không kẻ, chưa hyperlink, chưa TOC, chưa xuất PDF preview để so sánh.

## Phase 2 — Bố cục nâng cao (digital)  ⟵ HOÀN THÀNH PHẦN CỐT LÕI
- [x] Header/footer phát hiện theo text lặp lại → section header/footer Word.
- [x] Số trang: field `PAGE`, tự bù lệch theo số in trên PDF.
- [x] Mục lục: dòng "tiêu đề ... trang" → tab phải + dot leader + số trang.
- [x] Nhận diện trang bìa, giảm heading giả.
- [ ] Multi-column nâng cao (XY-cut nhiều cấp), sidebar, footnote.
- [ ] Bảng không kẻ (dùng alignment/cluster, không cần model).
- [ ] Bảng lồng bảng, bảng trải 2 trang (gộp hoặc lặp header), bảng xoay 90°.
- [ ] Hyperlink từ annotation, bookmark, TOC tự sinh.
- [ ] Font substitution theo metrics (đo width, chọn font thay thế gần nhất).
- [ ] Chế độ **Exact layout** (text frame theo toạ độ) + chế độ Reflow.
- [ ] Chuẩn hoá font tiếng Việt lỗi thời (VNI/TCVN3 → Unicode).

## Phase 3 — OCR cho PDF scan (CPU)  ⟵ HOÀN THÀNH PHẦN CỐT LÕI
- [x] Render 300 DPI, xám hoá + autocontrast.
- [x] Tesseract 5 (vie+eng) offline; tessdata đóng gói trong `models/tessdata`.
- [x] Ghép kết quả OCR vào cùng IDM (block + toạ độ + confidence).
- [x] Dò bảng kẻ khung trực tiếp trên ảnh, che vùng bảng rồi OCR thân trang.
- [x] Gán chữ vào ô, merge ô, dòng tiêu đề; lọc bảng giả (khung viền, con dấu).
- [x] Mục lục từ OCR (kể cả khi mất dot leader hoặc bị gộp dòng).
- [ ] Deskew / khử nhiễu / nhị phân thích nghi cho bản scan nghiêng, mờ.
- [ ] Đánh dấu vùng confidence thấp để người dùng rà lại.
- [ ] Tự nhận diện ngôn ngữ, xử lý trang cong nhẹ.

### Phase 3b — Engine DeepDoc + VietOCR ONNX (CPU)  ⟵ HOÀN THÀNH
- [x] Vendor adapter ONNX-only cho `hoaivannguyen/deepdoc_vietocr`, không dùng torch,
      không dùng `module/ocr.py`/`ocr_onnx.py` lỗi của upstream.
- [x] `det.onnx` (PaddleOCR DB) dò dòng + VietOCR seq2seq ONNX nhận dạng tiếng Việt.
- [x] Bảng có đường kẻ: dò bằng `grid.py`, crop rồi `tsr.onnx` lấy hàng/cột/header/ô gộp.
- [x] `layout.onnx` (AGPL-3.0) dò vùng bảng, kể cả bảng không kẻ; crop đưa qua `tsr.onnx`.
- [x] Gộp mục lục (tiêu đề + dot leader + số trang), nhận dạng số trang bằng decoder
      chỉ cho phép chữ số; bỏ mẩu dot-leader bị OCR thành chữ.
- [x] Chuẩn hoá cỡ chữ theo trang/tài liệu, regex số mục "loose" riêng cho DeepDoc.
- [x] `engine="deepdoc"` trong pipeline/service/CLI/GUI, extra `.[deepdoc]`, đóng gói model.
- [x] Đóng gói kèm `layout.onnx`; tài liệu ghi rõ nghĩa vụ AGPL-3.0 khi phân phối.
- [ ] Tăng độ chính xác TSR cho bảng không kẻ (lệch cột/gộp ô) và bảng nhiều tầng header.

## Phase 4 — Desktop app  ⟵ HOÀN THÀNH
- [x] Vỏ app **PySide6** (dùng lại pipeline Python; không cần sidecar Tauri).
- [x] Kéo thả nhiều file hoặc thư mục, hàng đợi, tiến trình từng trang, huỷ giữa chừng.
- [x] Preview song song PDF ↔ DOCX (LibreOffice headless nếu có, fallback văn bản/bảng).
- [x] Tự chọn + hiện file mới thêm, hiển thị đường dẫn đầy đủ.
- [x] Nút quan trọng nổi bật (Chuyển đổi / Dừng / ZIP), khoá điều khiển khi đang chạy.
- [x] Ẩn cửa sổ terminal của tiến trình con (Tesseract, LibreOffice, Explorer).
- [x] Hậu kỳ: chuẩn hoá dấu thanh tiếng Việt, từ điển sửa lỗi, space/dấu câu,
      giữ space giữa các span, làm sạch tên font, ép font, đồng nhất font/cỡ theo dòng.
- [x] Icon cho toolbar/nút thao tác; ẩn tuỳ chọn chuẩn hoá khỏi UI (luôn bật).
- [x] Tuỳ chọn: trang, chế độ, OCR, bảng, ảnh, ngắt trang, giữ số trang; batch xuất ZIP.
- [x] Nhớ tuỳ chọn qua `QSettings`; log + tổng tiến trình.
- [x] Đóng gói PyInstaller one-folder (`packaging/build.ps1`), kèm `models/tessdata`.
- [ ] Kéo thả để sắp xếp lại thứ tự trong hàng đợi.
- [ ] Sửa nhanh lỗi từng trang trong app (dời sang Phase 5).

## Phase 5 — QA, benchmark, đóng gói  ⟵ ĐANG LÀM
- [x] Dataset tiếng Việt `vi_core` (4 tài liệu, 10 trang): báo cáo digital, báo cáo scan,
      báo cáo nhiều bảng, đơn hành chính; có text + heading + TOC + header/footer + bảng ground truth.
- [x] Bộ metric: CER/WER, độ giống bảng, F1 heading, F1 TOC, header/footer, composite có trọng số.
- [x] Runner so sánh nhiều pipeline, tái dùng kết quả, xuất `results.json` / `report.md` / `summary.csv`.
- [x] Adapter pipeline built-in (auto/digital/OCR/DeepDoc) + external command (Marker, Docling, MinerU, ...),
      tự chuyển Markdown/TXT/HTML → DOCX để chấm cùng thang điểm.
- [x] CLI `python -m benchmark {run,list,validate,make-data}` và config mẫu.
- [ ] Mở rộng dataset lên 40–60 PDF thật đã ẩn danh, thêm TEDS/SSIM và chạy trong CI.
- [ ] UI rà soát vùng lỗi, convert lại từng trang.
- [ ] Installer offline (kèm model + tessdata), tự kiểm tra phụ thuộc.
- [ ] Tối ưu tốc độ: cache layout, đa tiến trình theo trang.
