"""① 多题模式：把一次OCR识别出的整段文本分割为多道独立题目

- 视觉模型（qwen/deepseek vision）模式：OCR 提示词要求题与题之间用 ``%%%`` 分隔，直接按 ``%%%`` 切分。
- PaddleOCR 模式：OCR 文本无天然分隔，调用大语言模型识别题目边界，
  让模型输出“分割位置的前10字符 + 后10字符”，再回到原文本中做最左匹配定位精确切点。
"""
from typing import List, Optional


def split_by_separator(text: str, sep: str = "%%%") -> List[str]:
    """按分隔符切分（视觉模型模式），清洗空白/首尾序号"""
    if not text:
        return []
    parts = [p.strip() for p in text.split(sep)]
    parts = [p for p in parts if p and p.strip()]
    return parts


def split_by_model(ocr_text: str, ai_service, engine: Optional[str] = None, model: Optional[str] = None) -> List[str]:
    """PaddleOCR 模式：用 LLM 识别多题边界，输出分割点前后文本片段，回原文本定位切分

    模型被要求用 JSON 输出每个分割位置的前10字符和后10字符，例如：
    {"splits": [{"before": "……前10字符", "after": "后10字符……"}]}
    """
    if not ocr_text or not ocr_text.strip():
        return []
    prompt = (
        "下面是一份可能包含多道练习题的OCR识别文本。请判断其中包含了多少道独立的题目，"
        "并找出每道题目之间的分割位置。\n"
        "对每个分割位置，请给出分割处【前10个字符】和【后10个字符】（用于在原文本中精确定位）。\n"
        "只输出JSON，格式如下：\n"
        '{"count": 题目数量, "splits": [{"before": "分割处前10字", "after": "分割处后10字"}]}\n'
        "如果只有一道题，输出 {\"count\": 1, \"splits\": []}。\n\nOCR文本：\n"
        + ocr_text
    )
    try:
        resp = ai_service.generate_response(prompt, engine=engine, model=model) if hasattr(ai_service, "generate_response") else None
        if not resp:
            return [ocr_text]
        import re, json as _json
        m = re.search(r'\{.*\}', resp, re.DOTALL)
        data = _json.loads(m.group(0)) if m else {}
        splits = data.get("splits", []) or []
        if not splits:
            return [ocr_text]
        return _apply_splits(ocr_text, splits)
    except Exception as e:
        print(f"[分题] LLM分题失败，按单题处理: {e}")
        return [ocr_text]


def _apply_splits(ocr_text: str, splits: List[dict]) -> List[str]:
    """用 (before, after) 片段在原文本中做匹配，切出多段题目"""
    # 规整空白，便于匹配
    import re
    norm = re.sub(r'\s+', '', ocr_text)
    # 计算每个分割点在 norm 中的位置
    cut_positions = []
    for s in splits:
        before = re.sub(r'\s+', '', s.get("before", ""))
        after = re.sub(r'\s+', '', s.get("after", ""))
        if not before and not after:
            continue
        # 找 before 末尾 + after 开头相邻的位置：before 的结尾应紧邻 after 的开头
        # 直接找 "before+after" 连接串在 norm 中出现的位置，切点在 before 结束处
        joint = before + after
        idx = norm.find(joint)
        if idx < 0:
            # 退而求其次：只按 before 定位
            idx = norm.find(before)
            if idx < 0:
                continue
            pos = idx + len(before)
        else:
            pos = idx + len(before)
        cut_positions.append(pos)
    if not cut_positions:
        return [ocr_text]
    cut_positions = sorted(set(cut_positions))
    # 按 norm 位置切分（因 norm 去掉了空白，需要映射回原始文本——用简单方案：把 norm 当正文切，段内用原字符）
    parts = []
    prev = 0
    for pos in cut_positions:
        if pos <= prev:
            continue
        seg = norm[prev:pos].strip()
        if seg:
            parts.append(seg)
        prev = pos
    tail = norm[prev:].strip()
    if tail:
        parts.append(tail)
    return parts if parts else [ocr_text]


def split_questions(ocr_text: str, ocr_source: str = "", ai_service=None, engine: Optional[str] = None, model: Optional[str] = None) -> List[str]:
    """统一分题入口"""
    if not ocr_text or not ocr_text.strip():
        return []
    # 视觉模型：直接按 %%%
    if "vision" in ocr_source or "%%%" in ocr_text:
        parts = split_by_separator(ocr_text)
        if len(parts) > 1:
            return parts
    # Paddle / 无分隔：优先 %%%，否则 LLM
    if "%%%" in ocr_text:
        parts = split_by_separator(ocr_text)
        if len(parts) > 1:
            return parts
    if ai_service is not None:
        parts = split_by_model(ocr_text, ai_service, engine, model)
        if len(parts) > 1:
            return parts
    return [ocr_text]
