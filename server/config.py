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
    DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "sk-3178b37524bf4a36a74fa8873d1ebdb5")
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    
    # 混元API (备用) — 2026-06 起旧版混元模型下线，迁移至 TokenHub 平台（hy3-preview）
    HUNYUAN_API_KEY = _env("HUNYUAN_API_KEY", "sk-Ca38LhzybBx5TlfZpgULLWIHdGU1OO2XDrzLgs31zPiPFocJ")
    HUNYUAN_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
    
    # 好未来题库API
    SEARCH_ACCESS_KEY_ID = "1481449208266358784"
    SEARCH_ACCESS_KEY_SECRET = _env("SEARCH_ACCESS_KEY_SECRET", "8c329c183c8f470f9b52d006cb7282c5")
    
    # PaddleOCR API
    OCR_API_URL = "https://74y4w193tej2z706.aistudio-app.com/layout-parsing"
    OCR_TOKEN = _env("OCR_TOKEN", "adf8faf595a1aa8baf581d19a565c2776b94843c")

# 功能开关
FEATURE_FLAGS = {
    "enable_question_search": True,  # 题库搜索开关 (节约费用)
    "enable_ai_animation": True,      # AI动画功能
    "enable_local_ocr": False,        # 本地PaddleOCR默认关闭（慢/占资源）；API OCR默认开启
}

# AI模型配置
AI_MODEL = {
    "primary": "deepseek",           # deepseek / hunyuan
    "temperature": 0.2,
    "max_tokens": 8000,
}

# JWT认证配置
JWT_SECRET = _env("JWT_SECRET", "learning-assistant-secret-key-change-in-production")
JWT_EXPIRE_HOURS = 72

# 数据库配置
DATABASE_URL = f"sqlite:///{DATA_DIR}/learning_assistant.db"