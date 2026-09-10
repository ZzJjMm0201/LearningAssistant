import json
import uuid
import os
import sys as _sys
# Windows GBK 控制台无法打印 ⁻₂ 等Unicode字符会崩线程 → 强制UTF-8+替换
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import asyncio

from server.config import HISTORY_DIR, REPORT_DIR, FEATURE_FLAGS
from server.database.models import init_database, SubmissionRecord, TrackingRecord, ConversationHistory, User
from server.services.ai_service import ai_service
from server.services.search_service import search_service
from server.services.report_generator import ReportGenerator
from server.utils.latex_processor import process_latex_blocks, clean_markdown_for_display
from server.utils.export_service import export_pdf, export_word

# ==================== 日志系统（文件轮转 + 控制台） ====================
import logging as _logging
from logging.handlers import RotatingFileHandler

def _setup_logging():
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    _fmt = _logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _file_h = RotatingFileHandler(log_dir / "server.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    _file_h.setFormatter(_fmt)
    _console_h = _logging.StreamHandler()
    _console_h.setFormatter(_fmt)
    _root = _logging.getLogger()
    _root.setLevel(_logging.INFO)
    _root.handlers = [_file_h, _console_h]
    # uvicorn 自身日志也写入同一文件（access日志由中间件替代，避免重复）
    _logging.getLogger("uvicorn").propagate = True
    _logging.getLogger("uvicorn.error").propagate = True
    _logging.getLogger("uvicorn.access").disabled = True
    _logging.getLogger("app").setLevel(_logging.INFO)
    print("[日志] 已初始化: logs/server.log (轮转5MB×5)")

_setup_logging()
from server.utils.tikz_md_renderer import get_available_latex_engines
from server.utils.image_utils import save_uploaded_image
from server.services.discovery_service import DiscoveryService
from server.services.solve_pipeline import solve_pipeline
from server.services.animation_service import generate_animation
from server.services.ocr_service import ocr_service
from server.services.auth_service import register, login, get_user_by_token, verify_token
from server.services.permission_service import can_use_ai, RESTRICTED_MSG

# ==================== MiKTeX 环境设置 ====================

import shutil
# 优先使用系统级MiKTeX，后退到用户级MiKTeX
_MIKTEX_BIN = next(
    (p for p in [
        r'C:\Program Files\MiKTeX\miktex\bin\x64',
        r'C:\Program Files (x86)\MiKTeX\miktex\bin',
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'MiKTeX', 'miktex', 'bin', 'x64'),
    ] if os.path.isdir(p)),
    None
)
if os.path.isdir(_MIKTEX_BIN):
    os.environ['PATH'] = _MIKTEX_BIN + os.pathsep + os.environ.get('PATH', '')
    print(f"[启动] MiKTeX 已加入PATH: {_MIKTEX_BIN}")
    # 确认LaTeX引擎可用
    engines = get_available_latex_engines()
    if engines:
        print(f"[启动] 可用LaTeX引擎: {engines}")
    else:
        print("[启动] 警告: 未找到LaTeX引擎")
else:
    print("[启动] MiKTeX 未安装（LaTeX图形渲染不可用）")

HISTORY_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)
(HISTORY_DIR / "animations").mkdir(parents=True, exist_ok=True)

# ==================== 服务生命周期 ====================

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    """服务生命周期：启动时预加载OCR模型"""
    # startup
    import threading
    # 本地 PaddleOCR 内存占用大，仅在本机OCR启用时才预加载（避免空载 OOM/卡顿）
    if FEATURE_FLAGS.get("enable_local_ocr", False):
        thread = threading.Thread(target=ocr_service.preload_local_model, daemon=True)
        thread.start()
        print("[启动] OCR模型预加载已触发（后台异步加载中...）")
    else:
        print("[启动] 本地OCR未启用，跳过 PaddleOCR 模型预加载")
    yield
    # shutdown
    print("[关闭] 服务关闭")

# 创建FastAPI应用
app = FastAPI(title="学习助手API", version="2.0.0", lifespan=lifespan)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 请求日志中间件（⑧）
@app.middleware("http")
async def _log_requests(request: Request, call_next):
    import time as _t
    _start = _t.time()
    try:
        response = await call_next(request)
    except Exception:
        _logging.getLogger("app").exception("%s %s 异常", request.method, request.url.path)
        raise
    _dur = (_t.time() - _start) * 1000
    _logging.getLogger("app").info(
        "%s %s -> %d (%.0fms)", request.method, request.url.path, response.status_code, _dur)
    return response

# 初始化数据库
SessionLocal = init_database()

# ==================== 数据模型 ====================

class AskRequest(BaseModel):
    session_id: str
    question: str
    context: Optional[list] = None

class ReportRequest(BaseModel):
    days: int = 30
    theme: str = "dark"  # dark / light（② 数据报告深浅色）

class ExportRequest(BaseModel):
    title: str = "导出"
    content: str = ""
    format: str = "pdf"  # pdf / word

class PomodoroRecommendRequest(BaseModel):
    ocr_text: str = ""
    summary: str = ""  # 学情摘要（可选）
    image_base64: str = ""  # 题目图片（可选，视觉模型直接看图推荐）


class TrackingData(BaseModel):
    session_id: str
    focus_state: str
    duration_seconds: float
    page_number: int = 0
    pomodoro_count: int = 0


class AuthRequest(BaseModel):
    username: str
    password: str


class TokenRequest(BaseModel):
    token: str

# ==================== API端点 ====================

@app.get("/")
async def root():
    return {"status": "ok", "version": "2.0.0"}

# Feature 7: 处理favicon.ico请求避免404日志
@app.get("/favicon.ico")
async def favicon():
    # 返回204 No Content以避免404日志，且不影响功能
    from fastapi.responses import Response
    return Response(status_code=204)

def get_engine(request: Request) -> str:
    """AI提供方（X-Engine头：deepseek/qwen/doubao/hunyuan），默认deepseek"""
    engine = (request.headers.get("X-Engine") or "deepseek").lower()
    return engine if engine in ("deepseek", "qwen", "doubao", "hunyuan") else "deepseek"


def get_llm_model(request: Request) -> Optional[str]:
    """大语言模型名（X-LLM-Model头），未指定用提供方默认"""
    m = (request.headers.get("X-LLM-Model") or "").strip()
    return m or None


def get_ocr_mode(request: Request) -> str:
    """OCR模式（X-OCR-Mode头：paddle/qwen），默认paddle"""
    mode = (request.headers.get("X-OCR-Mode") or "paddle").lower()
    return mode if mode in ("paddle", "qwen") else "paddle"


def get_vision_model(request: Request) -> Optional[str]:
    """视觉/OCR模型名（X-Vision-Model头）"""
    m = (request.headers.get("X-Vision-Model") or "").strip()
    return m or None


def get_answer_style(request: Request) -> Optional[str]:
    """② 回答风格（X-Style头：formal/plain/concise/lively），未指定用默认"""
    s = (request.headers.get("X-Style") or "").strip().lower()
    return s if s in ("formal", "plain", "concise", "lively", "dialect") else None


def get_personality(request: Request) -> Optional[str]:
    """⑨ 人格（X-Personality头：MBTI类型如INTJ，或auto）"""
    import urllib.parse
    v = (request.headers.get("X-Personality") or "").strip()
    return urllib.parse.unquote(v) or None


def get_detail(request: Request) -> Optional[str]:
    """⑨ 详细度（X-Detail头：very_detailed/detailed/brief/auto）"""
    v = (request.headers.get("X-Detail") or "").strip().lower()
    return v if v in ("very_detailed", "detailed", "brief", "auto") else None


def get_subject(request: Request) -> str:
    """⑨ 学科（X-Subject头，用于personality=auto时推荐老师人格）"""
    import urllib.parse
    v = (request.headers.get("X-Subject") or "").strip()
    return urllib.parse.unquote(v)


