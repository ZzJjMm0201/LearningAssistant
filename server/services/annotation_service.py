# -*- coding: utf-8 -*-
"""AI批注服务

两条通道：
  ① 视觉模型：在图片外围绘制像素刻度尺（带清晰刻度与数字），
     让视觉模型能直接按刻度定位，批注由视觉模型直接生成，不投喂大语言模型。
  ② PaddleOCR：取回文字块的坐标框（block_bbox / coordinate），
     连同题目内容一起投喂给大语言模型，由 LLM 决定批注位置与内容。

批注类型：画圈(circle)、划线(line)、加荧光(highlight)、加文本框(text)
输出：PNG 图片（下载/预览用）+ 批注列表
"""
import base64
import io
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from server.config import HISTORY_DIR, APIConfig

# ---------- 刻度尺绘制 ----------

def _hex_to_rgb(c: str) -> Tuple[int, int, int]:
    c = (c or '').lstrip('#')
    if len(c) == 3:
        c = ''.join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except Exception:
        return (255, 0, 0)


def add_rulers(image_path: str, out_path: str, step: int = 100, band: int = 46) -> Tuple[int, int]:
    """在图片外围加像素刻度尺：上/左为主刻度带，右/下为次刻度边

    每条刻度线标注其像素坐标，便于视觉模型直接读出“目标在第几像素”。
    返回 (原图宽, 原图高)。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert('RGB')
    w, h = img.size
    canvas = Image.new('RGB', (w + band * 2, h + band * 2), (255, 255, 255))
    canvas.paste(img, (band, band))
    d = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype('arial.ttf', 15)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    grid = (200, 200, 200)
    axis = (0, 0, 0)
    label = (200, 30, 30)

    # 上边：主刻度带（数字朝上，容易读）
    for x in range(0, w + 1, step):
        cx = band + x
        d.line([(cx, band - 26), (cx, band)], fill=axis, width=2)
        d.line([(cx, band), (cx, band + h)], fill=grid, width=1)
        if font:
            d.text((cx + 3, 4), str(x), fill=label, font=font)
    # 左边：主刻度带
    for y in range(0, h + 1, step):
        cy = band + y
        d.line([(band - 26, cy), (band, cy)], fill=axis, width=2)
        d.line([(band, cy), (band + w, cy)], fill=grid, width=1)
        if font:
            d.text((3, cy + 3), str(y), fill=label, font=font)

    # 次级刻度（每 step/5）
    sub = max(10, step // 5)
    for x in range(0, w + 1, sub):
        cx = band + x
        d.line([(cx, band - 8), (cx, band)], fill=(120, 120, 120), width=1)
    for y in range(0, h + 1, sub):
        cy = band + y
        d.line([(band - 8, cy), (band, cy)], fill=(120, 120, 120), width=1)

    # 外框
    d.rectangle([band, band, band + w, band + h], outline=axis, width=2)
    canvas.save(out_path)
    return w, h


def add_rulers_rgb(img, step: int = 100, band: int = 46):
    """同 add_rulers，但输入/输出都是 PIL Image（供绘图链路复用）"""
    from PIL import Image, ImageDraw, ImageFont
    if img.mode != 'RGB':
        img = img.convert('RGB')
    w, h = img.size
    canvas = Image.new('RGB', (w + band * 2, h + band * 2), (255, 255, 255))
    canvas.paste(img, (band, band))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('arial.ttf', 15)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
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
        d.line([(band, band + y - 8), (band, band + y)], fill=(120, 120, 120), width=1)
    d.rectangle([band, band, band + w, band + h], outline=axis, width=2)
    return canvas, w, h, band


# ---------- 批注绘制 ----------

def draw_annotations(image_path: str, annotations: List[Dict], out_path: str,
                     origin_offset: int = 0) -> bool:
    """按批注列表在图片上绘制标记

    annotations 每项：
      {"type": "circle|line|highlight|text", "x":..,"y":..,"x2":..,"y2":..,
       "text": "标注文字", "color": "#RRGGBB"}
    坐标为原图像素坐标；origin_offset 为原图在带刻度画布中的偏移（画刻度时是 band）。
    """
    from PIL import Image, ImageDraw, ImageFont
    try:
        img = Image.open(image_path).convert('RGB')
    except Exception:
        return False
    d = ImageDraw.Draw(img, 'RGBA')
    try:
        font = ImageFont.truetype('arial.ttf', 18)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    def P(x, y):
        return (float(x) - origin_offset, float(y) - origin_offset)

    for a in annotations or []:
        if not isinstance(a, dict):
            continue
        t = str(a.get('type') or 'circle').lower()
        col = _hex_to_rgb(a.get('color') or '#E53935')
        text = str(a.get('text') or '')
        try:
            x, y = float(a.get('x', 0)), float(a.get('y', 0))
        except Exception:
            continue
        x2, y2 = a.get('x2'), a.get('y2')
        try:
            x2 = float(x2) if x2 is not None else None
            y2 = float(y2) if y2 is not None else None
        except Exception:
            x2 = y2 = None

        if t == 'circle':
            if x2 is None or y2 is None:
                rx = ry = 40
                box = [P(x - rx, y - ry), P(x + rx, y + ry)]
            else:
                box = [P(x, y), P(x2, y2)]
            d.ellipse(box, outline=col + (255,), width=4)
        elif t == 'line':
            if x2 is None or y2 is None:
                x2, y2 = x + 140, y
            d.line([P(x, y), P(x2, y2)], fill=col + (255,), width=4)
        elif t == 'highlight':
            if x2 is None or y2 is None:
                x2, y2 = x + 24, y + 24
            d.rectangle([P(x, y), P(x2, y2)], fill=col + (90,))
        else:  # text
            d.rectangle([P(x - 4, y - 4), P(x + 8, y + 22)], fill=col + (40,))
            if font:
                d.text(P(x, y), text, fill=col + (255,), font=font)
            else:
                d.text(P(x, y), text, fill=col + (255,))
    try:
        img.save(out_path)
        return True
    except Exception:
        return False


def draw_annotations_rgb(img, annotations: List[Dict], origin_offset: int = 0):
    """同 draw_annotations，但直接改 PIL Image（内存链路）"""
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(img, 'RGBA')
    try:
        font = ImageFont.truetype('arial.ttf', 18)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    def P(x, y):
        return (float(x) + origin_offset, float(y) + origin_offset)

    for a in annotations or []:
        if not isinstance(a, dict):
            continue
        t = str(a.get('type') or 'circle').lower()
        col = _hex_to_rgb(a.get('color') or '#E53935')
        text = str(a.get('text') or '')
        try:
            x, y = float(a.get('x', 0)), float(a.get('y', 0))
        except Exception:
            continue
        try:
            x2 = float(a.get('x2')) if a.get('x2') is not None else None
            y2 = float(a.get('y2')) if a.get('y2') is not None else None
        except Exception:
            x2 = y2 = None
        if t == 'circle':
            if x2 is None or y2 is None:
                box = [P(x - 40, y - 40), P(x + 40, y + 40)]
            else:
                box = [P(x, y), P(x2, y2)]
            d.ellipse(box, outline=col + (255,), width=4)
        elif t == 'line':
            if x2 is None or y2 is None:
                x2, y2 = x + 140, y
            d.line([P(x, y), P(x2, y2)], fill=col + (255,), width=4)
        elif t == 'highlight':
            if x2 is None or y2 is None:
                x2, y2 = x + 24, y + 24
            d.rectangle([P(x, y), P(x2, y2)], fill=col + (90,))
        else:
            if font:
                d.text(P(x, y), text, fill=col + (255,), font=font)


# ---------- 提示词 ----------

VISION_ANNOTATE_PROMPT = """这是一张学生作业/试卷的照片，图片外围已经画好了像素刻度尺：
上边和左边的蓝色（深色）短线处标有数字，表示该位置的像素坐标（原点在左上角）。
图中还画了浅色网格线，方便你估算位置。

