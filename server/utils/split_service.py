"""① 多题模式：把一次OCR识别出的整段文本分割为多道独立题目

- 视觉模型（qwen/deepseek vision）模式：OCR 提示词要求题与题之间用 ``%%%`` 分隔，直接按 ``%%%`` 切分。
- PaddleOCR 模式：OCR 文本无天然分隔，调用大语言模型识别题目边界，
  让模型输出“分割位置的前10字符 + 后10字符”，再回到原文本中做最左匹配定位精确切点。
- 两条路径最后都会过一遍 :func:`merge_related`：
  题目跨栏、同篇阅读（短文+其下小题）、同一大题的小问，都视为同一题合回去。
"""
import re
from typing import List, Optional

# 顶层题号：真正的新题边界（第1题 / 1. / 一、 / 题目3）
_TOP_Q_RE = re.compile(
    r'^\s*(?:第\s*[0-9]{1,3}\s*[题问]|题目\s*[0-9]{1,3}|[0-9]{1,3}\s*[.、)）]|[一二三四五六七八九十]{1,2}\s*[、.．])'
)
# 小问/子题标记：属于同一道大题，不切分
_SUB_Q_RE = re.compile(
    r'^\s*(?:[（(]\s*[0-9]{1,2}\s*[)）]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]|[a-dA-D]\s*[).、]|第\s*[0-9一二三四五六七八九十]{1,2}\s*小?问)'
)
# 阅读材料特征：同篇阅读要连后面的小题算一题
# 注意中间允许插字（如“阅读下面的【】短文”），否则像“阅读下面的短文”这种常见表述会漏掉
_PASSAGE_RE = re.compile(
    r'阅读[^\n]{0,12}(材料|短文|文章|文本|语段)'
    r'|(材料|短文|文章|文本|语段)[^\n]{0,8}(回答|做题|完成下)'
    r'|阅读理解|完形填空|根据(短文|材料|文章)'
)


def merge_related(parts: List[str]) -> List[str]:
    """把“本来是同一道题却被切开”的片段合回去（跨栏 / 同篇阅读 / 同一大题的小问）

    合并判定（命中任意一条就并入上一题）：
      1. 片段以小问标记开头（(1)/①/第(2)问）→ 同一大题
      2. 片段没有顶层题号 → 上一题的续写（跨栏溢到另一栏、断行续写）
      3. 上一段是阅读材料/短文 → 同篇阅读，其下小题全部并入
    否则保留为独立题目。
    """
    if not parts:
        return parts
    out = [(parts[0] or '').strip()]
    for raw in parts[1:]:
        seg = (raw or '').strip()
        if not seg:
            continue
        prev = out[-1]
        is_sub = bool(_SUB_Q_RE.match(seg))
        has_top = bool(_TOP_Q_RE.match(seg))
        prev_is_passage = bool(_PASSAGE_RE.search(prev[:200]))
        if is_sub or (not has_top) or prev_is_passage:
            out[-1] = prev.rstrip() + "\n" + seg
        else:
            out.append(seg)
    return [p for p in out if p.strip()]


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
        "下面是一份可能包含多道练习题的OCR识别文本。请判断其中包含了多少道【彼此独立的大题】，"
        "并找出每两道大题之间的分割位置。\n"
        "重要：以下情况都属于同一道题，【不要】在这些位置切分：\n"
        "  (a) 分栏排版时同一道题从左边一栏续到右边一栏（跨栏）；\n"
        "  (b) 同一篇阅读材料/短文及其下面的全部小题（同篇阅读）；\n"
        "  (c) 同一道大题下面的各个小问，如 (1)(2)(3)、①②③、第(1)问（同一大题）；\n"
        "  (d) 题干、选项与附图属于同一道题。\n"
        "对每个分割位置，请给出分割处【前10个字符】和【后10个字符】（用于在原文本中精确定位）。\n"
        "只输出JSON，格式如下：\n"
        '{"count": 题目数量, "splits": [{"before": "分割处前10字", "after": "分割处后10字"}]}\n'
        "如果只有一道题，输出 {\"count\": 1, \"splits\": []}。\n\nOCR文本：\n"
        + ocr_text
    )
    try:
        resp = ai_service.generate_response(
                prompt, engine=engine, model=model,
                system="你是题目切分助手。只输出 JSON，不要任何解释文字。",
            )
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
    """统一分题入口：完全信任 AI 给出的题目边界（视觉用 %%%、Paddle 用 LLM 锚点），
    程序不再做二次合并。跨栏/同篇阅读/同一大题的判定由 OCR 提示词约束 AI。
    （merge_related 仍保留定义，供需要时调用。）"""
    if not ocr_text or not ocr_text.strip():
        return []
    # 视觉模型：AI 已用 %%% 明确标注独立题边界，直接采用
    if "vision" in ocr_source or "%%%" in ocr_text:
        parts = split_by_separator(ocr_text)
        if len(parts) > 1:
            return parts
    # Paddle：无分隔符时交由 LLM 识别边界
    if "%%%" in ocr_text:
        parts = split_by_separator(ocr_text)
        if len(parts) > 1:
            return parts
    if ai_service is not None:
        parts = split_by_model(ocr_text, ai_service, engine, model)
        if len(parts) > 1:
            return parts
    return [ocr_text]
