"""流式解题流水线。

流程（单题）：
    OCR → 题目信息 → 解题思路 → 完整解析 → LaTeX 渲染 → 思维导图 → 预判问题

多题时先分题，让用户勾选要解的题（由网关侧交互），然后并发逐题走完整流程。
每一步都以 SSE 事件下发，网关透传给客户端。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator

from ..ai import engine
from ..ai.prompts import (
    FULL_SOLUTION_PROMPT,
    MINDMAP_PROMPT,
    QUESTION_INFO_PROMPT,
    SOLVE_STEPS_PROMPT,
    SUGGESTED_QUESTIONS_PROMPT,
    extra_hints,
    teacher_desc,
)
from . import ocr as ocr_mod
from .split import looks_like_mindmap, split_questions

log = logging.getLogger("aiworker.solve")


def emit(stage: str, content: Any = None, qi: int | None = None) -> dict:
    ev: dict[str, Any] = {"stage": stage, "content": content}
    if qi is not None:
        ev["qi"] = qi
    return ev


class SolveParams:
    """一次解题请求的全部参数（由网关透传）。"""

    def __init__(self, payload: dict):
        self.session_id: str = payload.get("session_id") or ""
        self.user_id = payload.get("user_id")
        self.base_host: str = payload.get("base_host") or ""
        self.engine: str = payload.get("engine") or "deepseek"
        self.model: str = payload.get("model") or ""
        self.vision_model: str = payload.get("vision_model") or ""
        self.style: str = payload.get("style") or ""
        self.thinking: str = payload.get("thinking") or ""
        self.latex_helper: str = payload.get("latex_helper") or "auto"
        self.search_enabled: bool = bool(payload.get("search_enabled"))
        self.dialect: str = payload.get("dialect") or "普通话"
        self.grade: str = payload.get("grade") or ""
        self.personality: str = payload.get("personality") or ""
        self.subject: str = payload.get("subject") or ""
        self.detail: str = payload.get("detail") or "auto"
        self.weak_count: int = int(payload.get("weak_count") or 0)
        self.interactive_quiz: bool = bool(payload.get("interactive_quiz"))
        self.text_input: str = (payload.get("text_input") or "").strip()
        self.image_base64: str = payload.get("image_base64") or ""
        self.results_dir: Path = Path(payload.get("results_dir") or "")

    @property
    def teacher(self) -> str:
        return teacher_desc(self.style, self.dialect, self.personality, self.detail, self.subject)

    @property
    def extra(self) -> str:
        return extra_hints(self.dialect, self.grade)

    @property
    def want_latex(self) -> bool:
        """是否需要图解辅助。"""
        return _should_use_latex(self)


def _should_use_latex(p: SolveParams) -> bool:
    """图解辅助开关：auto 时按学科与难度决定。"""
    v = (p.latex_helper or "auto").lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    # auto：数学/物理且不是"易"难度时才画图
    subject = p.subject or ""
    return subject in ("数学", "物理")


def solve_stream(p: SolveParams) -> Iterator[dict]:
    """执行整条流水线，逐条产出 SSE 事件。"""
    # ---------- 阶段 1：OCR ----------
    if p.text_input:
        ocr_text = p.text_input
        ocr_time = 0.0
        ocr_source = "text"
        yield emit("info", "使用输入的文本，跳过识别")
    else:
        if not p.image_base64:
            yield emit("error", "未提供图片或文本")
            return
        yield emit("info", "正在识别题目文字...")
        res = ocr_mod.recognize(None, p.image_base64, mode="qwen", vision_model=p.vision_model)
        ocr_text, ocr_time, ocr_source = res.text, res.elapsed, res.source

    yield emit("ocr_complete", {"text": ocr_text, "time": round(ocr_time, 2), "source": ocr_source})

    if not ocr_text.strip() or ocr_text.startswith("OCR"):
        yield emit("error", "未能识别出题目文字，请换一张更清晰的照片")
        yield emit("complete", {"failed": True})
        return

    # ---------- 阶段 2：分题 ----------
    questions = split_questions(ocr_text, ocr_source, p.engine, p.model)
    if len(questions) > 1:
        # 多题：交给客户端勾选。网关会把这两个事件转成用户交互。
        yield emit("question_split", {
            "count": len(questions),
            "previews": [_preview(q) for q in questions],
        })
        # 等待网关回传选中项（由网关注入 selected_indices）
        selected = getattr(p, "selected_indices", None)
        if selected is None:
            selected = list(range(len(questions)))
        questions = [questions[i] for i in selected if 0 <= i < len(questions)]

    if not questions:
        yield emit("error", "分题后没有可解的题目")
        yield emit("complete", {"failed": True})
        return

    # ---------- 阶段 3：逐题解答 ----------
    results = []
    if len(questions) > 1:
        import concurrent.futures as cf

        with cf.ThreadPoolExecutor(max_workers=min(4, len(questions))) as pool:
            futures = {pool.submit(_solve_one, q, p, i): i for i, q in enumerate(questions)}
            for fut in cf.as_completed(futures):
                i = futures[fut]
                try:
                    results.append((i, fut.result()))
                except Exception as e:  # noqa: BLE001
                    log.exception("第 %d 题解答异常", i + 1)
                    yield emit("error", f"第{i + 1}题异常: {e}", qi=i)
        results.sort(key=lambda x: x[0])
        collected = [r for _, r in results]
    else:
        collected = [_solve_one(questions[0], p, None)]

    # ---------- 阶段 4：落盘结构化结果（供网关写库） ----------
    if p.results_dir and p.session_id:
        _save_result(p, ocr_text, ocr_time, collected)

    merged = collected[0] if len(collected) == 1 else _merge_multi(collected)
    yield emit("complete", {
        "session_id": p.session_id,
        "total_time": merged.get("total_time"),
        "count": len(collected),
    })


def _preview(q: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", q.strip())
    return s[:n] + ("…" if len(s) > n else "")


def _merge_multi(collected: list[dict]) -> dict:
    """多题结果合并成一份（用于总览展示）。"""
    out: dict[str, Any] = {"total_time": 0.0, "count": len(collected)}
    for key in ("question_info", "solution_steps", "full_solution", "mind_map"):
        parts = [c.get(key) or "" for c in collected if c.get(key)]
        out[key] = "\n\n---\n\n".join(parts)
    return out


def _solve_one(question: str, p: SolveParams, qi: int | None) -> dict:
    """解一道题，返回结构化结果。调用方负责把过程事件 yield 出去。"""
    import time

    t0 = time.time()
    cl = engine.client()
    result: dict[str, Any] = {
        "ocr_text": "", "question_info": {}, "solution_steps": "",
        "full_solution": "", "mind_map": "", "suggested_questions": [],
        "total_time": 0.0,
    }
    messages: list[dict] = [
        {"role": "system", "content": f"题目：\n{question}"},
    ]

    # 题目信息（结构化，供报告统计）
    try:
        info = engine.extract_json(cl.complete(
            [{"role": "user", "content": QUESTION_INFO_PROMPT.format(question=question)}],
            engine=p.engine, model=p.model, temperature=0.0, max_tokens=1500, json_mode=False,
        ))
        if isinstance(info, dict):
            result["question_info"] = info
    except Exception as e:  # noqa: BLE001
        log.warning("题目信息抽取失败: %s", e)

    # 解题思路
    try:
        steps = cl.complete(
            [{"role": "user", "content": SOLVE_STEPS_PROMPT.format(
                teacher_desc=p.teacher, extra=p.extra, question=question)}],
            engine=p.engine, model=p.model, max_tokens=4000,
        )
        result["solution_steps"] = steps
        messages.append({"role": "user", "content": "请给出完整的解题过程和答案"})
    except Exception as e:  # noqa: BLE001
        log.warning("解题思路生成失败: %s", e)
        messages.append({"role": "user", "content": "请给出完整的解题过程和答案"})

    # 完整解析（流式，让用户看到进度）
    want_latex = _should_use_latex(p)
    full_prompt = FULL_SOLUTION_PROMPT.format(
        teacher_desc=p.teacher, extra=p.extra, question=question,
        solution_steps=result["solution_steps"][:2000],
    )
    buf: list[str] = []
    try:
        # 推理型模型思考与正文共用额度，长输出必须给足 token
        for ch in cl.stream([{"role": "user", "content": full_prompt}],
                            engine=p.engine, model=p.model, max_tokens=12000):
            if ch.text:
                buf.append(ch.text)
    except Exception as e:  # noqa: BLE001
        log.warning("完整解析流式失败，改用阻塞: %s", e)
        try:
            txt = cl.complete([{"role": "user", "content": full_prompt}],
                              engine=p.engine, model=p.model, max_tokens=12000)
            buf = [txt]
        except Exception as e2:  # noqa: BLE001
            log.warning("完整解析失败: %s", e2)

    full = "".join(buf).strip()
    result["full_solution"] = full
    result["suggested_questions"] = []
    result["mind_map"] = ""
    result["total_time"] = round(time.time() - t0, 2)
    result["_messages"] = messages
    result["_question"] = question
    result["_has_latex_block"] = "```latex" in full
    result["_want_latex"] = want_latex
    return result


def solve_one_stream(question: str, p: SolveParams, qi: int | None = None) -> dict:
    """解一道题，边生成边产出事件。

    返回 {"events": [...], "data": {...}}。

    相对原项目的改进：思维导图、LaTeX 渲染这些"生成即完成"的步骤会实时下发事件，
    用户能看到真实进度，而不是在最后一次性看到全部结果。
    """
    import time

    t0 = time.time()
    cl = engine.client()
    events: list[dict] = []
    data: dict[str, Any] = {
        "question_info": {}, "solution_steps": "", "full_solution": "",
        "mind_map": "", "suggested_questions": [], "_messages": [],
        "total_time": 0.0,
    }

    def out(stage: str, content: Any = None) -> None:
        events.append(emit(stage, content, qi))

    # 1) 题目信息
    out("info", "正在分析题目结构...")
    try:
        info = engine.extract_json(cl.complete(
            [{"role": "user", "content": QUESTION_INFO_PROMPT.format(question=question)}],
            engine=p.engine, model=p.model, temperature=0.0, max_tokens=1500,
        ))
        if isinstance(info, dict):
            data["question_info"] = info
            out("question_info", info)
    except Exception as e:  # noqa: BLE001
        log.warning("题目信息抽取失败: %s", e)

    # 2) 解题思路
    out("info", "正在生成解题思路...")
    steps_prompt = SOLVE_STEPS_PROMPT.format(
        teacher_desc=p.teacher, extra=p.extra, question=question
    )
    steps = ""
    try:
        steps = cl.complete([{"role": "user", "content": steps_prompt}],
                            engine=p.engine, model=p.model, max_tokens=4000)
    except Exception as e:  # noqa: BLE001
        log.warning("解题思路生成失败: %s", e)
    data["solution_steps"] = steps
    if steps:
        out("solution_steps", steps)

    # 3) 完整解析（流式）
    out("info", "正在生成完整解析...")
    full_prompt = FULL_SOLUTION_PROMPT.format(
        teacher_desc=p.teacher, extra=p.extra, question=question,
        solution_steps=steps[:2000],
    )
    buf: list[str] = []
    try:
        for ch in cl.stream([{"role": "user", "content": full_prompt}],
                            engine=p.engine, model=p.model, max_tokens=12000):
            if ch.reasoning:
                out("thinking_chunk", ch.reasoning)
            if ch.text:
                buf.append(ch.text)
                out("solution_chunk", ch.text)
    except Exception as e:  # noqa: BLE001
        log.warning("流式解析失败，改用阻塞: %s", e)
        try:
            buf = [cl.complete([{"role": "user", "content": full_prompt}],
                               engine=p.engine, model=p.model, max_tokens=12000)]
        except Exception as e2:  # noqa: BLE001
            log.warning("完整解析失败: %s", e2)
            out("error", f"生成完整解析失败: {e2}")

    full = "".join(buf).strip()
    data["full_solution"] = full

    # 4) LaTeX 渲染（有需要且有编译环境时）
    rendered = full
    if p.want_latex and "```latex" in full:
        out("info", "正在渲染图形...")
        rendered = _render_latex(full, p, qi)
    data["full_solution"] = rendered
    out("solution_rendered", rendered)

    # 5) 思维导图
    out("info", "正在生成思维导图...")
    try:
        mm = cl.complete(
            [{"role": "user", "content": MINDMAP_PROMPT.format(question=question)}],
            engine=p.engine, model=p.model, max_tokens=2000,
        ).strip()
        # 只有真像导图才下发，否则前端会渲染成一坨散字
        if mm and looks_like_mindmap(mm):
            data["mind_map"] = mm
            out("mindmap", mm)
        elif mm:
            log.info("思维导图输出不符合树形格式，已丢弃")
    except Exception as e:  # noqa: BLE001
        log.warning("思维导图生成失败: %s", e)

    # 6) 预判问题
    try:
        qs = engine.extract_json(cl.complete(
            [{"role": "user", "content": SUGGESTED_QUESTIONS_PROMPT.format(question=question)}],
            engine=p.engine, model=p.model, temperature=0.2, max_tokens=800,
        ))
        if isinstance(qs, list) and qs:
            data["suggested_questions"] = [str(x)[:120] for x in qs[:5] if isinstance(x, (str, int, float))]
            out("suggested_questions", data["suggested_questions"])
    except Exception as e:  # noqa: BLE001
        log.warning("预判问题生成失败: %s", e)

    data["total_time"] = round(time.time() - t0, 2)
    return {"events": events, "data": data}


def _render_latex(md: str, p: SolveParams, qi: int | None) -> str:
    """渲染 Markdown 中的 LaTeX 块，失败则返回原文。"""
    try:
        from ..latex import renderer
    except Exception as e:  # noqa: BLE001
        log.warning("LaTeX 模块不可用: %s", e)
        return md

    if not renderer.available_engines():
        log.info("未安装 LaTeX 引擎，跳过图形渲染")
        return md

    out_dir = (p.results_dir.parent / "svgs" / p.session_id) if p.session_id else \
        Path(tempfile.gettempdir()) / "lago_svgs"

    def fix(code: str, error: str) -> str:
        prompt = (
            "这段 LaTeX/TikZ 代码编译失败，错误信息：\n"
            f"{error[:600]}\n\n代码：\n```latex\n{code}\n```\n\n"
            "请修正后只输出完整代码，不要解释。"
        )
        try:
            return engine.client().complete([{"role": "user", "content": prompt}],
                                           engine=p.engine, temperature=0.1, max_tokens=3000)
        except Exception:  # noqa: BLE001
            return ""

    try:
        return renderer.render_markdown(md, out_dir, f"/static/svgs/{p.session_id}",
                                        fix_fn=fix, max_retries=2)
    except Exception as e:  # noqa: BLE001
        log.warning("LaTeX 渲染异常: %s", e)
        return md


def _save_result(p: SolveParams, ocr_text: str, ocr_time: float, collected: list[dict]) -> None:
    """把结果写成 JSON，供网关读取入库。"""
    try:
        p.results_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ocr_text": ocr_text,
            "ocr_time": round(ocr_time, 2),
            "question_info": collected[0].get("question_info", {}) if collected else {},
            "solution_steps": "\n\n---\n\n".join(c.get("solution_steps", "") for c in collected),
            "full_solution": "\n\n---\n\n".join(c.get("full_solution", "") for c in collected),
            "mind_map": "\n\n".join(c.get("mind_map", "") for c in collected),
            "suggested_questions": [q for c in collected for q in (c.get("suggested_questions") or [])],
            "messages": [m for c in collected for m in (c.get("_messages") or [])],
        }
        out = p.results_dir / f"{p.session_id}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("保存解题结果失败: %s", e)


# ---------- 追问 ----------

def ask(messages: list[dict], question: str, p_engine: str, model: str,
        style: str, dialect: str, grade: str) -> str:
    """多轮追问。上下文已由网关清洗过。"""
    system = f"你是一位中学教师，正在回答学生的追问。请直接回答，不要复述题目原文。"
    if grade:
        system += f"按{grade}的认知水平作答。"
    if dialect and dialect != "普通话":
        system += f"可用{dialect}表达，但数学符号保持标准。"
    return engine.client().complete(
        messages + [{"role": "user", "content": question}],
        engine=p_engine, model=model, system=system, max_tokens=6000,
    )


def ask_stream(messages: list[dict], question: str, p_engine: str, model: str,
               style: str, dialect: str, grade: str) -> Iterator[dict]:
    """流式追问。"""
    system = f"你是一位中学教师，正在回答学生的追问。请直接回答，不要复述题目原文。"
    if grade:
        system += f"按{grade}的认知水平作答。"
    cl = engine.client()
    try:
        for ch in cl.stream(messages + [{"role": "user", "content": question}],
                            engine=p_engine, model=model, system=system, max_tokens=6000):
            if ch.text:
                yield emit("chunk", ch.text)
    except Exception as e:  # noqa: BLE001
        yield emit("error", f"追问失败: {e}")
    yield emit("complete", None)


# ---------- 知识延伸 ----------

def extend_stream(question: str, p: SolveParams) -> Iterator[dict]:
    """生成知识延伸。"""
    from ..ai.prompts import EXTEND_PROMPT

    prompt = EXTEND_PROMPT.format(teacher_desc=p.teacher, extra=p.extra, question=question)
    buf: list[str] = []
    try:
        for ch in engine.client().stream([{"role": "user", "content": prompt}],
                                         engine=p.engine, model=p.model, max_tokens=8000):
            if ch.text:
                buf.append(ch.text)
                yield emit("chunk", ch.text)
    except Exception as e:  # noqa: BLE001
        yield emit("error", f"知识延伸生成失败: {e}")

    content = "".join(buf).strip()
    yield emit("extension_result", {
        "title": "知识延伸",
        "content": content,
        "ocr_text": question,
    })
    yield emit("complete", None)


# ---------- 学情报告 ----------

def report_mistakes(stats: dict[str, int], limit: int = 10) -> str:
    """易错点梳理。"""
    from ..ai.prompts import MISTAKES_PROMPT

    if not stats:
        return "目前还没有足够的作答数据来梳理易错点。"
    top = sorted(stats.items(), key=lambda x: -x[1])[:limit]
    body = "\n".join(f"- {k}（出现 {v} 次）" for k, v in top)
    return engine.client().complete(
        [{"role": "user", "content": MISTAKES_PROMPT.format(mistakes=body)}],
        max_tokens=3000,
    )


def report_ai(stats: dict, subject: str, grade: str, theme: str,
              p_engine: str, model: str, days: int = 30) -> str:
    """AI 版学情报告。"""
    from ..ai.prompts import REPORT_AI_PROMPT

    fmt_map = lambda m: "、".join(f"{k}({v})" for k, v in (m or {}).items()) or "无数据"  # noqa: E731

    body = REPORT_AI_PROMPT.format(
        days=days,
        total=stats.get("total", 0),
        correct=stats.get("correct", 0),
        wrong=stats.get("wrong", 0),
        accuracy=stats.get("accuracy", 0),
        subjects=fmt_map(stats.get("subjects")),
        difficulty=fmt_map(stats.get("difficulty")),
        knowledge=fmt_map(dict(list((stats.get("knowledge") or {}).items())[:8])),
    )
    return engine.client().complete(
        [{"role": "user", "content": body}],
        engine=p_engine, model=model, max_tokens=4000,
    )


def pomodoro_recommend(ocr_text: str, summary: str, p_engine: str) -> dict:
    """按题目难度推荐专注时长。"""
    from ..ai.prompts import POMODORO_PROMPT

    try:
        raw = engine.client().complete(
            [{"role": "user", "content": POMODORO_PROMPT.format(question=ocr_text[:1500])}],
            engine=p_engine, temperature=0.2, max_tokens=600,
        )
        data = engine.extract_json(raw)
        if isinstance(data, dict) and data.get("minutes"):
            return {"minutes": int(data["minutes"]), "tip": str(data.get("tip", ""))}
    except Exception as e:  # noqa: BLE001
        log.warning("番茄钟推荐失败: %s", e)

    # 兜底：按题目长度给个保守建议
    n = len(ocr_text)
    minutes = 45 if n > 500 else (25 if n > 200 else 15)
    return {"minutes": minutes, "tip": f"题目较长，建议分 {minutes} 分钟完成，先通读一遍再动笔。"}