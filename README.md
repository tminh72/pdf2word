# pdf2word

Chuyển PDF sang Word (.docx) giữ bố cục — chạy **offline, trên CPU**, không gọi cloud.
Hướng tới báo cáo có bảng (báo cáo tài chính, báo cáo tháng, biểu mẫu).

## Trạng thái: Phase 1 + 2 + OCR (lõi Phase 3) + app desktop (Phase 4) + benchmark (Phase 5)

Đã làm được với **PDF có text layer**:

- Trích chữ, font, cỡ chữ, bold/italic, màu, toạ độ.
- Dựng **đoạn văn** (gom dòng, thụt đầu dòng, canh lề) và **heading** (theo cỡ chữ + số mục 1./2.1.).
- **Bảng kẻ khung**: dựng grid thật, **merge ô** (rowspan/colspan), lặp dòng tiêu đề trong Word.
  Hỗ trợ cả bảng vẽ bằng nét (Word) và bảng vẽ bằng rect tô mỏng (Excel export).
- **Bullet/numbering** (kể cả bullet bị PDF nhúng thành glyph lạ dạng `(cid:127)`).
- **Ảnh và biểu đồ vector** → PNG 200 DPI, chèn đúng vị trí.
- Xuất DOCX dùng **style thật** (`Title`, `Heading 1..4`, `Normal`) để còn mục lục/điều hướng.

Đã làm được thêm ở **Phase 2** (áp dụng cho cả digital và scan):

- **Header/footer lặp lại** → header/footer Word thật, tự bỏ khỏi nội dung.
- **Số trang**: chèn field `PAGE`, tự bù lệch (`= PAGE - 2`) theo số in trên báo cáo.
- **Mục lục**: dòng "tiêu đề ... trang" → tab phải + dot leader + số trang.
- Nhận diện **trang bìa** (title duy nhất), giảm heading giả.

Đã làm được với **PDF scan** (OCR offline, CPU):

- Tesseract 5 (`vie+eng`), render 300 DPI, xám hoá + autocontrast.
- Dò **bảng kẻ khung** trực tiếp trên ảnh (numpy run detection), che vùng bảng rồi OCR thân trang.
- OCR từng vùng bảng với `psm 6`, gán chữ vào ô, suy ra `rowspan/colspan`, dòng tiêu đề.
- Lọc bảng giả (khung trang trí, con dấu, cột quá hẹp, bảng rỗng).
- Tách **mục lục** kể cả khi Tesseract làm mất dấu chấm leader hoặc gộp 2 mục vào 1 dòng.

Đã thêm **engine OCR DeepDoc + VietOCR ONNX** (CPU, offline):

- `det.onnx` (PaddleOCR DB) dò dòng chữ; VietOCR seq2seq ONNX nhận dạng tiếng Việt.
- Bảng kẻ khung dò bằng thuật toán đường kẻ sẵn có, sau đó `tsr.onnx` lấy hàng/cột/header
  và ô gộp; không cần torch.
- Tự gộp mục lục bị tách thành tiêu đề + dấu chấm + số trang, nhận dạng số trang bằng
  decoder chỉ cho phép chữ số.
- Chuẩn hoá cỡ chữ theo trang/tài liệu để giảm heading giả do chiều cao hộp OCR.
- Dựng **mục lục** kể cả khi dấu chấm leader bị mất.
- **`layout.onnx` (AGPL-3.0)** dò vùng bảng, kể cả bảng không kẻ; vùng bảng đưa qua
  `tsr.onnx` để lấy hàng/cột/header. Xem `models/deepdoc/NOTICE.md` để biết nghĩa vụ
  license khi phân phối.

Đã có **app desktop** (PySide6, Phase 4):

- **Kéo & thả** nhiều PDF hoặc cả thư mục; file mới thêm được chọn sẵn và hiện ngay
  bản xem trước + đường dẫn đầy đủ.
- Hàng đợi chuyển đổi, tiến trình từng trang; nút **Chuyển đổi** / **Dừng** được làm nổi bật.
- Xem trước **PDF gốc ↔ DOCX kết quả**; DOCX render bằng LibreOffice headless nếu có,
  nếu không thì xem dạng văn bản + bảng.
- Tuỳ chọn: chế độ (auto/digital/OCR), khoảng trang, DPI OCR, ngôn ngữ, bảng, ảnh,
  ngắt trang, giữ số trang; xuất hàng loạt ra ZIP.
