"""
好未来AI开放平台 HTTP 签名（官方算法）
参考官方 Python Demo：HMAC-SHA1，key=secret+"&"
body 作为 request_body 参数（json.dumps 默认格式），参数按 key 排序 k=v 用 & 连接，签名 base64 后 URL 编码
"""
import uuid
import json
import base64
import hmac
import hashlib
from urllib.parse import quote
from typing import Dict, Any, Tuple


def build_sign_params(url_params: Dict[str, Any], body_params: Dict[str, Any]) -> Dict[str, Any]:
    """组合签名参数：公共参数 + request_body（body的json字符串）"""
    sign_param = {"request_body": json.dumps(body_params)}
    sign_param.update({k: v for k, v in url_params.items()})
    return sign_param


def url_format(params: Dict[str, Any]) -> str:
    """按key升序排序，k=v 用 & 连接（值不做URL编码）"""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def generate_signature(
    access_key_secret: str,
    url_params: Dict[str, Any],
    body_params: Dict[str, Any],
) -> Tuple[str, str, str]:
    """
    生成签名与nonce

    Returns:
        (signature, signature_nonce, timestamp)
    """
    timestamp = __import__("time").strftime("%Y-%m-%dT%H:%M:%S", __import__("time").localtime())
    signature_nonce = str(uuid.uuid1())

    params = dict(url_params)
    params["access_key_id"] = params.get("access_key_id", "")
    params["timestamp"] = timestamp
    params["signature_nonce"] = signature_nonce

    sign_param = build_sign_params(params, body_params)
    string_to_sign = url_format(sign_param)

    secret = access_key_secret + "&"
    h = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1)
    signature = base64.b64encode(h.digest()).decode("utf-8")

    return quote(signature, "utf-8"), signature_nonce, timestamp
