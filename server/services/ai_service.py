import json
import time
import re
from typing import Dict, List, Optional, Tuple, Generator
from openai import OpenAI
from server.config import APIConfig, AI_MODEL
from openai.types.chat import ChatCompletionMessageParam

# ==================== 方言（原“回答风格”已移除，仅保留方言设置） ====================
DIALECTS = ["普通话", "四川话", "东北话", "粤语", "上海话", "天津话", "陕西话", "河南话", "湖南话"]
DEFAULT_DIALECT = "普通话"

# ④ 年级候选（设置中选，投给AI时可影响讲解深度）
GRADES = ["小学", "初中", "高中", "考研"]

# ==================== ⑨ 人格（MBTI 16 型老师风格） ====================
PERSONALITIES: Dict[str, str] = {
    "INTJ": "像一位缜密渊博的学者：逻辑严密、提纲挈领，先给出底层原理再推导结论，善用结构化框架。",
    "INTP": "像一位爱追根问底的理论家：喜欢从第一性原理出发，指出多种开放的思路，鼓励学生自己再深入。",
    "ENTJ": "像一位雷厉风行的教练：目标导向、直击要害，善用总结和下一步行动建议推动学生前进。",
    "ENTP": "像一位脑洞大开的辩手：不断抛出反例和类比，多角度思辨，让学生看到问题的另一面。",
    "INFJ": "像一位温柔坚定的导师：循循善诱，关注学生的情绪和信心，善于启发式提问。",
    "INFP": "像一位理想主义的诗人：用故事和隐喻讲透概念，重视价值观和内在意义。",
    "ENFJ": "像一位热情的组织者：感染力强，擅长激励和带动学习氛围，把复杂问题拆解得很有节奏。",
    "ENFP": "像一位好奇的探险家：热情洋溢，用新鲜的角度激发兴趣，让学习变得有趣。",
    "ISTJ": "像一位严谨负责的教务主任：重规矩和步骤，强调规范、公式和必背要点。",
    "ISFJ": "像一位细心耐心的辅导员：温和稳重，反复强调基础，宁可多举例也要确保学生懂。",
    "ESTJ": "像一位循规蹈矩的管理者：强调纪律、流程和标准答案，把步骤列为清晰的清单。",
    "ESFJ": "像一位贴心的班主任：亲和力强，关心学生的掌握情况，及时鼓励和纠正。",
    "ISTP": "像一位动手派的工程师：喜欢用实际操作和具体例子，从“怎么用”切入。",
    "ISFP": "像一位安静的体验派老师：温柔随和，重视直观感受和图形化的理解。",
    "ESTP": "像一位果断的行动家：直截了当，喜欢用“试试看”的方式，强调快速上手。",
    "ESFP": "像一位活泼的表演者：生动热情，用肢体语言一样的描述让抽象概念变得鲜活。",
}

# ⑨ 16型人格的 temperature（原“回答风格temperature”已删除，改为按人格）
PERSONALITY_TEMPERATURE: Dict[str, float] = {
    "INTJ": 0.4, "INTP": 0.5, "ENTJ": 0.4, "ENTP": 0.7,
    "INFJ": 0.6, "INFP": 0.7, "ENFJ": 0.7, "ENFP": 0.8,
    "ISTJ": 0.3, "ISFJ": 0.5, "ESTJ": 0.3, "ESFJ": 0.6,
    "ISTP": 0.4, "ISFP": 0.6, "ESTP": 0.5, "ESFP": 0.8,
}

# ⑨ 各学科推荐的 MBTI 老师风格（auto 时按学科选择）
SUBJECT_PERSONALITY: Dict[str, str] = {
    "数学": "INTP", "物理": "INTP", "化学": "ISTJ", "生物": "ISFJ",
    "语文": "INFJ", "英语": "ENFJ", "历史": "ISTJ", "地理": "ISFP",
    "政治": "ESTJ", "通用": "ENTJ",
}

# ⑨ 详细度三档 + 自动
DETAIL_LEVELS = {
    "very_detailed": {"label": "非常细", "instruction": "详细度：非常细。逐步骤展开推导，关键步骤都给出理由和中间结果，适当时补充易错提醒和多种解法。"},
    "detailed": {"label": "较细", "instruction": "详细度：较细。步骤完整但不过分展开，关键结论说明依据，保留必要推导。"},
    "brief": {"label": "简略", "instruction": "详细度：简略。只保留核心思路、关键公式和结论，不展开琐碎推导。"},
}