- **Chuẩn hoá hậu kỳ** (chạy mặc định, không cần bật): sửa vị trí dấu thanh tiếng Việt
  (hoà→hòa, thuỷ→thủy, đựơc→được), áp dụng từ điển sửa lỗi, gộp space thừa,
  bỏ space trước dấu câu, **giữ space giữa các span để không dính chữ**,
  làm font/cỡ chữ đồng nhất theo dòng; có thể **ép font** (Times New Roman, Arial…).
- Không bật cửa sổ terminal khi chạy bản đóng gói; các tiến trình con
  (Tesseract, LibreOffice) được ẩn cửa sổ.
- Đóng gói one-folder bằng PyInstaller, chạy offline.

Chưa có (xem `PHASES.md`): hyperlink, chế độ "exact layout". Bảng không kẻ chỉ được
dựng ở engine `deepdoc` (nhờ `layout.onnx`); digital/Tesseract vẫn cần đường kẻ.

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# tuỳ chọn: cài như package để có lệnh pdf2word
.\.venv\Scripts\python.exe -m pip install -e .

# app desktop (PySide6)
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
# hoặc: .\.venv\Scripts\python.exe -m pip install -e ".[desktop]"

# engine DeepDoc + VietOCR ONNX (CPU; onnxruntime + opencv + pyclipper)
.\.venv\Scripts\python.exe -m pip install -e ".[deepdoc]"
# cài tất cả: .\.venv\Scripts\python.exe -m pip install -e ".[desktop,deepdoc]"
```

### OCR (chỉ cần cho PDF scan)

1. Cài **Tesseract 5** cho Windows (mặc định `C:\Program Files\Tesseract-OCR`).
2. Đặt model tiếng Việt vào `models/tessdata/`:
   `vie.traineddata`, `eng.traineddata`, `osd.traineddata` (và thư mục `configs/`).
   Repo đã kèm sẵn bộ này; nếu thiếu, tải `vie` từ
   `https://github.com/tesseract-ocr/tessdata_best`.
3. Nếu Tesseract ở chỗ khác, dùng `--tesseract` hoặc biến môi trường
   `PDF2WORD_TESSERACT`; thư mục model đổi bằng `--tessdata` / `PDF2WORD_TESSDATA`.

### DeepDoc + VietOCR (OCR thay thế, CPU)

Repo đã kèm sẵn model ONNX trong `models/deepdoc/` và engine `deepdoc` không cần
Tesseract. Chỉ cần cài extra `deepdoc` ở trên.

| File | Vai trò |
|---|---|
| `det.onnx` | dò dòng chữ (PaddleOCR DB) |
| `cnn.onnx`, `encoder.onnx`, `decoder.onnx` | VietOCR seq2seq nhận dạng tiếng Việt |
| `tsr.onnx` | nhận dạng cấu trúc bảng (hàng/cột/header/ô gộp) |
| `layout.onnx` | dò vùng bảng/tiêu đề/hình (kể cả bảng không kẻ) — **AGPL-3.0** |
| `vocab.txt` | bộ ký tự tiếng Việt cho decoder |

Đổi thư mục model bằng `--deepdoc-model` hoặc biến môi trường `PDF2WORD_DEEPDOC`.
Các model OCR/TSR là **Apache-2.0**; `layout.onnx` là **AGPL-3.0**.
Xem `models/deepdoc/NOTICE.md`.

> **Lưu ý license:** `layout.onnx` là **AGPL-3.0** và được nạp trực tiếp trong app.
> Khi **phân phối** bản đóng gói (`dist\pdf2word`, installer, gửi cho người khác),
> toàn bộ tác phẩm kết hợp phải tuân thủ AGPL-3.0 — phải cung cấp source code đầy đủ
> theo AGPL. Dùng nội bộ không phân phối có thể không kích hoạt nghĩa vụ này, nhưng
> nên kiểm tra với pháp lý. Nếu không chấp nhận AGPL, xoá `models/deepdoc/layout.onnx`;
> engine sẽ quay về chỉ dò bảng có đường kẻ.

### Sửa lỗi chính tả tuỳ chỉnh

`models/spellfix.txt` chứa các cặp `sai => đúng`, không phân biệt hoa/thường.
Thêm lỗi OCR/typo chắc chắn vào đây (tránh tên riêng). Đổi đường dẫn bằng biến
môi trường `PDF2WORD_SPELLFIX`. Ngoài từ điển, vị trí dấu thanh được chuẩn hoá tự động.

## Dùng