def get_dialect(request: Request) -> str:
    """③ 方言名称（X-Dialect头，style=dialect 时生效；客户端URL编码，这里解码）"""
    import urllib.parse
    v = (request.headers.get("X-Dialect") or "").strip()
    v = urllib.parse.unquote(v)
    return v or "普通话"


def get_grade(request: Request) -> str:
    """④ 年级（X-Grade头，如 高中；客户端URL编码，这里解码）"""
    import urllib.parse
    v = (request.headers.get("X-Grade") or "").strip()
    return urllib.parse.unquote(v)


def get_thinking_enabled(request: Request):
    """十一 思考模式（X-Thinking头：1/true/on 开启；auto 自动按难度；仅DeepSeek链路生效）
    返回 True / False / "auto"""
    v = (request.headers.get("X-Thinking") or "").strip().lower()
    if v == "auto":
        return "auto"
    return v in ("1", "true", "yes", "on")


def get_search_enabled(request: Request) -> bool:
    """④ 搜题开关（X-Search-Enabled头：0/false/off 关闭；默认开启）"""
    v = (request.headers.get("X-Search-Enabled") or "").strip().lower()
    return v not in ("0", "false", "off", "no")


def get_weak_count(user_id: Optional[int]) -> int:
    """⑨ 薄弱知识点数（mastery_records 中未完全掌握的数量，用于 detail=auto 时决定详细度）"""
    try:
        mastery_dir = HISTORY_DIR / "mastery_records"
        if not mastery_dir.exists():
            return 0
        weak = 0
        for f in mastery_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("user_id") == user_id and data.get("mastery_level") in ("not_mastered", "partially_mastered"):
                    weak += 1
            except Exception:
                continue
        return weak
    except Exception:
        return 0

@app.post("/solve")
async def solve_problem(request: Request, file: UploadFile = File(...)):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    request_id = str(uuid.uuid4())
    
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    content = await file.read()
    # 最长边压缩至1000像素以下，便于后续OCR识别
    save_uploaded_image(content, image_path)
    
    # 用同一个 request_id 启动流程；传入请求Host用于构造LaTeX图片URL，user_id用于数据隔离
    _user_id = get_current_user(request)
    solve_pipeline.start_solve(
        image_path=image_path,
        session_id=request_id,  # ← 传入相同ID
        base_host=request.headers.get("host") or None,
        user_id=_user_id,
        engine=get_engine(request),
        ocr_mode=get_ocr_mode(request),
        vision_model=get_vision_model(request),
        model=get_llm_model(request),
        style=get_answer_style(request),
        thinking=get_thinking_enabled(request),
        search_enabled=get_search_enabled(request),
        dialect=get_dialect(request),
        grade=get_grade(request),
        personality=get_personality(request),
        subject=get_subject(request),
        detail=get_detail(request),
        weak_count=get_weak_count(_user_id),
    )
    
    return {
        "request_id": request_id,  # ← 返回相同ID
        "status": "processing",
        "message": "解题已启动"
    }


class SolveTextRequest(BaseModel):
    text: str


@app.post("/solve/text")
async def solve_text(request: Request, body: SolveTextRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """⑧ 文字输入解题：跳过OCR与分题，直接解题"""
    request_id = str(uuid.uuid4())
    text = (body.text or "").strip()
    if not text:
        return {"status": "error", "message": "题目文本不能为空"}
    _user_id = get_current_user(request)
    # 用占位图片路径（无需真实图片）
    image_path = HISTORY_DIR / f"{request_id}.txt.jpg"
    solve_pipeline.start_solve(
        image_path=image_path,
        session_id=request_id,
        base_host=request.headers.get("host") or None,
        user_id=_user_id,
        engine=get_engine(request),
        ocr_mode=get_ocr_mode(request),
        vision_model=get_vision_model(request),
        model=get_llm_model(request),
        style=get_answer_style(request),
        thinking=get_thinking_enabled(request),
        search_enabled=get_search_enabled(request),
        dialect=get_dialect(request),
        grade=get_grade(request),
        personality=get_personality(request),
        subject=get_subject(request),
        detail=get_detail(request),
        weak_count=get_weak_count(_user_id),
        text_input=text,
    )
    return {
        "request_id": request_id,
        "status": "processing",
        "message": "文字解题已启动"
    }

@app.get("/solve/stream/{request_id}")
async def solve_stream(request_id: str):
    """SSE流式推送解题结果（异步轮询，不阻塞事件循环）"""
    
    async def event_stream():
        queue = solve_pipeline.get_queue(request_id)
        if queue is None:
            yield f"data: {json.dumps({'stage': 'error', 'content': '无效的request_id'}, ensure_ascii=False)}\n\n"
            return
        while True:
            if queue:
                event = queue.popleft()
                # 将事件转换为SSE格式
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                
                # 如果事件中包含大量文本，让出控制权
                if event.get("stage") in ("solution", "mindmap", "solution_chunk"):
                    await asyncio.sleep(0.01)
                
                if event.get("stage") == "complete":
                    break
            else:
                await asyncio.sleep(0.05)
        
        # 发送完成事件
        yield "data: {\"stage\": \"complete\", \"content\": \"解题完成\"}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用nginx缓冲
        }
    )

@app.post("/solve/confirm/{request_id}")
async def confirm_solve(request_id: str):
    """用户确认OCR结果后继续解题流程"""
    if solve_pipeline.confirm_continue(request_id):
        return {"status": "ok", "message": "已确认，继续处理"}
    return {"status": "error", "message": "request_id无效或已过期"}

@app.post("/solve/select_questions/{request_id}")
async def select_questions(request_id: str, body: dict):
    """① 多题：用户选定要解的题目索引后继续"""
    indices = body.get("indices", []) if isinstance(body, dict) else []
    if solve_pipeline.select_questions(request_id, indices):
        return {"status": "ok", "message": "已确认选题"}
    return {"status": "error", "message": "request_id无效或已超时"}

@app.post("/solve/cancel/{request_id}")
async def cancel_solve(request_id: str):
    """用户取消解题流程"""
    solve_pipeline.cancel_solve(request_id)
    return {"status": "ok", "message": "已取消"}

@app.get("/static/svgs/{rest_of_path:path}")
async def get_svg(rest_of_path: str):
    """获取SVG文件"""
    file_path = HISTORY_DIR / rest_of_path
    if file_path.exists():
        return FileResponse(file_path)
    return {"detail": "Not Found"}