# ==================== 重点颜色标记（⑥：[[#RRGGBB]]…[[#RRGGBB]]，深浅背景均可读） ====================
COLOR_RULES = """【重点颜色标记（重要，必须遵守）】
为了让学生一眼抓住重点，你在回答中【必须】给关键内容标色（每段至少1~3处）：
格式：[[#RRGGBB]]需要标记的文字[[#RRGGBB]]（前后两个标记的颜色相同；RRGGBB为十六进制颜色值，例如 [[#E53935]]易错警示[[#E53935]]）
规则：
1. 解题的关键步骤、核心公式结论、易错警示、重要突破口都要标色；只标记关键短语或短句（30字以内），不要整段或大段标色，更不要全篇标色
2. 颜色必须从下面“深浅背景都可读”的色板中选择：#E53935(警示红，用于易错点/陷阱)、#FB8C00(强调橙，用于关键突破口)、#1E88E5(要点蓝，用于核心结论)、#43A047(正确绿，用于最终答案)、#8E24AA(进阶紫，用于技巧/升华)、#00ACC1(补充青，用于补充说明)、#6D4C41(注意棕，用于注意事项)
3. 不要把标记写进LaTeX公式、代码块、图片说明或JSON输出里
4. 不要嵌套使用；一个标记结束后再开始下一个
5. 最终答案、关键公式结论、易错警示这三类内容必须标色，不得遗漏"""

# ==================== 追问绘图强提示（①：追问必须能输出LaTeX/TikZ图形） ====================
ASK_DRAW_RULE = ("回答要求：直接回答学生最新问题本身，用自然语言，禁止输出JSON格式或新的问题列表（除下方允许的LaTeX代码块外，不要输出其他代码块）。"
                 "如讲解涉及函数图像、几何图形、过程示意等需要图形辅助的内容，务必在回答中输出1~3个可独立编译的```latex ... ```代码块"
                 "（使用tikzpicture/pgfplots，不要documentclass等完整文档结构），每个代码块前用一行 **图N：标题** 说明该图作用，"
                 "并把代码块放在对应讲解文字之后。若题目本身不含图形也可不输出。")


# ② 回答风格（正式/鼓励/幽默；另保留历史值 plain/concise/lively）
STYLE_INSTRUCTIONS: Dict[str, str] = {
    "formal": "回答风格：正式。用规范、严谨、条理清晰的书面化表达，不使用网络用语、玩笑和夸张修辞。",
    "encouraging": "回答风格：鼓励。多肯定学生的思路与进步，指出问题前先肯定做得对的地方，用正向、温暖的语言给出改进建议。",
    "humorous": "回答风格：幽默。在保证知识准确的前提下，用轻松风趣的比喻、类比和小玩笑让讲解更有意思；不要过度玩梗，不能影响严谨性。",
    "plain": "回答风格：平实。用朴素直白的语言讲解，少用修辞。",
    "concise": "回答风格：简洁。只讲要点，句子短，不铺陈。",
    "lively": "回答风格：生动。多用类比和场景化描述让抽象概念具体起来。",
}

# ② 回答风格的 temperature（正式更稳定、幽默更活泼）
STYLE_TEMPERATURE: Dict[str, float] = {
    "formal": 0.2, "plain": 0.3, "concise": 0.2, "lively": 0.7,
    "encouraging": 0.7, "humorous": 0.9,
}


def style_instruction(style: Optional[str], dialect: str = "") -> str:
    """② 回答风格（正式/鼓励/幽默）+ ③ 方言口吻；两者可叠加生效"""
    parts = []
    s = (style or "").strip().lower()
    ins = STYLE_INSTRUCTIONS.get(s)
    if ins:
        parts.append("（" + ins + "）")
    d = (dialect or "").strip()
    if d and d != "普通话":
        parts.append(f"（讲解口吻：用{d}的口吻讲解，语气、用词、口头禅都贴近{d}本地说话方式；"
                     f"但专业术语、公式、数字必须保持准确，不因口吻影响正确性。）")
    return "\n".join(parts)


def _resolve_personality(personality: Optional[str], subject: str = "") -> Optional[str]:
    """⑨ 把 personality(auto/16型/空)解析成具体 MBTI 大写代码；无法解析返回 None"""
    p = (personality or "").strip()
    if not p or p == "none":
        return None
    if p == "auto":
        subj = (subject or "通用").strip()
        for key, mbti in SUBJECT_PERSONALITY.items():
            if key in subj:
                return mbti
        return SUBJECT_PERSONALITY["通用"]
    return p.upper() if p.upper() in PERSONALITIES else None


