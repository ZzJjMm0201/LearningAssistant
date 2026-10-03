"""
AI 提供方接入层：统一封装 OpenAI 兼容端点。

设计原则：
1. 全部通过 OpenAI 兼容协议接入，新增一家只需在 ENGINES 里加一条配置，不用改调用代码。
2. 密钥只从环境变量读，不落代码、不落日志。
3. 流式与阻塞两种模式都提供：解题走流式（用户要看到"正在思考"），追问/报告走阻塞。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

log = logging.getLogger("aiworker.engine")


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key, "").strip()
    return v if v else default


@dataclass(frozen=True)
class Engine:
    """一个 OpenAI 兼容端点。"""

    name: str
    base_url: str
    api_key: str
    default_model: str
    default_vision: str = ""
    # 部分提供方的视觉模型挂在同一 key 下但需单独指定
    vision_model: str = ""


def _engines() -> dict[str, Engine]:
    """按环境变量组装可用引擎。未配 key 的引擎自动跳过。"""
    deepseek = Engine(
        name="deepseek",
        base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=_env("DEEPSEEK_API_KEY"),
        default_model=_env("DEEPSEEK_DEFAULT_MODEL", "deepseek-chat"),
        default_vision=_env("DEEPSEEK_DEFAULT_VISION", "deepseek-chat"),
        vision_model=_env("DEEPSEEK_DEFAULT_VISION", "deepseek-chat"),
    )
    qwen = Engine(
        name="qwen",
        base_url=_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        api_key=_env("QWEN_API_KEY"),
        default_model=_env("QWEN_DEFAULT_LLM", "qwen-plus"),
        default_vision=_env("QWEN_DEFAULT_VISION", "qwen-vl-max"),
        vision_model=_env("QWEN_DEFAULT_VISION", "qwen-vl-max"),
    )
    doubao = Engine(
        name="doubao",
        base_url=_env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        api_key=_env("DOUBAO_API_KEY"),
        default_model=_env("DOUBAO_DEFAULT_LLM", "doubao-pro"),
    )
    hunyuan = Engine(
        name="hunyuan",
        base_url=_env("HUNYUAN_BASE_URL", "https://hunyuan.tencentcloudapi.com/v1"),
        api_key=_env("HUNYUAN_API_KEY"),
        default_model=_env("HUNYUAN_DEFAULT_LLM", "hunyuan-turbos"),
    )
    return {e.name: e for e in (deepseek, qwen, doubao, hunyuan) if e.api_key}


ENGINES = _engines()


def resolve(engine: str, model: str = "", vision: bool = False) -> Engine:
    """按名字取引擎；未配置时退回任意可用引擎，再不行抛错。"""
    if engine and engine in ENGINES:
        e = ENGINES[engine]
    elif ENGINES:
        e = next(iter(ENGINES.values()))
    else:
        raise RuntimeError(
            "未配置任何可用的模型 API Key。请在 .env 中设置 DEEPSEEK_API_KEY 等环境变量。"
        )
    final = model.strip()
    if not final:
        final = (e.vision_model or e.default_vision) if vision else e.default_model
    e = Engine(
        name=e.name,
        base_url=e.base_url,
        api_key=e.api_key,
        default_model=e.default_model,
        default_vision=e.default_vision,
        vision_model=e.vision_model,
    )
    return e, final


def available_engines() -> list[str]:
    return sorted(ENGINES.keys())


@dataclass
class Chunk:
    """流式响应的一个增量。"""

    text: str = ""
    reasoning: str = ""  # 推理型模型的思考内容
    finish: str = ""


class Client:
    """带重试的 OpenAI 兼容客户端。"""

    def __init__(self, timeout: float = 300.0):
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))

    def close(self) -> None:
        self._client.close()

    # ---------- 内部 ----------

    def _post(self, e: Engine, path: str, payload: dict, timeout: float) -> dict:
        url = e.base_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {e.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        # 指数退避重试 3 次：模型服务偶发 5xx / 超时是常态
        for attempt in range(3):
            try:
                resp = self._client.post(url, json=payload, headers=headers)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"上游返回 {resp.status_code}: {resp.text[:200]}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp.json()
            except Exception as ex:  # noqa: BLE001
                last_err = ex
                if attempt < 2:
                    wait = 1.5 * (attempt + 1)
                    log.warning("调用 %s 第 %d 次失败: %s，%.1fs 后重试", e.name, attempt + 1, ex, wait)
                    time.sleep(wait)
        raise RuntimeError(f"调用 {e.name} 失败: {last_err}")

    def _messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(messages)
        return out

    # ---------- 对外 ----------

    def complete(
        self,
        messages: list[dict],
        engine: str = "deepseek",
        model: str = "",
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 8000,
        json_mode: bool = False,
    ) -> str:
        """阻塞式调用，返回完整文本。"""
        e, m = resolve(engine, model)
        payload: dict[str, Any] = {
            "model": m,
            "messages": self._messages(system, messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(e, "/chat/completions", payload, timeout=max(120.0, max_tokens / 4))
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        # 推理型模型把正文放在 content，思考过程放在 reasoning_content
        return (msg.get("content") or "").strip()

    def stream(
        self,
        messages: list[dict],
        engine: str = "deepseek",
        model: str = "",
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 8000,
    ) -> Iterator[Chunk]:
        """流式调用，逐块产出增量。

        注意：推理型模型（deepseek-reasoner 等）思考与正文**共用** max_tokens 额度，
        思考耗尽会导致正文为空。因此长输出场景必须显式给足 max_tokens。
        """
        e, m = resolve(engine, model)
        payload = {
            "model": m,
            "messages": self._messages(system, messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        url = e.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {e.api_key}",
            "Content-Type": "application/json",
        }
        with self._client.stream("POST", url, json=payload, headers=headers,
                                timeout=httpx.Timeout(600.0, connect=15.0)) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", "replace")
                raise RuntimeError(f"流式调用失败 {resp.status_code}: {body[:300]}")
            for line in resp.iter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if text or reasoning or ch.get("finish_reason"):
                        yield Chunk(text=text, reasoning=reasoning, finish=ch.get("finish_reason") or "")

    def vision(
        self,
        image_base64: str,
        prompt: str,
        engine: str = "deepseek",
        model: str = "",
        max_tokens: int = 4000,
    ) -> str:
        """视觉模型调用（图片走 base64 data URI）。"""
        e, m = resolve(engine, model, vision=True)
        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
            {"type": "text", "text": prompt},
        ]
        payload = {
            "model": m,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        data = self._post(e, "/chat/completions", payload, timeout=180.0)
        choices = data.get("choices") or []
        if not choices:
            return ""
        return ((choices[0].get("message") or {}).get("content") or "").strip()

    def vision_json(self, image_base64: str, prompt: str, **kw) -> Any:
        """让视觉模型输出 JSON 并稳健解析（容忍 ```json 包裹与前后废话）。"""
        raw = self.vision(image_base64, prompt, **kw)
        return extract_json(raw)


def extract_json(raw: str) -> Any:
    """从模型输出里稳健地提取 JSON。

    处理三种常见情况：裸 JSON、```json 包裹、前后有解释文字。
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0].strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 退化：抓第一个 {...} 或 [...] 块
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i = s.find(open_c)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(s)):
            if s[j] == open_c:
                depth += 1
            elif s[j] == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[i : j + 1])
                    except json.JSONDecodeError:
                        # 容错：去掉尾随逗号再试
                        try:
                            import re

                            fixed = re.sub(r",\s*([}\]])", r"\1", s[i : j + 1])
                            return json.loads(fixed)
                        except json.JSONDecodeError:
                            break
    return None


_client_singleton: Client | None = None


def client() -> Client:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = Client()
    return _client_singleton