@app.post("/ask")
async def ask_question(request: AskRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """多轮对话 - 继续提问（从数据库加载该会话的历史上下文）"""
    db = SessionLocal()
    try:
        from server.database.models import ConversationHistory as CH
        # 从数据库读取该会话的历史消息作为上下文
        history = (
            db.query(CH)
            .filter(CH.session_id == request.session_id)
            .order_by(CH.id.asc())
            .all()
        )
        messages: list = clean_conversation_history(
            [{"role": h.role, "content": h.content} for h in history]
        )

        response = ai_service.continue_conversation(messages, request.question, engine=get_engine(request), model=get_llm_model(request), style=get_answer_style(request), dialect=get_dialect(request), grade=get_grade(request))

        # 保存本次问答到数据库，保证后续追问上下文连续
        for role, content in (("user", request.question), ("assistant", response)):
            conv = CH(session_id=request.session_id, role=role, content=content)
            db.add(conv)
        db.commit()

        return {"answer": response, "session_id": request.session_id}
    finally:
        db.close()

@app.post("/ask/stream")
async def ask_question_stream(request: Request, body: AskRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        raise HTTPException(status_code=403, detail=_perm_err.get("message", "AI使用受限"))

    """多轮对话 - 继续提问（SSE流式，回答与AI解题同样式）"""
    db = SessionLocal()
    try:
        from server.database.models import ConversationHistory as CH
        history = (
            db.query(CH)
            .filter(CH.session_id == body.session_id)
            .order_by(CH.id.asc())
            .all()
        )
        messages: list = clean_conversation_history(
            [{"role": h.role, "content": h.content} for h in history]
        )
        engine = get_engine(request)
        llm_model = get_llm_model(request)
        style = get_answer_style(request)
        dialect = get_dialect(request)
        grade = get_grade(request)

        async def event_stream():
            accumulated = ""
            prev_sent = ""
            for chunk in ai_service.continue_conversation_stream(messages, body.question, engine=engine, model=llm_model, style=style, dialect=dialect, grade=grade):
                accumulated = chunk
                # AI流式回调返回“累积到当前”的全文：只下发新增部分，客户端累加后不会重复
                delta = chunk[len(prev_sent):] if chunk.startswith(prev_sent) else chunk
                prev_sent = chunk
                if delta:
                    yield f"data: {json.dumps({'stage': 'answer_chunk', 'content': delta}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.01)
            
            # ① 追问若包含LaTeX图形代码块 → 本地渲染为图片并下发渲染结果（含“（*图形渲染失败*）”提示）
            final_answer = accumulated
            if "```" in final_answer:
                try:
                    from server.utils.latex_processor import process_latex_blocks_with_retry
                    svg_dir = HISTORY_DIR / f"svgs_ask_{body.session_id[:16]}"
                    svg_dir.mkdir(parents=True, exist_ok=True)
                    rendered = process_latex_blocks_with_retry(
                        final_answer, svg_dir, ai_service=ai_service,
                        engine=engine, model=llm_model
                    )
                    rendered = rewrite_rendered_images(rendered, get_request_host(request), f"svgs_ask_{body.session_id[:16]}")
                    if rendered != final_answer:
                        final_answer = rendered
                        yield f"data: {json.dumps({'stage': 'answer_rendered', 'content': final_answer}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.01)
                except Exception as e:
                    print(f"[ask/stream] 追问LaTeX渲染失败: {e}")
            
            # 保存本次问答到数据库（流结束、response返回后执行，需新开会话）
            try:
                db2 = SessionLocal()
                try:
                    for role, content in (("user", body.question), ("assistant", final_answer)):
                        conv = CH(session_id=body.session_id, role=role, content=content)
                        db2.add(conv)
                    db2.commit()
                finally:
                    db2.close()
            except Exception as e:
                print(f"[ask/stream] 保存对话失败: {e}")
            yield f"data: {json.dumps({'stage': 'complete', 'content': ''}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    finally:
        db.close()

@app.post("/animation")
async def create_animation(request: Request, file: UploadFile = File(...)):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """生成AI动画"""
    request_id = str(uuid.uuid4())
    # 保存图片（最长边压缩至1000像素以下，便于后续OCR）
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    content = await file.read()
    save_uploaded_image(content, image_path)
    # OCR 识别
    ocr_text, _, _ = ocr_service.recognize(str(image_path))
    if not ocr_text or ocr_text.startswith("OCR"):
        return {"status": "error", "message": "OCR识别失败"}
    
    # 生成动画
    html_path = generate_animation(ocr_text, engine=get_engine(request))
    if html_path:
        # 返回动画文件的 URL
        filename = Path(html_path).name
        url = f"/static/animations/{filename}"
        # ③ 保存 AI 动画记录到历史
        try:
            from server.database.models import AuxRecord
            db2 = SessionLocal()
            try:
                rec = AuxRecord(
                    session_id=request_id,
                    user_id=get_current_user(request),
                    record_type="animation",
                    title="AI动画",
                    content=url,
                    extra_json={"ocr_text": ocr_text[:200]},
                )
                db2.add(rec)
                db2.commit()
            finally:
                db2.close()
        except Exception as e:
            print(f"[动画] 保存历史记录失败: {e}")
        return {
            "status": "ok",
            "url": url,
            "request_id": request_id
        }
    else:
        return {"status": "error", "message": "动画生成失败"}



@app.post("/animation/text")
async def create_animation_text(request: Request, body: SolveTextRequest):
    """⑧ 文字输入直接生成AI动画（跳过OCR，复用动画生成）"""
    # ⑮ 账号权限
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err
    text = (body.text or "").strip()
    if not text:
        return {"status": "error", "message": "题目文本不能为空"}
    request_id = str(uuid.uuid4())
    html_path = generate_animation(text, engine=get_engine(request))
    if html_path:
        filename = Path(html_path).name
        url = f"/static/animations/{filename}"
        try:
            from server.database.models import AuxRecord
            db2 = SessionLocal()
            try:
                rec = AuxRecord(
                    session_id=request_id,
                    user_id=get_current_user(request),
                    record_type="animation",
                    title="AI动画",
                    content=url,
                    extra_json={"ocr_text": text[:200]},
                )
                db2.add(rec)
                db2.commit()
            finally:
                db2.close()
        except Exception as e:
            print(f"[动画-文本] 保存历史记录失败: {e}")
        return {"status": "ok", "url": url, "request_id": request_id}
    else:
        return {"status": "error", "message": "动画生成失败"}

@app.get("/static/animations/{filename}")
async def get_animation(filename: str):
    """获取动画文件"""
    file_path = HISTORY_DIR / "animations" / filename
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")
    return {"detail": "Not Found"}

@app.post("/report/data")
async def data_report(request: Request, body: ReportRequest):
    """生成数据版学情报告（按用户隔离）"""
    db = SessionLocal()
    try:
        generator = ReportGenerator(db)
        theme = "light" if (body.theme or "").lower() == "light" else "dark"
        html_path = generator.generate_data_report_html(body.days, user_id=get_current_user(request), theme=theme)
        
        if html_path:
            filename = Path(html_path).name
            return {
                "status": "ok",
                "url": f"/static/reports/{filename}"
            }
        else:
            return {"status": "error", "message": "没有足够的数据生成报告"}
    finally:
        db.close()

@app.post("/report/ai")
async def ai_report(request: Request, body: ReportRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """生成AI版学情报告（非流式，兼容旧客户端）；⑦ 规定时段题目 <5 题则不生成"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        from datetime import timedelta
        user_id = get_current_user(request)
        q = db.query(SubmissionRecord)
        if body.days and body.days > 0:
            q = q.filter(SubmissionRecord.timestamp >= datetime.utcnow() - timedelta(days=body.days))
        if user_id is not None:
            q = q.filter(or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None)))
        else:
            q = q.filter(SubmissionRecord.user_id.is_(None))
        record_count = q.count()
        if record_count < 5:
            return {"status": "error", "message": f"当前时段仅 {record_count} 道题，积累题目数量太少，暂不生成报告（至少需 5 题）"}

        generator = ReportGenerator(db)
        summary = generator.get_report_summary(body.days, user_id=user_id)
        
        if summary == "暂无学习记录":
            return {"status": "error", "message": "暂无学习记录"}
        
        ai_report_text = ai_service.generate_ai_report(summary, engine=get_engine(request), model=get_llm_model(request), style=get_answer_style(request), dialect=get_dialect(request), grade=get_grade(request))
        
        return {
            "status": "ok",
            "report": ai_report_text,
            "summary": summary
        }
    finally:
        db.close()

@app.post("/report/ai/stream")
async def ai_report_stream(request: Request, body: ReportRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        raise HTTPException(status_code=403, detail=_perm_err.get("message", "AI使用受限"))

    """生成AI版学情报告（SSE流式）；⑦ 规定时段题目 <5 题则不生成"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        from datetime import timedelta
        user_id = get_current_user(request)
        q = db.query(SubmissionRecord)
        if body.days and body.days > 0:
            q = q.filter(SubmissionRecord.timestamp >= datetime.utcnow() - timedelta(days=body.days))
        if user_id is not None:
            q = q.filter(or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None)))
        else:
            q = q.filter(SubmissionRecord.user_id.is_(None))
        record_count = q.count()
        if record_count < 5:
            return {"status": "error", "message": f"当前时段仅 {record_count} 道题，积累题目数量太少，暂不生成报告（至少需 5 题）"}

        generator = ReportGenerator(db)
        summary = await asyncio.to_thread(generator.get_report_summary, body.days, user_id=user_id)
        
        if summary == "暂无学习记录":
            return {"status": "error", "message": "暂无学习记录"}
        
        async def event_stream():
            # 先发摘要供客户端展示
            yield f"data: {json.dumps({'stage': 'summary', 'content': summary}, ensure_ascii=False)}\n\n"
            # 流式生成报告正文
            for chunk in ai_service.generate_ai_report_stream(summary, engine=get_engine(request), model=get_llm_model(request), style=get_answer_style(request), dialect=get_dialect(request), grade=get_grade(request)):
                yield f"data: {json.dumps({'stage': 'report_chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)
            yield f"data: {json.dumps({'stage': 'complete', 'content': ''}, ensure_ascii=False)}\n\n"
        
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    finally:
        db.close()

@app.get("/static/reports/{filename}")
async def get_report(filename: str):
    """获取报告文件"""
    file_path = REPORT_DIR / filename
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")
    return {"detail": "Not Found"}

@app.post("/export")
async def export_content(request: Request, body: ExportRequest):
    """④ 导出 PDF / Word（服务端生成，公式转纯文本；返回文件URL供客户端下载）"""
    import uuid as _uuid
    export_dir = REPORT_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    fmt = (body.format or "pdf").lower()
    if fmt not in ("pdf", "word"):
        fmt = "pdf"
    ext = "pdf" if fmt == "pdf" else "docx"
    fname = f"export_{_uuid.uuid4().hex[:10]}.{ext}"
    out_path = export_dir / fname
    import asyncio
    _host = get_request_host(request)
    # 导出含 pandoc 子进程/图片下载等阻塞操作，放到线程避免卡死事件循环
    ok = await asyncio.to_thread(
        export_pdf if fmt == "pdf" else export_word,
        body.title, body.content, out_path, _host
    )
    if not ok:
        return {"status": "error", "message": "导出失败"}
    return {"status": "ok", "url": f"/static/exports/{fname}", "filename": fname}

@app.get("/static/exports/{filename}")
async def get_export_file(filename: str):
    """获取导出的 PDF/Word 文件"""
    file_path = REPORT_DIR / "exports" / filename
    if not file_path.exists():
        return {"detail": "Not Found"}
    mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if filename.endswith(".docx") else "application/pdf"
    return FileResponse(file_path, media_type=mt, filename=filename)

@app.post("/pomodoro/recommend")
async def pomodoro_recommend(request: Request, body: PomodoroRecommendRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """番茄钟 AI 推荐做题时长：基于题目图片/文字+学情，返回 {duration_minutes, reason}"""
    import re as _re
    try:
        ocr_text = body.ocr_text
        # 优先级：有图片 base64 → 用视觉模型直接看题描述
        if body.image_base64:
            # E: 与“AI解答”一致，用设置面板所选 OCR 模型(X-OCR-Mode / X-Vision-Model)
            # 而非写死千问视觉，避免误用慢模型导致 30s+ 等待
            import base64 as _b64
            import tempfile
            try:
                img_bytes = _b64.b64decode(body.image_base64)
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    tf.write(img_bytes)
                    tmp_path = tf.name
                desc, _, _ = ocr_service.recognize(
                    tmp_path, mode=get_ocr_mode(request), vision_model=get_vision_model(request))
                if desc and not desc.startswith("OCR"):
                    ocr_text = desc
                else:
                    print("[Pomodoro] 图片OCR为空/失败，改用传入文本")
            except Exception as e:
                print(f"[Pomodoro] 图片识别失败，回退文字: {e}")
        prompt = (
            "你是学习规划助手。请根据学生当前要做的题目和学情，推荐一个合适的番茄钟专注时长（分钟，取 25/30/35/40/45/50 之一），"
            "并给出简短理由。\n\n"
            f"题目内容：{ocr_text[:800]}\n"
            f"学情摘要：{body.summary[:500] or '暂无学情数据'}\n\n"
            "只输出 JSON：{\"duration_minutes\": 建议时长(整数), \"reason\": \"推荐理由(一句话)\"}"
        )
        resp = ai_service.generate_response(prompt, engine=get_engine(request), model=get_llm_model(request)) or ""
        m = _re.search(r'\{[^{}]*\}', resp, _re.DOTALL)
        data = {}
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = {}
        dur = data.get("duration_minutes", 30)
        if not isinstance(dur, int):
            try:
                dur = int(dur)
            except Exception:
                dur = 30
        dur = min(50, max(25, dur))
        reason = str(data.get("reason", "建议保持常规专注时长"))
        return {"status": "ok", "duration_minutes": dur, "reason": reason}
    except Exception as e:
        print(f"[Pomodoro] 推荐失败: {e}")
        return {"status": "error", "message": f"推荐失败: {e}"}



@app.get("/static/{filename:path}")
async def get_history_image(filename: str):
    """获取历史图片等静态文件"""
    # 检查history目录
    file_path = HISTORY_DIR / filename
    if file_path.exists():
        return FileResponse(file_path)
    return {"detail": "Not Found"}

@app.post("/extend")
async def knowledge_extension(request: Request, file: UploadFile = File(...)):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """知识延伸"""
    request_id = str(uuid.uuid4())
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    # 最长边压缩至1000像素以下，便于后续OCR识别
    save_uploaded_image(await file.read(), image_path)
    
    solve_pipeline.start_knowledge_extension(image_path, request_id, user_id=get_current_user(request), engine=get_engine(request), ocr_mode=get_ocr_mode(request), vision_model=get_vision_model(request), model=get_llm_model(request), style=get_answer_style(request), dialect=get_dialect(request), grade=get_grade(request))
    
    return {"request_id": request_id, "status": "processing"}



@app.post("/extend/text")
async def knowledge_extension_text(request: Request, body: SolveTextRequest):
    """⑧ 文字输入直接知识延伸（跳过OCR）"""
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err
    text = (body.text or "").strip()
    if not text:
        return {"status": "error", "message": "题目文本不能为空"}
    request_id = str(uuid.uuid4())
    image_path = HISTORY_DIR / f"{request_id}.txt.jpg"
    solve_pipeline.start_knowledge_extension(
        image_path, request_id,
        user_id=get_current_user(request),
        engine=get_engine(request),
        ocr_mode=get_ocr_mode(request),
        vision_model=get_vision_model(request),
        model=get_llm_model(request),
        style=get_answer_style(request),
        dialect=get_dialect(request),
        grade=get_grade(request),
        text_input=text,
    )
    return {"request_id": request_id, "status": "processing"}

@app.get("/extend/stream/{request_id}")
async def extend_stream(request_id: str):
    """SSE流式推送知识延伸结果（异步轮询，不阻塞事件循环）"""
    async def event_stream():
        queue = solve_pipeline.get_queue(request_id)
        if queue is None:
            yield f"data: {json.dumps({'stage': 'error', 'content': '无效的request_id'}, ensure_ascii=False)}\n\n"
            return
        while True:
            if queue:
                event = queue.popleft()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("stage") == "complete":
                    break
            else:
                await asyncio.sleep(0.05)
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/history")
async def get_history(request: Request, body: dict):
    """获取历史记录（按用户隔离；返回完整内容，清除图片链接）"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from sqlalchemy import or_
        from datetime import datetime, time as dtime, timedelta
        import re
        
        start_date = body.get("start_date", "")
        end_date = body.get("end_date", "")
        user_id = get_current_user(request)
        
        # 数据库存UTC时间，客户端传本地时间：过滤时本地→UTC换算，展示时UTC→本地换算
        local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        
        query = db.query(SubmissionRecord)
        
        if start_date:
            start_dt = datetime.fromisoformat(start_date) - local_offset
            query = query.filter(SubmissionRecord.timestamp >= start_dt)
        if end_date:
            end_dt = datetime.fromisoformat(end_date)
            # 只传日期（如"2026-08-26"）时表示包含当天全天，而非当天0点
            if end_dt.time() == dtime(0, 0):
                end_dt = end_dt + timedelta(days=1) - timedelta(microseconds=1)
            query = query.filter(SubmissionRecord.timestamp <= end_dt - local_offset)
        
        # 多用户隔离：登录用户看自己的+公共(NULL)；未登录只能看公共(NULL)
        if user_id is not None:
            query = query.filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            )
        else:
            query = query.filter(SubmissionRecord.user_id.is_(None))
        
        # 全量返回（个人学习记录量级小，不分页截断；避免“部分题目莫名消失”），并在Python侧应用筛选
        all_records = query.order_by(SubmissionRecord.timestamp.desc()).all()
        
        # ③ 筛选参数（客户端“筛选”面板：学科/年级/难度，支持多选：数组或逗号分隔字符串）
        def _parse_filters(val):
            if isinstance(val, list):
                return {str(v).strip() for v in val if str(v).strip()}
            if isinstance(val, str) and val.strip():
                return {v.strip() for v in val.split(",") if v.strip()}
            return set()
        filter_subjects = _parse_filters(body.get("subject"))
        filter_grades = _parse_filters(body.get("grade"))
        filter_difficulties = _parse_filters(body.get("difficulty"))
        # ⑤ 掌握程度筛选（值：完全掌握/部分掌握/完全没掌握/未记录）
        filter_masteries = _parse_filters(body.get("mastery"))
        
        result = []
        all_subjects = set()
        # 学科统计基于筛选后的全量记录
        for r in all_records:
            subj = ""
            qi = r.question_info
            if isinstance(qi, dict):
                subj = qi.get("subject", "")
            if not subj and r.ocr_text:
                extracted = extract_subjects(cleanup_markdown_images(r.ocr_text))
                if extracted and extracted[0] != "其他":
                    subj = extracted[0]
            if subj:
                all_subjects.add(subj)
        
        # 图片URL统一重写到当前请求Host（LaTeX图/原图均指向当前服务地址）
        host = get_request_host(request)
        
        for r in all_records:
            # 清理OCR文本中的图片链接（仅OCR文本清理）
            clean_ocr = cleanup_markdown_images(r.ocr_text) if r.ocr_text else ""
            clean_steps = cleanup_markdown_images(r.solution_steps) if r.solution_steps else ""
            # 完整解析保留LaTeX图片标记，仅把图片URL重写到当前服务地址
            clean_solution = rewrite_static_urls(r.full_solution, host) if r.full_solution else ""
            
            # 提取年级学科信息
            grade = ""
            subject = ""
            difficulty = ""
            knowledge_points = []
            question_info = r.question_info
            if isinstance(question_info, dict):
                grade = question_info.get("grade", "")
                subject = question_info.get("subject", "")
                difficulty = question_info.get("difficulty", "")
                knowledge_points = question_info.get("knowledge_points", [])
            
            # Feature 18: 如果question_info中没有学科，尝试从OCR文本提取
            if not subject and clean_ocr:
                extracted = extract_subjects(clean_ocr)
                if extracted and extracted[0] != "其他":
                    subject = extracted[0]
            
            # ③ 应用学科/年级/难度多选筛选
            if filter_subjects and subject not in filter_subjects:
                continue
            if filter_grades and grade not in filter_grades:
                continue
            if filter_difficulties and difficulty not in filter_difficulties:
                continue
            
            if subject:
                all_subjects.add(subject)
            
            # 原图URL；显示时间优先使用原图文件的修改时间（即文件“属性”里的日期，本地时区）
            image_url = ""
            display_ts = ""
            if r.original_image_path:
                image_path = Path(r.original_image_path)
                if image_path.exists():
                    image_url = f"/static/{image_path.name}"
                    display_ts = datetime.fromtimestamp(image_path.stat().st_mtime).isoformat()
            if not display_ts and r.timestamp is not None:
                display_ts = (r.timestamp + local_offset).isoformat()
            
            # 掌握程度（读取 mastery_records 下按 session_id 保存的选项）
            mastery_label = ""
            mf = HISTORY_DIR / "mastery_records" / f"{r.session_id}.json"
            if mf.exists():
                try:
                    mj = json.loads(mf.read_text(encoding="utf-8"))
                    mastery_label = _MASTERY_LABELS.get(mj.get("mastery_level", ""), "")
                except Exception:
                    pass
            
            # ⑤ 应用掌握程度多选筛选（“未记录”匹配没有掌握程度文件的记录）
            if filter_masteries:
                if mastery_label:
                    if mastery_label not in filter_masteries:
                        continue
                else:
                    if "未记录" not in filter_masteries:
                        continue
            
            result.append({
                "id": r.id,
                "session_id": r.session_id or "",
                "timestamp": display_ts,
                "record_type": "solve",
                "ocr_text": clean_ocr,
                "question_info_raw": json.dumps(question_info, ensure_ascii=False) if isinstance(question_info, dict) else str(question_info),
                "grade": grade,
                "subject": subject,
                "difficulty": difficulty,
                "knowledge_points": knowledge_points,
                "solution_steps": clean_steps,
                "full_solution": clean_solution,
                "image_url": image_url,
                "mastery_level": mastery_label,
            })
        
        # ③ 合并知识延伸 / AI动画记录到历史列表
        from server.database.models import AuxRecord
        aux_query = db.query(AuxRecord)
        if user_id is not None:
            aux_query = aux_query.filter(or_(AuxRecord.user_id == user_id, AuxRecord.user_id.is_(None)))
        else:
            aux_query = aux_query.filter(AuxRecord.user_id.is_(None))
        for a in aux_query.order_by(AuxRecord.timestamp.desc()).all():
            try:
                a_ts = (a.timestamp + local_offset).isoformat() if a.timestamp else ""
            except Exception:
                a_ts = ""
            extra = a.extra_json or {}
            result.append({
                "id": a.id,
                "session_id": a.session_id or "",
                "timestamp": a_ts,
                "record_type": a.record_type,  # extension / animation
                "ocr_text": "",
                "question_info_raw": "",
                "grade": "",
                "subject": (extra.get("subject") or "") if isinstance(extra, dict) else "",
                "difficulty": "",
                "knowledge_points": [],
                "solution_steps": "",
                "full_solution": a.content or "",
                "image_url": "",
                "mastery_level": "",
                "title": a.title or "",
            })
        
        # 按时间降序排序（solve + aux 合并后）
        def _ts_key(r):
            try:
                return r["timestamp"]
            except Exception:
                return ""
        result.sort(key=_ts_key, reverse=True)
        
        return {
            "status": "ok",
            "records": result,
            "total_count": len(result),
            "subject_count": len(all_subjects)  # Feature 18: 学科数
        }
    finally:
        db.close()

@app.delete("/history")
async def clear_history(request: Request):
    """清除历史记录（仅本人记录；未登录时仅清空无主数据）；文件移入回收站而非硬删"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from sqlalchemy import or_
        user_id = get_current_user(request)
        if user_id is not None:
            records = db.query(SubmissionRecord).filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            ).all()
        else:
            records = db.query(SubmissionRecord).filter(SubmissionRecord.user_id.is_(None)).all()
        for rec in records:
            _move_record_files_to_recycle_bin(rec)
            db.delete(rec)
        # ③ 同时清除知识延伸/AI动画记录
        from server.database.models import AuxRecord
        aux_list = db.query(AuxRecord).filter(
            AuxRecord.user_id == user_id if user_id is not None else AuxRecord.user_id.is_(None)
        ).all()
        for a in aux_list:
            db.delete(a)
        db.commit()
        return {"status": "ok", "message": f"历史记录已清除（{len(records)} 条，文件已移入回收站）"}
    finally:
        db.close()

@app.delete("/history/{record_id}")
async def delete_history_record(record_id: int, request: Request):
    """删除单条历史记录（校验归属）；文件移入回收站"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        user_id = get_current_user(request)
        record = db.query(SubmissionRecord).filter(SubmissionRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")
        # 未登录只能删公共(NULL)记录；登录用户只能删自己的记录
        if record.user_id is not None and (user_id is None or record.user_id != user_id):
            raise HTTPException(status_code=403, detail="无权删除他人的记录")
        _move_record_files_to_recycle_bin(record)
        db.delete(record)
        db.commit()
        return {"status": "ok", "message": f"记录 {record_id} 已删除（文件已移入回收站）"}
    finally:
        db.close()

@app.post("/history/batch-delete")
async def batch_delete_history(request: Request, body: dict):
    """批量删除历史记录（校验归属）；文件移入回收站"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from sqlalchemy import or_
        ids = body.get("ids", [])
        if not ids:
            return {"status": "error", "message": "未指定要删除的记录ID"}
        user_id = get_current_user(request)
        query = db.query(SubmissionRecord).filter(SubmissionRecord.id.in_(ids))
        if user_id is not None:
            query = query.filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            )
        else:
            query = query.filter(SubmissionRecord.user_id.is_(None))
        records = query.all()
        for rec in records:
            _move_record_files_to_recycle_bin(rec)
            db.delete(rec)
        db.commit()
        return {"status": "ok", "message": f"已删除 {len(records)} 条记录（文件已移入回收站）", "deleted_count": len(records)}
    finally:
        db.close()

