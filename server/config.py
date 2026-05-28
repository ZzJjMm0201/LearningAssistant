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

# API配置
class APIConfig:
    # DeepSeek API
    DEEPSEEK_API_KEY = "sk-3178b37524bf4a36a74fa8873d1ebdb5"
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    
    # 混元API (备用)
    HUNYUAN_API_KEY = "sk-Ca38LhzybBx5TlfZpgULLWIHdGU1OO2XDrzLgs31zPiPFocJ"
    HUNYUAN_BASE_URL = "https://api.hunyuan.cloud.tencent.com/v1"
    
    # 好未来题库API
    SEARCH_ACCESS_KEY_ID = "1481449208266358784"
    SEARCH_ACCESS_KEY_SECRET = "8c329c183c8f470f9b52d006cb7282c5"
    
    # PaddleOCR API
    OCR_API_URL = "https://74y4w193tej2z706.aistudio-app.com/layout-parsing"
    OCR_TOKEN = "adf8faf595a1aa8baf581d19a565c2776b94843c"

# 功能开关
FEATURE_FLAGS = {
    "enable_question_search": True,  # 题库搜索开关 (节约费用)
    "enable_ai_animation": True,      # AI动画功能
}

# AI模型配置
AI_MODEL = {
    "primary": "deepseek",           # deepseek / hunyuan
    "temperature": 0.2,
    "max_tokens": 8000,
}

# JWT认证配置
JWT_SECRET = "learning-assistant-secret-key-change-in-production"
JWT_EXPIRE_HOURS = 72

# 数据库配置
DATABASE_URL = f"sqlite:///{DATA_DIR}/learning_assistant.db"