import json
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
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

@app.post("/solve")
async def solve_problem(file: UploadFile = File(...)):
    request_id = str(uuid.uuid4())
    
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    with open(image_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # 用同一个 request_id 启动流程
    solve_pipeline.start_solve(
        image_path=image_path,
        session_id=request_id  # ← 传入相同ID
    )
    
    return {
        "request_id": request_id,  # ← 返回相同ID
        "status": "processing",
        "message": "解题已启动"
    }

@app.get("/solve/stream/{request_id}")
async def solve_stream(request_id: str):
    """SSE流式推送解题结果"""
    
    async def event_stream():
        # ===== 从真正的解题流程获取事件 =====
        for event in solve_pipeline.get_events(request_id):
            # 将事件转换为SSE格式
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            
            # 如果事件中包含大量文本，让出控制权
            if event.get("stage") in ("solution", "mindmap", "solution_chunk"):
                await asyncio.sleep(0.01)
        
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
    """多轮对话 - 继续提问"""
    response = ai_service.continue_conversation(
        request.context or [],
        request.question
    )
    return {"answer": response, "session_id": request.session_id}

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
    html_path = generate_animation(ocr_text)
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
async def data_report(request: ReportRequest):
    """生成数据版学情报告"""
    db = SessionLocal()
    try:
        generator = ReportGenerator(db)
        html_path = generator.generate_data_report_html(request.days)
        
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
async def ai_report(request: ReportRequest):
    """生成AI版学情报告"""
    db = SessionLocal()
    try:
        generator = ReportGenerator(db)
        summary = generator.get_report_summary(request.days)
        
        if summary == "暂无学习记录":
            return {"status": "error", "message": "暂无学习记录"}
        
        ai_report_text = ai_service.generate_ai_report(summary)
        
        return {
            "status": "ok",
            "report": ai_report_text,
            "summary": summary
        }
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
async def knowledge_extension(file: UploadFile = File(...)):
    """知识延伸"""
    request_id = str(uuid.uuid4())
    image_path = HISTORY_DIR / f"{request_id}.jpg"
    with open(image_path, "wb") as f:
        f.write(await file.read())
    
    solve_pipeline.start_knowledge_extension(image_path, request_id)
    
    return {"request_id": request_id, "status": "processing"}

@app.get("/extend/stream/{request_id}")
async def extend_stream(request_id: str):
    """SSE流式推送知识延伸结果"""
    async def event_stream():
        for event in solve_pipeline.get_events(request_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/history")
async def get_history(request: dict):
    """获取历史记录（返回完整内容，清除图片链接）"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        from datetime import datetime
        import re
        
        start_date = request.get("start_date", "")
        end_date = request.get("end_date", "")
        
        query = db.query(SubmissionRecord)
        
        if start_date:
            query = query.filter(SubmissionRecord.timestamp >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(SubmissionRecord.timestamp <= datetime.fromisoformat(end_date))
        
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
                "timestamp": r.timestamp.isoformat() if r.timestamp is not None else "",
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
async def clear_history():
    """清除所有历史记录"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        db.query(SubmissionRecord).delete()
        db.commit()
        return {"status": "ok", "message": "历史记录已清除"}
    finally:
        db.close()

@app.delete("/history/{record_id}")
async def delete_history_record(record_id: int):
    """删除单条历史记录"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        record = db.query(SubmissionRecord).filter(SubmissionRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(record)
        db.commit()
        return {"status": "ok", "message": f"记录 {record_id} 已删除"}
    finally:
        db.close()

@app.post("/history/batch-delete")
async def batch_delete_history(request: dict):
    """批量删除历史记录"""
    db = SessionLocal()
    try:
        from server.database.models import SubmissionRecord
        ids = request.get("ids", [])
        if not ids:
            return {"status": "error", "message": "未指定要删除的记录ID"}
        deleted = db.query(SubmissionRecord).filter(SubmissionRecord.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        return {"status": "ok", "message": f"已删除 {deleted} 条记录", "deleted_count": deleted}
    finally:
        db.close()

@app.post("/tracking/sync")
async def sync_tracking_data(data: list[TrackingData]):
    """同步跟踪学习数据"""
    db = SessionLocal()
    try:
        for item in data:
            record = TrackingRecord(
                session_id=item.session_id,
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

# ==================== Feature 4: 掌握程度 ====================

class MasteryRequest(BaseModel):
    request_id: str
    mastery_level: str

@app.post("/mastery")
async def save_mastery(request: MasteryRequest):
    """保存掌握程度记录"""
    mastery_dir = HISTORY_DIR / "mastery_records"
    mastery_dir.mkdir(exist_ok=True)
    
    record = {
        "request_id": request.request_id,
        "mastery_level": request.mastery_level,
        "timestamp": datetime.now().isoformat()
    }
    
    file_path = mastery_dir / f"{request.request_id}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    
    return {"status": "ok", "message": "掌握程度已保存"}

# ==================== Feature 20: GeoGebra ====================

class GeoGebraRequest(BaseModel):
    ocr_text: str = ""
    subject: str = ""

@app.post("/geogebra")
async def generate_geogebra(request: GeoGebraRequest):
    """Feature 20: 生成GeoGebra图形命令"""
    try:
        prompt = f"""请根据以下数学题目内容，生成可以在GeoGebra中输入的命令。
        
题目内容: {request.ocr_text if request.ocr_text else '绘制基本数学图形'}

要求:
1. 每行一个GeoGebra命令
2. 使用标准GeoGebra语法，英文函数名
3. 先定义基础对象(如函数、点、滑块)，再定义依赖对象
4. 如果题目涉及函数，请定义函数并绘制图像
5. 如果涉及几何，请绘制对应的几何图形并标注关键点
6. 不要输出任何解释文字，只输出命令

请直接输出GeoGebra命令，每行一个:"""
        
        response = ai_service.generate_response(prompt)
        
        # 清理响应，提取纯命令
        commands_text = response.strip()
        # 移除可能的markdown代码块标记
        import re
        commands_text = re.sub(r'```[\w]*\n?', '', commands_text)
        commands_text = re.sub(r'```', '', commands_text)
        
        # 按行分割命令
        commands = [line.strip() for line in commands_text.split('\n') 
                   if line.strip() and not line.strip().startswith('//') and not line.strip().startswith('#')]
        
        return {"status": "ok", "commands": commands, "raw": commands_text}
    except Exception as e:
        # 返回默认示例命令
        default_commands = [
            "f(x) = sin(x)",
            "g(x) = x^2",
            "A = (0, 0)",
            "B = (1, 1)",
            "a = 1",
            "b = 2",
        ]
        return {"status": "ok", "commands": default_commands, "message": f"使用默认图形(生成失败: {str(e)})"}

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
    
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        discovery.stop()