@app.post("/history/render/{record_id}")
async def render_history_record(record_id: int, request: Request):
    """把历史记录的完整解析中的LaTeX代码块渲染为图片（本地编译，不调AI），并缓存回数据库"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from server.utils.latex_processor import process_latex_blocks
        record = db.query(SubmissionRecord).filter(SubmissionRecord.id == record_id).first()
        if not record or not record.full_solution:
            return {"status": "error", "message": "记录不存在或无解析内容"}
        if "```" not in record.full_solution:
            return {"status": "ok", "full_solution": record.full_solution, "rendered": False}
        svg_dir = Path(record.rendered_svg_dir) if record.rendered_svg_dir else None
        if not svg_dir or not svg_dir.exists():
            svg_dir = HISTORY_DIR / f"svgs_{record.session_id}"
            svg_dir.mkdir(parents=True, exist_ok=True)
        try:
            rendered = process_latex_blocks(record.full_solution, svg_dir)
        except Exception as e:
            print(f"[history/render] 渲染失败 id={record_id}: {e}")
            return {"status": "ok", "full_solution": record.full_solution, "rendered": False}
        host = get_request_host(request)
        rendered = rewrite_rendered_images(rendered, host, f"svgs_{record.session_id}")
        if rendered != record.full_solution:
            record.full_solution = rendered
            record.rendered_svg_dir = str(svg_dir)
            db.commit()
        return {"status": "ok", "full_solution": rendered, "rendered": True}
    finally:
        db.close()

@app.post("/tracking/sync")
async def sync_tracking_data(request: Request, data: list[TrackingData]):
    """同步跟踪学习数据（记录所属用户）"""
    db = SessionLocal()
    try:
        user_id = get_current_user(request)
        for item in data:
            record = TrackingRecord(
                session_id=item.session_id,
                user_id=user_id,
                focus_state=item.focus_state,
                duration_seconds=item.duration_seconds,
                page_number=item.page_number,
                pomodoro_count=item.pomodoro_count,
            )
            db.add(record)
        db.commit()
        return {"status": "ok", "synced": len(data)}
    finally:
        db.close()

@app.post("/auth/register")
async def auth_register(request: AuthRequest):
    """注册用户"""
    if not request.username or len(request.username.strip()) == 0:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(request.username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少3个字符")
    if len(request.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6个字符")

    db = SessionLocal()
    try:
        result = register(db, request.username.strip(), request.password)
        return {"status": "ok", "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.post("/auth/login")
async def auth_login(request: AuthRequest):
    """用户登录"""
    db = SessionLocal()
    try:
        result = login(db, request.username.strip(), request.password)
        return {"status": "ok", "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.post("/auth/verify")
async def auth_verify(request: TokenRequest):
    """验证token并返回用户信息"""
    db = SessionLocal()
    try:
        user = get_user_by_token(db, request.token)
        if user is None:
            raise HTTPException(status_code=401, detail="Token无效或已过期")
        return {"status": "ok", "user": user}
    finally:
        db.close()


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "features": FEATURE_FLAGS,
    }


# ==================== 用户认证辅助 ====================

def _resolve_user(request: Request):
    """解析 token -> (user_id, username, is_admin)，未登录返回 (None,None,False)"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        try:
            uid = verify_token(token)
            if uid is not None:
                try:
                    db = SessionLocal()
                    try:
                        user = db.query(User).filter(User.id == uid).first()
                        if user:
                            return uid, user.username, bool(user.is_admin)
                    finally:
                        db.close()
                except Exception:
                    pass
                return uid, None, False
        except Exception:
            return None, None, False
    return None, None, False


