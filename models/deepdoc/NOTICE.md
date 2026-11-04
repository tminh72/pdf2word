# DeepDoc ONNX model assets

This folder bundles the CPU-friendly ONNX models needed by the `deepdoc`
OCR engine in pdf2word.

| File | Purpose | Origin | License |
| --- | --- | --- | --- |
| `det.onnx` | Text detection (DB / PaddleOCR) | PaddleOCR, redistributed by `hoaivannguyen/deepdoc_vietocr` | Apache-2.0 |
| `cnn.onnx` | VietOCR CNN backbone (`vgg_seq2seq`) | VietOCR, redistributed by `hoaivannguyen/deepdoc_vietocr` | Apache-2.0 |
| `encoder.onnx` | VietOCR transformer encoder | VietOCR, redistributed by `hoaivannguyen/deepdoc_vietocr` | Apache-2.0 |
| `decoder.onnx` | VietOCR transformer decoder | VietOCR, redistributed by `hoaivannguyen/deepdoc_vietocr` | Apache-2.0 |
| `tsr.onnx` | Table structure recognition (DeepDoc) | InfiniFlow DeepDoc / RAGFlow | Apache-2.0 |
| `layout.onnx` | Page layout detection (YOLOv10 doclayout) | InfiniFlow DeepDoc / RAGFlow | **AGPL-3.0** |
| `vocab.txt` | Vietnamese character vocabulary used by the VietOCR decoder | VietOCR `config/base.yml` | Apache-2.0 |

## AGPL-3.0 notice

`layout.onnx` is licensed under **AGPL-3.0** (model metadata: *AGPL-3.0 License,
https://github.com/infiniflow/ragflow/blob/main/LICENSE*). It is used to detect
table regions, including tables without ruling lines.

Because this model is bundled and loaded in-process by the application,
**distributing the application (including `dist/pdf2word` or the one-folder
build) makes the combined work subject to AGPL-3.0 obligations.** If you convey
the application to others, you must provide the complete corresponding source
code under AGPL-3.0, including your modifications. Internal use without
distribution may not trigger those obligations, but that depends on how the
software is delivered and should be reviewed by legal counsel.

If AGPL-3.0 is not acceptable, remove `layout.onnx`; the engine then falls back
to ruled-table detection only (`det.onnx` + `tsr.onnx`, Apache-2.0).

## Upstream references

- https://github.com/hoaivannguyen/deepdoc_vietocr
- https://github.com/infiniflow/ragflow
- https://github.com/pbcquoc/vietocr
- https://github.com/ultralytics/ultralytics
