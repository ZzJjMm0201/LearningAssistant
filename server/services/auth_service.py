"""认证服务：注册、登录、JWT token管理"""

import bcrypt
import jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from server.config import JWT_SECRET, JWT_EXPIRE_HOURS
from server.database.models import User


def hash_password(password: str) -> str:
    """用 bcrypt 生成密码 hash"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_token(user_id: int) -> str:
    """生成 JWT token"""
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_token(token: str) -> int | None:
    """验证 JWT token，返回 user_id 或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("user_id")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def register(db: Session, username: str, password: str) -> dict:
    """注册用户，返回 token 和用户信息"""
    # 检查用户名是否已存在
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise ValueError("用户名已存在")

    # 创建用户
    user = User(
        username=username,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 生成 token
    token = create_token(user.id)

    return {
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "created_at": user.created_at.isoformat() if user.created_at else "",
            "is_admin": user.is_admin,
        },
    }


def login(db: Session, username: str, password: str) -> dict:
    """登录验证，返回 token 和用户信息"""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise ValueError("用户名或密码错误")

    if not verify_password(password, user.password_hash):
        raise ValueError("用户名或密码错误")

    token = create_token(user.id)

    return {
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "created_at": user.created_at.isoformat() if user.created_at else "",
            "is_admin": user.is_admin,
        },
    }


def get_user_by_token(db: Session, token: str) -> dict | None:
    """通过 token 获取用户信息"""
    user_id = verify_token(token)
    if user_id is None:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    return {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "is_admin": user.is_admin,
    }