def _ai_denied_response(username: str, is_admin: bool):
    """返回 (error 字典, None) / (None, allowed_bool) 结构"""
    if can_use_ai(username, is_admin):
        return None, False
    return ({"status": "error", "code": 403,
             "message": RESTRICTED_MSG}, True)


# ⑮ AI类端点：受限则拒（新账号默认False；管理员/白名单放行）
def check_ai_permission(request: Request):
    """受限时返回 {"status":"error","code":403,"message":...}，否则返回 None"""
    _, username, is_admin = _resolve_user(request)
    err, denied = _ai_denied_response(username, is_admin) if username is not None else (None, False)
    if username is None:
        # 未登录仍沿用旧行为（透传受服务端登录保护），不额外拦截
        return None
    return err if denied else None


def get_current_user(request: Request) -> Optional[int]:
    """从 Authorization: Bearer <token> 解析当前用户ID；未登录返回 None"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        try:
            uid = verify_token(token)
            if uid is not None:
                return uid
        except Exception:
            return None
    return None

# ==================== Feature 4: 掌握程度 ====================

class MasteryRequest(BaseModel):
    request_id: str
    mastery_level: str

@app.post("/mastery")
async def save_mastery(request: Request, body: MasteryRequest):
    """保存掌握程度记录（记录所属用户）"""
    mastery_dir = HISTORY_DIR / "mastery_records"
    mastery_dir.mkdir(exist_ok=True)
    
    record = {
        "request_id": body.request_id,
        "mastery_level": body.mastery_level,
        "timestamp": datetime.now().isoformat(),
        "user_id": get_current_user(request),
    }
    
    file_path = mastery_dir / f"{body.request_id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    
    return {"status": "ok", "message": "掌握程度已保存"}

# ==================== Feature 20: GeoGebra ====================

class GeoGebraRequest(BaseModel):
    ocr_text: str = ""
    subject: str = ""

@app.post("/geogebra")
async def generate_geogebra(request: Request, body: GeoGebraRequest):
    # ⑮ 账号权限：受限用户拒绝(因AI资源有限; 管理员/白名单放行)
    _perm_err = check_ai_permission(request)
    if _perm_err is not None:
        return _perm_err

    """Feature 20: 生成GeoGebra图形（AI生成命令 + 官方GeoGebra Applet页面）"""
    try:
        prompt = f"""请根据以下数学题目内容，生成可以在GeoGebra中输入的命令。

