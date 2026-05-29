import json
import time
import re
from typing import Dict, List, Optional, Tuple, Generator
from openai import OpenAI
from server.config import APIConfig, AI_MODEL
from openai.types.chat import ChatCompletionMessageParam

class AIService:
    """AI多轮对话服务"""
    
    def __init__(self):
        self.client = OpenAI(
            api_key=APIConfig.DEEPSEEK_API_KEY,
            base_url=APIConfig.DEEPSEEK_BASE_URL
        )
        self.model = "deepseek-chat"
        self.temperature = AI_MODEL["temperature"]
        self.max_tokens = AI_MODEL["max_tokens"]
    
    def solve_problem_stream(self, ocr_text: str, search_result: Optional[str] = None) -> Generator[Dict, None, None]:
        """
        多轮解题对话 - 流式返回各阶段结果
        
        Yields:
            Dict: {"stage": "info"|"steps"|"solution"|"mindmap"|"questions", "content": ...}
        """
        start_time = time.time()
        
        # 构建系统提示
        system_prompt = self._build_system_prompt(ocr_text, search_result)
        
        # 初始化对话历史
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        
        # === 第一阶段：结构化提取题目信息 ===
        info_prompt = """请分析这道题目，以JSON格式输出以下信息：
    {
        "grade": "年级（如：高一、八年级、小学三年级）",
        "subject": "学科（语文、数学、英语、物理、化学、生物、历史、地理、政治）",
        "difficulty": "难度（易、较易、中、较难、难）",
        "knowledge_points": ["知识点1", "知识点2"],
        "key_points": ["重点1", "重点2"],
        "easy_mistakes": ["易错点1", "易错点2"],
        "question_type": "题型（选择题、填空题、解答题、证明题等）"
    }
    只输出JSON，不要其他内容。"""
        
        messages.append({"role": "user", "content": info_prompt})
        info_response = self._call_api(messages)
        question_info = self._parse_json_response(info_response)
        
        yield {"stage": "info", "content": question_info}
        
        # === 第二阶段：解题思路 ===
        steps_prompt = """基于以上题目分析，请给出清晰的解题思路。
    要求：
    1. 分步骤说明，每步简洁明了
    2. 指出解题的关键突破口
    3. 200字左右即可"""
        
        messages.append({"role": "assistant", "content": info_response})
        messages.append({"role": "user", "content": steps_prompt})
        steps_response = self._call_api(messages)
        
        yield {"stage": "steps", "content": steps_response}
        
        # === 第三阶段：完整解析与LaTeX图形（流式输出） ===
        solution_prompt = """请给出完整的解题过程和答案。
    要求：
    1. 步骤完整，逻辑清晰
    2. 使用LaTeX语法编写数学公式
    3. 如果需要绘制图形辅助理解，请使用LaTeX的tikz/pgfplots包编写图形代码，嵌入Markdown代码块中（```latex ... ```）
    4. 图形要标注关键点、线、面的名称
    5. 最后附上：
    **学科：**[学科]
    **知识点：**[用顿号隔开]
    **题目难度：**[难度]"""
        
        messages.append({"role": "assistant", "content": steps_response})
        messages.append({"role": "user", "content": solution_prompt})
        
        # 流式输出完整解析
        accumulated_solution = ""
        for chunk in self._call_api_streaming(messages, max_tokens=8000):
            accumulated_solution = chunk
            yield {"stage": "solution_chunk", "content": accumulated_solution}
        
        solution_response = accumulated_solution
        
        yield {"stage": "solution", "content": solution_response}
        
        # === 第四阶段：思维导图 ===
        mindmap_prompt = """请用纯文本缩进格式，为这道题生成一个解题思维导图。
    格式示例：
    核心概念：XXX
    └── 知识点一
        ├── 关键公式
        └── 应用条件
    └── 知识点二
        └── 解题技巧"""
        
        messages.append({"role": "assistant", "content": solution_response})
        messages.append({"role": "user", "content": mindmap_prompt})
        mindmap_response = self._call_api(messages)
        
        yield {"stage": "mindmap", "content": mindmap_response}
        
        # === 第五阶段：预判问题 ===
        questions_prompt = """请生成3个学生可能会问的后续问题，以JSON数组格式输出：
    ["问题1", "问题2", "问题3"]
    只输出JSON数组。"""
        
        messages.append({"role": "assistant", "content": mindmap_response})
        messages.append({"role": "user", "content": questions_prompt})
        questions_response = self._call_api(messages)
        suggested_questions = self._parse_json_response(questions_response)
        
        yield {"stage": "questions", "content": suggested_questions}
        
        # 记录总时间
        elapsed = time.time() - start_time
        yield {"stage": "complete", "content": {"total_time": elapsed, "messages": messages}}
    
    def generate_ai_report(self, stats_summary: str) -> str:
        """生成AI版学情报告"""
        prompt = f"""你是一位经验丰富的教育顾问。请根据以下学生的学习数据，生成一份温暖的学情报告。

    {stats_summary}

    报告要求：
    1. 用温暖鼓励的语气
    2. 分析学生的知识掌握情况
    3. 指出需要加强的领域
    4. 给出具体的学习建议
    5. 500字左右"""
        
        messages: List[ChatCompletionMessageParam] = [
            {"role": "system", "content": "你是一位经验丰富的教育顾问，擅长用温暖鼓励的方式与学生沟通。"},
            {"role": "user", "content": prompt}
        ]
        
        response = self._call_api(messages)
        
        return response
    
    def generate_knowledge_extension(self, ocr_text: str, search_result: Optional[str] = None) -> Generator[Dict, None, None]:
        """
        知识延伸多轮对话
        重点：总结归纳 + 易错点 + 知识拓展 + 延伸问题
        """
        start_time = time.time()
        system_prompt = self._build_system_prompt(ocr_text, search_result)
        messages = [{"role": "system", "content": system_prompt}]
        
        # ===== 第一阶段：知识点总结与易错点 =====
        summary_prompt = """请分析这道题目，以JSON格式输出：
    {
        "core_concept": "核心概念（一句话）",
        "knowledge_summary": "知识点总结（100字内）",
        "easy_mistakes": ["易错点1及避坑方法", "易错点2及避坑方法", "易错点3及避坑方法"],
        "extension_topics": ["可延伸的知识点1", "可延伸的知识点2"],
        "difficulty": "易/较易/中/较难/难",
        "subject": "学科"
    }
    只输出JSON。"""
        
        messages.append({"role": "user", "content": summary_prompt})
        info_response = self._call_api(messages)
        info = self._parse_json_response(info_response)
        yield {"stage": "info", "content": info}
        
        # ===== 第二阶段：易错点详解 =====
        mistakes_prompt = f"""针对以下易错点，逐一给出详细解释和避坑方法：
    {json.dumps(info.get('easy_mistakes', []), ensure_ascii=False) if isinstance(info, dict) else []}
    要求：
    1. 每个易错点单独一段
    2. 说明为什么容易错
    3. 给出正确的思路
    4. 用具体例子说明"""
        
        messages.append({"role": "assistant", "content": info_response})
        messages.append({"role": "user", "content": mistakes_prompt})
        mistakes_detail = self._call_api(messages)
        yield {"stage": "mistakes", "content": mistakes_detail}
        
        # ===== 第三阶段：知识拓展 =====
        core_concept = info.get('core_concept', '') if isinstance(info, dict) else ''
        extension_topics = info.get('extension_topics', []) if isinstance(info, dict) else []
        
        extension_prompt = f"""请针对以下核心概念进行知识拓展：
核心概念：{core_concept}
可延伸知识点：{extension_topics}

要求：
1. 从基础概念延伸到高级应用
2. 包含有趣的科普内容或实际应用场景
3. 如果涉及公式，用LaTeX格式
4. 300字左右"""
        
        messages.append({"role": "assistant", "content": mistakes_detail})
        messages.append({"role": "user", "content": extension_prompt})
        extension_content = self._call_api(messages)
        yield {"stage": "extension", "content": extension_content}
        
        # ===== 第四阶段：延伸问题 =====
        questions_prompt = """请生成3个延伸思考问题，以JSON数组格式输出：
    ["问题1", "问题2", "问题3"]
    这些问题应该：
    1. 引导深入思考
    2. 联系其他知识点
    3. 有一定挑战性
    只输出JSON数组。"""
        
        messages.append({"role": "assistant", "content": extension_content})
        messages.append({"role": "user", "content": questions_prompt})
        questions_response = self._call_api(messages)
        questions = self._parse_json_response(questions_response)
        yield {"stage": "questions", "content": questions}
        
        elapsed = time.time() - start_time
        yield {"stage": "complete", "content": {"total_time": elapsed, "messages": messages}}
    
    def continue_conversation(self, messages: List[ChatCompletionMessageParam], user_question: str) -> str:
        """多轮对话 - 继续提问"""
        messages.append({"role": "user", "content": user_question})
        return self._call_api(messages)
    
    def _call_api(self, messages, max_tokens=None):
        """调用AI API (非流式)"""
        print(f"[AI] 调用API，模型={self.model}，消息数={len(messages)}")
        for attempt in range(3):
            try:
                print(f"[AI] 尝试 {attempt + 1}/3...")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    stream=False,
                )
                content = response.choices[0].message.content
                print(f"[AI] 调用成功，返回长度={len(content) if content else 0}")
                return content if content is not None else ""
            except Exception as e:
                print(f"[AI] 第{attempt + 1}次尝试失败: {e}")
                if attempt == 2:
                    raise
                time.sleep(1)
        return ""
    
    def _call_api_streaming(self, messages, max_tokens=None):
        """
        调用AI API (流式) - 逐chunk生成
        用于实现实时流式输出体验
        
        Yields:
            str: 每个chunk的文本内容
        """
        print(f"[AI-Stream] 开始流式调用，模型={self.model}，消息数={len(messages)}")
        for attempt in range(3):
            try:
                print(f"[AI-Stream] 尝试 {attempt + 1}/3...")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    stream=True,
                )
                reasoning_content = ""
                content = ""
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_content += delta.reasoning_content
                        elif delta.content:
                            content += delta.content
                            yield content  # 增量返回完整累积内容用于淡入式显示
                print(f"[AI-Stream] 流式调用成功，总长度={len(content)}")
                return
            except Exception as e:
                print(f"[AI-Stream] 第{attempt + 1}次尝试失败: {e}")
                if attempt == 2:
                    raise
                time.sleep(1)
        return
    
    def _build_system_prompt(self, ocr_text: str, search_result: Optional[str] = None) -> str:
        """构建系统提示"""
        prompt = """你是一位全科精通的教师，正在帮助学生解答学习问题。"""
        
        if search_result:
            prompt += f"\n\n以下是大题库中搜索到的相同或相似题目，供参考：\n{search_result}"
        
        prompt += f"\n\n学生需要解答的题目是：\n{ocr_text}"
        
        return prompt
    
    def _parse_json_response(self, response: str) -> Dict | List:
        """安全解析AI返回的JSON"""
        # 尝试提取JSON部分
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            response = json_match.group(1)
        else:
            # 尝试直接找JSON数组或对象
            json_match = re.search(r'(\[.*\]|\{.*\})', response, re.DOTALL)
            if json_match:
                response = json_match.group(1)
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # 返回原始文本作为fallback
            return {"raw": response}
    
    def _stream_to_completion(self, messages: List[ChatCompletionMessageParam], max_tokens: Optional[int] = None) -> str:
        """流式收集完整响应（内部使用）"""
        # 如果后端不支持真实流式接口，降级为一次性获取完整响应
        full_response = ""
        for chunk in self._call_api_stream(messages, max_tokens):
            if chunk:
                full_response += chunk
        return full_response

    def _call_api_stream(self, messages: List[ChatCompletionMessageParam], max_tokens: Optional[int] = None):
        """
        提供流式接口的降级实现：如果底层SDK/后端支持stream=True，
        可在此处实现真正的增量yield。当前实现作为回退，
        直接调用一次性接口并把结果作为单个chunk返回，避免调用方出错。
        """
        yield self._call_api(messages, max_tokens=max_tokens)

# 全局单例
ai_service = AIService()