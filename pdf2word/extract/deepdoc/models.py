"""Bọc ONNX Runtime cho 4 model DeepDoc/VietOCR chạy CPU.

Không dùng torch. Mọi session được tạo lazy và tái sử dụng trong một lần
chuyển đổi.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .paddle_ops import (
    DBPostProcess,
    DetResizeForTest,
    KeepKeys,
    NormalizeImage,
    ToCHWImage,
    clip_points,
    nms,
    order_points_clockwise,
    transform,
)
from .vocab import VietVocab

TSR_LABELS = [
    "table",
    "table column",
    "table row",
    "table column header",
    "table projected row header",
    "table spanning cell",
    "table grid cell",
]

LAYOUT_LABELS = {
    0: "title",
    1: "plain text",
    2: "abandon",
    3: "figure",
    4: "figure_caption",
    5: "table",
    6: "table_caption",
    7: "table_footnote",
    8: "isolate_formula",
    9: "formula_caption",
}


def _ort():
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - phụ thuộc môi trường
        raise RuntimeError(
            "Engine deepdoc cần onnxruntime. Cài: pip install -e .[deepdoc]"
        ) from exc
    return ort


def find_model_dir(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.environ.get("PDF2WORD_DEEPDOC"),
    ]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "models", "deepdoc"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "models", "deepdoc"))
    here = Path(__file__).resolve()
    candidates.append(str(here.parents[3] / "models" / "deepdoc"))
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "det.onnx")):
            return os.path.abspath(candidate)
    raise RuntimeError(
        "Không tìm thấy model DeepDoc (det.onnx). Đặt biến môi trường "
        "PDF2WORD_DEEPDOC trỏ tới thư mục models/deepdoc."
    )


@dataclass
class ModelPaths:
    root: str

    def __getattr__(self, name: str) -> str:
        mapping = {
            "det": "det.onnx",
            "cnn": "cnn.onnx",
            "encoder": "encoder.onnx",
            "decoder": "decoder.onnx",
            "tsr": "tsr.onnx",
            "layout": "layout.onnx",
            "vocab": "vocab.txt",
        }
        if name in mapping:
            return os.path.join(self.root, mapping[name])
        raise AttributeError(name)


class Detector:
    """DB text detection."""

    def __init__(self, path: str, limit_side_len: int = 1600):
        ort = _ort()
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.pre = [
            DetResizeForTest(limit_side_len=limit_side_len, limit_type="max"),
            NormalizeImage(
                std=[0.229, 0.224, 0.225],
                mean=[0.485, 0.456, 0.406],
                scale=1.0 / 255.0,
                order="hwc",
            ),
            ToCHWImage(),
            KeepKeys(keep_keys=["image", "shape"]),
        ]
        self.post = DBPostProcess(
            thresh=0.3,
            box_thresh=0.5,
            max_candidates=1000,
            unclip_ratio=1.5,
            use_dilation=False,
            score_mode="fast",
            box_type="quad",
        )

    def detect(self, image_bgr: np.ndarray) -> list[np.ndarray]:
        h, w = image_bgr.shape[:2]
        data = transform({"image": image_bgr}, self.pre)
        if data is None:
            return []
        arr, shape = data
        pred = self.session.run(None, {self.input_name: np.expand_dims(arr, 0)})[0]
        points = self.post({"maps": pred}, np.expand_dims(shape, 0))[0]["points"]
        quads: list[np.ndarray] = []
        for pts in points:
            pts = np.asarray(pts, dtype="float32")
            if pts.shape[0] < 4:
                continue
            quad = clip_points(order_points_clockwise(pts[:4]), h, w)
            if (quad[:, 1].max() - quad[:, 1].min()) < 3:
                continue
            if (quad[:, 0].max() - quad[:, 0].min()) < 3:
                continue
            quads.append(quad)
        return quads


def _crop_quad(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    width = int(
        max(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[2] - quad[3]))
    )
    height = int(
        max(np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2]))
    )
    width = max(width, 1)
    height = max(height, 1)
    dst_pts = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(quad, dst_pts)
    crop = cv2.warpPerspective(
        image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    if crop.shape[0] / max(crop.shape[1], 1) >= 1.5:
        crop = np.rot90(crop)
    return crop


def _prepare_line(image_bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    from PIL import Image

    crop = _crop_quad(image_bgr, quad)
    if crop.size == 0:
        return np.zeros((3, 32, 32), dtype=np.float32)
    pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    width, height = pil.size
    new_w = int(32 * width / max(height, 1))
    new_w = ((new_w + 9) // 10) * 10
    new_w = max(32, min(512, new_w))
    pil = pil.resize((new_w, 32), Image.LANCZOS)
    arr = np.asarray(pil, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return arr


class Recognizer:
    """VietOCR seq2seq ONNX, chạy theo lô nhỏ để nhanh hơn trên CPU."""

    def __init__(self, paths: ModelPaths, batch_size: int = 12, max_seq_length: int = 128):
        ort = _ort()
        self.batch_size = max(1, batch_size)
        self.max_seq_length = max_seq_length
        self.cnn = ort.InferenceSession(paths.cnn, providers=["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(paths.encoder, providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(paths.decoder, providers=["CPUExecutionProvider"])
        self.vocab = VietVocab.from_file(paths.vocab)
        self._cnn_in = self.cnn.get_inputs()[0].name
        self._enc_in = self.encoder.get_inputs()[0].name
        self._dec_in = self.decoder.get_inputs()[0].name
        # Bản ONNX đi kèm cố định batch CNN = 1 nên không thể gộp lô.
        cnn_shape = self.cnn.get_inputs()[0].shape
        if isinstance(cnn_shape[0], int) and cnn_shape[0] == 1:
            self.batch_size = 1

    def _decode_batch(self, arrays: list[np.ndarray], allowed_ids: set[int] | None = None) -> list[str]:
        max_w = max(a.shape[2] for a in arrays)
        batch = np.ones((len(arrays), 3, 32, max_w), dtype=np.float32)
        for i, arr in enumerate(arrays):
            batch[i, :, :, : arr.shape[2]] = arr

        src = self.cnn.run(None, {self._cnn_in: batch})[0]
        encoder_out, hidden = self.encoder.run(None, {self._enc_in: src})
        ids: list[list[int]] = [[1] for _ in arrays]
        done = [False] * len(arrays)
        for _ in range(self.max_seq_length):
            if all(done):
                break
            tgt = np.array([[seq[-1]] for seq in ids], dtype=np.int64)
            if len(arrays) == 1:
                tgt = tgt.reshape(-1)
            logits, hidden, _ = self.decoder.run(
                None,
                {
                    "tgt": tgt,
                    "hidden": np.ascontiguousarray(hidden),
                    "encoder_outputs": np.ascontiguousarray(encoder_out),
                },
            )
            if allowed_ids is not None:
                masked = np.full_like(logits, -1e9)
                for token in allowed_ids:
                    if token < masked.shape[1]:
                        masked[:, token] = logits[:, token]
                logits = masked
            nxt = np.argmax(logits, axis=1).astype(int).tolist()
            for i, token in enumerate(nxt):
                if done[i]:
                    continue
                ids[i].append(token)
                if token == 2:
                    done[i] = True
        return [self.vocab.decode(seq) for seq in ids]

    def recognize(self, image_bgr: np.ndarray, quads: list[np.ndarray]) -> list[str]:
        if not quads:
            return []
        # Nhóm theo bề rộng tương đối để padding ít làm lệch kết quả.
        order = sorted(range(len(quads)), key=lambda i: _quad_width(quads[i]))
        texts = [""] * len(quads)
        for start in range(0, len(order), self.batch_size):
            chunk = order[start : start + self.batch_size]
            arrays = [_prepare_line(image_bgr, quads[i]) for i in chunk]
            decoded = self._decode_batch(arrays)
            for i, text in zip(chunk, decoded):
                texts[i] = text.strip()
        return texts

    def recognize_numeric(self, image_bgr: np.ndarray, quad: np.ndarray) -> str:
        """Nhận dạng một ô hẹp chỉ chứa chữ số (số trang mục lục)."""
        allowed = {2}  # eos
        allowed.update(self.vocab.c2i[ch] for ch in "0123456789" if ch in self.vocab.c2i)
        arr = _prepare_line(image_bgr, quad)
        text = self._decode_batch([arr], allowed_ids=allowed)[0]
        return "".join(ch for ch in text if ch.isdigit())


def _quad_width(quad: np.ndarray) -> float:
    return float(np.linalg.norm(quad[0] - quad[1]))


class LayoutDetector:
    """YOLOv10 layout detection (DeepDoc/RAGFlow), chạy CPU.

    Model nhận ảnh letterbox 1024x1024 và trả ``[1, 300, 6]`` gồm
    ``x1, y1, x2, y2, score, class``. Dùng để tìm vùng bảng kể cả bảng không kẻ.
    """

    def __init__(self, path: str, score_threshold: float = 0.35):
        ort = _ort()
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.size_h = int(shape[2]) if isinstance(shape[2], int) else 1024
        self.size_w = int(shape[3]) if isinstance(shape[3], int) else 1024
        self.score_threshold = score_threshold
        self.labels = LAYOUT_LABELS

    def detect(self, image_bgr: np.ndarray) -> list[dict]:
        if image_bgr.size == 0:
            return []
        h, w = image_bgr.shape[:2]
        size_h, size_w = self.size_h, self.size_w
        r = min(size_h / h, size_w / w)
        new_w, new_h = int(round(w * r)), int(round(h * r))
        dw = (size_w - new_w) / 2.0
        dh = (size_h - new_h) / 2.0

        resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        top = int(round(dh - 0.1))
        bottom = int(round(dh + 0.1))
        left = int(round(dw - 0.1))
        right = int(round(dw + 0.1))
        canvas = cv2.copyMakeBorder(
            resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = rgb.transpose(2, 0, 1)[None]

        out = self.session.run(None, {self.input_name: tensor})[0]
        boxes = np.squeeze(out)
        if boxes.ndim != 2 or boxes.shape[1] < 6:
            return []
        scores = boxes[:, 4]
        keep = scores >= self.score_threshold
        boxes, scores = boxes[keep], scores[keep]
        if not len(boxes):
            return []

        xyxy = boxes[:, :4].astype(np.float32).copy()
        xyxy[:, [0, 2]] = (xyxy[:, [0, 2]] - left) / r
        xyxy[:, [1, 3]] = (xyxy[:, [1, 3]] - top) / r
        xyxy[:, [0, 2]] = np.clip(xyxy[:, [0, 2]], 0, w - 1)
        xyxy[:, [1, 3]] = np.clip(xyxy[:, [1, 3]], 0, h - 1)
        classes = boxes[:, 5].astype(int)

        dets: list[dict] = []
        for cls in np.unique(classes):
            idx = np.where(classes == cls)[0]
            for k in nms(xyxy[idx], scores[idx], 0.45):
                i = idx[k]
                dets.append(
                    {
                        "label": self.labels.get(int(cls), str(cls)),
                        "score": float(scores[i]),
                        "bbox": [float(v) for v in xyxy[i]],
                    }
                )
        return dets


class Tsr:
    """Table structure recognition (DETR-like, 640x640)."""

    def __init__(self, path: str):
        ort = _ort()
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.size = int(shape[2]) if isinstance(shape[2], int) else 640

    def detect(self, crop_bgr: np.ndarray) -> list[dict]:
        if crop_bgr.size == 0:
            return []
        size = self.size
        image = cv2.resize(crop_bgr.astype(np.float32), (size, size)) / 255.0
        image = image.transpose(2, 0, 1)[None]
        out = self.session.run(None, {self.input_name: image})[0]
        boxes = np.squeeze(out).T if out.ndim == 3 else np.squeeze(out)
        if boxes.ndim != 2 or boxes.shape[1] < 6:
            return []
        scores = np.max(boxes[:, 4:], axis=1)
        keep = scores > 0.2
        boxes, scores = boxes[keep], scores[keep]
        if not len(boxes):
            return []
        classes = np.argmax(boxes[:, 4:], axis=1)
        ch, cw = crop_bgr.shape[:2]
        xyxy = boxes[:, :4].copy()
        xyxy *= np.array([cw / size, ch / size, cw / size, ch / size])
        xyxy[:, 0] -= xyxy[:, 2] / 2
        xyxy[:, 1] -= xyxy[:, 3] / 2
        xyxy[:, 2] += xyxy[:, 0]
        xyxy[:, 3] += xyxy[:, 1]

        dets: list[dict] = []
        for cls in np.unique(classes):
            idx = np.where(classes == cls)[0]
            for k in nms(xyxy[idx], scores[idx], 0.2):
                i = idx[k]
                label = TSR_LABELS[cls] if cls < len(TSR_LABELS) else str(cls)
                dets.append(
                    {
                        "label": label,
                        "score": float(scores[i]),
                        "bbox": [float(v) for v in xyxy[i]],
                    }
                )
        return dets


__all__ = [
    "Detector",
    "LAYOUT_LABELS",
    "LayoutDetector",
    "ModelPaths",
    "Recognizer",
    "Tsr",
    "find_model_dir",
]