题目内容: {body.ocr_text if body.ocr_text else '绘制基本数学图形'}

要求:
1. 每行一个GeoGebra命令
2. 使用标准GeoGebra语法，英文函数名
3. 先定义基础对象(如函数、点、滑块)，再定义依赖对象
4. 如果题目涉及函数，请定义函数并绘制图像
5. 如果涉及几何，请绘制对应的几何图形并标注关键点
6. 不要输出任何解释文字，只输出命令"""
        
        response = ai_service.generate_response(prompt, engine=get_engine(request), model=get_llm_model(request))
        
        # 清理响应，提取纯命令
        import re as _re
        commands_text = response.strip()
        commands_text = _re.sub(r'```[\w]*\n?', '', commands_text)
        commands_text = _re.sub(r'```', '', commands_text)
        
        commands = [line.strip() for line in commands_text.split('\n') 
                   if line.strip() and not line.strip().startswith('//') and not line.strip().startswith('#')]
        
        if not commands:
            return {"status": "error", "message": "AI未生成有效命令"}
        
        # 生成嵌入官方GeoGebra Applet的HTML页面（参考WPS云盘生成器方案）
        import json as _json
        commands_array = _json.dumps(commands)
        ggb_dir = HISTORY_DIR / "geogebra"
        ggb_dir.mkdir(parents=True, exist_ok=True)
        html_name = f"ggb_{uuid.uuid4().hex[:8]}.html"
        html_path = ggb_dir / html_name
        # ⑥ deployggb.js 优先使用本地缓存（实体机/模拟器加载更快），失败回退CDN
        ggb_script = ensure_geogebra_assets()
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
    <title>数学图形</title>
    <script src="{ggb_script}"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ width: 100%; height: 100%; background: #1A1A2E; overflow: hidden; }}
        #ggb-container {{ width: 100%; height: 100%; }}
    </style>
</head>
<body>
    <div id="ggb-container"></div>
    <script>
        const commands = {commands_array};
        const params = {{
            "appName": "classic",
            "width": window.innerWidth,
            "height": window.innerHeight,
            "showToolBar": false,
            "showAlgebraInput": true,
            "showMenuBar": false,
            "showResetIcon": true,
            "enableShiftDragZoom": true,
            "language": "zh",
            "borderColor": "#1A1A2E",
            "bgColor": "#1A1A2E",
            "perspective": "G",
            "useBrowserStorage": false
        }};
        const applet = new GGBApplet(params, true);
        applet.inject('ggb-container', 'preferHTML5');
        // 等待Applet加载完成后逐条执行命令
        let attempts = 0;
        function runCommands() {{
            try {{
                const api = applet.getAppletObject();
                if (api) {{
                    commands.forEach(cmd => {{
                        try {{ api.evalCommand(cmd); }} catch (e2) {{ console.warn('命令执行失败:', cmd, e2); }}
                    }});
                }} else {{
                    throw new Error('not ready');
                }}
            }} catch (e3) {{
                attempts++;
                if (attempts < 60) setTimeout(runCommands, 500);
            }}
        }}
        window.addEventListener('load', function() {{ setTimeout(runCommands, 1500); }});
    </script>
</body>
</html>"""
        html_path.write_text(html_content, encoding='utf-8')
        
        return {
            "status": "ok",
            "url": f"/static/geogebra/{html_name}",
            "commands": commands,
            "raw": commands_text,
        }
    except Exception as e:
        print(f"[GeoGebra] 生成失败: {e}")
        return {"status": "error", "message": f"图形生成失败: {e}"}