```powershell
# chuyển đổi (tự nhận digital hay scan)
.\.venv\Scripts\python.exe -m pdf2word convert bao-cao.pdf -o bao-cao.docx

# ép engine + chỉnh OCR
.\.venv\Scripts\python.exe -m pdf2word convert scan.pdf --engine ocr --ocr-dpi 300 --lang vie+eng
.\.venv\Scripts\python.exe -m pdf2word convert bao-cao.pdf --engine digital

# OCR bằng DeepDoc + VietOCR ONNX (không cần Tesseract)
.\.venv\Scripts\python.exe -m pdf2word convert scan.pdf --engine deepdoc --ocr-dpi 300
# đổi thư mục model nếu không dùng models/deepdoc/
.\.venv\Scripts\python.exe -m pdf2word convert scan.pdf --engine deepdoc --deepdoc-model D:\models\deepdoc

# chọn trang, bỏ ảnh, bỏ bảng, giữ số trang ở lề
.\.venv\Scripts\python.exe -m pdf2word convert bao-cao.pdf --pages 1-5 --no-images --keep-page-numbers

# ép font và tắt chuẩn hoá chính tả hậu kỳ
.\.venv\Scripts\python.exe -m pdf2word convert bao-cao.pdf --font "Times New Roman"
.\.venv\Scripts\python.exe -m pdf2word convert bao-cao.pdf --no-polish

# xem PDF là digital hay scan
.\.venv\Scripts\python.exe -m pdf2word profile bao-cao.pdf

# dump IDM để debug
.\.venv\Scripts\python.exe -m pdf2word convert in.pdf --dump-idm in.idm.json

# chấm điểm với text tham chiếu
.\.venv\Scripts\python.exe -m pdf2word eval out.docx reference.txt
```

### App desktop

```powershell
# chạy trực tiếp
.\.venv\Scripts\python.exe -m pdf2word.gui
# chạy không kèm cửa sổ terminal (Windows)
.\.venv\Scripts\pythonw.exe -m pdf2word.gui
# hoặc sau khi pip install -e ".[desktop]"
pdf2word-desktop
```

Kéo & thả PDF (hoặc thư mục) vào danh sách → file đầu tiên được chọn và hiện ngay
bản xem trước. Chọn tuỳ chọn → **Chuyển đổi**. Kết quả nằm cạnh file gốc nếu để trống
"Thư mục ra"; nút **Đóng gói ZIP…** gom toàn bộ kết quả. Khung bên phải có 3 tab:
PDF gốc, DOCX kết quả, Nhật ký.

- **Font chữ**: “Giữ font gốc (đã chuẩn hoá)” chỉ làm sạch tên font PostScript
  (`ABCDEF+TimesNewRomanPSMT` → `Times New Roman`); chọn font cụ thể để ép toàn bộ DOCX.
- **Chuẩn hoá chính tả & khoảng trắng**: chạy mặc định, không có nút bật/tắt để UI gọn;
  sửa dấu thanh, từ điển `models/spellfix.txt`, space/dấu câu, ligature.
- **Đồng nhất theo dòng**: giữ đúng space giữa các span (tránh `tỷđồng`, `phẩm.Ban`),
  thống nhất font và cỡ chữ trong cùng dòng; cỡ chênh lệch lớn (chú thích, superscript)
  được giữ nguyên.
- **Xem DOCX dạng trang in** cần LibreOffice (tự dò `soffice` trong PATH hoặc
  `C:\Program Files\LibreOffice`). Không có LibreOffice thì app vẫn xem được
  văn bản + bảng, chỉ không render đúng trang.
- **PDF scan** cần Tesseract 5 như phần OCR ở trên; app báo lỗi rõ nếu thiếu.

Đóng gói bản chạy không cần Python:

```powershell
.\packaging\build.ps1                    # dist\pdf2word\pdf2word.exe
.\packaging\build.ps1 -BundleTesseract   # copy luôn Tesseract-OCR vào dist
```

Bản đóng gói là **one-folder** (không phải one-file) để khởi động nhanh và dễ
thay model. Máy đích cần Tesseract 5, trừ khi build kèm `-BundleTesseract`.

Tạo PDF mẫu + chạy test:

