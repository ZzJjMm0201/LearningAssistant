"""
解题完整流程编排
负责串联所有服务：OCR → 搜题 → AI对话 → LaTeX渲染 → 存储
"""
import json
import uuid
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Generator, Optional
from collections import deque

from server.config import HISTORY_DIR, FEATURE_FLAGS
from server.database.models import SessionLocal, SubmissionRecord, ConversationHistory
from server.services.ocr_service import ocr_service
from server.services.search_service import search_service
from server.services.ai_service import ai_service
from server.utils.latex_processor import process_latex_blocks, process_latex_blocks_with_retry

from typing import Dict, Generator, Optional, Union

# SSE事件队列 - 存储每个request_id的事件流
_event_queues: Dict[str, deque] = {}
_event_threads: Dict[str, threading.Thread] = {}
# 用户确认等待标志
_pending_confirm: Dict[str, threading.Event] = {}
# ① 多题选择：等待标志 / 已选题目索引（0-based）
_pending_question_select: Dict[str, threading.Event] = {}
_selected_questions: Dict[str, list] = {}

def _normalize_mindmap(text: str) -> str:
    """思维导图围栏规范化：AI输出已含```代码块则原样使用，否则包裹（避免双层围栏格式错乱）"""
    if not text:
        return text
    if text.count("```") >= 2 or text.strip().startswith("```"):
        return text
    return "```\n" + text + "\n```"