@app.get("/static/geogebra/{filename}")
async def get_geogebra_file(filename: str):
    """获取生成的GeoGebra页面"""
    file_path = HISTORY_DIR / "geogebra" / filename
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")
    return {"detail": "Not Found"}

# ==================== 工具函数 ====================

# 掌握程度英文键 → 中文显示
_MASTERY_LABELS = {
    "completely_mastered": "完全掌握",
    "partially_mastered": "部分掌握",
    "not_mastered": "完全没掌握",
}


def get_request_host(request: Request) -> str:
    """获取客户端访问本服务的地址（优先请求Host头，回退server_ip.txt）"""
    host = (request.headers.get("host") or "").strip()
    if not host:
        try:
            ip_file = Path(__file__).resolve().parent / "server_ip.txt"
            host = ip_file.read_text(encoding="utf-8").strip()
        except Exception:
            host = "127.0.0.1:8000"
    return host.removeprefix("http://").removeprefix("https://").rstrip("/")


def rewrite_static_urls(text: str, host: str) -> str:
    """把Markdown中的 /static/ 图片地址重写到当前服务地址（防旧IP失效导致图片不显示）"""
    if not text:
        return text
    import re as _re
    base = f"http://{host}/static/"
    # 已有绝对地址（http(s)://旧host/static/...）→ 换成当前host
    text = _re.sub(r'https?://[^/]+/static/', base, text)
    return text


def rewrite_rendered_images(text: str, host: str, svg_rel_dir: str) -> str:
    """② 把LaTeX渲染产物中的相对图片引用(diagram_xxx.png)补全为绝对URL，并把旧host统一到当前host
    svg_rel_dir: 图片所在目录（相对history根），如 svgs_xxx 或 svgs_ask_xxx"""
    if not text:
        return text
    import re as _re
    base = f"http://{host}/static/"
    text = _re.sub(r'!\[([^\]]*)\]\((diagram_[^)\s]+\.(?:png|svg))\)',
                   rf'![\1]({base}{svg_rel_dir}/\2)', text)
    text = _re.sub(r'https?://[^/]+/static/', base, text)
    return text


def _move_record_files_to_recycle_bin(record) -> str:
    """删除记录时把相关文件（原图/svg目录/solution.md/掌握程度）移入回收站目录"""
    import shutil
    from datetime import datetime as _dt
    bin_root = HISTORY_DIR / "recycle_bin"
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    dest = bin_root / f"{ts}_{record.session_id or record.id}"
    dest.mkdir(parents=True, exist_ok=True)
    candidates = []
    if record.original_image_path and Path(record.original_image_path).exists():
        candidates.append(Path(record.original_image_path))
    if record.rendered_svg_dir and Path(record.rendered_svg_dir).exists():
        candidates.append(Path(record.rendered_svg_dir))
    md_path = HISTORY_DIR / f"{record.session_id}_solution.md"
    if md_path.exists():
        candidates.append(md_path)
    mastery_path = HISTORY_DIR / "mastery_records" / f"{record.session_id}.json"
    if mastery_path.exists():
        candidates.append(mastery_path)
    for p in candidates:
        try:
            shutil.move(str(p), str(dest / p.name))
        except Exception as e:
            print(f"[recycle] 移动失败 {p}: {e}")
    return str(dest)


_GEOGEBRA_ASSETS_DIR = HISTORY_DIR / "geogebra_assets"


def ensure_geogebra_assets() -> str:
    """确保本地 deployggb.js 存在（从 geogebra.org 下载缓存一次），返回本地URL；失败回退CDN"""
    try:
        _GEOGEBRA_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        target = _GEOGEBRA_ASSETS_DIR / "deployggb.js"
        # 校验：文件存在且大小合理（deployggb.js 实际约 37KB，内容含 GeoGebra 标识）
        valid = target.exists() and target.stat().st_size > 10_000
        if valid:
            try:
                head = target.read_bytes()[:512].lower()
                if b"geogebra" not in head and b"ggbapplet" not in head:
                    valid = False
            except Exception:
                valid = False
        if not valid:
            import urllib.request
            print("[GeoGebra] 下载 deployggb.js 到本地缓存...")
            tmp = target.with_suffix(".js.tmp")
            try:
                with urllib.request.urlopen(
                        "https://www.geogebra.org/apps/deployggb.js", timeout=60) as resp, open(tmp, "wb") as f:
                    f.write(resp.read())
                if tmp.exists() and tmp.stat().st_size > 10_000:
                    # ⑦ 补丁：CDN base → 本地镜像（若镜像目录存在）
                    _txt = tmp.read_text(encoding="utf-8", errors="replace")
                    _m = __import__("re").search(r'https://www\.geogebra\.org/apps/([0-9.]+)/', _txt)
                    if _m:
                        _ver = _m.group(1)
                        _mirror = _GEOGEBRA_ASSETS_DIR.parent / "geogebra_apps" / _ver
                        if _mirror.exists():
                            _txt = _txt.replace(
                                f"https://www.geogebra.org/apps/{_ver}/",
                                f"/static/geogebra_apps/{_ver}/")
                            tmp.write_text(_txt, encoding="utf-8")
                            print(f"[GeoGebra] 已应用本地镜像补丁: apps/{_ver}/")
                    tmp.replace(target)
                    print(f"[GeoGebra] 本地缓存完成: {target.stat().st_size} bytes")
                else:
                    print(f"[GeoGebra] 下载结果异常({tmp.stat().st_size if tmp.exists() else 0} bytes)，回退CDN")
                    if tmp.exists():
                        tmp.unlink()
                    return "https://www.geogebra.org/apps/deployggb.js"
            except Exception as e:
                print(f"[GeoGebra] 下载失败，回退CDN: {e}")
                if tmp.exists():
                    tmp.unlink()
                return "https://www.geogebra.org/apps/deployggb.js"
        return "/static/geogebra_assets/deployggb.js"
    except Exception as e:
        print(f"[GeoGebra] 本地缓存失败，回退CDN: {e}")
        return "https://www.geogebra.org/apps/deployggb.js"