```powershell
.\.venv\Scripts\python.exe tools\make_sample.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Kết quả trên bộ mẫu hiện tại: **CER = 0.00%**, 3 bảng (1 bảng có ô gộp), 1 biểu đồ,
heading/bullet đúng cấu trúc. 43 test (5 DeepDoc, 3 OCR, 4 GUI, 14 hậu kỳ,
9 benchmark, 8 pipeline/khác) đều pass.

Kết quả trên báo cáo scan thật 30 trang (`sample/real/sample.pdf`):

- 30/30 trang OCR, ~150 s trên CPU (≈5 s/trang), **23 bảng** dựng lại được.
- Nhận đúng header/footer, số trang in lệch 2 trang → field `= PAGE - 2`.
- 26 dòng mục lục thành tab leader, 58 heading.

## Benchmark nhiều pipeline

Repo có sẵn bộ benchmark độc lập trong `benchmark/` để so sánh pdf2word với các
model/tool khác trên **cùng dataset, cùng thang điểm**. Benchmark chạy offline,
không cần GPU.

```powershell
# xem tài liệu trong dataset
.\.venv\Scripts\python.exe -m benchmark list

# kiểm tra manifest + ground truth
.\.venv\Scripts\python.exe -m benchmark validate

# chạy 3 pipeline built-in trên toàn bộ dataset
.\.venv\Scripts\python.exe -m benchmark run --run-id baseline

# chỉ chạy một số tài liệu / pipeline
.\.venv\Scripts\python.exe -m benchmark run --docs bao_cao_tai_chinh,don_de_nghi `
    --pipeline pdf2word-auto

# tái dùng DOCX đã có, chỉ chấm lại
.\.venv\Scripts\python.exe -m benchmark run --run-id baseline --reuse

# sinh lại dataset tổng hợp
.\.venv\Scripts\python.exe -m benchmark make-data --force
```

Kết quả nằm trong `benchmark/results/<run-id>/`:

- `results.json` — dữ liệu thô, đủ metric từng tài liệu và môi trường chạy.
- `report.md` — bảng tổng hợp + từng pipeline, dễ đọc trong PR/issue.
- `summary.csv` — mở bằng Excel.
- `artifacts/<pipeline>/<doc>.docx` — DOCX thực tế dùng để chấm.

### Dataset `vi_core`

Dataset tổng hợp, tự biên soạn, **không chứa dữ liệu khách hàng**:

| ID | Kiểu | Trang | Nội dung |
|---|---|---:|---|
| `bao_cao_tai_chinh` | digital | 3 | Báo cáo tài chính, heading, TOC, 2 bảng |
| `thong_bao_tuyen_sinh_scan` | scanned | 3 | Thông báo scan 200 DPI, header/footer lặp lại |
| `bao_cao_bang` | digital | 3 | Báo cáo nhiều bảng, có ô gộp |
| `don_de_nghi` | digital | 1 | Đơn hành chính, form 2 bảng |

Mỗi tài liệu có text tham chiếu trong `refs/*.txt` và ground truth cấu trúc trong
`refs/*.json` (heading, TOC, header/footer, bảng). Thêm tài liệu mới chỉ cần tạo
PDF + 2 file refs rồi khai báo trong `benchmark/data/vi_core/manifest.json`.

### Metric

- **CER / WER** trên text; bỏ dấu chấm dẫn mục lục để không phạt tab leader.
- **Bảng**: độ giống ô + F1 số lượng bảng (không chấm màu/viền).
- **Heading F1**, **TOC F1**: so khớp cấu trúc, không phân biệt hoa/thường/dấu.
- **Header/Footer**: có đủ text mong đợi trong header/footer Word hay không.
- **Composite**: trung bình có trọng số `text 0.45 · bảng 0.25 · heading 0.10 ·
  TOC 0.10 · header/footer 0.10`; metric không áp dụng được bỏ qua.

### Kết quả baseline (dataset `vi_core`)

| Pipeline | Docs | Composite | CER | WER | Bảng | Heading | TOC | H/F | s/doc |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `pdf2word-auto` | 4/4 | 0.998 | 0.3% | 1.2% | 1.000 | 1.000 | 1.000 | 1.000 | 1.09 |
| `pdf2word-digital` | 4/4 | 0.750 | 25.0% | 25.1% | 1.000 | 0.750 | 1.000 | 0.000 | 0.13 |
| `pdf2word-ocr` | 4/4 | 0.648 | 18.1% | 19.2% | 0.007 | 0.778 | 1.000 | 1.000 | 3.59 |
| `pdf2word-deepdoc` | 4/4 | 0.817 | 3.2% | 10.3% | 0.717 | 0.437 | 0.000 | 1.000 | 10.21 |

