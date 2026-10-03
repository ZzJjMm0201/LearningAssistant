"""OCR 识别：视觉模型通道 + PaddleOCR 双通道。

两条通道的分工：
  - 视觉模型（qwen 模式）：能理解图形与排版，对流程图/统计图友好，但没有精确坐标
  - Paddle 双通道（paddle 模式）：公式识别更准，且能返回文字块坐标（批注定位依赖它）
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..ai.prompts import OCR_VISION_PROMPT
from ..ai.prompts import OCR_PADDLE_SUFFIX

log = logging.getLogger("aiworker.ocr")

OCR_API_URL = os.environ.get("OCR_API_URL", "").strip()
OCR_TOKEN = os.environ.get("OCR_TOKEN", "").strip()
OCR_API_TIMEOUT = float(os.environ.get("OCR_API_TIMEOUT", "15"))


class OCRResult:
    __slots__ = ("text", "elapsed", "source")

    def __init__(self, text: str, elapsed: float, source: str):
        self.text = text
        self.elapsed = elapsed
        self.source = source

    def to_dict(self) -> dict:
        return {"text": self.text, "time": round(self.elapsed, 2), "source": self.source}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _pad_ocr_payload(data_b64: str) -> dict:
    return {
        "file": data_b64,
        "fileType": 1,
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }


# ---------- 云端 PaddleOCR-VL API ----------

def _via_api(data_b64: str) -> tuple[str, float]:
    """调用 PaddleOCR-VL 云端 API。

    未配置 OCR_API_URL 时直接跳过——原项目把某个实例 URL 硬编码在源码里，
    那是别人的服务、随时会失效，不应沿用。
    """
    if not OCR_API_URL or not OCR_TOKEN:
        return "", 0.0

    import httpx

    start = time.time()
    try:
        resp = httpx.post(
            OCR_API_URL,
            json=_pad_ocr_payload(data_b64),
            headers={"Authorization": f"token {OCR_TOKEN}", "Content-Type": "application/json"},
            timeout=OCR_API_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("OCR API 返回 %d", resp.status_code)
            return "", time.time() - start
        result = (resp.json() or {}).get("result") or {}
        texts: list[str] = []
        for res in result.get("layoutParsingResults") or []:
            md = (res.get("markdown") or {}).get("text", "")
            if md:
                texts.append(md)
        return "\n\n".join(texts), time.time() - start
    except Exception as e:  # noqa: BLE001
        log.warning("OCR API 调用失败: %s", e)
        return "", time.time() - start


def _via_api_with_boxes(data_b64: str) -> tuple[str, list[dict], float]:
    """带文字块坐标的识别，供批注定位使用。"""
    if not OCR_API_URL or not OCR_TOKEN:
        return "", [], 0.0

    import httpx

    start = time.time()
    try:
        resp = httpx.post(
            OCR_API_URL,
            json=_pad_ocr_payload(data_b64),
            headers={"Authorization": f"token {OCR_TOKEN}", "Content-Type": "application/json"},
            timeout=OCR_API_TIMEOUT,
        )
        if resp.status_code != 200:
            return "", [], time.time() - start
        result = (resp.json() or {}).get("result") or {}
        texts: list[str] = []
        blocks: list[dict] = []
        for res in result.get("layoutParsingResults") or []:
            md = (res.get("markdown") or {}).get("text", "")
            if md:
                texts.append(md)
            for blk in ((res.get("prunedResult") or {}).get("parsing_res_list") or []):
                txt = str(blk.get("block_content") or "").strip()
                bbox = blk.get("block_bbox")
                if txt and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    blocks.append({"text": txt, "bbox": [float(v) for v in bbox[:4]]})
        return "\n\n".join(texts), blocks, time.time() - start
    except Exception as e:  # noqa: BLE001
        log.warning("OCR API 带坐标识别失败: %s", e)
        return "", [], time.time() - start


# ---------- 本地 PaddleOCR ----------

_local_lock = threading.Lock()
_local_ocr: Any = None
_local_ready = False


def preload_local_model() -> bool:
    """预加载本地 PaddleOCR 模型。

    显式调用才加载：这个模型常驻内存数百 MB，空载预加载会拖慢启动并无谓占用。
    """
    global _local_ocr, _local_ready
    with _local_lock:
        if _local_ready and _local_ocr is not None:
            return True
        try:
            from paddleocr import PaddleOCR

            log.info("正在加载本地 PaddleOCR 模型...")
            start = time.time()
            _local_ocr = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_server_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            _local_ready = True
            log.info("本地 PaddleOCR 加载完成，耗时 %.1fs", time.time() - start)
            return True
        except ImportError:
            log.warning("未安装 paddleocr，本地 OCR 通道不可用")
        except Exception as e:  # noqa: BLE001
            log.warning("本地 PaddleOCR 加载异常（忽略）: %s", e)
        return False


def _via_local(image_path: str) -> tuple[str, float]:
    """本地识别。需要先把 base64 落盘（PaddleOCR 接受路径或图片对象）。"""
    if not (_local_ready and _local_ocr is not None):
        return "", 0.0
    start = time.time()
    try:
        result = _local_ocr.predict(image_path)
        if not result:
            return "", time.time() - start
        first = result[0]
        texts = first.get("rec_texts", []) if hasattr(first, "get") else []
        return "\n".join(texts), time.time() - start
    except Exception as e:  # noqa: BLE001
        log.warning("本地 OCR 识别失败: %s", e)
        return "", time.time() - start


# ---------- 统一入口 ----------

def _merge(local_text: str, api_text: str) -> str:
    """合并两路结果。

    原项目把两段原文都拼在一起（"【API识别结果】...【本地识别结果】..."），
    会让下游 LLM 看到同一道题的两份转写、误以为是两道题。
    这里改为：以较长的那份为主体，短的只补充主体中缺失的段落。
    """
    if not local_text:
        return api_text
    if not api_text:
        return local_text

    # 逐段贪心合并：已出现的段落不重复添加
    seen: set[str] = set()
    parts: list[str] = []
    for src in (local_text, api_text):
        for seg in (s.strip() for s in src.split("\n\n")):
            key = seg.replace(" ", "").replace("\n", "")[:80]
            if len(key) < 4 or key in seen:
                continue
            seen.add(key)
            parts.append(seg)
    return "\n\n".join(parts)


def recognize(image_path: str | None, image_b64: str | None, mode: str = "qwen",
              vision_model: str = "") -> OCRResult:
    """识别题目。mode=paddle 走 Paddle 双通道，mode=qwen 走视觉模型。"""
    start = time.time()

    if image_b64 is None:
        if not image_path:
            return OCRResult("OCR 失败：未提供图片", 0.0, "failed")
        with open(image_path, "rb") as f:
            image_b64 = b64(f.read())

    if mode == "qwen":
        from ..ai import engine

        try:
            text = engine.client().vision(image_b64, OCR_VISION_PROMPT, model=vision_model, max_tokens=4000)
            if text and text.strip():
                return OCRResult(text.strip(), time.time() - start, "qwen_vision")
        except Exception as e:  # noqa: BLE001
            log.warning("视觉识别失败，回退 Paddle: %s", e)
        # 视觉失败不返回空：落到 Paddle 通道继续

    # Paddle 双通道并行
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"api": pool.submit(_via_api, image_b64)}
        use_local = os.environ.get("ENABLE_LOCAL_OCR", "false").lower() in ("1", "true", "yes", "on")
        if use_local:
            if _local_ocr is not None:
                futures["local"] = pool.submit(_via_local, image_path)
            else:
                # 未预加载就在这里现加载：首次识别慢，但不丢功能
                if preload_local_model() and image_path:
                    futures["local"] = pool.submit(_via_local, image_path)

        for name, fut in futures.items():
            try:
                out = fut.result(timeout=120)
                if out and out[0]:
                    results[name] = out[0]
            except Exception as e:  # noqa: BLE001
                log.warning("OCR 通道 %s 异常: %s", name, e)

    local_text = results.get("local", "")
    api_text = results.get("api", "")
    if local_text and api_text:
        return OCRResult(_merge(local_text, api_text), time.time() - start, "merged")
    if api_text:
        return OCRResult(polish_paddle(api_text), time.time() - start, "api")
    if local_text:
        return OCRResult(polish_paddle(local_text), time.time() - start, "local")

    # 两条 Paddle 通道都没有 → 最后兜底用视觉模型
    from ..ai import engine

    try:
        text = engine.client().vision(image_b64, OCR_VISION_PROMPT, model=vision_model, max_tokens=4000)
        if text.strip():
            return OCRResult(text.strip(), time.time() - start, "qwen_vision")
    except Exception as e:  # noqa: BLE001
        log.warning("视觉兜底失败: %s", e)

    return OCRResult("OCR 识别失败：所有通道均无结果", time.time() - start, "failed")


def polish_paddle(text: str) -> str:
    """用 LLM 校正 PaddleOCR 的公式与上下标错误。

    Paddle 对印刷体公式很准，但上下标、数字与字母混淆（0/O、1/l/x²/x2）
    这类错误很常见，且会让后续解题整体跑偏。这里做一次轻量校正：
    只允许"修正明显错误"，不允许改写内容。
    """
    if not text.strip():
        return text
    from ..ai import engine

    try:
        return engine.client().complete(
            [{"role": "user", "content": OCR_PADDLE_SUFFIX + "\n\n识别结果：\n" + text[:3000]}],
            system="你是公式校对助手。只修正明显的识别错误，保持原文结构与内容不变。",
            temperature=0.0, max_tokens=4000,
        ).strip() or text
    except Exception as e:  # noqa: BLE001
        log.warning("公式校正失败，使用原始结果: %s", e)
        return text


def recognize_with_boxes(image_b64: str, image_path: str | None) -> tuple[str, list[dict], int, int]:
    """带坐标的识别（供批注定位）。返回 (文本, 文字块列表, 宽, 高)。"""
    width = height = 0
    try:
        if image_path:
            from PIL import Image

            with Image.open(image_path) as im:
                width, height = im.size
    except Exception:  # noqa: BLE001
        pass

    text, blocks, _ = _via_api_with_boxes(image_b64)
    if not blocks:
        # 云端不可用时退回普通识别：此时没有坐标，只能让 LLM 盲猜
        res = recognize(image_path, image_b64, mode="paddle")
        return res.text, [], width, height
    return text, blocks, width, height