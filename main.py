import json
import uuid
import os
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
from server.utils.tikz_md_renderer import get_available_latex_engines
from server.services.discovery_service import DiscoveryService
from server.services.solve_pipeline import solve_pipeline
from server.services.animation_service import generate_animation
from server.services.ocr_service import ocr_service
from server.services.auth_service import register, login, get_user_by_token, verify_token

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
    thread = threading.Thread(target=ocr_service.preload_local_model, daemon=True)
    thread.start()
    print("[启动] OCR模型预加载已触发（后台异步加载中...）")
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

# 初始化数据库
SessionLocal = init_database()

# ==================== 数据模型 ====================

class AskRequest(BaseModel):
    session_id: str
    question: str
    context: Optional[list] = None

class ReportRequest(BaseModel):
    days: int = 30

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
    """读取客户端选择的AI引擎（X-Engine请求头），默认deepseek"""
    return (request.headers.get("X-Engine") or "deepseek").lower()

@app.post("/solve")
async def solve_problem(request: Request, file: UploadFile = File(...)):
    request_id = str(uuid.uuid4())
    
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    with open(image_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # 用同一个 request_id 启动流程；传入请求Host用于构造LaTeX图片URL，user_id用于数据隔离
    solve_pipeline.start_solve(
        image_path=image_path,
        session_id=request_id,  # ← 传入相同ID
        base_host=request.headers.get("host") or None,
        user_id=get_current_user(request),
        engine=get_engine(request),
    )
    
    return {
        "request_id": request_id,  # ← 返回相同ID
        "status": "processing",
        "message": "解题已启动"
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
        messages: list = [{"role": h.role, "content": h.content} for h in history]
        if not messages:
            # 无历史时用空上下文，避免AI无背景作答
            messages = []

        response = ai_service.continue_conversation(messages, request.question, engine=get_engine(request))

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
        messages: list = [{"role": h.role, "content": h.content} for h in history]
        engine = get_engine(request)

        async def event_stream():
            accumulated = ""
            for chunk in ai_service.continue_conversation_stream(messages, body.question, engine=engine):
                accumulated = chunk
                yield f"data: {json.dumps({'stage': 'answer_chunk', 'content': accumulated}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)
            # 保存本次问答到数据库（流结束、response返回后执行，需新开会话）
            try:
                db2 = SessionLocal()
                try:
                    for role, content in (("user", body.question), ("assistant", accumulated)):
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
async def create_animation(file: UploadFile = File(...)):
    """生成AI动画"""
    request_id = str(uuid.uuid4())
    # 保存图片
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    with open(image_path, "wb") as f:
        content = await file.read()
        f.write(content)
    # OCR 识别
    ocr_text, _, _ = ocr_service.recognize(str(image_path))
    if not ocr_text or ocr_text.startswith("OCR"):
        return {"status": "error", "message": "OCR识别失败"}
    
    # 生成动画
    html_path = generate_animation(ocr_text, engine=get_engine(request))
    if html_path:
        # 返回动画文件的 URL
        filename = Path(html_path).name
        return {
            "status": "ok",
            "url": f"/static/animations/{filename}",
            "request_id": request_id
        }
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
        html_path = generator.generate_data_report_html(body.days, user_id=get_current_user(request))
        
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
    """生成AI版学情报告（非流式，兼容旧客户端）"""
    db = SessionLocal()
    try:
        generator = ReportGenerator(db)
        summary = generator.get_report_summary(body.days, user_id=get_current_user(request))
        
        if summary == "暂无学习记录":
            return {"status": "error", "message": "暂无学习记录"}
        
        ai_report_text = ai_service.generate_ai_report(summary, engine=get_engine(request))
        
        return {
            "status": "ok",
            "report": ai_report_text,
            "summary": summary
        }
    finally:
        db.close()

@app.post("/report/ai/stream")
async def ai_report_stream(request: Request, body: ReportRequest):
    """生成AI版学情报告（SSE流式）"""
    db = SessionLocal()
    try:
        generator = ReportGenerator(db)
        summary = await asyncio.to_thread(generator.get_report_summary, body.days, user_id=get_current_user(request))
        
        if summary == "暂无学习记录":
            return {"status": "error", "message": "暂无学习记录"}
        
        async def event_stream():
            # 先发摘要供客户端展示
            yield f"data: {json.dumps({'stage': 'summary', 'content': summary}, ensure_ascii=False)}\n\n"
            # 流式生成报告正文
            for chunk in ai_service.generate_ai_report_stream(summary, engine=get_engine(request)):
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
    """知识延伸"""
    request_id = str(uuid.uuid4())
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    with open(image_path, "wb") as f:
        f.write(await file.read())
    
    solve_pipeline.start_knowledge_extension(image_path, request_id, user_id=get_current_user(request), engine=get_engine(request))
    
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
        
        records = query.order_by(SubmissionRecord.timestamp.desc()).limit(50).all()
        
        result = []
        all_subjects = set()
        for r in records:
            # 清理OCR文本中的图片链接
            clean_ocr = cleanup_markdown_images(r.ocr_text) if r.ocr_text else ""
            clean_steps = cleanup_markdown_images(r.solution_steps) if r.solution_steps else ""
            clean_solution = cleanup_markdown_images(r.full_solution) if r.full_solution else ""
            
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
            
            if subject:
                all_subjects.add(subject)
            
            # 生成原图URL
            image_url = ""
            if r.original_image_path:
                image_path = Path(r.original_image_path)
                if image_path.exists():
                    image_url = f"/static/{image_path.name}"
            
            result.append({
                "id": r.id,
                "timestamp": (r.timestamp + local_offset).isoformat() if r.timestamp is not None else "",
                "ocr_text": clean_ocr,
                "question_info_raw": json.dumps(question_info, ensure_ascii=False) if isinstance(question_info, dict) else str(question_info),
                "grade": grade,
                "subject": subject,
                "difficulty": difficulty,
                "knowledge_points": knowledge_points,
                "solution_steps": clean_steps,
                "full_solution": clean_solution,
                "image_url": image_url,
            })
        
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
    """清除历史记录（仅本人记录；未登录时仅清空无主数据）"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from sqlalchemy import or_
        user_id = get_current_user(request)
        if user_id is not None:
            db.query(SubmissionRecord).filter(
                or_(SubmissionRecord.user_id == user_id, SubmissionRecord.user_id.is_(None))
            ).delete(synchronize_session=False)
        else:
            db.query(SubmissionRecord).filter(SubmissionRecord.user_id.is_(None)).delete(synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": "历史记录已清除"}
    finally:
        db.close()

@app.delete("/history/{record_id}")
async def delete_history_record(record_id: int, request: Request):
    """删除单条历史记录（校验归属）"""
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
        db.delete(record)
        db.commit()
        return {"status": "ok", "message": f"记录 {record_id} 已删除"}
    finally:
        db.close()

@app.post("/history/batch-delete")
async def batch_delete_history(request: Request, body: dict):
    """批量删除历史记录（校验归属）"""
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
        deleted = query.delete(synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": f"已删除 {deleted} 条记录", "deleted_count": deleted}
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
        
        response = ai_service.generate_response(prompt, engine=get_engine(request))
        
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
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
    <title>数学图形</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ width: 100%; height: 100%; background: #1A1A2E; overflow: hidden; }}
        #ggb-container {{ width: 100%; height: 100%; }}
    </style>
</head>
<body>
    <div id="ggb-container"></div>
    <script src="https://www.geogebra.org/apps/deployggb.js"></script>
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
    
    # Feature 19: 保存服务器IP到文件
    ip_file = Path(__file__).parent / "server_ip.txt"
    with open(ip_file, "w") as f:
        f.write(f"{local_ip}:8000")
    print(f"[启动] 服务器IP已保存到: {ip_file}")
    
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