Đọc bảng: `auto` chọn đúng engine nên đạt gần tối đa trên dữ liệu tổng hợp.
`digital` cố tình chạy trên bản scan để lộ điểm yếu (không có text layer).
`ocr` chạy trên cả PDF số nên chậm và sai nhiều hơn — đây là baseline, không phải
khuyến nghị dùng OCR cho PDF số.

`deepdoc` có **CER thấp nhất trong các engine OCR** nhưng chậm hơn Tesseract trên CPU.
Sau khi bật `layout.onnx` + tinh chỉnh hậu kỳ (gộp mục lục, chuẩn hoá cỡ chữ, cắt
hàng/cột trống), chạy riêng `deepdoc` đạt **composite 0.890 · CER 3.2% · bảng 0.789 ·
heading 0.663 · TOC 1.000** (xem `benchmark/results/deepdoc-layout/report.md`).
Bảng không kẻ đã dựng được (`don_de_nghi` từ 0.333 lên 0.500); điểm còn mất chủ yếu do
TSR gộp sai ô có đường kẻ mờ hoặc bảng nhiều tầng header.

### Thêm pipeline/model khác

Sao chép `benchmark/configs/external.example.json`, cài tool/model, rồi bật
`"enabled": true` và sửa `command`. Placeholder hỗ trợ: `{input}`, `{output}`,
`{outdir}`, `{stem}`, `{doc_id}`, `{lang}`. Artifact có thể là `.docx`, `.md`,
`.txt` hoặc `.html`; Markdown/TXT/HTML được chuyển sang DOCX để chấm chung.

Ví dụ tối giản:

```json
{
  "name": "so-sanh",
  "pipelines": [
    {"name": "pdf2word-auto", "type": "builtin", "engine": "auto", "polish": true},
    {
      "name": "marker",
      "type": "external",
      "command": ["marker_single", "{input}", "--output_dir", "{outdir}"],
      "artifact": "md",
      "artifact_glob": "**/*.md",
      "timeout": 1800
    }
  ]
}
```

Lưu ý: dataset hiện là dữ liệu tổng hợp để kiểm tra hồi quy và so sánh tương đối;
không nên dùng điểm tuyệt đối để kết luận cho tài liệu thật. Muốn đánh giá sát
nghiệp vụ, bổ sung PDF thật đã ẩn danh vào dataset.

## Kiến trúc

```
PDF ──► Router (digital? scan?)
          │
          ├─ pipeline.py ──► digital.py ── pdfminer (text/style) + pypdfium2 (render)
          │                        ├─ text.py        dòng → đoạn
          │                        ├─ grid.py        dòng kẻ → grid + merge ô (dùng chung)
          │                        └─ tables.py      bảng từ vector
          │
          ├─ pipeline.py ──► ocr.py ── render 300 DPI + Tesseract 5
          │                        ├─ grid.py        bảng từ ảnh (cùng thuật toán)
          │                        └─ postprocess.py heading / TOC / header-footer / lề
          │
          └─ pipeline.py ──► extract/deepdoc/ ── render 300 DPI
                                   ├─ layout.onnx   dò vùng bảng (kể cả không kẻ)
                                   ├─ det.onnx      dò dòng chữ (DB)
                                   ├─ VietOCR ONNX  nhận dạng tiếng Việt theo dòng
                                   ├─ grid.py       dò bảng có đường kẻ
                                   ├─ tsr.onnx      hàng/cột/header/ô gộp từ crop bảng
                                   └─ postprocess.py (dùng chung)
          ▼
        IDM  (idm.py: Document / Page / Block / Table / Cell / Span)
          ▼
        docx/writer.py  →  .docx (python-docx)
```

Điểm cốt lõi: **IDM** là hợp đồng giữa extractor và renderer. Digital và OCR cùng đổ vào
một IDM, nên renderer DOCX không phải sửa khi đổi engine.

## Cấu trúc thư mục

