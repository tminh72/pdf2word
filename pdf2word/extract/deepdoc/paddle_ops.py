"""Tiền xử lý và hậu xử lý text detection kiểu PaddleOCR (CPU).

Bản rút gọn từ ``module/operators.py`` và ``module/postprocess.py`` của
``hoaivannguyen/deepdoc_vietocr`` (Apache-2.0). Chỉ giữ phần cần cho model
``det.onnx``; bỏ phụ thuộc shapely để nhẹ hơn.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pyclipper


def transform(data, ops):
    for op in ops:
        data = op(data)
        if data is None:
            return None
    return data


class DetResizeForTest:
    """Resize ảnh về cạnh dài ``limit_side_len`` (bội số 32)."""

    def __init__(self, limit_side_len: int = 1600, limit_type: str = "max", **kwargs):
        self.limit_side_len = int(limit_side_len)
        self.limit_type = limit_type

    def __call__(self, data):
        img = data["image"]
        src_h, src_w = img.shape[:2]
        limit = self.limit_side_len
        if self.limit_type == "max":
            ratio = limit / max(src_h, src_w) if max(src_h, src_w) > limit else 1.0
        elif self.limit_type == "min":
            ratio = limit / min(src_h, src_w) if min(src_h, src_w) < limit else 1.0
        else:
            ratio = limit / max(src_h, src_w)
        resize_h = max(int(round(src_h * ratio / 32) * 32), 32)
        resize_w = max(int(round(src_w * ratio / 32) * 32), 32)
        img = cv2.resize(img, (resize_w, resize_h))
        data["image"] = img
        data["shape"] = np.array(
            [src_h, src_w, resize_h / float(src_h), resize_w / float(src_w)]
        )
        return data


class NormalizeImage:
    def __init__(self, scale=1.0 / 255.0, mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225), order="chw", **kwargs):
        self.scale = np.float32(scale)
        shape = (3, 1, 1) if order == "chw" else (1, 1, 3)
        self.mean = np.array(mean, dtype="float32").reshape(shape)
        self.std = np.array(std, dtype="float32").reshape(shape)

    def __call__(self, data):
        img = np.asarray(data["image"])
        data["image"] = (img.astype("float32") * self.scale - self.mean) / self.std
        return data


class ToCHWImage:
    def __call__(self, data):
        data["image"] = np.asarray(data["image"]).transpose((2, 0, 1))
        return data


class KeepKeys:
    def __init__(self, keep_keys, **kwargs):
        self.keep_keys = keep_keys

    def __call__(self, data):
        return [data[key] for key in self.keep_keys]


def _polygon_length(box: np.ndarray) -> float:
    if len(box) < 2:
        return 1.0
    return float(np.linalg.norm(np.roll(box, -1, axis=0) - box, axis=1).sum())


def _polygon_area(box: np.ndarray) -> float:
    if len(box) < 3:
        return 0.0
    x, y = box[:, 0], box[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


class DBPostProcess:
    """Hậu xử lý Differentiable Binarization -> đa giác chữ."""

    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.5,
        max_candidates: int = 1000,
        unclip_ratio: float = 1.5,
        use_dilation: bool = False,
        score_mode: str = "fast",
        box_type: str = "quad",
        **kwargs,
    ):
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3
        self.score_mode = score_mode
        self.box_type = box_type
        self.dilation_kernel = None if not use_dilation else np.array([[1, 1]])

    def unclip(self, box: np.ndarray, unclip_ratio: float) -> np.ndarray:
        area = _polygon_area(box)
        length = max(_polygon_length(box), 1.0)
        distance = area * unclip_ratio / length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box.tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        return np.array(offset.Execute(distance))

    def get_mini_boxes(self, contour):
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda p: p[0])
        if points[1][1] > points[0][1]:
            index_1, index_4 = 0, 1
        else:
            index_1, index_4 = 1, 0
        if points[3][1] > points[2][1]:
            index_2, index_3 = 2, 3
        else:
            index_2, index_3 = 3, 2
        box = [points[index_1], points[index_2], points[index_3], points[index_4]]
        return box, min(bounding_box[1])

    def box_score_fast(self, bitmap: np.ndarray, box: np.ndarray) -> float:
        h, w = bitmap.shape[:2]
        box = box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype("int32"), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype("int32"), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype("int32"), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype("int32"), 0, h - 1)
        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype("int32"), 1)
        return float(cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0])

    def boxes_from_bitmap(self, pred: np.ndarray, bitmap: np.ndarray, dest_w: int, dest_h: int):
        height, width = bitmap.shape
        contours, _ = cv2.findContours(
            (bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes, scores = [], []
        for contour in contours[: self.max_candidates]:
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue
            points = np.array(points)
            score = self.box_score_fast(pred, points.reshape(-1, 2))
            if score < self.box_thresh:
                continue
            box = self.unclip(points, self.unclip_ratio).reshape(-1, 1, 2)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2 or len(box) < 4:
                continue
            box = np.array(box)
            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_w), 0, dest_w)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_h), 0, dest_h)
            boxes.append(box.astype("int32"))
            scores.append(score)
        return boxes, scores

    def __call__(self, outs_dict, shape_list):
        pred = outs_dict["maps"]
        if not isinstance(pred, np.ndarray):
            pred = pred.numpy()
        pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh
        boxes_batch = []
        for batch_index in range(pred.shape[0]):
            src_h, src_w, _ratio_h, _ratio_w = shape_list[batch_index]
            if self.dilation_kernel is not None:
                mask = cv2.dilate(
                    np.array(segmentation[batch_index]).astype(np.uint8), self.dilation_kernel
                )
            else:
                mask = segmentation[batch_index]
            boxes, _scores = self.boxes_from_bitmap(
                pred[batch_index], mask, int(src_w), int(src_h)
            )
            boxes_batch.append({"points": boxes})
        return boxes_batch


def nms(bboxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """NMS xyxy (lấy từ upstream, giữ nguyên hành vi)."""
    x1, y1, x2, y2 = bboxes[:, 0], bboxes[:, 1], bboxes[:, 2], bboxes[:, 3]
    areas = (y2 - y1) * (x2 - x1)
    indices: list[int] = []
    index = scores.argsort()[::-1]
    while index.size > 0:
        i = index[0]
        indices.append(int(i))
        x11 = np.maximum(x1[i], x1[index[1:]])
        y11 = np.maximum(y1[i], y1[index[1:]])
        x22 = np.minimum(x2[i], x2[index[1:]])
        y22 = np.minimum(y2[i], y2[index[1:]])
        w = np.maximum(0, x22 - x11 + 1)
        h = np.maximum(0, y22 - y11 + 1)
        overlaps = w * h
        ious = overlaps / (areas[i] + areas[index[1:]] - overlaps + 1e-9)
        idx = np.where(ious <= iou_thresh)[0]
        index = index[idx + 1]
    return indices


def order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    tmp = np.delete(pts, (np.argmin(s), np.argmax(s)), axis=0)
    diff = np.diff(tmp, axis=1)
    rect[1] = tmp[np.argmin(diff)]
    rect[3] = tmp[np.argmax(diff)]
    return rect


def clip_points(points: np.ndarray, h: int, w: int) -> np.ndarray:
    points = points.astype("float32").copy()
    points[:, 0] = np.clip(points[:, 0], 0, w - 1)
    points[:, 1] = np.clip(points[:, 1], 0, h - 1)
    return points


__all__ = [
    "DBPostProcess",
    "DetResizeForTest",
    "KeepKeys",
    "NormalizeImage",
    "ToCHWImage",
    "clip_points",
    "nms",
    "order_points_clockwise",
    "transform",
]