请你像老师批改作业一样，在图上做出批注。输出 JSON，格式严格如下：
{
  "annotations": [
    {
      "type": "circle|line|highlight|text",
      "x": 数字, "y": 数字,
      "x2": 数字, "y2": 数字,
      "text": "批注文字（仅 type=text 时用，简短，20字以内）",
      "color": "#RRGGBB",
      "reason": "为什么在这里批注（一句话）"
    }
  ]
}

要求：
1. 坐标为原图像素坐标（不含外围刻度带），请借助刻度尺的数字**尽量准确**地定位
2. type 含义：circle=把错误/关键处圈出来；line=在关键句下面划线；highlight=用荧光色标出重点；text=在旁边写批注文字
3. circle/line/highlight 需要给出 x,y（起点）与 x2,y2（终点/对角）；text 只需 x,y
4. 颜色建议：#E53935(红，错误/警示)、#FB8C00(橙，注意)、#43A047(绿，正确)、#1E88E5(蓝，说明)
5. 批注要有教学价值：指出错误、标出关键步骤、提示易错点；不要漫天批注，控制在 2~8 条
6. 如果这是一道题或一段作答，优先圈出第一处错误、划出关键条件、并在空白处写一句提示
7. 只输出 JSON，不要任何解释文字

请特别注意：坐标必须是原图的真实像素值。例如你看到目标在上边刻度"300"、左边刻度"150"附近，就写 x=300, y=150。
"""


def build_paddle_annotate_prompt(ocr_text: str, blocks: List[Dict], width: int, height: int) -> str:
    """PaddleOCR 通道：把文字块坐标投喂给大语言模型"""
    lines = []
    for i, b in enumerate(blocks[:80]):
        try:
            txt = str(b.get('text') or '').strip().replace('\n', ' ')
        except Exception:
            txt = ''
        if not txt:
            continue
        c = b.get('bbox') or [0, 0, 0, 0]
        lines.append('  {"id": %d, "text": %s, "bbox": [%s]}' % (
            i, json.dumps(txt[:80], ensure_ascii=False),
            ', '.join(str(round(float(v))) for v in c[:4])))
    blocks_json = '[\n' + ',\n'.join(lines) + '\n]' if lines else '[]'
    return f"""你是一位批改作业的老师。下面是一张学生作业图片的 OCR 结果。

