"""多题切分：把一次识别出的整段文本切成若干道独立题目。

两条路径：
  - 视觉通道：OCR 提示词已要求模型用 %%% 分隔，直接按分隔符切
  - Paddle 通道：文本无天然分隔，让 LLM 给出每个分割点前后各 10 字，再回原文本定位切点

关键点：宁可漏切也不可错切。错切会把一道题劈成两半，学生看到两个半截题目，
比"没识别出分题"糟糕得多。因此定位失败时宁可返回整段文本。
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("aiworker.split")

SEP = "%%%"

# 顶层题号：真正的新题边界（第1题 / 1. / 一、 / 题目3）
_TOP_Q_RE = re.compile(
    r"^\s*(?:第\s*[0-9]{1,3}\s*[题问]|题目\s*[0-9]{1,3}|[0-9]{1,3}\s*[.、)）]|[一二三四五六七八九十]{1,2}\s*[、.．])"
)
# 小问标记：属于同一道大题
_SUB_Q_RE = re.compile(
    r"^\s*(?:[（(]\s*[0-9]{1,2}\s*[)）]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]|[a-dA-D]\s*[).、]|第\s*[0-9一二三四五六七八九十]{1,2}\s*小?问)"
)


def split_by_separator(text: str, sep: str = SEP) -> list[str]:
    """按分隔符切分，清洗空白与首尾序号。"""
    if not text:
        return []
    parts = [p.strip() for p in text.split(sep)]
    return [p for p in parts if p and p.strip()]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def split_by_model(ocr_text: str, engine: str, model: str) -> list[str]:
    """让 LLM 指出分割位置，再回原文本精确定位切分。"""
    from ..ai import engine as engine_mod
    from ..ai.prompts import SPLIT_PROMPT

    try:
        resp = engine_mod.client().complete(
            [{"role": "user", "content": SPLIT_PROMPT + "\n\nOCR文本：\n" + ocr_text}],
            engine=engine,
            model=model,
            system="你是题目切分助手。只输出 JSON，不要任何解释文字。",
            temperature=0.0,
            max_tokens=2000,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("LLM 分题失败，按单题处理: %s", e)
        return [ocr_text]

    data = engine_mod.extract_json(resp)
    if not isinstance(data, dict):
        return [ocr_text]
    splits = data.get("splits") or []
    if not splits:
        return [ocr_text]

    return _apply_splits(ocr_text, splits)


def _apply_splits(ocr_text: str, splits: list[dict]) -> list[str]:
    """用 (before, after) 锚点在原文本中定位切点。

    全部空白归一化后再匹配，避免 OCR 文本里的换行/空格差异导致定位失败。
    """
    norm = _norm(ocr_text)
    cuts: list[int] = []

    for s in splits:
        before = _norm(s.get("before", ""))
        after = _norm(s.get("after", ""))
        if not before and not after:
            continue

        pos = -1
        joint = before + after
        if joint:
            i = norm.find(joint)
            if i >= 0:
                pos = i + len(before)
        if pos < 0 and before:
            i = norm.find(before)
            if i >= 0:
                pos = i + len(before)

        if pos > 0:
            cuts.append(pos)

    if not cuts:
        return [ocr_text]

    cuts = sorted(set(cuts))
    parts: list[str] = []
    prev = 0
    for pos in cuts:
        if pos <= prev or pos >= len(norm):
            continue
        seg = norm[prev:pos].strip()
        if seg:
            parts.append(seg)
        prev = pos
    tail = norm[prev:].strip()
    if tail:
        parts.append(tail)

    # 切出来的片段过短（<10 字）多半是切错了，放弃分题按整体处理
    if len(parts) <= 1 or any(len(p) < 10 for p in parts):
        return [ocr_text]
    return parts


def merge_related(parts: list[str]) -> list[str]:
    """把"本是同一道题却被切开"的片段合回去。

    判定：片段以小问标记开头 / 片段没有顶层题号（跨栏续写、断行续写）→ 并入上一题。
    """
    if not parts:
        return parts
    out = [(parts[0] or "").strip()]
    for raw in parts[1:]:
        seg = (raw or "").strip()
        if not seg:
            continue
        is_sub = bool(_SUB_Q_RE.match(seg))
        has_top = bool(_TOP_Q_RE.match(seg))
        if is_sub or not has_top:
            out[-1] = out[-1].rstrip() + "\n" + seg
        else:
            out.append(seg)
    return [p for p in out if p.strip()]


def split_questions(text: str, ocr_source: str = "", engine: str = "deepseek",
                    model: str = "") -> list[str]:
    """统一分题入口。"""
    if not text or not text.strip():
        return []

    # 视觉通道：模型已用 %%% 标注边界
    if "vision" in ocr_source or SEP in text:
        parts = split_by_separator(text)
        if len(parts) > 1:
            return _sanitize(parts)

    # Paddle 通道：交给 LLM 找边界
    parts = split_by_model(text, engine, model)
    if len(parts) > 1:
        return _sanitize(parts)

    return [text.strip()]


def _sanitize(parts: list[str]) -> list[str]:
    """分题结果清洗：丢掉过短片段，但至少保留一份。"""
    cleaned = [p.strip() for p in parts if p and len(p.strip()) >= 10]
    return cleaned or [p.strip() for p in parts if p and p.strip()] or [""]


def looks_like_mindmap(s: str) -> bool:
    """判断一段文本是否真的是思维导图（含树形符号或规范缩进）。"""
    if not s:
        return False
    return len(re.findall(r"[\u2500-\u257F]", s)) >= 3 or s.count("\n    ") >= 3