"""视频关键帧提取：长按连续拍摄时，一段视频 = 一页页作业。

按"画面变化 + 清晰度"挑帧：
  - 帧差用**显著变化像素占比**而非平均像素差。拍作业时画面大半是白纸，
    平均差会被大片白色稀释（真实换页也可能只算出 0.05），占比则对稀疏排版依然敏感。
  - 清晰度用 Laplacian 方差。
  - 最小间隔防抖，同一页被连续取多次时再按差异去重一轮。

依赖仅 OpenCV。
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger("aiworker.keyframe")


def extract(video_path: str, output_dir: str) -> tuple[list[dict], str]:
    """抽关键帧。返回 (帧列表, 错误信息)。"""
    try:
        import cv2
    except ImportError:
        return [], "未安装 opencv，无法处理视频（pip install opencv-python-headless）"

    # 真换页 ≈0.10~0.12，同页噪声 ≈0.01，取中间偏低
    diff_threshold = float(os.environ.get("KF_DIFF_THRESHOLD", "0.05"))
    min_sharpness = float(os.environ.get("KF_MIN_SHARPNESS", "12.0"))
    min_gap = float(os.environ.get("KF_MIN_GAP_SEC", "0.6"))
    max_frames = int(os.environ.get("KF_MAX_FRAMES", "12"))
    sample_fps = float(os.environ.get("KF_SAMPLE_FPS", "4.0"))

    out_dir = Path(output_dir) / "keyframes"
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return [], "无法打开视频文件（编码不支持或文件损坏）"

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(round(fps / sample_fps)))

        frames: list[dict] = []
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                small = _downscale(cv2, frame)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frames.append({
                    "t": idx / fps if fps > 0 else 0.0,
                    "frame": frame,
                    "small": small,
                    "sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                })
            idx += 1

        if not frames:
            return [], "视频里没有读到任何画面"

        picked: list[dict] = []
        last_small = None
        last_t = -999.0
        for f in frames:
            changed = last_small is None or _diff_score(cv2, last_small, f["small"]) >= diff_threshold
            gap_ok = (f["t"] - last_t) >= min_gap
            clear = f["sharp"] >= min_sharpness
            if changed and gap_ok and clear:
                picked.append(f)
                last_small, last_t = f["small"], f["t"]
            elif changed and gap_ok:
                # 换页了但太糊：更新基线，让后续清晰帧仍能被检出
                last_small, last_t = f["small"], f["t"]

        # 全都太糊 → 放宽清晰度，取最清晰的若干帧
        if not picked:
            picked = sorted(frames, key=lambda x: -x["sharp"])[:max_frames]
            picked.sort(key=lambda x: x["t"])

        # 去重：同一页被连续取多次
        dedup: list[dict] = []
        for f in picked:
            if not dedup or _diff_score(cv2, dedup[-1]["small"], f["small"]) >= diff_threshold * 0.6:
                dedup.append(f)
        picked = dedup[:max_frames]

        token = uuid.uuid4().hex[:8]
        out: list[dict] = []
        for n, f in enumerate(picked, 1):
            name = f"page_{token}_{n:02d}.jpg"
            fp = out_dir / name
            # 保存原分辨率：OCR 需要清晰度
            cv2.imwrite(str(fp), f["frame"], [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            out.append({
                "path": str(fp), "name": name, "index": n,
                "time_sec": round(f["t"], 2), "sharpness": round(f["sharp"], 1),
            })

        log.info("抽帧完成: %d 帧 → %s", len(out), out_dir)
        return out, ""
    finally:
        cap.release()


def _downscale(cv2, frame, max_side: int = 240):
    h, w = frame.shape[:2]
    scale = max_side / float(max(h, w))
    if scale >= 1.0:
        return frame
    return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _diff_score(cv2, a, b) -> float:
    """两帧差异 0~1：亮度变化超过阈值的像素占比。"""
    ga = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    gb = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    diff = cv2.absdiff(ga, gb)
    changed = int((diff > 25).sum())  # 25 以下视为传感器噪声与轻微抖动
    return changed / float(diff.size)