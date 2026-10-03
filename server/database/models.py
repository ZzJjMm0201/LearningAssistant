import json
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, JSON, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from server.config import DATABASE_URL

Base = declarative_base()

class SubmissionRecord(Base):
    """解题记录表"""
    __tablename__ = 'submission_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # 所属用户（多用户隔离；NULL=旧数据，所有用户可见以兼容）
    user_id = Column(Integer, nullable=True, index=True)
    
    # OCR结果
    ocr_text = Column(Text)
    ocr_time_seconds = Column(Float)
    
    # 题目结构化信息 (JSON)
    question_info = Column(JSON)
    """
    {
        "grade": "高一",
        "subject": "数学",
        "difficulty": "中",
        "knowledge_points": ["二次函数", "不等式"],
        "key_points": ["对称轴", "判别式"],
        "easy_mistakes": ["忽略定义域", "符号错误"]
    }
    """
    
    # AI解析结果
    solution_steps = Column(Text)       # 解题思路
    full_solution = Column(Text)        # 完整解析 (含LaTeX)
    mind_map = Column(Text)             # 思维导图
    suggested_questions = Column(JSON)  # 预判问题列表
    
    # 题库搜索结果
    search_result = Column(Text)
    search_time_seconds = Column(Float)
    
    # AI处理时间
    ai_process_time_seconds = Column(Float)
    
    # 原始文件
    original_image_path = Column(String(512))
    rendered_svg_dir = Column(String(512))
    
    # 用户反馈
    user_rating = Column(Integer, default=0)  # 1-5星评价
    is_correct = Column(Boolean, nullable=True)  # 用户作答是否正确

class TrackingRecord(Base):
    """跟踪学习记录表"""
    __tablename__ = 'tracking_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # 所属用户（多用户隔离）
    user_id = Column(Integer, nullable=True, index=True)
    
    # 专注度数据
    focus_state = Column(String(32))  # writing, thinking, page_turning, seeking_help
    duration_seconds = Column(Float)
    
    # 页数统计
    page_number = Column(Integer, default=0)
    
    # 番茄钟数据
    pomodoro_count = Column(Integer, default=0)

class User(Base):
    """用户表"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_admin = Column(Boolean, default=False)


class AuxRecord(Base):
    """辅助记录表（③ 历史记录也收集：知识延伸 / AI动画）"""
    __tablename__ = 'aux_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, nullable=True, index=True)

    # 类型：extension（知识延伸）/ animation（AI动画）
    record_type = Column(String(16), nullable=False, index=True)
    # 展示标题
    title = Column(String(200))
    # 正文内容（知识延伸：合并总结/易错点/拓展/延伸题；动画：HTML文件URL）
    content = Column(Text)
    # 扩展JSON（延伸题列表等）
    extra_json = Column(JSON, nullable=True)


class ConversationHistory(Base):
    """多轮对话历史表"""
    __tablename__ = 'conversation_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # 所属用户（多用户隔离）
    user_id = Column(Integer, nullable=True, index=True)
    
    role = Column(String(16))  # user / assistant / system
    content = Column(Text)
    
    metadata_json = Column(JSON, nullable=True)

def init_database():
    """初始化数据库（含轻量迁移：为旧表补充新增列）"""
    import sqlalchemy
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(engine)
    # 轻量迁移：老版本数据库没有 user_id 列时自动补充
    try:
        with engine.connect() as conn:
            inspector = sqlalchemy.inspect(engine)
            for table in ("submission_records", "tracking_records", "conversation_history"):
                cols = [c["name"] for c in inspector.get_columns(table)]
                if "user_id" not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER"))
                    conn.commit()
                    print(f"[迁移] {table} 添加 user_id 列")
    except Exception as e:
        print(f"[迁移] 检查/添加 user_id 列失败（不影响启动）: {e}")
    return sessionmaker(bind=engine)


# 全局SessionLocal：方便在其它模块直接导入使用
# 这样可以避免每个模块都显式调用 init_database()
SessionLocal = init_database()