图片尺寸：{width} x {height} 像素（原点在左上角，x 向右、y 向下）。
识别出的文本块及其位置（bbox = [左, 上, 右, 下]，单位像素）：
{blocks_json}

图片完整文字内容：
{ocr_text}

请你像老师批改作业一样，在这张图上做出批注。输出 JSON，格式严格如下：
{{
  "annotations": [
    {{
      "type": "circle|line|highlight|text",
      "x": 数字, "y": 数字,
      "x2": 数字, "y2": 数字,
      "text": "批注文字（仅 type=text 时用，简短，20字以内）",
      "color": "#RRGGBB",
      "reason": "为什么在这里批注（一句话）"
    }}
  ]
}}

要求：
1. 坐标必须落在上面的 bbox 范围内或紧邻其边缘（例如圈出错处就用该文本的 bbox）
2. type 含义：circle=圈出错处/关键处；line=在关键句下划线（用 bbox 的上下边）；highlight=荧光标重点；text=在旁白处写字
3. circle/line/highlight 给 x,y 与 x2,y2；text 只给 x,y（可以放在对应文本块右侧或下方的空白处）
4. 颜色建议：#E53935(红，错误/警示)、#FB8C00(橙，注意)、#43A047(绿，正确)、#1E88E5(蓝，说明)
5. 批注要有教学价值：指出错误、标出关键步骤、提示易错点；控制在 2~8 条
6. 只输出 JSON，不要任何解释文字
"""


def parse_annotations(raw: str) -> List[Dict]:
    """从模型输出里稳健地解析批注 JSON"""
    if not raw:
        return []
    s = raw.strip()
    s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
    s = re.sub(r'```\s*$', '', s).strip()
    m = re.search(r'\{[\s\S]*\}', s)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        # 容错：去掉尾随逗号
        try:
            fixed = re.sub(r',\s*([}\]])', r'\1', m.group(0))
            data = json.loads(fixed)
        except Exception:
            return []
    items = data.get('annotations') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for a in items:
        if not isinstance(a, dict):
            continue
        t = str(a.get('type') or 'circle').lower()
        if t not in ('circle', 'line', 'highlight', 'text'):
            t = 'circle'
        def _f(v, d=0.0):
            try:
                return float(v)
            except Exception:
                return d
        item = {
            'type': t,
            'x': _f(a.get('x')), 'y': _f(a.get('y')),
            'color': str(a.get('color') or '#E53935'),
            'text': str(a.get('text') or '')[:60],
            'reason': str(a.get('reason') or '')[:120],
        }
        if a.get('x2') is not None:
            item['x2'] = _f(a.get('x2'))
        if a.get('y2') is not None:
            item['y2'] = _f(a.get('y2'))
        out.append(item)
    return out
