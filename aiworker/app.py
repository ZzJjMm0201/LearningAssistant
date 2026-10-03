"""AI 服务 FastAPI 应用。

所有端点都在这里挂载；业务逻辑在 ocr/ latex/ vision/ ai/ 各子模块。
本服务只监听 127.0.0.1，由 Go 网关代理调用。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .ai import engine
from .ocr import ocr as ocr_mod
from .ocr import pipeline as pipe
from .ocr.split import split_questions
from .vision import animation as anim_mod
from .vision import annotate as ann_mod
from .vision import exporter as exp_mod
from .vision import keyframe as kf_mod

log = logging.getLogger("aiworker")

BASE_DIR = Path(__file__).resolve().parent.parent
HIST_DIR = Path(os.environ.get("HISTORY_DIR", BASE_DIR / "history"))
ANNOTATE_DIR = Path(os.environ.get("ANNOTATE_DIR", BASE_DIR / "annotations"))
ANIM_DIR = Path(os.environ.get("ANIM_DIR", BASE_DIR / "history" / "animations"))
EXPORT_DIR = Path(os.environ.get("EXPORT_DIR", BASE_DIR / "exports"))
RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", BASE_DIR / "history" / "results"))

app = FastAPI(title="学习助手 AI 服务", version="3.0.0-go")


# ---------- 请求体 ----------

class OCRReq(BaseModel):
    image_base64: str
    mode: str = "qwen"
    vision_model: str = ""


class SplitReq(BaseModel):
    text: str
    ocr_source: str = ""
    engine: str = "deepseek"
    model: str = ""


class SolveReq(BaseModel):
    session_id: str = ""
    user_id: int | None = None
    base_host: str = ""
    engine: str = "deepseek"
    model: str = ""
    vision_model: str = ""
    style: str = ""
    thinking: str = ""
    latex_helper: str = "auto"
    search_enabled: bool = False
    dialect: str = "普通话"
    grade: str = ""
    personality: str = ""
    subject: str = ""
    detail: str = "auto"
    weak_count: int = 0
    interactive_quiz: bool = False
    text_input: str = ""
    image_base64: str = ""


class AskReq(BaseModel):
    messages: list[dict] = []
    question: str
    engine: str = "deepseek"
    model: str = ""
    style: str = ""
    dialect: str = "普通话"
    grade: str = ""


class ExtendReq(BaseModel):
    session_id: str = ""
    user_id: int | None = None
    text_input: str = ""
    image_base64: str = ""
    engine: str = "deepseek"
    model: str = ""
    style: str = ""
    dialect: str = "普通话"
    grade: str = ""
    vision_model: str = ""


class AnnotateReq(BaseModel):
    image_base64: str
    mode: str = "qwen"
    vision_model: str = ""
    output_path: str = ""
    base_url: str = "/static/annotations"


class AnimateReq(BaseModel):
    text: str
    solution: str = ""
    engine: str = "deepseek"
    theme: str = "dark"
    output_dir: str = ""
    base_url: str = "/static/animations"


class KeyframeReq(BaseModel):
    video_base64: str
    output_dir: str = ""


class LatexReq(BaseModel):
    markdown: str
    output_dir: str
    base_url: str = "/static/svgs"
    engine: str = "xelatex"
    model: str = ""
    vision_model: str = ""
    enable_review: bool = False


class MistakesReq(BaseModel):
    stats: dict[str, int]
    limit: int = 10


class ReportReq(BaseModel):
    stats: dict[str, Any]
    subject: str = ""
    grade: str = ""
    theme: str = "dark"
    engine: str = "deepseek"
    model: str = ""
    days: int = 30


class PomodoroReq(BaseModel):
    ocr_text: str = ""
    summary: str = ""
    engine: str = "deepseek"


class ExportReq(BaseModel):
    title: str = "导出"
    content: str = ""
    format: str = "pdf"
    host: str = ""


# ---------- SSE 工具 ----------

def sse(events) -> StreamingResponse:
    """把事件迭代器包装成 SSE 响应。"""
    def gen():
        for ev in events:
            try:
                data = json.dumps(ev, ensure_ascii=False)
            except (TypeError, ValueError):
                data = json.dumps({"stage": ev.get("stage"), "content": str(ev.get("content"))},
                                  ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def err(msg: str, code: int = 500) -> JSONResponse:
    return JSONResponse({"error": str(msg)}, status_code=code)


# ---------- 基础 ----------

@app.get("/health")
def health():
    """健康检查。同时报告可用能力，便于网关据此降级提示。"""
    try:
        from .latex import renderer

        latex_ok = bool(renderer.available_engines())
    except Exception:  # noqa: BLE001
        latex_ok = False
    return {
        "status": "ok",
        "engines": engine.available_engines(),
        "latex": latex_ok,
        "ocr_api": bool(os.environ.get("OCR_API_URL")),
    }


# ---------- OCR ----------

@app.post("/ocr")
def ocr(req: OCRReq):
    res = ocr_mod.recognize(None, req.image_base64, mode=req.mode, vision_model=req.vision_model)
    return res.to_dict()


@app.post("/ocr/boxes")
def ocr_boxes(req: OCRReq):
    text, blocks, w, h = ocr_mod.recognize_with_boxes(req.image_base64, None)
    return {"text": text, "blocks": blocks, "width": w, "height": h}


@app.post("/split")
def split(req: SplitReq):
    questions = split_questions(req.text, req.ocr_source, req.engine, req.model)
    return {"questions": questions}


# ---------- 解题 ----------

@app.post("/solve/stream")
def solve(req: SolveReq):
    p = pipe.SolveParams(req.model_dump())
    p.results_dir = RESULTS_DIR
    return sse(_solve_events(p))


def _head_events(p: pipe.SolveParams):
    """OCR + 分题，返回事件并把题目存进 p._questions。"""
    if p.text_input:
        ocr_text, ocr_time, source = p.text_input, 0.0, "text"
        yield pipe.emit("info", "使用输入的文本，跳过识别")
    else:
        if not p.image_base64:
            yield pipe.emit("error", "未提供图片或文本")
            return
        yield pipe.emit("info", "正在识别题目文字...")
        res = ocr_mod.recognize(None, p.image_base64, mode="qwen", vision_model=p.vision_model)
        ocr_text, ocr_time, source = res.text, res.elapsed, res.source

    yield pipe.emit("ocr_complete", {"text": ocr_text, "time": round(ocr_time, 2), "source": source})

    if not ocr_text.strip() or ocr_text.startswith("OCR"):
        yield pipe.emit("error", "未能识别出题目文字，请换一张更清晰的照片")
        return

    questions = split_questions(ocr_text, source, p.engine, p.model)
    if len(questions) > 1:
        yield pipe.emit("question_split", {
            "count": len(questions),
            "previews": [pipe._preview(q) for q in questions],
        })
        selected = getattr(p, "selected_indices", None)
        if selected is None:
            selected = list(range(len(questions)))
        questions = [questions[i] for i in selected if 0 <= i < len(questions)]

    if not questions:
        yield pipe.emit("error", "分题后没有可解的题目")
        return

    p._questions = questions
    p._ocr_text = ocr_text
    p._ocr_time = ocr_time


def _solve_events(p: pipe.SolveParams):
    # 没配 API Key 时立即退出：让用户一眼看到原因，
    # 而不是逐环节失败后收到一堆空结果
    if not engine.available_engines():
        msg = "未配置任何可用的模型 API Key。请在 .env 中设置 DEEPSEEK_API_KEY 等环境变量后重启 AI 服务。"
        yield pipe.emit("error", msg)
        yield pipe.emit("complete", {"failed": True})
        return

    for ev in _head_events(p):
        if ev.get("stage") == "complete":
            yield ev
            return
        yield ev
        if ev.get("stage") == "error":
            yield pipe.emit("complete", {"failed": True})
            return

    questions = getattr(p, "_questions", [])
    results = []
    for i, q in enumerate(questions):
        qi = i if len(questions) > 1 else None
        if qi is not None:
            yield pipe.emit("info", f"正在解答第 {i + 1} 题", qi=qi)
        res = pipe.solve_one_stream(q, p, qi)
        for ev in res["events"]:
            yield ev
        results.append(res["data"])

    if p.session_id:
        pipe._save_result(p, getattr(p, "_ocr_text", ""), getattr(p, "_ocr_time", 0.0), results)

    merged = pipe._merge_multi(results)
    yield pipe.emit("complete", {
        "session_id": p.session_id,
        "total_time": merged.get("total_time"),
        "count": len(results),
    })


# ---------- 追问 ----------

NO_KEY_MSG = "未配置任何可用的模型 API Key。请在 .env 中设置 DEEPSEEK_API_KEY 等环境变量后重启 AI 服务。"


def _require_key() -> JSONResponse | None:
    """未配 API Key 时返回一个 503，由各 AI 端点在开头调用。"""
    if not engine.available_engines():
        return JSONResponse({"error": NO_KEY_MSG}, status_code=503)
    return None


@app.post("/ask")
def ask(req: AskReq):
    if e := _require_key():
        return e
    try:
        answer = pipe.ask(req.messages, req.question, req.engine, req.model,
                          req.style, req.dialect, req.grade)
        return {"answer": answer}
    except Exception as e:  # noqa: BLE001
        return err(e, 502)


@app.post("/ask/stream")
def ask_stream(req: AskReq):
    return sse(pipe.ask_stream(req.messages, req.question, req.engine, req.model,
                               req.style, req.dialect, req.grade))


# ---------- 知识延伸 ----------

@app.post("/extend/stream")
def extend_stream(req: ExtendReq):
    if e := _require_key():
        return e

    def gen():
        # 文字优先；否则先 OCR 取题目文本
        if req.text_input:
            text = req.text_input
        else:
            res = ocr_mod.recognize(None, req.image_base64, mode="qwen",
                                    vision_model=req.vision_model)
            text = res.text
        if not text.strip():
            yield pipe.emit("error", "未能识别出题目内容")
            yield pipe.emit("complete", None)
            return
        p = pipe.SolveParams({"engine": req.engine, "model": req.model, "style": req.style,
                              "dialect": req.dialect, "grade": req.grade})
        yield from pipe.extend_stream(text, p)

    return sse(gen())


# ---------- 批注 ----------

@app.post("/annotate")
def annotate(req: AnnotateReq):
    out = req.output_path or str(ANNOTATE_DIR / "latest.png")
    # 先落盘：PaddleOCR 本地通道与 PIL 都需要文件路径
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(base64.b64decode(req.image_base64))
        tmp_path = tf.name
    try:
        return ann_mod.annotate(tmp_path, req.image_base64, req.mode,
                                req.vision_model, out, req.base_url)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------- 动画 ----------

@app.post("/animation")
def animation(req: AnimateReq):
    out_dir = req.output_dir or str(ANIM_DIR)
    try:
        return anim_mod.generate(req.text, req.solution, req.engine, req.theme,
                                 Path(out_dir), req.base_url)
    except Exception as e:  # noqa: BLE001
        return err(e, 502)


# ---------- 关键帧 ----------

@app.post("/keyframes")
def keyframes(req: KeyframeReq):
    out_dir = req.output_dir or str(HIST_DIR)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(base64.b64decode(req.video_base64))
        tmp_path = tf.name
    try:
        frames, error = kf_mod.extract(tmp_path, out_dir)
        return {"frames": frames, "error": error}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------- LaTeX ----------

@app.post("/latex/render")
def latex_render(req: LatexReq):
    from .latex import renderer

    def fix(code: str, error: str) -> str:
        prompt = (
            "这段 LaTeX/TikZ 代码编译失败，错误信息如下：\n"
            f"{error[:600]}\n\n"
            f"代码：\n```latex\n{code}\n```\n\n"
            "请修正代码，只输出修正后的完整代码，不要解释。"
            "常见问题：包未加载、命令名拼错、坐标越界、TikZ 库未 require。"
        )
        try:
            return engine.client().complete(
                [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=3000
            )
        except Exception as e:  # noqa: BLE001
            log.warning("AI 修 LaTeX 失败: %s", e)
            return ""

    md = renderer.render_markdown(
        req.markdown, Path(req.output_dir), req.base_url,
        engine_name=req.engine, fix_fn=fix, max_retries=2,
    )
    return {"markdown": md}


# ---------- 报告 ----------

@app.post("/report/mistakes")
def report_mistakes(req: MistakesReq):
    if e := _require_key():
        return e
    try:
        return {"text": pipe.report_mistakes(req.stats, req.limit)}
    except Exception as e:  # noqa: BLE001
        return err(e, 502)


@app.post("/report/ai")
def report_ai(req: ReportReq):
    if e := _require_key():
        return e
    try:
        text = pipe.report_ai(req.stats, req.subject, req.grade, req.theme,
                              req.engine, req.model, req.days)
        return {"report": text}
    except Exception as e:  # noqa: BLE001
        return err(e, 502)


# ---------- 番茄钟 ----------

@app.post("/pomodoro/recommend")
def pomodoro(req: PomodoroReq):
    try:
        return pipe.pomodoro_recommend(req.ocr_text, req.summary, req.engine)
    except Exception as e:  # noqa: BLE001
        return err(e, 502)


# ---------- 导出 ----------

@app.post("/export")
def export(req: ExportReq):
    try:
        return exp_mod.export(req.title, req.content, req.format, req.host, EXPORT_DIR)
    except Exception as e:  # noqa: BLE001
        return err(e, 502)