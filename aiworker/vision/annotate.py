"""AI 批注：像老师一样在作业原图上圈错、划线、写批注。

两条通道的定位方式不同：
  - 视觉模型：在图片外围画像素刻度尺 + 网格线，让模型按刻度读坐标
  - LLM + PaddleOCR 坐标：把文字块的 bbox 直接喂给 LLM，让它照着 bbox 圈

后者更准（坐标来自真实检测而非估算），优先使用；前者作为没有坐标时的兜底。
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("aiworker.annotate")

# 图片外围刻度带宽度（像素）
RULER_BAND = 46


def _hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = (c or "").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return (229, 57, 53)


def _load_cjk_font(size: int):
    """加载支持中文的字体。

    必须用中文字体：arial.ttf 没有中文字形，中文会渲染成方框。
    """
    from PIL import ImageFont

    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:  # noqa: BLE001
                continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def add_rulers(img, step: int = 100, band: int = RULER_BAND):
    """在图片外围加像素刻度尺，返回 (新画布, 原宽, 原高, 偏移)。"""
    from PIL import Image, ImageDraw

    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    canvas = Image.new("RGB", (w + band * 2, h + band * 2), (255, 255, 255))
    canvas.paste(img, (band, band))
    d = ImageDraw.Draw(canvas)
    font = _load_cjk_font(15)

    grid, axis, label = (200, 200, 200), (0, 0, 0), (200, 30, 30)

    for x in range(0, w + 1, step):
        cx = band + x
        d.line([(cx, band - 26), (cx, band)], fill=axis, width=2)
        d.line([(cx, band), (cx, band + h)], fill=grid, width=1)
        if font:
            d.text((cx + 3, 4), str(x), fill=label, font=font)
    for y in range(0, h + 1, step):
        cy = band + y
        d.line([(band - 26, cy), (band, cy)], fill=axis, width=2)
        d.line([(band, cy), (band + w, cy)], fill=grid, width=1)
        if font:
            d.text((3, cy + 3), str(y), fill=label, font=font)

    sub = max(10, step // 5)
    for x in range(0, w + 1, sub):
        d.line([(band + x, band - 8), (band + x, band)], fill=(120, 120, 120), width=1)
    for y in range(0, h + 1, sub):
        d.line([(band - 8, band + y), (band, band + y)], fill=(120, 120, 120), width=1)

    d.rectangle([band, band, band + w, band + h], outline=axis, width=2)
    return canvas, w, h, band


def parse_annotations(raw: str) -> list[dict]:
    """从模型输出稳健解析批注 JSON。"""
    from ..ai import engine

    data = engine.extract_json(raw)
    if not isinstance(data, dict):
        return []
    items = data.get("annotations")
    if not isinstance(items, list):
        return []

    def f(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    out: list[dict] = []
    for a in items:
        if not isinstance(a, dict):
            continue
        t = str(a.get("type") or "circle").lower()
        if t not in ("circle", "line", "highlight", "text"):
            t = "circle"
        item: dict[str, Any] = {
            "type": t,
            "x": f(a.get("x")),
            "y": f(a.get("y")),
            "color": str(a.get("color") or "#E53935"),
            "text": str(a.get("text") or "")[:60],
            "reason": str(a.get("reason") or "")[:120],
        }
        if a.get("x2") is not None:
            item["x2"] = f(a.get("x2"))
        if a.get("y2") is not None:
            item["y2"] = f(a.get("y2"))
        out.append(item)
    return out[:12]  # 上限：避免模型发散画满整页


def draw_annotations(img, annotations: list[dict], offset: int = 0):
    """按批注列表绘制标记。坐标会被夹到图内并规范 x2>=x、y2>=y。"""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    font = _load_cjk_font(max(16, int(min(W, H) / 45)))

    def P(x: float, y: float) -> tuple[float, float]:
        return (x - offset, y - offset)

    for a in annotations:
        if not isinstance(a, dict):
            continue
        t = str(a.get("type") or "circle").lower()
        col = _hex_to_rgb(a.get("color") or "#E53935")
        text = str(a.get("text") or "")

        try:
            x = min(max(float(a.get("x", 0)), 0.0), float(W))
            y = min(max(float(a.get("y", 0)), 0.0), float(H))
        except (TypeError, ValueError):
            continue

        x2 = y2 = None
        if a.get("x2") is not None:
            try:
                x2 = min(max(float(a["x2"]), 0.0), float(W))
            except (TypeError, ValueError):
                x2 = None
        if a.get("y2") is not None:
            try:
                y2 = min(max(float(a["y2"]), 0.0), float(H))
            except (TypeError, ValueError):
                y2 = None

        # 模型偶尔给出反向坐标（x2<x 且 y2<y），对角线语义下应交换
        if x2 is not None and y2 is not None and x2 < x and y2 < y:
            x, x2 = x2, x

        try:
            if t == "circle":
                if x2 is None or y2 is None:
                    r = 40
                    box = [P(x - r, y - r), P(x + r, y + r)]
                else:
                    box = [P(x, y), P(x2, y2)]
                d.ellipse(box, outline=col + (255,), width=4)
            elif t == "line":
                if x2 is None:
                    x2 = x + 140
                d.line([P(x, y), P(x2, y)], fill=col + (255,), width=4)
            elif t == "highlight":
                if x2 is None:
                    x2 = x + 24
                if y2 is None:
                    y2 = y + 24
                d.rectangle([P(x, y), P(x2, y2)], fill=col + (90,))
            else:  # text
                d.rectangle([P(x - 4, y - 4), P(x + 8, y + 22)], fill=col + (40,))
                if font:
                    d.text(P(x, y), text, fill=col + (255,), font=font)
        except Exception as e:  # noqa: BLE001
            log.warning("绘制批注失败（跳过该条）: %s", e)

    return img


def annotate(
    image_path: str,
    image_b64: str,
    mode: str,
    vision_model: str,
    output_path: str,
    base_url: str,
) -> dict:
    """生成批注图。返回 {image_url, annotations}。"""
    from PIL import Image

    from ..ai import engine
    from ..ai.prompts import ANNOTATE_LLM_PROMPT, ANNOTATE_VISION_PROMPT
    from . import ocr as ocr_mod

    annotations: list[dict] = []
    offset = 0

    # 优先走"LLM + 真实坐标"通道：bbox 来自 PaddleOCR 检测，比模型估算准得多
    ocr_text, blocks, width, height = ocr_mod.recognize_with_boxes(image_b64, image_path)
    if blocks:
        lines = []
        for i, b in enumerate(blocks[:80]):
            txt = str(b.get("text") or "").strip().replace("\n", " ")
            if not txt:
                continue
            bb = b.get("bbox") or [0, 0, 0, 0]
            lines.append('  {"id": %d, "text": %s, "bbox": [%s]}' % (
                i, json.dumps(txt[:80], ensure_ascii=False),
                ", ".join(str(round(float(v))) for v in bb[:4]),
            ))
        blocks_json = "[\n" + ",\n".join(lines) + "\n]" if lines else "[]"
        prompt = ANNOTATE_LLM_PROMPT.format(
            width=width or 1000, height=height or 1000,
            blocks=blocks_json, ocr_text=ocr_text[:3000],
        )
        try:
            annotations = parse_annotations(
                engine.client().complete([{"role": "user", "content": prompt}], max_tokens=2500)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 批注通道失败: %s", e)

    # 没有坐标 → 走"刻度尺 + 视觉模型"通道
    if not annotations:
        img = Image.open(image_path).convert("RGB")
        canvas, w, h, band = add_rulers(img)
        try:
            ruler_b64 = _img_to_b64(canvas)
            annotations = parse_annotations(
                engine.client().vision(ruler_b64, ANNOTATE_VISION_PROMPT,
                                       model=vision_model, max_tokens=2500)
            )
            offset = band
        except Exception as e:  # noqa: BLE001
            log.warning("视觉批注通道失败: %s", e)

    if not annotations:
        return {"image_url": "", "annotations": [], "message": "未能生成批注，请换一张更清晰的图片"}

    # 在原图（不带刻度）上绘制
    img = Image.open(image_path).convert("RGB")
    draw_annotations(img, annotations, offset=offset)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    log.info("批注图已保存: %s（%d 条批注）", out, len(annotations))

    return {
        "image_url": f"{base_url.rstrip('/')}/{out.name}",
        "annotations": annotations,
    }


def _img_to_b64(img) -> str:
    import base64

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")