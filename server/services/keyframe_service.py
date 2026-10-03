# -*- coding: utf-8 -*-
"""关键帧提取服务（长按多页拍摄核心）

思路：一段视频 = 一次连续拍摄。按「画面变化 + 清晰度」挑出关键帧，
每帧视作一页（一题/一段内容），再交给 OCR + 分题流水线。

依赖仅 OpenCV（本环境 cv2 4.10.0 可用，无 ffmpeg）。
"""
import os
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import cv2
import numpy as np


class KeyframeExtractor:
    """从视频中提取关键帧"""

    def __init__(
        self,
        history_dir: Path,
        diff_threshold: float = 0.05,      # 显著变化像素占比阈值；真换页≈0.10~0.12，同页噪声≈0.01，取中间偏低
        min_sharpness: float = 12.0,       # 清晰度下限（Laplacian 方差）
        min_gap_sec: float = 0.6,          # 两个关键帧的最小时间间隔，防抖
        max_frames: int = 12,              # 单次最多取多少页
        sample_fps: float = 4.0,           # 每秒抽样帧数（降采样，省 CPU）
    ):
        self.history_dir = Path(history_dir)
        self.diff_threshold = diff_threshold
        self.min_sharpness = min_sharpness
        self.min_gap_sec = min_gap_sec
        self.max_frames = max_frames
        self.sample_fps = sample_fps
        self.out_dir = self.history_dir / "keyframes"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 内部工具 ----------

    @staticmethod
    def _sharpness(gray: np.ndarray) -> float:
        """Laplacian 方差：越大越清晰"""
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def _downscale(frame: np.ndarray, max_side: int = 240) -> np.ndarray:
        """缩到小图做帧差，省算力"""
        h, w = frame.shape[:2]
        scale = max_side / float(max(h, w))
        if scale >= 1.0:
            return frame
        return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _diff_score(a: np.ndarray, b: np.ndarray) -> float:
        """两帧差异（0~1）。

        用「显著变化像素占比」而非平均像素差：
        拍作业时画面大部分是白纸/背景，只有文字与图形在变；
        平均值会被大片白色稀释（真实换页也可能只算出 0.05），
        而占比只统计「亮度变化超过阈值的像素」有多少，对排版稀疏的页面仍然敏感。
        """
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        ga = cv2.GaussianBlur(ga, (5, 5), 0)
        gb = cv2.GaussianBlur(gb, (5, 5), 0)
        diff = cv2.absdiff(ga, gb)
        # 亮度变化 > 25 才算「真的变了」，滤掉传感器噪声与轻微抖动
        changed = int((diff > 25).sum())
        return changed / float(diff.size)

    # ---------- 主流程 ----------

    def extract(self, video_path: str) -> Tuple[List[Dict], str]:
        """
        返回 (关键帧列表, 错误信息)。
        关键帧 = {"path": ..., "index": int, "time_sec": float, "sharpness": float}
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return [], "无法打开视频文件（编码不支持或文件损坏）"

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = (total / fps) if fps > 0 else 0.0
            step = max(1, int(round(fps / self.sample_fps)))  # 抽样间隔（帧）

            frames: List[Dict] = []   # 抽到的候选帧
            idx = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % step == 0:
                    t = idx / fps if fps > 0 else 0.0
                    small = self._downscale(frame)
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    frames.append({
                        "idx": idx,
                        "t": t,
                        "frame": frame,
                        "small": small,
                        "sharp": self._sharpness(gray),
                    })
                idx += 1

            if not frames:
                return [], "视频里没有读到任何画面"

            # —— 挑关键帧 ——
            picked: List[Dict] = []
            last_small: Optional[np.ndarray] = None
            last_t = -999.0
            for f in frames:
                is_first = (last_small is None)
                changed = is_first or (self._diff_score(last_small, f["small"]) >= self.diff_threshold)
                gap_ok = (f["t"] - last_t) >= self.min_gap_sec
                clear = f["sharp"] >= self.min_sharpness
                # 换页 且 够清晰 且 间隔够 → 取
                if changed and gap_ok and clear:
                    picked.append(f)
                    last_small = f["small"]
                    last_t = f["t"]
                elif changed and gap_ok:
                    # 换页了但太糊：留作候选（若最终没别的可用，兜底）
                    last_small = f["small"]
                    last_t = f["t"]

            # 太糊导致一张没取到 → 放宽清晰度，取差异最大的若干帧
            if not picked:
                best = sorted(frames, key=lambda x: x["sharp"], reverse=True)[: self.max_frames]
                best.sort(key=lambda x: x["t"])
                picked = best

            # 去重（同一页被连续取多次的情况）：按差异再过滤一轮
            dedup: List[Dict] = []
            for f in picked:
                if not dedup:
                    dedup.append(f)
                    continue
                if self._diff_score(dedup[-1]["small"], f["small"]) >= (self.diff_threshold * 0.6):
                    dedup.append(f)
            picked = dedup[: self.max_frames]

            # —— 落盘 ——
            out: List[Dict] = []
            token = uuid.uuid4().hex[:8]
            for n, f in enumerate(picked, 1):
                name = f"page_{token}_{n:02d}.jpg"
                fp = self.out_dir / name
                # 保存原分辨率（OCR 需要清晰度），质量 92
                cv2.imwrite(str(fp), f["frame"], [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                out.append({
                    "path": str(fp),
                    "name": name,
                    "index": n,
                    "time_sec": round(f["t"], 2),
                    "sharpness": round(f["sharp"], 1),
                })
            return out, ""
        finally:
            cap.release()


def extract_keyframes(history_dir: Path, video_path: str, **kw) -> Tuple[List[Dict], str]:
    """便捷函数"""
    return KeyframeExtractor(history_dir, **kw).extract(video_path)
