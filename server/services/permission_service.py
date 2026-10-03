# -*- coding: utf-8 -*-
"""⑮ 账号权限：基于 permissions 配置文件控制 AI 资源使用。

规则文件：<项目根>/permissions.txt（utf-8，按行 "用户名=True|False"，# 开头为注释）
- 管理员(is_admin=true) 始终可用；
- 文件里显式标记 =True 的用户可用；
- 其余（新账号 / 未列出 / =False）受限，AI 类接口返回提示"因AI资源有限...，请联系管理员开通"。
- 若文件不存在视为不限（新部署不受影响）。
"""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PERMISSIONS_FILE = os.path.join(PROJECT_ROOT, "permissions.txt")

# 内存缓存便于频繁判断；文件用最后修改时间做失效
_cache_mtime = None
_cache_map = {}


def _load_permissions() -> dict:
    global _cache_mtime, _cache_map
    if not os.path.exists(PERMISSIONS_FILE):
        return None  # 没配置 → 不限
    mtime = os.path.getmtime(PERMISSIONS_FILE)
    if _cache_mtime == mtime:
        return _cache_map
    mapping = {}
    try:
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    name = k.strip()
                    allowed = v.strip().lower() in ("true", "1", "yes", "on")
                    if name:
                        mapping[name] = allowed
    except Exception as e:
        print(f"[permission] 读取权限文件失败: {e}")
        return {}
    _cache_map = mapping
    _cache_mtime = mtime
    return mapping


def can_use_ai(username: str, is_admin: bool) -> bool:
    """判断用户名是否获准使用 AI 资源"""
    if is_admin:
        return True
    mapping = _load_permissions()
    if mapping is None:
        return True  # 未配置权限文件 → 默认全部可用
    # 显式 True 放行；False 或未列出 → 受限（新账号默认 False）
    return mapping.get(username, False) is True


RESTRICTED_MSG = ("因AI资源有限，当前账号已暂停AI功能使用权限；如需使用请联系管理员开通。")

__all__ = ["can_use_ai", "RESTRICTED_MSG", "PERMISSIONS_FILE"]


# 仅用于命令行快速自检（不 import 时执行）
if __name__ == "__main__":
    print("permissions.txt 位置:", PERMISSIONS_FILE)
    print("存在:", os.path.exists(PERMISSIONS_FILE))