# 解题流水线内部使用的提示词前缀（追问时应从上下文剔除，防止AI模仿之前的JSON输出格式）
_INTERNAL_PROMPT_PREFIXES = (
    "请分析这道题目，以JSON格式",     # 题目信息JSON提取
    "基于以上题目分析，请给出清晰的解题思路",  # 解题思路
    "请给出完整的解题过程和答案",     # 完整解析（其回复会保留作为题解上下文）
    "请为这道题生成有助于学生理解",   # LaTeX图解
    "请用纯文本缩进格式，为这道题生成",  # 思维导图
    "请生成3个学生可能会问",          # 预判问题JSON
)


def clean_conversation_history(history: list) -> list:
    """从解题会话历史中提取干净的追问上下文。

    保留：system题面、完整解析、真实的追问问答对；
    剔除：内部流水线提示词及其直接回复（如JSON题目信息、JSON预判问题等）。
    否则AI会把上一个“输出JSON数组”的指令延续到追问中，导致回答是JSON格式。
    """
    cleaned: list = []
    solution_text = ""
    skip_next_assistant = False
    capture_next = False
    for h in history:
        content = (h.get("content") or "").strip()
        role = h.get("role")
        if role == "system":
            cleaned.append({"role": "system", "content": content})
            continue
        if role == "user":
            if content.startswith("请给出完整的解题过程和答案"):
                capture_next = True
                continue
            if content.startswith(_INTERNAL_PROMPT_PREFIXES):
                # 内部提示词：连同紧随其后的assistant回复一起剔除
                skip_next_assistant = True
                continue
            capture_next = False
            skip_next_assistant = False
            cleaned.append({"role": "user", "content": content})
        else:  # assistant
            if capture_next:
                capture_next = False
                solution_text = content
                continue
            if skip_next_assistant:
                skip_next_assistant = False
                continue
            cleaned.append({"role": "assistant", "content": content})
    # 题面之后插入完整解析作为上下文
    if solution_text:
        if cleaned and cleaned[0].get("role") == "system":
            cleaned.insert(1, {"role": "assistant", "content": solution_text})
        else:
            cleaned.insert(0, {"role": "system", "content": "以下是这道题的完整解析，供你参考。"})
            cleaned.insert(1, {"role": "assistant", "content": solution_text})
    return cleaned


def cleanup_markdown_images(text: str) -> str:
    """清理Markdown中的图片标记，保留alt文本"""
    import re
    if not text:
        return text
    # 移除 ![alt](url) 格式的图片
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'[图片: \1]', text)
    # 移除 <img ...> 格式
    text = re.sub(r'<img[^>]+>', '[图片]', text)
    return text

def extract_subjects(text: str) -> list:
    """Feature 18: 从题目文本中提取学科"""
    import re
    subjects = set()
    subject_map = {
        "数学": ["数学", "方程", "函数", "几何", "代数", "三角", "导数", "积分", "概率", "统计"],
        "物理": ["物理", "力学", "电学", "光学", "热学", "磁场", "电场", "速度", "加速度", "牛顿"],
        "化学": ["化学", "反应", "分子", "原子", "元素", "化合", "氧化", "还原", "酸碱"],
        "英语": ["英语", "English", "grammar", "vocabulary", "reading", "writing"],
        "语文": ["语文", "阅读", "作文", "古诗", "文言文", "修辞", "成语"],
        "生物": ["生物", "细胞", "基因", "DNA", "遗传", "生态"],
        "地理": ["地理", "气候", "地形", "经纬", "地图"],
        "历史": ["历史", "朝代", "战争", "革命", "改革"],
    }
    for subject, keywords in subject_map.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                subjects.add(subject)
                break
    return list(subjects) if subjects else ["其他"]

# ==================== 启动 ====================

if __name__ == "__main__":
    import uvicorn
    
    import socket
    def get_local_ip():
        # 优先读取 server_ip.txt（用户可手动指定固定IP）
        try:
            ip_file = Path(__file__).resolve().parent / "server_ip.txt"
            if ip_file.exists():
                content = ip_file.read_text(encoding="utf-8").strip()
                ip = content.split(":")[0].strip()
                if ip:
                    print(f"[启动] 从 server_ip.txt 读取IP: {ip}")
                    return ip
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    local_ip = get_local_ip()
    print(f"本机IP地址: {local_ip}")
    
    # Feature 19: 保存服务器IP到文件（仅当文件不存在时写入，保留手动指定）
    ip_file = Path(__file__).parent / "server_ip.txt"
    if not ip_file.exists():
        with open(ip_file, "w") as f:
            f.write(f"{local_ip}:8000")
    else:
        print(f"[启动] server_ip.txt 已存在，保留内容: {ip_file.read_text(encoding='utf-8').strip()}")
    
    discovery = DiscoveryService(server_host=local_ip, api_port=8000)
    discovery.start()

    # ===== 诊断: 记录收到的终止信号，帮助定位服务被意外关闭的原因 =====
    import signal as _signal_mod
    import sys as _sys_mod
    import time as _time_mod
    from uvicorn.server import Server as _UvicornServer

    _server_start_ts = _time_mod.time()
    _SIGINT_GRACE_SECONDS = 30.0  # 启动保护窗口：此时间内首次SIGINT仅记录不退出
    _sigint_state = {"count": 0}
    _orig_handle_exit = _UvicornServer.handle_exit

    def _dump_console_processes():
        """枚举与本进程共享同一控制台的进程PID（Windows上Ctrl+C会发给其中所有进程）"""
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            buf = (ctypes.c_uint * 64)()
            n = k32.GetConsoleProcessList(buf, 64)
            pids = list(buf[:n])
            print(f"[诊断] 共享控制台的进程PID: {pids}（本进程={os.getpid()}）")
            print("[诊断] 若列表中存在本进程之外的进程（如助手/代理工具），它可能正是Ctrl+C的来源")
        except Exception as e:
            print(f"[诊断] 无法枚举控制台进程: {e}")

    def _diagnose_handle_exit(self, sig, frame):
        try:
            sig_name = _signal_mod.Signals(sig).name
        except ValueError:
            sig_name = str(sig)
        elapsed = _time_mod.time() - _server_start_ts
        now_str = datetime.now().strftime('%H:%M:%S')
        # 启动保护：30秒内的首次SIGINT仅记录并忽略，防止工具/终端误发Ctrl+C杀掉服务
        if sig == _signal_mod.SIGINT and elapsed < _SIGINT_GRACE_SECONDS:
            _sigint_state["count"] += 1
            if _sigint_state["count"] == 1:
                print(f"\n[诊断] {now_str} 启动仅{elapsed:.0f}秒即收到 SIGINT (Ctrl+C)，已忽略以保持服务运行。"
                      f"若确实要停止服务，请再次按 Ctrl+C。")
                _dump_console_processes()
                return
        print(f"\n[诊断] {now_str} 收到终止信号: {sig_name} ({sig})，"
              f"距启动约{elapsed:.0f}秒，stdin_isatty={_sys_mod.stdin.isatty()}，服务即将关闭。"
              f"若未手动按 Ctrl+C，请检查是否有其他程序/终端操作发送了该信号。")
        _dump_console_processes()
        return _orig_handle_exit(self, sig, frame)

    _UvicornServer.handle_exit = _diagnose_handle_exit

    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        discovery.stop()