```
pdf2word/
  idm.py            mô hình tài liệu trung gian
  pipeline.py       chọn engine digital / ocr / deepdoc / auto
  extract/
    router.py       phân loại digital / scan / mixed
    digital.py      extractor PDF có text layer
    ocr.py          extractor PDF scan (render + Tesseract + bảng từ ảnh)
    deepdoc/        engine OCR DeepDoc + VietOCR ONNX (CPU)
      engine.py     điều phối trang, mục lục, gán bảng, dựng IDM
      models.py     ONNX Detector / Recognizer / TSR
      paddle_ops.py tiền/hậu xử lý DB detection
      table_grid.py TSR → lưới ô + header/ô gộp
      vocab.py      từ vựng VietOCR (233 lớp)
    text.py         char → dòng → đoạn, thứ tự đọc
    grid.py         dựng lưới bảng + merge ô (dùng chung digital/OCR)
    postprocess.py  heading / TOC / header-footer / lề (dùng chung)
    tables.py       nhận diện bảng từ đường kẻ vector
    fonts.py        tên font, bold/italic, màu
  docx/writer.py    IDM → DOCX
  polish.py         chuẩn hoá chính tả/font/khoảng trắng hậu kỳ
  service.py        chuyển đổi dùng chung (CLI + GUI), ZIP
  gui/
    app.py          cửa sổ chính: hàng đợi, tuỳ chọn, kéo thả, ZIP
    preview.py      xem PDF (pypdfium2) + DOCX (văn bản/LibreOffice)
    worker.py       luồng nền chuyển đổi + render LibreOffice
  eval.py           CER/WER
  cli.py            giao diện dòng lệnh
benchmark/          benchmark độc lập: dataset, metric, adapter, runner, CLI
  data/vi_core/     dataset tiếng Việt + manifest + refs
  configs/          pipeline built-in và ví dụ pipeline ngoài
  results/          results.json / report.md / summary.csv theo từng lần chạy
packaging/          spec PyInstaller + build.ps1 + launcher
models/tessdata/    vie + eng + osd cho Tesseract
models/spellfix.txt từ điển sửa lỗi chính tả tuỳ chỉnh
models/deepdoc/     det/cnn/encoder/decoder/tsr/layout ONNX + vocab + NOTICE
tests/              smoke test end-to-end (digital + OCR + GUI + hậu kỳ + benchmark)
tools/              sinh PDF mẫu, kiểm tra DOCX
sample/             PDF mẫu + kết quả
```

## Giới hạn đã biết

- Bảng **không kẻ** chỉ được dựng ở engine `deepdoc` nhờ `layout.onnx`; chất lượng phụ
  thuộc model layout + TSR (có thể lệch số cột hoặc gộp ô sai). Digital/Tesseract vẫn
  chỉ nhận bảng có đường kẻ.
- Engine `deepdoc` chậm hơn Tesseract trên CPU (≈10–15 s/trang ở 300 DPI) và chất lượng
  dấu tiếng Việt phụ thuộc model VietOCR (có thể nhầm `TUYẾN`/`TUYỂN`, `ĐẰNG`/`ĐẲNG`).
- Scan mờ/nghiêng nhiều hoặc vùng có con dấu, chữ ký có thể ra chữ rác.
- Sửa chính tả tự động chỉ chuẩn hoá vị trí dấu thanh + từ điển `models/spellfix.txt`;
  không tự phát hiện mọi lỗi nghĩa, tên riêng hoặc thiếu dấu hoàn toàn.
- Header/footer chỉ nhận khi lặp lại đủ nhiều trang và nằm sát mép trên/dưới.
- Trang bị xoay (rotate ≠ 0): toạ độ ảnh có thể lệch.
- Xem DOCX dạng trang in trong app cần LibreOffice; không có thì chỉ xem văn bản/bảng.
- Font lạ không có trên máy sẽ được Word thay thế (chưa map theo metrics).

## License

Mã nguồn dùng các thư viện permissive: pypdfium2 (Apache-2.0/BSD), pdfminer.six (MIT),
python-docx (MIT), Pillow (MIT-CMU), numpy (BSD), Tesseract (Apache-2.0),
onnxruntime (MIT), opencv-python-headless (Apache-2.0), pyclipper (MIT).

Engine `deepdoc` dùng model ONNX Apache-2.0: PaddleOCR DB detection, VietOCR
(`vgg_seq2seq`) và DeepDoc TSR (`tsr.onnx`). Riêng `layout.onnx` là **AGPL-3.0** và
được dùng để dò bảng; do đó **bản đóng gói có kèm `layout.onnx` phải tuân thủ AGPL-3.0
khi phân phối** (cung cấp source code đầy đủ theo AGPL). Xem `models/deepdoc/NOTICE.md`.

Nếu cần giữ license permissive, xoá `models/deepdoc/layout.onnx`: app vẫn chạy và chỉ
dò bảng có đường kẻ. Không dùng PyMuPDF/pdf2docx (AGPL) trong mọi trường hợp.
