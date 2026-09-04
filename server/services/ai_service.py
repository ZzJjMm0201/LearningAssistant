import json
import time
import re
from typing import Dict, List, Optional, Tuple, Generator
from openai import OpenAI
from server.config import APIConfig, AI_MODEL
from openai.types.chat import ChatCompletionMessageParam

# ==================== 回答风格（②：可自定义，含各自temperature） ====================
ANSWER_STYLES: Dict[str, Dict] = {
    "formal": {
        "label": "严谨规范",
        "temperature": 0.3,
        "instruction": "回答风格：严谨规范。用词准确、条理清晰、术语规范，像标准教辅解析。",
    },
    "plain": {
        "label": "通俗易懂",
        "temperature": 0.7,
        "instruction": "回答风格：通俗易懂。像老师面对面讲解，多用比喻和生活化例子把概念讲明白，可适当口语化。",
    },
    "concise": {
        "label": "简洁精炼",
        "temperature": 0.4,
        "instruction": "回答风格：简洁精炼。直奔重点，只保留关键步骤和结论，不铺垫、不啰嗦。",
    },
    "lively": {
        "label": "活泼有趣",
        "temperature": 0.9,
        "instruction": "回答风格：活泼亲切。语气轻松自然，可少量使用emoji和鼓励性话语，但内容必须保持准确。",
    },
    "dialect": {
        "label": "方言",
        "temperature": 0.85,
        "instruction": "回答风格：用地道的{方言}口吻讲解（语气、用词、口头禅都贴近{方言}本地说话方式），但专业术语、公式、数字必须保持准确，不要因为方言影响正确性。",
    },
}
DEFAULT_STYLE = "formal"

# ④ 可选择的方言名称（③ 风格选“方言”后二级选择）
DIALECTS = ["四川话", "东北话", "粤语", "上海话", "天津话", "陕西话", "河南话", "湖南话"]

# ④ 年级候选（设置中选，投给AI时可影响讲解深度）
GRADES = ["小学", "初中", "高中", "考研"]

# ==================== 重点颜色标记（⑥：[[#RRGGBB]]…[[#RRGGBB]]，深浅背景均可读） ====================
COLOR_RULES = """【重点颜色标记（重要）】
为了让重点更醒目，你可以在文字中给关键短句标色：
格式：[[#RRGGBB]]需要标记的文字[[#RRGGBB]]（前后两个标记的颜色相同；RRGGBB为十六进制颜色值，例如 [[#E53935]]易错警示[[#E53935]]）
规则：
1. 只标记关键短语或短句（30字以内），如核心结论、易错警示、重要公式结论；不要整段或大段标色，更不要全篇标色
2. 颜色必须从下面“深浅背景都可读”的色板中选择：#E53935(警示红)、#FB8C00(强调橙)、#1E88E5(要点蓝)、#43A047(正确绿)、#8E24AA(进阶紫)、#00ACC1(补充青)、#6D4C41(注意棕)
3. 不要把标记写进LaTeX公式、代码块、图片说明或JSON输出里
4. 不要嵌套使用；一个标记结束后再开始下一个"""

# ==================== 追问绘图强提示（①：追问必须能输出LaTeX/TikZ图形） ====================
ASK_DRAW_RULE = ("回答要求：直接回答学生最新问题本身，用自然语言，禁止输出JSON格式或新的问题列表（除下方允许的LaTeX代码块外，不要输出其他代码块）。"
                 "如讲解涉及函数图像、几何图形、过程示意等需要图形辅助的内容，务必在回答中输出1~3个可独立编译的```latex ... ```代码块"
                 "（使用tikzpicture/pgfplots，不要documentclass等完整文档结构），每个代码块前用一行 **图N：标题** 说明该图作用，"
                 "并把代码块放在对应讲解文字之后。若题目本身不含图形也可不输出。")


def style_instruction(style: Optional[str], dialect: str = "") -> str:
    s = ANSWER_STYLES.get(style or "")
    if not s:
        return ""
    instr = s["instruction"]
    if style == "dialect" and dialect:
        instr = instr.replace("{方言}", dialect)
    elif style == "dialect":
        instr = instr.replace("{方言}", "四川话")
    return instr


