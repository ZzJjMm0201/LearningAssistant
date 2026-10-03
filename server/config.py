import os
from pathlib import Path

# 基础路径
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
HISTORY_DIR = BASE_DIR / "history"

# 创建目录
for dir_path in [DATA_DIR, REPORT_DIR, HISTORY_DIR]:
    dir_path.mkdir(exist_ok=True)

# ==================== 环境变量支持 ====================
# 密钥优先从环境变量或项目根目录 .env 文件读取，未设置时使用下方默认值。
# 复制 .env.example 为 .env 并填入自己的密钥即可，无需修改本文件。

def _env(key: str, default: str) -> str:
    """读取环境变量，回退到默认值"""
    try:
        _env_file = Path(__file__).resolve().parent.parent / ".env"
        if _env_file.exists():
            for line in _env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass
    return os.environ.get(key, default) or default


# API配置
class APIConfig:
    # DeepSeek API
    DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    # 16.1 DeepSeek 可选模型名（客户端“具体模型”可选；不传时用 API 默认模型）
    # 已按本环境 deepseek 提供方实际可用的模型 ID 核实：
    #   deepseek-v4-flash / deepseek-v4-pro / deepseek-chat / deepseek-reasoner
    DEEPSEEK_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
    
    # 千问 API（阿里云百炼 OpenAI 兼容端点；Key 放 .env）
    QWEN_API_KEY = _env("QWEN_API_KEY", "")
    QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    # 千问默认模型（客户端可在设置中选择）
    QWEN_DEFAULT_LLM = "qwen3.8-max"
    QWEN_DEFAULT_VISION = "qwen3.8-max"

    # 默认视觉模型（⑬ 已改用 DeepSeek 视觉，与 LLM 同源、API 互通；千问欠费后不可用）
    # 客户端设置里仍可选千问，只是默认走这个；若要换回把值改成 "qwen3.8-max" 即可。
    VISION_PROVIDER = _env("VISION_PROVIDER", "deepseek")
    DEEPSEEK_DEFAULT_VISION = _env("DEEPSEEK_DEFAULT_VISION", "deepseek-v4-flash-vision-exp")

    # 豆包（火山方舟，OpenAI 兼容 /api/v3）
    DOUBAO_API_KEY = _env("DOUBAO_API_KEY", "")
    DOUBAO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
    DOUBAO_DEFAULT_LLM = "Doubao-Seed-2.1-pro"

    # 混元（腾讯，OpenAI 兼容 /v1）
    HUNYUAN_API_KEY = _env("HUNYUAN_API_KEY", "")
    HUNYUAN_BASE_URL = "https://tokenhub-intl.tencentmaas.com/v1"
    HUNYUAN_DEFAULT_LLM = "hy4-preview"
    
    # 好未来题库API
    SEARCH_ACCESS_KEY_ID = _env("SEARCH_ACCESS_KEY_ID", "")
    SEARCH_ACCESS_KEY_SECRET = _env("SEARCH_ACCESS_KEY_SECRET", "")
    
    # PaddleOCR API
    OCR_API_URL = "https://74y4w193tej2z706.aistudio-app.com/layout-parsing"
    OCR_TOKEN = _env("OCR_TOKEN", "")
    # OCR 快速失败：云端服务偶发挂起（实测 60s 超时），缩短超时避免用户干等
    OCR_API_TIMEOUT = int(_env("OCR_API_TIMEOUT", "15"))
    # 默认 OCR 模式：paddle=本地/API 双通道；qwen=视觉大模型
    OCR_DEFAULT_MODE = _env("OCR_DEFAULT_MODE", "qwen")

# 功能开关
# ⚠️ 好未来题库搜题：体验次数已用尽且平台可能随时禁用，**全局关闭**。
#    关闭后服务端不再发起搜题请求（不消耗额度），客户端开关也一并禁用不可点。
#    若要恢复：把 enable_question_search 改回 True，并同步改前端 SEARCH_DISABLED。
FEATURE_FLAGS = {
    "enable_question_search": False,  # 题库搜索开关（⑲ 已禁用：好未来额度将尽）
    "enable_ai_animation": True,      # AI动画功能
    "enable_local_ocr": True,         # 本地PaddleOCR兜底：云端 PaddleOCR API 已不可用（60s超时），开启本地通道
    "enable_latex_review": False,     # ⑦ 视觉模型审核LaTeX图形（默认关闭，耗时）
}

# AI模型配置
AI_MODEL = {
    "primary": "deepseek",
    "temperature": 0.2,
    "max_tokens": 8000,
}

# JWT认证配置
JWT_SECRET = _env("JWT_SECRET", "learning-assistant-secret-key-change-in-production")
JWT_EXPIRE_HOURS = 72

# 数据库配置
DATABASE_URL = f"sqlite:///{DATA_DIR}/learning_assistant.db"