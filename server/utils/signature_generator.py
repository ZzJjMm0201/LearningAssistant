import hmac
import hashlib
import json
from typing import Dict, Any


def generate_signature(url_params: Dict[str, Any], body_params: Dict[str, Any], secret: str) -> str:
    """
    基于HMAC-SHA256的签名生成器（简化实现）

    采用对URL参数和body参数排序后JSON序列化的方式生成待签名字符串，
    然后使用secret做HMAC-SHA256，返回十六进制摘要。

    这是一个兼容性实现，满足项目中搜索请求的签名需求。
    """
    # 规范化参数为字符串并排序
    url_norm = {k: str(v) for k, v in sorted(url_params.items())}
    body_norm = {k: str(v) for k, v in sorted(body_params.items())}

    payload = json.dumps({"url": url_norm, "body": body_norm}, separators=(',', ':'), sort_keys=True, ensure_ascii=False)

    digest = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return digest