def style_temperature(style: Optional[str]) -> Optional[float]:
    s = ANSWER_STYLES.get(style or "")
    return s["temperature"] if s else None


def grade_instruction(grade: Optional[str]) -> str:
    """④ 年级设置：把讲解深度适配到学生年级（投给AI时注入）"""
    g = (grade or "").strip()
    if not g:
        return ""
    return (f"请面向【{g}】学生讲解：解释深度、举例、公式推导的详细程度都要适配{g}学生的接受能力，"
            f"不要使用超出{g}水平过多的超纲内容（除非题目本身需要）。")


class AIService:
    """AI多轮对话服务"""
    
    def __init__(self):
        self.client = OpenAI(
            api_key=APIConfig.DEEPSEEK_API_KEY,
            base_url=APIConfig.DEEPSEEK_BASE_URL
        )
        self.model = "deepseek-chat"
        # 千问（Qwen）客户端 —— 阿里云百炼 OpenAI 兼容端点（Key 在 .env）
        self.qwen_client = None
        if APIConfig.QWEN_API_KEY:
            self.qwen_client = OpenAI(
                api_key=APIConfig.QWEN_API_KEY,
                base_url=APIConfig.QWEN_BASE_URL
            )
        self.qwen_model = APIConfig.QWEN_DEFAULT_LLM
        self.temperature = AI_MODEL["temperature"]
        self.max_tokens = AI_MODEL["max_tokens"]
    
    def _get_client(self, engine: Optional[str] = None, model: Optional[str] = None):
        """按提供方选择客户端与模型（deepseek / qwen），默认deepseek
        engine: 提供方；model: 具体模型名（未指定用默认）"""
        if engine == "qwen":
            if self.qwen_client is None:
                raise RuntimeError("未配置千问API Key（请检查 .env 的 QWEN_API_KEY）")
            return self.qwen_client, model or self.qwen_model
        return self.client, model or self.model
    
    def _thinking_supported(self, engine: Optional[str]) -> bool:
        """思考模式（十一）：仅DeepSeek链路支持（千问模型未知是否支持，跳过）"""
        return (engine or "deepseek") != "qwen"
    
    def solve_problem_stream(self, ocr_text: str, search_result: Optional[str] = None, engine: Optional[str] = None, model: Optional[str] = None,
                             style: Optional[str] = None, thinking: bool = False, dialect: str = "", grade: str = "") -> Generator[Dict, None, None]:
        """
        多轮解题对话 - 流式返回各阶段结果
        
        Yields:
            Dict: {"stage": "info"|"steps"|"solution"|"mindmap"|"questions"|"thinking_chunk", "content": ...}
        style: 回答风格id（②，影响文字类阶段的temperature与措辞）
        dialect: 方言名称（③，style=dialect 时生效）
        grade: 年级（④，影响讲解深度）
        thinking: 是否开启思考模式（十一，仅作用于“完整解析”阶段，思考内容经 thinking_chunk 下发）
        """
        start_time = time.time()
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        st_temp = style_temperature(style)
        use_thinking = thinking and self._thinking_supported(engine)
        
        # 构建系统提示（含风格 + 颜色标记规则）
        system_prompt = self._build_system_prompt(ocr_text, search_result)
        if st_ins:
            system_prompt += "\n\n" + st_ins
        system_prompt += "\n\n" + COLOR_RULES
        
        # 初始化对话历史
        messages: List[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        
        # === 第一阶段：结构化提取题目信息（⑩：含年级/学科/难度/知识点/易错点/难点） ===
        info_prompt = """请分析这道题目，以JSON格式输出以下信息：
    {
        "grade": "年级（如：高一、八年级、小学三年级）",
        "subject": "学科（语文、数学、英语、物理、化学、生物、历史、地理、政治）",
        "difficulty": "难度（易、较易、中、较难、难）",
        "knowledge_points": ["知识点1", "知识点2"],
        "easy_mistakes": ["易错点1", "易错点2"],
        "difficult_points": ["难点1", "难点2"],
        "question_type": "题型（选择题、填空题、解答题、证明题等）"
    }
    只输出JSON，不要其他内容。"""
        
        messages.append({"role": "user", "content": info_prompt})
        info_response = self._call_api(messages, engine=engine, model=model)
        question_info = self._parse_json_response(info_response)
        
        yield {"stage": "info", "content": question_info}
        
        # === 第二阶段：解题思路 ===
        steps_prompt = """基于以上题目分析，请给出清晰的解题思路。
    要求：
    1. 分步骤说明，每步简洁明了
    2. 指出解题的关键突破口
    3. 200字左右即可
    4. 直接输出纯文本！禁止使用JSON格式、代码块或其他任何结构化标记，不要模仿上一轮的JSON输出
    5. 开头不要再重复题目的年级/学科/难度等信息（客户端已在顶部用标签展示）"""
        if st_ins:
            steps_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": info_response})
        messages.append({"role": "user", "content": steps_prompt})
        # 流式输出解题思路
        accumulated_steps = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                           temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_steps = evt["content"]
                yield {"stage": "steps_chunk", "content": accumulated_steps}
        steps_response = accumulated_steps
        
        yield {"stage": "steps", "content": steps_response}
        
        # === 第三阶段：完整解析（流式输出；可开思考模式；末尾不再附年级/学科/难度——⑩） ===
        solution_prompt = """请给出完整的解题过程和答案。
    要求：
    1. 步骤完整，逻辑清晰
    2. 使用LaTeX语法编写数学公式
    3. 暂不要生成LaTeX/TikZ图形代码，稍后会有专门环节为本题补充图形
    4. 结尾不要再附“学科/知识点/题目难度”之类的汇总（客户端已在顶部用标签展示）"""
        if st_ins:
            solution_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": steps_response})
        messages.append({"role": "user", "content": solution_prompt})
        
        # 流式输出完整解析（思考模式：先流式下发思维链）
        accumulated_solution = ""
        thinking_accumulated = ""
        if use_thinking:
            yield {"stage": "thinking_start", "content": ""}
        
        def _on_reasoning(accum: str):
            nonlocal thinking_accumulated
            thinking_accumulated = accum
            yield {"stage": "thinking_chunk", "content": accum}
        
        for evt in self._call_api_streaming(messages, max_tokens=8000, engine=engine, model=model,
                                            temperature=st_temp, thinking=use_thinking,
                                            on_reasoning=_on_reasoning):
            if evt["stage"] == "thinking_chunk":
                yield evt
            else:
                accumulated_solution = evt["content"]
                yield {"stage": "solution_chunk", "content": accumulated_solution}
        
        solution_response = accumulated_solution
        
        yield {"stage": "solution", "content": solution_response}
        
        # === 第三点五阶段：生成LaTeX辅助图形（多图，单独一轮对话） ===
        latex_prompt = """请为这道题生成有助于学生理解的LaTeX/TikZ图形代码。
要求：
1. 生成1-6个图形，每个图形单独一个```latex ... ```代码块
2. 图形按解题步骤顺序排列，覆盖：题目情景图、关键几何关系、函数图像、过程示意图等
3. 每个代码块前用一行文字说明该图的作用（如：**图1：题目情景示意**）
4. 只使用tikz/pgfplots，代码要能在xelatex直接编译（不要documentclass等完整文档结构）
5. 图形要标注关键点、线、面的名称，越直观越好"""
        
        messages.append({"role": "assistant", "content": solution_response})
        messages.append({"role": "user", "content": latex_prompt})
        # ② LaTeX图形生成改为流式（客户端显示“图形正在生成”占位）
        latex_accumulated = ""
        for evt in self._call_api_streaming(messages, max_tokens=4000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                latex_accumulated = evt["content"]
                yield {"stage": "latex_chunk", "content": latex_accumulated}
        latex_response = latex_accumulated
        
        yield {"stage": "latex_extras", "content": latex_response}
        
        # === 第四阶段：思维导图 ===
        mindmap_prompt = """请用纯文本缩进格式，为这道题生成一个解题思维导图。
    格式示例：
    核心概念：XXX
    └── 知识点一
        ├── 关键公式
        └── 应用条件
    └── 知识点二
        └── 解题技巧"""
        if st_ins:
            mindmap_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": solution_response})
        messages.append({"role": "user", "content": mindmap_prompt})
        # 流式输出思维导图
        accumulated_mindmap = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_mindmap = evt["content"]
                yield {"stage": "mindmap_chunk", "content": accumulated_mindmap}
        mindmap_response = accumulated_mindmap
        
        yield {"stage": "mindmap", "content": mindmap_response}
        
        # === 第五阶段：预判问题（同时提供答案，便于客户端折叠展示） ===
        questions_prompt = """请生成3个学生可能会问的后续问题，并同时给出每个问题的简要答案。
以JSON数组格式输出，每项包含 question 和 answer 两个字段：
[
  {"question": "问题1", "answer": "简要答案1"},
  {"question": "问题2", "answer": "简要答案2"},
  {"question": "问题3", "answer": "简要答案3"}
]
答案要准确、简洁（50字以内）。只输出JSON数组。"""
        
        messages.append({"role": "assistant", "content": mindmap_response})
        messages.append({"role": "user", "content": questions_prompt})
        questions_response = self._call_api(messages, engine=engine, model=model)
        suggested_questions = self._parse_json_response(questions_response)
        
        yield {"stage": "questions", "content": suggested_questions}
        
        # 记录总时间
        elapsed = time.time() - start_time
        yield {"stage": "complete", "content": {"total_time": elapsed, "messages": messages}}
    
    def generate_ai_report(self, stats_summary: str, engine: Optional[str] = None, model: Optional[str] = None,
                           style: Optional[str] = None, dialect: str = "", grade: str = "") -> str:
        """生成AI版学情报告（非流式，兼容旧调用）"""
        return "".join(self.generate_ai_report_stream(stats_summary, engine=engine, model=model, style=style, dialect=dialect, grade=grade))

    def generate_ai_report_stream(self, stats_summary: str, engine: Optional[str] = None, model: Optional[str] = None,
                                  style: Optional[str] = None, dialect: str = "", grade: str = ""):
        """生成AI版学情报告（流式，逐步yield增量文本；旧客户端按增量累加）"""
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        prompt = f"""你是一位经验丰富的教育顾问。请根据以下学生的学习数据，生成一份温暖的学情报告。

    {stats_summary}

    报告要求：
    1. 用温暖鼓励的语气
    2. 分析学生的知识掌握情况
    3. 指出需要加强的领域
    4. 给出具体的学习建议
    5. 500字左右"""
        if st_ins:
            prompt += "\n" + st_ins
        prompt += "\n\n" + COLOR_RULES
        
        messages: List[ChatCompletionMessageParam] = [
            {"role": "system", "content": "你是一位经验丰富的教育顾问，擅长用温暖鼓励的方式与学生沟通。"},
            {"role": "user", "content": prompt}
        ]
        
        prev_sent = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=style_temperature(style)):
            if evt["stage"] != "content":
                continue
            chunk = evt["content"]
            # 回调返回累积全文，只yield新增部分，客户端累加后不会重复
            delta = chunk[len(prev_sent):] if chunk.startswith(prev_sent) else chunk
            prev_sent = chunk
            if delta:
                yield delta
    
    def generate_knowledge_extension(self, ocr_text: str, search_result: Optional[str] = None, engine: Optional[str] = None, model: Optional[str] = None,
                                     style: Optional[str] = None, dialect: str = "", grade: str = "") -> Generator[Dict, None, None]:
        """
        知识延伸多轮对话
        重点：总结归纳 + 易错点 + 知识拓展 + 延伸问题
        """
        start_time = time.time()
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        st_temp = style_temperature(style)
        system_prompt = self._build_system_prompt(ocr_text, search_result)
        if st_ins:
            system_prompt += "\n\n" + st_ins
        system_prompt += "\n\n" + COLOR_RULES
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
        info_response = self._call_api(messages, engine=engine, model=model)
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
        if st_ins:
            mistakes_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": info_response})
        messages.append({"role": "user", "content": mistakes_prompt})
        # 流式输出易错点详解
        accumulated_mistakes = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_mistakes = evt["content"]
                yield {"stage": "mistakes_chunk", "content": accumulated_mistakes}
        mistakes_detail = accumulated_mistakes
        
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
        if st_ins:
            extension_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": mistakes_detail})
        messages.append({"role": "user", "content": extension_prompt})
        # 流式输出知识拓展
        accumulated_extension = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_extension = evt["content"]
                yield {"stage": "extension_chunk", "content": accumulated_extension}
        extension_content = accumulated_extension
        
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
        questions_response = self._call_api(messages, engine=engine, model=model)
        questions = self._parse_json_response(questions_response)
        yield {"stage": "questions", "content": questions}
        
        elapsed = time.time() - start_time
        yield {"stage": "complete", "content": {"total_time": elapsed, "messages": messages}}
    
    def continue_conversation(self, messages: List[ChatCompletionMessageParam], user_question: str, engine: Optional[str] = None, model: Optional[str] = None,
                              style: Optional[str] = None, dialect: str = "", grade: str = "") -> str:
        """多轮对话 - 继续提问（非流式）"""
        system_add = ("你是学习助手。" + ASK_DRAW_RULE + "\n" + COLOR_RULES)
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        if st_ins:
            system_add += "\n" + st_ins
        messages.append({"role": "system", "content": system_add})
        messages.append({"role": "user", "content": user_question})
        return self._call_api(messages, engine=engine, model=model, temperature=style_temperature(style))

    def continue_conversation_stream(self, messages: List[ChatCompletionMessageParam], user_question: str, engine: Optional[str] = None, model: Optional[str] = None,
                                     style: Optional[str] = None, dialect: str = "", grade: str = "") -> Generator[str, None, None]:
        """多轮对话 - 继续提问（流式，逐chunk累积文本）"""
        system_add = ("你是学习助手。" + ASK_DRAW_RULE + "\n" + COLOR_RULES)
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        if st_ins:
            system_add += "\n" + st_ins
        messages.append({"role": "system", "content": system_add})
        messages.append({"role": "user", "content": user_question})
        accumulated = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=style_temperature(style)):
            if evt["stage"] == "content":
                accumulated = evt["content"]
                yield accumulated

    def generate_response(self, prompt: str, engine: Optional[str] = None, model: Optional[str] = None) -> str:
        """通用单轮生成（GeoGebra命令等）"""
        messages = [
            {"role": "system", "content": "你是一个GeoGebra命令生成专家，直接输出可以在GeoGebra输入栏中执行的命令，每行一个命令，不要任何解释文字。"},
            {"role": "user", "content": prompt},
        ]
        return self._call_api(messages, max_tokens=2000, engine=engine, model=model)

    def fix_latex(self, latex_code: str, error_text: str, engine: Optional[str] = None, model: Optional[str] = None) -> str:
        """LaTeX编译失败时，把关键报错发给AI修复代码"""
        prompt = f"""以下LaTeX/TikZ代码编译失败，请修复它。

代码：
```latex
{latex_code}
```

编译报错（关键信息）：
```
{error_text[:500]}
```

要求：
1. 只输出修复后的完整LaTeX代码（放在```latex代码块中），不要任何解释文字
2. 保持图形的意图不变
3. 如果错误是缺少宏包，请添加需要的\\usepackage"""
        messages = [
            {"role": "system", "content": "你是LaTeX/TikZ绘图代码修复专家。"},
            {"role": "user", "content": prompt},
        ]
        response = self._call_api(messages, max_tokens=3000, engine=engine, model=model) or ""
        m = re.search(r'```latex\s*\n(.*?)\n```', response, re.DOTALL)
        if m:
            response = m.group(1)
        else:
            response = re.sub(r'```[\w]*\n?', '', response)
            response = re.sub(r'```', '', response)
        return response.strip()
    
    def _call_api(self, messages, max_tokens=None, engine=None, model=None, temperature: Optional[float] = None):
        """调用AI API (非流式)"""
        client, m = self._get_client(engine, model)
        temp = temperature if temperature is not None else self.temperature
        print(f"[AI] 调用API，引擎={engine or 'deepseek'}，模型={m}，消息数={len(messages)}")
        for attempt in range(3):
            try:
                print(f"[AI] 尝试 {attempt + 1}/3...")
                response = client.chat.completions.create(
                    model=m,
                    messages=messages,
                    temperature=temp,
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
    
    def _call_api_streaming(self, messages, max_tokens=None, engine=None, model=None,
                            temperature: Optional[float] = None, thinking: bool = False,
                            on_reasoning=None):
        """
        调用AI API (流式) - 逐chunk生成
        用于实现实时流式输出体验
        
        Args:
            temperature: 覆盖默认温度（回答风格温度）
            thinking: 思考模式（十一；仅DeepSeek链路；不支持的模型自动降级重试）
            on_reasoning: 思考模式回调，收到累计思维链文本时调用（用于转发给客户端）
        
        Yields:
            Dict[str, str]: {"stage": "content", "content": 累积文本}
            或（仅thinking）: {"stage": "thinking", "content": 累积思维链}（内容阶段开始后不再yield thinking）
        """
        client, model = self._get_client(engine, model)
        temp = temperature if temperature is not None else self.temperature
        print(f"[AI-Stream] 开始流式调用，引擎={engine or 'deepseek'}，模型={model}，消息数={len(messages)}")
        thinking_on = thinking
        for attempt in range(4):
            try:
                print(f"[AI-Stream] 尝试 {attempt + 1}/4...")
                kwargs = dict(
                    model=model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tokens or self.max_tokens,
                    stream=True,
                )
                if thinking_on:
                    kwargs["reasoning_effort"] = "high"
                    kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
                response = client.chat.completions.create(**kwargs)
                reasoning_content = ""
                content = ""
                thinking_done = False
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_content += delta.reasoning_content
                            thinking_done = True
                            if on_reasoning is not None:
                                # 思维链期间不下发正文
                                cb = on_reasoning(reasoning_content)
                                if cb is not None:
                                    try:
                                        for evt in cb:
                                            yield evt
                                    except TypeError:
                                        pass
                        elif delta.content:
                            content += delta.content
                            yield {"stage": "content", "content": content}  # 增量返回完整累积内容用于淡入式显示
                if thinking_on and reasoning_content and not content:
                    # 只有思维链、没有正文的响应也要正常结束
                    pass
                print(f"[AI-Stream] 流式调用成功，总长度={len(content)}")
                return
            except Exception as e:
                print(f"[AI-Stream] 第{attempt + 1}次尝试失败: {e}")
                err = str(e)
                # 思考模式不被该模型支持（400/参数错误）：自动降级为普通模式重试
                if thinking_on and ("400" in err or "thinking" in err.lower() or "reasoning" in err.lower()):
                    print("[AI-Stream] 模型不支持思考模式，自动降级为普通模式")
                    thinking_on = False
                    continue
                if attempt == 3:
                    raise
                time.sleep(1)
        return
    
    def recognize_image_with_vision(self, image_path: str, model: Optional[str] = None, prompt: Optional[str] = None) -> Tuple[str, float]:
        """使用视觉模型识别图片（OCR/图表描述）
        模型名以 deepseek 开头 → DeepSeek客户端；否则走千问客户端
        ③ 提示词：手写内容转 *斜体* Markdown；流程图/统计图用自然语言描述（不输出纯文本）
        """
        import base64 as _b64
        start = time.time()
        try:
            model = model or APIConfig.QWEN_DEFAULT_VISION
            is_deepseek_model = str(model).lower().startswith("deepseek")
            client = self.client if is_deepseek_model else self.qwen_client
            if client is None:
                return f"OCR识别失败：未配置{'DeepSeek' if is_deepseek_model else '千问'}API Key", round(time.time() - start, 2)
            with open(image_path, "rb") as f:
                img_b64 = _b64.b64encode(f.read()).decode("ascii")
            prompt = prompt or (
                "请识别这张图片中的全部文字并完整输出（保持原有顺序和格式）。"
                "③ 手写的内容（包括手写解题过程、批注）请在输出中改用 *斜体*（Markdown斜体，单个星号包裹）表示，以与印刷体区分；印刷体保持原样。"
                "如果图片中包含流程图、统计图、几何图形等非纯文字内容，请用自然语言描述其内容，不要尝试把图形转成文字列表。"
                "只输出识别/描述结果，不要任何额外解释。"
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    ],
                }],
                temperature=0.1,
                max_tokens=5000,
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                return "", round(time.time() - start, 2)
            return text, round(time.time() - start, 2)
        except Exception as e:
            print(f"[VisionOCR] 失败: {e}")
            return "", round(time.time() - start, 2)
    
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