def personality_instruction(personality: Optional[str], subject: str = "") -> str:
    """⑨ 人格：MBTI 16型或 auto（自动按学科推荐）。返回附加 instruction"""
    p = _resolve_personality(personality, subject)
    if not p:
        return ""
    desc = PERSONALITIES.get(p)
    if not desc:
        return ""
    return f"（讲师人格：{desc}请以这种老师的口吻和方式讲解，但内容必须准确、严谨，格式规范。）"


def detail_instruction(detail: Optional[str], weak_count: int = 0) -> str:
    """⑨ 详细度：very_detailed/detailed/brief/auto（auto 按薄弱知识点数，1~2个→较细，>2个→非常细）"""
    d = (detail or "auto").strip()
    if d == "auto":
        # 不完全匹配比较薄弱知识点（weak_count 由外部传入）
        if weak_count > 2:
            d = "very_detailed"
        elif weak_count >= 1:
            d = "detailed"
        else:
            return ""  # 无薄弱知识点时用默认
    lv = DETAIL_LEVELS.get(d)
    return lv["instruction"] if lv else ""


def style_temperature(style: Optional[str]) -> Optional[float]:
    """② 回答风格 temperature：正式→0.2、鼓励→0.7、幽默→0.9；未识别返回 None(用模型默认)"""
    return STYLE_TEMPERATURE.get((style or "").strip().lower())


