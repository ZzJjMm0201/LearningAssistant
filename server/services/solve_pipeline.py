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

from server.config import HISTORY_DIR
from server.database.models import SessionLocal, SubmissionRecord, ConversationHistory
from server.services.ocr_service import ocr_service
from server.services.search_service import search_service
from server.services.ai_service import ai_service
from server.utils.latex_processor import process_latex_blocks, extract_question_info_from_solution

from typing import Dict, Generator, Optional, Union

# SSE事件队列 - 存储每个request_id的事件流
_event_queues: Dict[str, deque] = {}
_event_threads: Dict[str, threading.Thread] = {}
# 用户确认等待标志
_pending_confirm: Dict[str, threading.Event] = {}

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
        self.db = SessionLocal()
    
    def start_solve(self, image_path: Path, session_id: Optional[str] = None, base_host: Optional[str] = None, user_id: Optional[int] = None) -> str:
        # 使用 session_id 作为 request_id，如果不提供则生成新ID
        request_id = session_id or str(uuid.uuid4())
        
        _event_queues[request_id] = deque()
        
        thread = threading.Thread(
            target=self._solve_worker,
            args=(request_id, image_path, session_id, base_host, user_id)
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
    
    def _emit_event(self, request_id: str, stage: str, content):
        """发送事件到队列"""
        queue = _event_queues.get(request_id)
        if queue is not None:
            queue.append({"stage": stage, "content": content})
    
    def confirm_continue(self, request_id: str):
        """用户确认OCR结果后，继续流程"""
        event = _pending_confirm.get(request_id)
        if event:
            event.set()
            return True
        return False
    
    def cancel_solve(self, request_id: str):
        """用户取消后，清理并结束"""
        self._emit_event(request_id, "info", "用户已取消")
        self._emit_event(request_id, "complete", {"request_id": request_id, "cancelled": True})
        event = _pending_confirm.get(request_id)
        if event:
            event.set()  # 让worker线程继续执行并退出
        self._cleanup(request_id)
    
    def _solve_worker(self, request_id: str, image_path: Path, session_id: Optional[str] = None, base_host: Optional[str] = None, user_id: Optional[int] = None):
        """后台解题工作线程"""
        print(f"[{request_id}] ========== 解题流水线启动 ==========\n")
        print(f"[{request_id}] 图片路径: {image_path}")
        print(f"[{request_id}] 图片存在: {image_path.exists()}, 大小: {image_path.stat().st_size} bytes")
        try:
            # ========== 阶段1: OCR识别 ==========
            print(f"[{request_id}] 开始OCR识别...")
            self._emit_event(request_id, "info", "正在识别题目文字...")
            
            ocr_text, ocr_time, ocr_source = ocr_service.recognize(str(image_path))
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
            
            # ========== 等待用户确认OCR结果 ==========
            self._emit_event(request_id, "waiting_confirm", {
                "text": ocr_text
            })
            confirm_event = threading.Event()
            _pending_confirm[request_id] = confirm_event
            # 等待最多30秒用户确认
            if not confirm_event.wait(timeout=30):
                # 超时未确认，继续流程（等同于确认）
                print(f"[{request_id}] OCR确认超时，自动继续")
            else:
                print(f"[{request_id}] 用户已确认OCR结果")
            _pending_confirm.pop(request_id, None)
            
            # ========== 阶段2: 题库搜索 ==========
            self._emit_event(request_id, "info", "正在搜索题库...")
            
            search_result, search_time = search_service.search(ocr_text)
            
            if search_result:
                self._emit_event(request_id, "search_complete", {
                    "found": True,
                    "time": search_time
                })
            else:
                self._emit_event(request_id, "search_complete", {
                    "found": False,
                    "time": search_time
                })
            
            # ========== 阶段3: AI多轮对话（流式） ==========
            self._emit_event(request_id, "info", "AI正在分析题目...")
            solution_content = ""
            solution_chunks = []  # 存储每个流式chunk
            latex_extras_content = ""  # 补充的LaTeX图形代码
            
            # 使用流式处理AI对话的各个阶段
            for event in ai_service.solve_problem_stream(ocr_text, search_result):
                stage = event["stage"]
                content = event["content"]
                
                if stage == "info":
                    self._emit_event(request_id, "question_info", content)
                elif stage == "steps_chunk":
                    # 流式chunk: content 是累积到当前的完整文本
                    self._emit_event(request_id, "solution_steps_chunk", content)
                elif stage == "steps":
                    self._emit_event(request_id, "solution_steps", content)
                elif stage == "solution_chunk":
                    # 流式chunk: content 是累积到当前的完整文本
                    solution_content = content
                    solution_chunks.append(content)
                    self._emit_event(request_id, "solution_chunk", content)
                elif stage == "solution":
                    solution_content = content
                    self._emit_event(request_id, "solution", content)
                elif stage == "latex_extras":
                    # 补充的LaTeX图形代码（可多个），随后统一渲染
                    latex_extras_content = content
                    self._emit_event(request_id, "info", "正在生成图解...")
                elif stage == "mindmap_chunk":
                    # 流式chunk: content 是累积到当前的完整文本（已规范化围栏）
                    self._emit_event(request_id, "mindmap_chunk", _normalize_mindmap(content))
                elif stage == "mindmap":
                    self._emit_event(request_id, "mindmap", _normalize_mindmap(content))
                elif stage == "questions":
                    self._emit_event(request_id, "suggested_questions", content)
                elif stage == "complete":
                    # 记录总耗时
                    total_time = content.get("total_time", 0)
                    messages = content.get("messages", [])
                    
                    self._emit_event(request_id, "info", f"AI分析完成，耗时{total_time:.1f}s")
                    
                    # ========== 阶段4: LaTeX图形渲染 ==========
                    if solution_content:
                        print(f"[{request_id}] 开始渲染图形...")
                        svg_dir = HISTORY_DIR / f"svgs_{request_id}"
                        svg_dir.mkdir(exist_ok=True)
                        
                        self._emit_event(request_id, "info", "正在渲染图形...")
                        
                        import re
                        # 动态构造图片URL：优先使用请求的Host头，回退到server_ip.txt
                        host = (base_host or "").strip()
                        if not host:
                            try:
                                ip_file = Path(__file__).resolve().parent.parent.parent / "server_ip.txt"
                                host = ip_file.read_text(encoding="utf-8").strip()
                            except Exception:
                                host = "127.0.0.1:8000"
                        host = host.removeprefix("http://").removeprefix("https://").rstrip("/")
                        base_url = f"http://{host}/static/svgs_{request_id}"
                        
                        def _rewrite_img_urls(text: str) -> str:
                            return re.sub(
                                r'!\[([^\]]*)\]\((diagram_[^)]+\.(?:svg|png))\)',
                                rf'![\1]({base_url}/\2)',
                                text
                            )
                        
                        # 1) 渲染完整解析中可能出现的LaTeX块
                        processed_solution = process_latex_blocks(str(solution_content), svg_dir)
                        processed_solution = _rewrite_img_urls(processed_solution)
                        
                        # 2) 渲染补充图解（AI单独一轮生成，可能多个）
                        if latex_extras_content:
                            processed_extras = process_latex_blocks(str(latex_extras_content), svg_dir)
                            processed_extras = _rewrite_img_urls(processed_extras)
                            final_solution = (
                                processed_solution
                                + "\n\n---\n\n## 📐 图解辅助\n\n"
                                + processed_extras
                            )
                        else:
                            final_solution = processed_solution
                        
                        self._emit_event(request_id, "solution_rendered", final_solution)
                        
                        # 提取题目结构化信息
                        question_info = extract_question_info_from_solution(str(solution_content))
                        self._emit_event(request_id, "question_info", question_info)
                    
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
                    )
                    
                    self._emit_event(request_id, "complete", {
                        "request_id": request_id,
                        "total_time": total_time
                    })
                    break
                
            
        except Exception as e:
            print(f"[{request_id}] 解题流程异常: {e}")
            import traceback
            traceback.print_exc()
            self._emit_event(request_id, "error", f"处理异常: {str(e)}")
            self._emit_event(request_id, "complete", None)
        finally:
            self._cleanup(request_id)
    
    def start_knowledge_extension(self, image_path: Path, session_id: Optional[str] = None, user_id: Optional[int] = None) -> str:
        """启动知识延伸流程"""
        request_id = session_id or str(uuid.uuid4())
        _event_queues[request_id] = deque()
        
        thread = threading.Thread(
            target=self._extension_worker,
            args=(request_id, image_path, session_id, user_id)
        )
        thread.daemon = True
        _event_threads[request_id] = thread
        thread.start()
        return request_id

    def _extension_worker(self, request_id: str, image_path: Path, session_id: Optional[str] = None, user_id: Optional[int] = None):
        """知识延伸工作线程"""
        print(f"[{request_id}] ========== 知识延伸流程启动 ==========")
        
        try:
            # OCR
            self._emit_event(request_id, "info", "正在识别内容...")
            ocr_text, ocr_time, ocr_source = ocr_service.recognize(str(image_path))
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
            
            for event in ai_service.generate_knowledge_extension(ocr_text):
                stage = event["stage"]
                content = event["content"]
                
                if stage == "info":
                    self._emit_event(request_id, "question_info", content)
                elif stage == "mistakes_chunk":
                    # 流式chunk: content 是累积到当前的完整文本
                    self._emit_event(request_id, "mistakes_chunk", content)
                elif stage == "mistakes":
                    self._emit_event(request_id, "mistakes", content)
                elif stage == "extension_chunk":
                    # 流式chunk: content 是累积到当前的完整文本
                    self._emit_event(request_id, "extension_chunk", content)
                elif stage == "extension":
                    self._emit_event(request_id, "extension", content)
                elif stage == "questions":
                    self._emit_event(request_id, "suggested_questions", content)
                elif stage == "complete":
                    total_time = content.get("total_time", 0)
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
                messages: list, user_id: Optional[int] = None):
        """保存解题记录到数据库"""
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
                rendered_svg_dir=str(HISTORY_DIR / f"svgs_{request_id}"),
            )
            
            self.db.add(record)
            
            # 保存 Markdown 文件
            md_path = HISTORY_DIR / f"{request_id}_solution.md"
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
                self.db.add(conv)
            
            self.db.commit()
            print(f"[{request_id}] 记录已保存，Markdown文件: {md_path}")
            
        except Exception as e:
            print(f"[{request_id}] 保存记录失败: {e}")
            import traceback
            traceback.print_exc()
            self.db.rollback()
    
    def _cleanup(self, request_id: str):
        """清理资源"""
        if request_id in _event_threads:
            del _event_threads[request_id]
        # 保留事件队列一段时间以便客户端获取


# 全局单例
solve_pipeline = SolvePipeline()