class SolvePipeline:
    """解题流水线"""
    
    def __init__(self):
        pass
    
    def start_solve(self, image_path: Path, session_id: Optional[str] = None, base_host: Optional[str] = None, user_id: Optional[int] = None, engine: Optional[str] = None, ocr_mode: str = "paddle", vision_model: Optional[str] = None, model: Optional[str] = None,
                    style: Optional[str] = None, thinking=False, search_enabled: bool = True, dialect: str = "", grade: str = "", personality=None, subject: str = "", detail=None, weak_count: int = 0, text_input: Optional[str] = None) -> str:
        # 使用 session_id 作为 request_id，如果不提供则生成新ID
        request_id = session_id or str(uuid.uuid4())
        
        _event_queues[request_id] = deque()
        
        thread = threading.Thread(
            target=self._solve_worker,
            args=(request_id, image_path, session_id, base_host, user_id, engine, ocr_mode, vision_model, model,
                  style, thinking, search_enabled, dialect, grade, personality, subject, detail, weak_count, text_input)
        )
        thread.daemon = True
        _event_threads[request_id] = thread
        thread.start()
        
        return request_id
    
    def get_events(self, request_id: str) -> Generator[Dict, None, None]:
        """获取SSE事件流（同步生成器，仅供后台线程使用）"""
        queue = _event_queues.get(request_id)
        if not queue:
            yield {"stage": "error", "content": "无效的request_id"}
            return
        
        while True:
            if queue:
                event = queue.popleft()
                yield event
                if event.get("stage") == "complete":
                    break
            else:
                time.sleep(0.1)

    def get_queue(self, request_id: str):
        """获取事件队列（供异步端点轮询，避免阻塞事件循环）"""
        return _event_queues.get(request_id)
    
    def _emit_event(self, request_id: str, stage: str, content, qi: Optional[int] = None):
        """发送事件到队列；qi=题目索引（①多题模式，单题时为None）"""
        queue = _event_queues.get(request_id)
        if queue is not None:
            evt = {"stage": stage, "content": content}
            if qi is not None:
                evt["qi"] = qi
            queue.append(evt)
    
    def confirm_continue(self, request_id: str):
        """用户确认OCR结果后，继续流程"""
        event = _pending_confirm.get(request_id)
        if event:
            event.set()
            return True
        return False

    def select_questions(self, request_id: str, indices):
        """① 多题：用户选定要解的题目索引（0-based），继续流程"""
        ev = _pending_question_select.get(request_id)
        if ev is None:
            return False
        try:
            _selected_questions[request_id] = [int(i) for i in (indices or [])]
        except Exception:
            _selected_questions[request_id] = []
        ev.set()
        return True
    
    def cancel_solve(self, request_id: str):
        """用户取消后，清理并结束"""
        self._emit_event(request_id, "info", "用户已取消")
        self._emit_event(request_id, "complete", {"request_id": request_id, "cancelled": True})
        event = _pending_confirm.get(request_id)
        if event:
            event.set()  # 让worker线程继续执行并退出
        self._cleanup(request_id)
    
    def _solve_worker(self, request_id: str, image_path: Path, session_id: Optional[str] = None, base_host: Optional[str] = None, user_id: Optional[int] = None, engine: Optional[str] = None, ocr_mode: str = "paddle", vision_model: Optional[str] = None, model: Optional[str] = None,
                      style: Optional[str] = None, thinking=False, search_enabled: bool = True, dialect: str = "", grade: str = "", personality=None, subject: str = "", detail=None, weak_count: int = 0, text_input: Optional[str] = None):
        """后台解题工作线程（① 支持多题：OCR后分题，逐题走完整流程）"""
        print(f"[{request_id}] ========== 解题流水线启动 ==========\n")
        print(f"[{request_id}] 图片路径: {image_path}")
        print(f"[{request_id}] 图片存在: {image_path.exists()}, 大小: {image_path.stat().st_size} bytes")
        try:
            # ========== 阶段1: OCR识别（⑧ 文字输入模式跳过OCR） ==========
            if text_input and text_input.strip():
                ocr_text = text_input.strip()
                ocr_time = 0.0
                ocr_source = "text"
                print(f"[{request_id}] 文字输入模式，跳过OCR，长度: {len(ocr_text)}")
                self._emit_event(request_id, "ocr_complete", {
                    "text": ocr_text,
                    "time": 0.0,
                    "source": "text"
                })
            else:
                print(f"[{request_id}] 开始OCR识别...")
                self._emit_event(request_id, "info", "正在识别题目文字...")

                ocr_text, ocr_time, ocr_source = ocr_service.recognize(str(image_path), mode=ocr_mode, vision_model=vision_model)
                print(f"[{request_id}] OCR完成，来源={ocr_source}，耗时{ocr_time}s，文本长度: {len(ocr_text)}")
                print(f"[{request_id}] OCR内容预览: {ocr_text[:200]}...")

                self._emit_event(request_id, "ocr_complete", {
                    "text": ocr_text,
                    "time": ocr_time,
                    "source": ocr_source
                })

            if not ocr_text or ocr_text.startswith("OCR"):
                self._emit_event(request_id, "error", "OCR识别失败，请重试")
                self._emit_event(request_id, "complete", None)
                return

            # ⑥ 图片模糊：视觉模型返回 ---end---，直接终止并提示重新拍摄
            if "---end---" in ocr_text:
                print(f"[{request_id}] OCR判为模糊，终止解题")
                self._emit_event(request_id, "blurred", "图片模糊或无法辨认，请重新拍摄")
                self._emit_event(request_id, "complete", None)
                return

            # ========== 等待用户确认OCR结果 ==========
            self._emit_event(request_id, "waiting_confirm", {"text": ocr_text})
            confirm_event = threading.Event()
            _pending_confirm[request_id] = confirm_event
            if not confirm_event.wait(timeout=30):
                print(f"[{request_id}] OCR确认超时，自动继续")
            else:
                print(f"[{request_id}] 用户已确认OCR结果")
            _pending_confirm.pop(request_id, None)

            # ========== ① 多题分题（⑧ 文字输入模式免分题） ==========
            if ocr_source == "text":
                questions = [ocr_text]
                print(f"[{request_id}] 文字输入模式，免分题")
            else:
                from server.utils.split_service import split_questions
                questions = split_questions(ocr_text, ocr_source, ai_service=ai_service, engine=engine, model=model)
                questions = [q for q in questions if q and q.strip()]
                if len(questions) == 0:
                    questions = [ocr_text]
                print(f"[{request_id}] 分题结果: {len(questions)} 道题")

            if len(questions) > 1:
                self._emit_event(request_id, "question_split", {"count": len(questions), "questions": questions})
                # ① 等待用户选题（默认/超时全选；客户端5秒倒计时自动确认）
                sel_event = threading.Event()
                _pending_question_select[request_id] = sel_event
                if not sel_event.wait(timeout=120):
                    print(f"[{request_id}] 多题选择超时，默认全选")
                    selected = list(range(len(questions)))
                else:
                    selected = _selected_questions.pop(request_id, list(range(len(questions))))
                _pending_question_select.pop(request_id, None)
                selected = sorted({i for i in selected if isinstance(i, int) and 0 <= i < len(questions)})
                if not selected:
                    selected = list(range(len(questions)))
                questions = [questions[i] for i in selected]
                print(f"[{request_id}] 用户选定 {len(questions)} 道题，索引 {selected}")

            # 逐题走完整流程（循环）
            for qi, q_text in enumerate(questions):
                q_session = f"{session_id}__q{qi}" if len(questions) > 1 else session_id
                self._solve_one(
                    request_id=request_id, qi=qi if len(questions) > 1 else None,
                    ocr_text=q_text, session_id=q_session, base_host=base_host,
                    user_id=user_id, engine=engine, model=model, style=style,
                    thinking=thinking, search_enabled=search_enabled,
                    dialect=dialect, grade=grade,
                    personality=personality, subject=subject, detail=detail, weak_count=weak_count,
                    ocr_time=ocr_time, vision_model=vision_model,
                )

            self._emit_event(request_id, "complete", {"request_id": request_id})

        except Exception as e:
            print(f"[{request_id}] 解题流程异常: {e}")
            import traceback
            traceback.print_exc()
            self._emit_event(request_id, "error", f"处理异常: {str(e)}")
            self._emit_event(request_id, "complete", None)
        finally:
            self._cleanup(request_id)

    def _solve_one(self, request_id: str, qi: Optional[int], ocr_text: str, session_id: Optional[str], base_host: Optional[str], user_id: Optional[int], engine: Optional[str], model: Optional[str],
                   style: Optional[str], thinking, search_enabled: bool, dialect: str, grade: str, personality=None, subject: str = "", detail=None, weak_count: int = 0, ocr_time: float = 0.0, vision_model: Optional[str] = None):
        """解单道题：题库搜索 + AI多轮 + LaTeX渲染 + 保存（事件带 qi 标记）"""
        # ========== 阶段2: 题库搜索（④ 设置中可关闭） ==========
        search_result, search_time, search_items = "", 0, []
        if search_enabled:
            self._emit_event(request_id, "info", "正在搜索题库...", qi)
            try:
                search_result, search_time, search_items = search_service.search(ocr_text)
            except Exception as e:
                print(f"[{request_id}] 搜索异常（忽略继续）: {e}")
                search_result, search_time, search_items = "", 0, []
        else:
            print(f"[{request_id}] 搜题已关闭（设置中关闭）")

        self._emit_event(request_id, "search_complete", {"found": bool(search_result), "time": search_time}, qi)
        if search_items:
            self._emit_event(request_id, "search_results", search_items, qi)

        # ========== 阶段3: AI多轮对话（流式） ==========
        self._emit_event(request_id, "info", "AI正在分析题目...", qi)
        solution_content = ""
        latex_extras_content = ""
        first_question_info = None

        for event in ai_service.solve_problem_stream(ocr_text, search_result, engine=engine, model=model,
                                                     style=style, thinking=thinking, dialect=dialect, grade=grade):
            stage = event["stage"]
            content = event["content"]

            if stage == "thinking_start":
                self._emit_event(request_id, "thinking_start", content, qi)
                continue
            elif stage == "thinking_chunk":
                self._emit_event(request_id, "thinking_chunk", content, qi)
                continue

            if stage == "info":
                first_question_info = content
                self._emit_event(request_id, "question_info", content, qi)
            elif stage == "steps_chunk":
                self._emit_event(request_id, "solution_steps_chunk", content, qi)
            elif stage == "steps":
                self._emit_event(request_id, "solution_steps", content, qi)
            elif stage == "solution_chunk":
                solution_content = content
                self._emit_event(request_id, "solution_chunk", content, qi)
            elif stage == "solution":
                solution_content = content
                self._emit_event(request_id, "solution", content, qi)
            elif stage == "latex_extras":
                latex_extras_content = content
                self._emit_event(request_id, "info", "正在生成图解...", qi)
            elif stage == "latex_chunk":
                self._emit_event(request_id, "latex_chunk", content, qi)
            elif stage == "mindmap_chunk":
                self._emit_event(request_id, "mindmap_chunk", _normalize_mindmap(content), qi)
            elif stage == "mindmap":
                self._emit_event(request_id, "mindmap", _normalize_mindmap(content), qi)
            elif stage == "questions":
                self._emit_event(request_id, "suggested_questions", content, qi)
            elif stage == "complete":
                total_time = content.get("total_time", 0)
                messages = content.get("messages", [])
                usage = content.get("usage", {})
                self._emit_event(request_id, "info", f"AI分析完成，耗时{total_time:.1f}s", qi)
                # ⑫ 转发 AI引擎 + token 用量给客户端
                if usage:
                    self._emit_event(request_id, "ai_usage", usage, qi)

                # ========== 阶段4: LaTeX图形渲染 ==========
                final_solution = ""
                if solution_content:
                    print(f"[{request_id}] 开始渲染图形...")
                    svg_dir = HISTORY_DIR / f"svgs_{session_id or request_id}"
                    svg_dir.mkdir(exist_ok=True)
                    self._emit_event(request_id, "info", "正在渲染图形...", qi)

                    import re
                    host = (base_host or "").strip()
                    if not host:
                        try:
                            ip_file = Path(__file__).resolve().parent.parent.parent / "server_ip.txt"
                            host = ip_file.read_text(encoding="utf-8").strip()
                        except Exception:
                            host = "127.0.0.1:8000"
                    host = host.removeprefix("http://").removeprefix("https://").rstrip("/")
                    base_url = f"http://{host}/static/svgs_{session_id or request_id}"

                    def _rewrite_img_urls(text: str) -> str:
                        return re.sub(
                            r'!\[([^\]]*)\]\((diagram_[^)]+\.(?:svg|png))\)',
                            rf'![\1]({base_url}/\2)',
                            text
                        )

                    processed_solution = process_latex_blocks_with_retry(str(solution_content), svg_dir, ai_service=ai_service, engine=engine, model=model, vision_model=vision_model, enable_review=FEATURE_FLAGS.get("enable_latex_review", False))
                    processed_solution = _rewrite_img_urls(processed_solution)

                    if latex_extras_content:
                        processed_extras = process_latex_blocks_with_retry(str(latex_extras_content), svg_dir, ai_service=ai_service, engine=engine, model=model, vision_model=vision_model, enable_review=FEATURE_FLAGS.get("enable_latex_review", False))
                        processed_extras = _rewrite_img_urls(processed_extras)
                        final_solution = processed_solution + "\n\n---\n\n## 📐 图解辅助\n\n" + processed_extras
                    else:
                        final_solution = processed_solution

                    self._emit_event(request_id, "solution_rendered", final_solution, qi)

                    try:
                        _qi = first_question_info if first_question_info is not None else {}
                        if isinstance(_qi, dict) and "raw" not in _qi:
                            self._emit_event(request_id, "question_info", _qi, qi)
                    except Exception:
                        pass

                # ========== 阶段5: 存储记录 ==========
                self._save_record(
                    request_id=request_id,
                    session_id=session_id,
                    ocr_text=ocr_text,
                    ocr_time=ocr_time,
                    search_result=search_result,
                    search_time=search_time,
                    messages=messages,
                    user_id=user_id,
                    full_solution_rendered=final_solution if solution_content else "",
                )
                break

    def start_knowledge_extension(self, image_path: Path, session_id: Optional[str] = None, user_id: Optional[int] = None, engine: Optional[str] = None, ocr_mode: str = "paddle", vision_model: Optional[str] = None, model: Optional[str] = None,
                                  style: Optional[str] = None, dialect: str = "", grade: str = "", text_input: Optional[str] = None) -> str:
        """启动知识延伸流程（⑧ text_input 时跳过OCR）"""
        request_id = session_id or str(uuid.uuid4())
        _event_queues[request_id] = deque()
        
        thread = threading.Thread(
            target=self._extension_worker,
            args=(request_id, image_path, session_id, user_id, engine, ocr_mode, vision_model, model, style, dialect, grade, text_input)
        )
        thread.daemon = True
        _event_threads[request_id] = thread
        thread.start()
        return request_id

    def _extension_worker(self, request_id: str, image_path: Path, session_id: Optional[str] = None, user_id: Optional[int] = None, engine: Optional[str] = None, ocr_mode: str = "paddle", vision_model: Optional[str] = None, model: Optional[str] = None,
                          style: Optional[str] = None, dialect: str = "", grade: str = "", text_input: Optional[str] = None):
        """知识延伸工作线程"""
        print(f"[{request_id}] ========== 知识延伸流程启动 ==========\n")
        
        try:
            # OCR（⑧ 文字输入模式跳过）
            if text_input and text_input.strip():
                ocr_text = text_input.strip()
                ocr_time = 0.0
                ocr_source = "text"
                print(f"[{request_id}] 文字输入模式，跳过OCR，长度: {len(ocr_text)}")
                self._emit_event(request_id, "ocr_complete", {"text": ocr_text, "time": 0.0, "source": "text"})
            else:
                self._emit_event(request_id, "info", "正在识别内容...")
                ocr_text, ocr_time, ocr_source = ocr_service.recognize(str(image_path), mode=ocr_mode, vision_model=vision_model)
                print(f"[{request_id}] OCR完成，来源={ocr_source}，耗时{ocr_time}s，文本长度: {len(ocr_text)}")
                self._emit_event(request_id, "ocr_complete", {"text": ocr_text, "time": ocr_time, "source": ocr_source})
            
            if not ocr_text or ocr_text.startswith("OCR"):
                self._emit_event(request_id, "error", "OCR识别失败")
                self._emit_event(request_id, "complete", None)
                return
            
            # 确保 ocr_text 非 None，满足下游函数的类型要求
            assert ocr_text is not None

            # AI 知识延伸
            self._emit_event(request_id, "info", "正在分析内容...")
            
            ext_info = {}
            ext_summary = ""
            ext_extension = ""
            ext_questions = []
            ext_similar = []
            
            for event in ai_service.generate_knowledge_extension(ocr_text, engine=engine, model=model, style=style, dialect=dialect, grade=grade):
                stage = event["stage"]
                content = event["content"]
                
                if stage == "info":
                    ext_info = content if isinstance(content, dict) else {}
                    self._emit_event(request_id, "question_info", content)
                elif stage == "summary_chunk":
                    self._emit_event(request_id, "summary_chunk", content)
                elif stage == "summary":
                    ext_summary = content or ""
                    self._emit_event(request_id, "summary", content)
                elif stage == "similar_questions":
                    ext_similar = content if isinstance(content, list) else []
                    self._emit_event(request_id, "similar_questions", content)
                elif stage == "extension_chunk":
                    self._emit_event(request_id, "extension_chunk", content)
                elif stage == "extension":
                    ext_extension = content or ""
                    self._emit_event(request_id, "extension", content)
                elif stage == "questions":
                    ext_questions = content if isinstance(content, list) else []
                    self._emit_event(request_id, "suggested_questions", content)
                elif stage == "complete":
                    total_time = content.get("total_time", 0)
                    # ③ 保存知识延伸记录到历史
                    try:
                        from server.database.models import AuxRecord
                        db = SessionLocal()
                        try:
                            parts = []
                            if ext_summary:
                                parts.append(f"## 知识点总结\n\n{ext_summary}")
                            if ext_extension:
                                parts.append(f"## 知识拓展\n\n{ext_extension}")
                            body = "\n\n".join(parts)
                            title = (ext_info.get("core_concept") or "知识延伸") if isinstance(ext_info, dict) else "知识延伸"
                            rec = AuxRecord(
                                session_id=session_id or request_id,
                                user_id=user_id,
                                record_type="extension",
                                title=title,
                                content=body,
                                extra_json={"questions": ext_questions, "similar": ext_similar, "subject": (ext_info or {}).get("subject", "")},
                            )
                            db.add(rec)
                            db.commit()
                            print(f"[{request_id}] 知识延伸记录已保存")
                        finally:
                            db.close()
                    except Exception as e:
                        print(f"[{request_id}] 保存知识延伸记录失败: {e}")
                    self._emit_event(request_id, "complete", {
                        "request_id": request_id,
                        "total_time": total_time
                    })
                    break
            
        except Exception as e:
            print(f"[{request_id}] 知识延伸异常: {e}")
            import traceback
            traceback.print_exc()
            self._emit_event(request_id, "error", str(e))
            self._emit_event(request_id, "complete", None)
        finally:
            self._cleanup(request_id)
    
    def _save_record(self, request_id: str, session_id: Optional[str], ocr_text: str, 
                ocr_time: float, search_result: Optional[str], search_time: float,
                messages: list, user_id: Optional[int] = None, full_solution_rendered: str = ""):
        """保存解题记录到数据库（每次调用使用独立会话，避免多线程共享Session导致保存失败）
        full_solution_rendered: 已渲染LaTeX图片的完整解析（带图片URL），优先存储它"""
        db = SessionLocal()
        try:
            question_info = {}
            solution_steps = ""
            full_solution = ""
            mind_map = ""
            
            # 按消息顺序判断内容类型（assistant 的第2、3、4条回复）
            assistant_count = 0
            for msg in messages:
                content = msg.get("content", "")
                role = msg.get("role", "")
                
                if role == "assistant":
                    assistant_count += 1
                    
                    if assistant_count == 1:
                        # 第一条 assistant 回复 = 题目信息（JSON）
                        try:
                            import re
                            json_match = re.search(r'\{[^}]+\}', content)
                            if json_match:
                                question_info = json.loads(json_match.group())
                        except:
                            pass
                            
                    elif assistant_count == 2:
                        # 第二条 assistant 回复 = 解题思路
                        solution_steps = content
                        
                    elif assistant_count == 3:
                        # 第三条 assistant 回复 = 完整解析
                        full_solution = content
                        
                    elif assistant_count == 4:
                        # 第四条 assistant 回复 = 思维导图
                        mind_map = content
            
            print(f"[{request_id}] 内容分类: steps={len(solution_steps)}chars, solution={len(full_solution)}chars, mindmap={len(mind_map)}chars")
            
            # 若已渲染（含LaTeX图片），用渲染结果作为完整解析
            if full_solution_rendered:
                full_solution = full_solution_rendered
            
            # 保存到数据库
            record = SubmissionRecord(
                session_id=session_id or request_id,
                user_id=user_id,
                ocr_text=ocr_text,
                ocr_time_seconds=ocr_time,
                question_info=question_info,
                solution_steps=solution_steps,
                full_solution=full_solution,
                mind_map=mind_map,
                search_result=search_result,
                search_time_seconds=search_time,
                original_image_path=str(HISTORY_DIR / f"{request_id}.jpg"),
                rendered_svg_dir=str(HISTORY_DIR / f"svgs_{session_id or request_id}"),
            )
            
            db.add(record)
            
            # 保存 Markdown 文件（多题时按每题 session_id 区分）
            md_path = HISTORY_DIR / f"{session_id or request_id}_solution.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# 解题结果\n\n")
                f.write(f"## 解题思路\n\n{solution_steps}\n\n")
                f.write(f"## 完整解析\n\n{full_solution}\n\n")
                f.write(f"## 思维导图\n\n{mind_map}\n\n")
            
            # 保存对话历史
            for msg in messages:
                conv = ConversationHistory(
                    session_id=session_id or request_id,
                    user_id=user_id,
                    role=msg["role"],
                    content=msg["content"],
                )
                db.add(conv)
            
            db.commit()
            print(f"[{request_id}] 记录已保存，Markdown文件: {md_path}")
            
        except Exception as e:
            print(f"[{request_id}] 保存记录失败: {e}")
            import traceback
            traceback.print_exc()
            db.rollback()
        finally:
            db.close()
    
    def _cleanup(self, request_id: str):
        """清理资源"""
        if request_id in _event_threads:
            del _event_threads[request_id]
        # 保留事件队列一段时间以便客户端获取


# 全局单例
solve_pipeline = SolvePipeline()