def personality_temperature(personality: Optional[str], subject: str = "") -> Optional[float]:
    """⑨ 人格 temperature：按解析出的 MBTI 取温；无则 None(用模型默认)"""
    p = _resolve_personality(personality, subject)
    if not p:
        return None
    return PERSONALITY_TEMPERATURE.get(p)


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
        # 豆包（火山方舟）
        self.doubao_client = None
        if APIConfig.DOUBAO_API_KEY:
            self.doubao_client = OpenAI(
                api_key=APIConfig.DOUBAO_API_KEY,
                base_url=APIConfig.DOUBAO_BASE_URL
            )
        self.doubao_model = APIConfig.DOUBAO_DEFAULT_LLM
        # 混元（腾讯）
        self.hunyuan_client = None
        if APIConfig.HUNYUAN_API_KEY:
            self.hunyuan_client = OpenAI(
                api_key=APIConfig.HUNYUAN_API_KEY,
                base_url=APIConfig.HUNYUAN_BASE_URL
            )
        self.hunyuan_model = APIConfig.HUNYUAN_DEFAULT_LLM
        self.temperature = AI_MODEL["temperature"]
        self.max_tokens = AI_MODEL["max_tokens"]
        # ⑫ Token 用量累积（每次 solve 流程前 reset，complete 时下发）
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # ③ 估算用字符计数（provider 不返回 usage 时兜底）
        self._est_prompt_chars = 0
        self._est_completion_chars = 0
        self.active_engine = "deepseek"
        self.active_model = self.model

    def reset_usage(self, engine: Optional[str] = None, model: Optional[str] = None):
        """⑫ 开始一次解题/追问前清空用量，并记录本次引擎/模型"""
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._est_prompt_chars = 0
        self._est_completion_chars = 0
        self.active_engine = engine or "deepseek"
        _, m = self._get_client(engine, model)
        self.active_model = m

    @staticmethod
    def _msg_chars(messages) -> int:
        """估算 messages 文本字符数（用于 token 估算）"""
        total = 0
        for m in messages or []:
            try:
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if isinstance(content, str):
                    total += len(content)
                elif isinstance(content, list):
                    for it in content:
                        if isinstance(it, dict) and isinstance(it.get("text"), str):
                            total += len(it["text"])
            except Exception:
                pass
        return total

    @staticmethod
    def _estimate_tokens(chars: int) -> int:
        """粗略估算 token 数：中文字符≈1 token，其它≈4字符/token"""
        if not chars:
            return 0
        cjk = sum(1 for ch in str(chars) if '一' <= ch <= '鿿') if False else 0
        # 简单按字符数统计：对字符串参数
        return 0

    def _estimate_from_chars(self) -> Dict:
        import re as _re
        def _t(s):
            if not s:
                return 0
            cjk = len(_re.findall(r'[一-鿿]', s))
            other = len(s) - cjk
            return cjk + (other // 4) + (1 if other % 4 else 0)
        pt = _t(('' if self._est_prompt_chars == 0 else str(self._est_prompt_chars)))
        # 上面 _t 需要字符串，改用字符计数近似：这里直接用存的数量近似
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def get_usage(self) -> Dict:
        """⑫ 返回当前累积用量 + 引擎/模型名；provider 未返回 usage 时按字符数估算兜底"""
        u = dict(self.usage)
        if u.get("total_tokens", 0) == 0 and (self._est_prompt_chars or self._est_completion_chars):
            # 中文字符≈1 token，其余≈4字符/token
            import re as _re
            def _t(chars):
                if not chars:
                    return 0
                s = str(chars)
                cjk = len(_re.findall(r'[一-鿿]', s))
                other = len(s) - cjk
                return cjk + (other // 4) + (1 if other % 4 else 0)
            u["prompt_tokens"] = self._estimate_count(self._est_prompt_chars)
            u["completion_tokens"] = self._estimate_count(self._est_completion_chars)
            u["total_tokens"] = u["prompt_tokens"] + u["completion_tokens"]
        return {
            "engine": self.active_engine,
            "model": self.active_model,
            **u,
        }

    @staticmethod
    def _estimate_count(chars: int) -> int:
        """按“中文字符≈1 token、其余≈4字符/token”估算"""
        if not chars:
            return 0
        import re as _re
        s = str(chars)
        cjk = len(_re.findall(r'[一-鿿]', s))
        other = len(s) - cjk
        return cjk + (other // 4) + (1 if other % 4 else 0)
    
    def _get_client(self, engine: Optional[str] = None, model: Optional[str] = None):
        """按提供方选择客户端与模型（deepseek / qwen / doubao / hunyuan），默认deepseek
        engine: 提供方；model: 具体模型名（未指定用默认）"""
        if engine == "qwen":
            if self.qwen_client is None:
                raise RuntimeError("未配置千问API Key（请检查 .env 的 QWEN_API_KEY）")
            return self.qwen_client, model or self.qwen_model
        if engine == "doubao":
            if self.doubao_client is None:
                raise RuntimeError("未配置豆包API Key（请检查 .env 的 DOUBAO_API_KEY）")
            return self.doubao_client, model or self.doubao_model
        if engine == "hunyuan":
            if self.hunyuan_client is None:
                raise RuntimeError("未配置混元API Key（请检查 .env 的 HUNYUAN_API_KEY）")
            return self.hunyuan_client, model or self.hunyuan_model
        return self.client, model or self.model
    
    def _thinking_supported(self, engine: Optional[str]) -> bool:
        """思考模式（十一）：仅DeepSeek链路支持（其它引擎未知是否支持，跳过）"""
        return (engine or "deepseek") == "deepseek"
    
    def solve_problem_stream(self, ocr_text: str, search_result: Optional[str] = None, engine: Optional[str] = None, model: Optional[str] = None,
                             style: Optional[str] = None, thinking: bool = False, dialect: str = "", grade: str = "",
                             personality: Optional[str] = None, subject: str = "", detail: Optional[str] = None, weak_count: int = 0, latex_helper=None,
                             interactive_quiz: bool = False) -> Generator[Dict, None, None]:
        """
        多轮解题对话 - 流式返回各阶段结果
        
        Yields:
            Dict: {"stage": "info"|"steps"|"solution"|"mindmap"|"questions"|"thinking_chunk", "content": ...}
        style: 回答风格id（②，影响文字类阶段的temperature与措辞）
        dialect: 方言名称（③，style=dialect 时生效）
        grade: 年级（④，影响讲解深度）
        thinking: 是否开启思考模式（十一，仅作用于“完整解析”阶段，思考内容经 thinking_chunk 下发）
        personality: 人格MBTI类型或auto（⑨）
        subject: 学科（⑧，personality=auto 时用于推荐）
        detail: 详细度 very_detailed/detailed/brief/auto（⑨）
        weak_count: 薄弱知识点数（⑨，detail=auto 时用于决定详细度）
        interactive_quiz: ⑯ 边解答边设问开关；开启则在 steps 阶段后额外下发 quiz 事件
        """
        start_time = time.time()
        self.reset_usage(engine, model)
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        if g_ins:
            st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
        p_ins = personality_instruction(personality, subject)
        if p_ins:
            st_ins = (st_ins + "\n" + p_ins) if st_ins else p_ins
        d_ins = detail_instruction(detail, weak_count)
        if d_ins:
            st_ins = (st_ins + "\n" + d_ins) if st_ins else d_ins
        thinking_auto = (thinking == "auto")
        use_thinking = (thinking is True) and self._thinking_supported(engine)
        latex_helper_auto = (latex_helper == "auto")
        use_latex_helper = (latex_helper is True)
        
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

        # ⑨ 从 question_info 提取学科与难度，用于 auto 判定
        info_subject = (question_info or {}).get("subject", "") if isinstance(question_info, dict) else ""
        info_difficulty = (question_info or {}).get("difficulty", "") if isinstance(question_info, dict) else ""

        # ⑨ 人格 auto：用题目实际学科重算（info_subject 优先）；温度也按解析后人格取
        eff_subj = info_subject or subject or ""
        if (personality or "").strip() == "auto":
            p_ins = personality_instruction("auto", eff_subj)
            # 重建 st_ins（去旧 p_ins，加新 p_ins）
            st_ins = style_instruction(style, dialect)
            if g_ins:
                st_ins = (st_ins + "\n" + g_ins) if st_ins else g_ins
            if p_ins:
                st_ins = (st_ins + "\n" + p_ins) if st_ins else p_ins
            if d_ins:
                st_ins = (st_ins + "\n" + d_ins) if st_ins else d_ins
        p_temp = personality_temperature(personality, eff_subj)

        # ⑨ 详细度 auto：需薄弱知识点数（weak_count 已由外部传入）——d_ins 在 info 前已按 weak_count 算好，无需重算

        # ⑨ 思考 auto：难度为“较难/难”时开启
        if thinking_auto:
            use_thinking = (info_difficulty in ("较难", "难")) and self._thinking_supported(engine)

        # 图解辅助 auto：数学/物理 且 较难/难 时开启
        if latex_helper_auto:
            _subj = (info_subject or subject or "")
            use_latex_helper = (("数学" in _subj or "物理" in _subj) and (info_difficulty in ("较难", "难")))

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
                                           temperature=p_temp):
            if evt["stage"] == "content":
                accumulated_steps = evt["content"]
                yield {"stage": "steps_chunk", "content": accumulated_steps}
        steps_response = accumulated_steps
        
        yield {"stage": "steps", "content": steps_response}

        # === ⑯ 边解答边设问：基于“题目 + 解题思路”生成 2~5 个简单、顺应思路的小问题 ===
        # 客户端在展示完整解析的过程中插入这些提问，用户作答后判定正误并继续讲解。
        if interactive_quiz:
            try:
                quiz_prompt = """请基于上面的题目与解题思路，设计 2~5 个“边讲解边提问”的小问题，用于检查学生是否跟上思路。
    要求：
    1. 题目要简单，是顺着解题思路的自然小步（如“这一步为什么要移项？”“符号说明了什么？”），不要求学生算复杂结果
    2. 难易递进：第一个只考“看懂没”，后面的逐渐深一点
    3. 每题必须是可判定的：给出 2~4 个选项，并标明哪一个是正确答案
    4. 每题配一句简短解析（答对或答错都能看懂为什么）
    5. 按以下 JSON 输出，只输出 JSON，不要其他内容：
    {
      "quiz": [
        {
          "question": "问题文本",
          "options": ["选项A", "选项B", "选项C"],
          "answer_index": 0,
          "explanation": "为什么选它"
        }
      ]
    }
    answer_index 是 options 里正确选项的下标（从 0 开始）。"""
                messages.append({"role": "assistant", "content": steps_response})
                messages.append({"role": "user", "content": quiz_prompt})
                quiz_raw = self._call_api(messages, engine=engine, model=model)
                quiz_data = self._parse_json_response(quiz_raw) if quiz_raw else None
                quiz_list = []
                if isinstance(quiz_data, dict):
                    quiz_list = quiz_data.get("quiz") or []
                elif isinstance(quiz_data, list):
                    quiz_list = quiz_data
                norm = []
                for q in (quiz_list or []):
                    if not isinstance(q, dict):
                        continue
                    text = str(q.get("question") or "").strip()
                    opts = q.get("options") or []
                    if not text or not isinstance(opts, list):
                        continue
                    opts = [str(o).strip() for o in opts if str(o).strip()]
                    if len(opts) < 2:
                        continue
                    opts = opts[:4]
                    try:
                        ai_idx = int(q.get("answer_index", 0))
                    except Exception:
                        ai_idx = 0
                    if ai_idx < 0 or ai_idx >= len(opts):
                        ai_idx = 0
                    norm.append({
                        "question": text,
                        "options": opts,
                        "answer_index": ai_idx,
                        "explanation": str(q.get("explanation") or "").strip(),
                    })
                    if len(norm) >= 5:
                        break
                if norm:
                    yield {"stage": "quiz", "content": {"questions": norm}}
            except Exception as e:
                print(f"[边解答边设问] 生成失败（不影响解题）: {e}")
        
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
                                            temperature=p_temp, thinking=use_thinking,
                                            on_reasoning=_on_reasoning):
            if evt["stage"] == "thinking_chunk":
                yield evt
            else:
                accumulated_solution = evt["content"]
                yield {"stage": "solution_chunk", "content": accumulated_solution}
        
        solution_response = accumulated_solution
        
        yield {"stage": "solution", "content": solution_response}
        
        # === 第三点五阶段：生成LaTeX辅助图形（图解辅助开关控制） ===
        if use_latex_helper:
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
                                                temperature=p_temp):
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
        └── 解题技巧
    严格要求：只输出上面这种纯文本缩进树。禁止使用代码块围栏、禁止输出 Markdown 标记、
    禁止把公式写成数学源码；公式一律用中文或普通文字描述。"""
        if st_ins:
            mindmap_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": solution_response})
        messages.append({"role": "user", "content": mindmap_prompt})
        # 流式输出思维导图
        accumulated_mindmap = ""
        # 【修复】max_tokens=2000 对【推理型】模型不够：思考过程也计入 completion_tokens。
        # 实测思维导图这步会被 reasoning 全程吃满、返回空内容（库里出现过 mindmap 为空，
        # 进而被按位置取到了别的步骤的正文）。这里给足额度。
        for evt in self._call_api_streaming(messages, max_tokens=16000, engine=engine, model=model,
                                            temperature=p_temp):
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
        yield {"stage": "complete", "content": {"total_time": elapsed, "messages": messages, "usage": self.get_usage()}}
    
    def generate_ai_report(self, stats_summary: str, engine: Optional[str] = None, model: Optional[str] = None,
                           style: Optional[str] = None, dialect: str = "", grade: str = "",
                           personality: Optional[str] = None, detail: Optional[str] = None,
                           subject: str = "", weak_count: int = 0) -> str:
        """生成AI版学情报告（非流式，兼容旧调用）"""
        return "".join(self.generate_ai_report_stream(
            stats_summary, engine=engine, model=model, style=style, dialect=dialect, grade=grade,
            personality=personality, detail=detail, subject=subject, weak_count=weak_count))

    def generate_ai_report_stream(self, stats_summary: str, engine: Optional[str] = None, model: Optional[str] = None,
                                  style: Optional[str] = None, dialect: str = "", grade: str = "",
                                  personality: Optional[str] = None, detail: Optional[str] = None,
                                  subject: str = "", weak_count: int = 0):
        """生成AI版学情报告（流式，逐步yield增量文本；旧客户端按增量累加）
        6.2：报告必须与解题使用同一套用户偏好（风格/方言/年级/人格/详细度），
        否则用户在设置里选了“四川话/高中/幽默”却在报告里看不到任何变化。"""
        st_ins = style_instruction(style, dialect)
        g_ins = grade_instruction(grade)
        p_ins = personality_instruction(personality, subject)
        d_ins = detail_instruction(detail, weak_count)
        extra_ins = "\n".join([x for x in (g_ins, p_ins, d_ins) if x])
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
        if extra_ins:
            prompt += "\n" + extra_ins
        prompt += "\n\n" + COLOR_RULES
        
        messages: List[ChatCompletionMessageParam] = [
            {"role": "system", "content": "你是一位经验丰富的教育顾问，擅长用温暖鼓励的方式与学生沟通。"},
            {"role": "user", "content": prompt}
        ]
        
        # ② 风格/人格温度：优先人格温度，其次风格温度
        temp = personality_temperature(personality, subject)
        if temp is None:
            temp = style_temperature(style)
        prev_sent = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=temp):
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
        知识延伸多轮对话（②：删易错点板块，改为 知识点总结 + 知识拓展 + 相似题推荐 + 延伸思考(带答案)）
        阶段：info(JSON含相似题) → summary(知识点总结) → similar_questions(相似题推荐) → extension(知识拓展) → questions(延伸思考QA)
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
        
        # ===== 第一阶段：结构化分析（含相似题推荐、延伸思考题） =====
        summary_prompt = """请分析这道题目，以JSON格式输出：
    {
        "core_concept": "核心概念（一句话）",
        "knowledge_summary": "知识点总结（100字内）",
        "extension_topics": ["可延伸的知识点1", "可延伸的知识点2"],
        "similar_questions": [
            {"question": "相似题1（与本题同知识点的另一道题）", "answer": "答案1（要给出完整解题步骤与结论，150~250字，不要只写一两句）"},
            {"question": "相似题2", "answer": "答案2（完整步骤与结论，150~250字）"},
            {"question": "相似题3", "answer": "答案3（完整步骤与结论，150~250字）"}
        ],
        "extension_questions": [
            {"question": "延伸思考题1", "answer": "完整答案1"},
            {"question": "延伸思考题2", "answer": "完整答案2"},
            {"question": "延伸思考题3", "answer": "完整答案3"}
        ],
        "difficulty": "易/较易/中/较难/难",
        "subject": "学科"
    }
    相似题推荐要换成与本题知识点相关的、还没做过的题目；每道相似题的答案要写完整解题步骤和最终结论（150~250字），不要只给简答。延伸思考题要有深度、引导思考，并给出完整答案。只输出JSON。"""
        
        messages.append({"role": "user", "content": summary_prompt})
        info_response = self._call_api(messages, engine=engine, model=model)
        info = self._parse_json_response(info_response)
        yield {"stage": "info", "content": info}
        
        # ===== 第二阶段：知识点总结（流式） =====
        knowledge_summary = (info or {}).get('knowledge_summary', '') if isinstance(info, dict) else ''
        core_concept = (info or {}).get('core_concept', '') if isinstance(info, dict) else ''
        summary_expand_prompt = f"""请围绕核心概念“{core_concept}”，把知识点总结展开成清晰易懂的讲解：
{knowledge_summary}
要求：
1. 分点讲清楚每个知识点的含义
2. 结合本题说明如何运用
3. 200字左右"""
        if st_ins:
            summary_expand_prompt += "\n" + st_ins
        
        messages.append({"role": "assistant", "content": info_response})
        messages.append({"role": "user", "content": summary_expand_prompt})
        accumulated_summary = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_summary = evt["content"]
                yield {"stage": "summary_chunk", "content": accumulated_summary}
        summary_content = accumulated_summary
        yield {"stage": "summary", "content": summary_content}
        
        # ===== 第三阶段：相似题推荐（直接下发 info 里的 similar_questions） =====
        similar = (info or {}).get('similar_questions', []) if isinstance(info, dict) else []
        if not isinstance(similar, list):
            similar = []
        yield {"stage": "similar_questions", "content": similar}
        
        # ===== 第四阶段：知识拓展（流式） =====
        extension_topics = (info or {}).get('extension_topics', []) if isinstance(info, dict) else []
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
        
        messages.append({"role": "assistant", "content": summary_content})
        messages.append({"role": "user", "content": extension_prompt})
        accumulated_extension = ""
        for evt in self._call_api_streaming(messages, max_tokens=2000, engine=engine, model=model,
                                            temperature=st_temp):
            if evt["stage"] == "content":
                accumulated_extension = evt["content"]
                yield {"stage": "extension_chunk", "content": accumulated_extension}
        extension_content = accumulated_extension
        yield {"stage": "extension", "content": extension_content}
        
        # ===== 第五阶段：延伸思考（带答案的QA，可追问） =====
        ext_questions = (info or {}).get('extension_questions', []) if isinstance(info, dict) else []
        if not isinstance(ext_questions, list):
            ext_questions = []
        yield {"stage": "questions", "content": ext_questions}
        
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

    def generate_response(self, prompt: str, engine: Optional[str] = None, model: Optional[str] = None,
                          system: Optional[str] = None, max_tokens: int = 2000) -> str:
        """通用单轮生成。

        system 缺省时沿用 GeoGebra 专用系统提示（兼容既有调用）；
        批注 / 番茄钟 / 分题等任务必须显式传入各自的 system，
        否则模型会被 GeoGebra 提示误导，输出偏离预期格式
        （这正是批注报"未生成有效批注"的根因）。
        """
        sys_prompt = system or (
            "你是一个GeoGebra命令生成专家，直接输出可以在GeoGebra输入栏中执行的命令，"
            "每行一个，不要任何解释文字。"
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]
        return self._call_api(messages, max_tokens=max_tokens, engine=engine, model=model)

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
                # ③ 累积估算字符（provider 无 usage 时兜底）
                self._est_prompt_chars += self._msg_chars(messages)
                self._est_completion_chars += len(content or "")
                # ⑫ 累积 token 用量
                try:
                    u = response.usage
                    if u:
                        self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                        self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                        self.usage["total_tokens"] += getattr(u, "total_tokens", 0) or 0
                except Exception:
                    pass
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
                    stream_options={"include_usage": True},
                )
                if thinking_on:
                    kwargs["reasoning_effort"] = "high"
                    kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
                response = client.chat.completions.create(**kwargs)
                reasoning_content = ""
                content = ""
                thinking_done = False
                for chunk in response:
                    # ⑫ 流式最后一个 chunk 带 usage
                    if getattr(chunk, "usage", None):
                        u = chunk.usage
                        try:
                            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
                            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
                            self.usage["total_tokens"] += getattr(u, "total_tokens", 0) or 0
                        except Exception:
                            pass
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
                # ③ 累积估算字符（provider 无 usage 时兜底）
                self._est_prompt_chars += self._msg_chars(messages)
                self._est_completion_chars += len(content)
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
    
    def recognize_image_with_vision(self, image_path: str, model: Optional[str] = None, prompt: Optional[str] = None,
                                    max_tokens: int = 5000) -> Tuple[str, float]:
        """使用视觉模型识别图片（OCR/图表描述）
        模型名以 deepseek 开头 → DeepSeek客户端；否则走千问客户端
        ③ 提示词：手写内容转 *斜体* Markdown；④ 手写颜色用 [[#RRGGBB]] 标注；
        ① 只在彼此独立的大题之间插入 %%%（跨栏/同篇阅读/同一大题不切分）

        max_tokens（【修复】新增参数）：默认视觉模型是【推理型】，思考过程也计入
        completion_tokens。实测批注场景下 5000 会被 reasoning_tokens 全部吃满，
        返回 finish_reason=length 且 content 为空 → 上层误判为“AI 未生成有效批注”。
        需要长输出的调用方（如批注）应显式给足额度。
        """
        import base64 as _b64
        start = time.time()
        try:
            # ⑬ 默认视觉模型：优先用 VISION_PROVIDER 指定的 provider
            # （千问欠费后直接用不了，默认改为与 LLM 同源的 DeepSeek 视觉）
            if not model:
                _vp = str(getattr(APIConfig, "VISION_PROVIDER", "deepseek") or "deepseek").lower()
                if _vp == "qwen":
                    model = APIConfig.QWEN_DEFAULT_VISION
                elif _vp == "deepseek":
                    model = getattr(APIConfig, "DEEPSEEK_DEFAULT_VISION", "deepseek-v4-flash-vision-exp")
                else:
                    model = APIConfig.QWEN_DEFAULT_VISION
            is_deepseek_model = str(model).lower().startswith("deepseek")
            client = self.client if is_deepseek_model else self.qwen_client
            # ① 视觉通道降级：若指定 provider 不可用（如千问欠费/未配 Key），
            #    自动回退到本机可用的视觉 provider，避免整条 OCR 链路失败。
            if client is None:
                _fallback = getattr(APIConfig, "DEEPSEEK_DEFAULT_VISION", "deepseek-v4-flash-vision-exp")
                if is_deepseek_model or self.client is None:
                    # DeepSeek 也不可用：尝试反向回退到千问
                    if not is_deepseek_model and self.qwen_client is not None:
                        pass
                    else:
                        return ("OCR识别失败：未配置可用的视觉模型 API Key"
                                "（DeepSeek 与千问均不可用）"), round(time.time() - start, 2)
                else:
                    print(f"[OCR] 视觉模型 {model} 的 provider 不可用，自动降级为 {_fallback}")
                    model = _fallback
                    is_deepseek_model = True
                    client = self.client
            with open(image_path, "rb") as f:
                img_b64 = _b64.b64encode(f.read()).decode("ascii")
            prompt = prompt or (
                "请识别这张图片中的全部文字并完整输出（保持原有顺序和格式）。"
                "③ 手写的内容（包括手写解题过程、批注）请在输出中改用 *斜体*（Markdown斜体，单个星号包裹）表示，以与印刷体区分；印刷体保持原样。"
                "④ 颜色标注：手写内容如果有特殊字体颜色（红笔、蓝笔、荧光笔等区别于普通黑色的颜色），"
                "请在对应文字前后各加一个【相同的】颜色标记，写成 [[#RRGGBB]]这段文字[[#RRGGBB]]，"
                "其中 RRGGBB 换成你在图里实际看到的颜色的十六进制值（例如红笔用 [[#E53935]]、蓝笔用 [[#1E88E5]]、"
                "绿笔用 [[#43A047]]、橙色用 [[#FB8C00]]）。颜色标记只包住有颜色的文字，普通黑色/灰度文字不要加；不要嵌套使用。"
                "① 分题：只有当图片里包含【多道彼此独立的大题】时，才在两道大题之间输出一个分隔符 %%%（单独一行的三个百分号）。"
                "以下情况【绝对不能】加 %%%（它们都属于同一道题）："
                "(a) 分栏排版时同一道题从左边一栏续到右边一栏（跨栏）；"
                "(b) 同一篇阅读材料/短文及其下面的全部小题（同篇阅读）；"
                "(c) 同一道大题下面的各个小问，如 (1)(2)(3)、①②③、第(1)问 等（同一大题）；"
                "(d) 题干、选项与附图属于同一道题的情况。"
                "⑥ 如果图片模糊、严重反光、文字根本无法辨认，无法完成识别，请只输出一行 ---end---，不要输出其他内容。"
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
                max_tokens=max_tokens,
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
