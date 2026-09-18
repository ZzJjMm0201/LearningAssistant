"""
OCR服务封装
支持PaddleOCR-VL API和本地PaddleOCR两种模式同时运行、合并结果
本地模型在服务器启动时预加载，避免首次请求延迟
"""
import base64
import os
import time
import re
import threading
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ====== PaddlePaddle 兼容性修复 ======
# 必须在任何Paddle导入前设置环境变量，禁用oneDNN避免算子兼容性问题
os.environ.setdefault('FLAGS_use_mkldnn', '0')
os.environ.setdefault('FLAGS_use_onednn', '0')
os.environ.setdefault('FLAGS_enable_pir_api', '0')  # 禁用PIR新执行器，使用旧版执行器
from typing import Tuple, Optional, List, Dict

from server.config import APIConfig, FEATURE_FLAGS


class OCRService:
    """OCR识别服务 - API和本地双通道并行"""
    
    def __init__(self):
        self.api_url = APIConfig.OCR_API_URL
        self.token = APIConfig.OCR_TOKEN
        # 云端 OCR 偶发挂起，超时可配置（默认 15s，避免用户干等 60s）
        self._api_timeout = getattr(APIConfig, "OCR_API_TIMEOUT", 15)
        self._local_ocr = None
        self._local_ready = False
        self._local_lock = threading.Lock()
        # 预加载完成标志：避免启动期本地通道被误跳过
        self._preload_done = False
        # 线程池用于并行OCR
        self._executor = ThreadPoolExecutor(max_workers=2)
    
    def preload_local_model(self):
        """预加载本地PaddleOCR模型（服务启动时调用一次）"""
        self._init_paddle_env()
        print("[OCR] 正在预加载本地PaddleOCR模型...")
        self._preload_done = True
        try:
            from paddleocr import PaddleOCR
            start = time.time()
            # 创建PaddleOCR实例即会加载模型到内存
            self._local_ocr = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_server_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            elapsed = time.time() - start
            self._local_ready = True
            print(f"[OCR] 本地PaddleOCR模型预加载完成，耗时{elapsed:.1f}s")
        except ImportError:
            print("[OCR] 未安装paddleocr，本地模式不可用")
            self._local_ready = False
        except Exception as e:
            err_msg = str(e)
            if "No module named" in err_msg:
                print(f"[OCR] 本地模式不可用（缺少依赖），仅使用API模式")
            else:
                print(f"[OCR] 本地模型预加载完成（警告）: {err_msg[:200]}")
                # 虽然虚警但模型实际已加载，标记为可用
                self._local_ready = True
    
    def _init_paddle_env(self):
        """配置PaddlePaddle环境变量以解决兼容性问题"""
        import os
        # 禁用oneDNN优化，避免某些算子兼容性问题
        os.environ['FLAGS_use_mkldnn'] = '0'
        os.environ['FLAGS_use_onednn'] = '0'
    
    def recognize(self, image_path: str, mode: str = "paddle", vision_model: Optional[str] = None) -> Tuple[str, float, str]:
        """
        OCR识别（Paddle双通道 或 千问视觉模型）
        
        Args:
            image_path: 图片文件路径
            mode: "paddle"=PaddleOCR API+本地双通道（默认）；"qwen"=千问视觉模型（流程图/统计图会用自然语言描述）
            vision_model: 千问视觉模型名（mode=qwen 时生效，默认 qwen3.8-max）
        
        Returns:
            (识别文本, 耗时秒数, 来源说明)
        """
        if mode == "qwen":
            from server.services.ai_service import ai_service
            text, elapsed = ai_service.recognize_image_with_vision(image_path, model=vision_model)
            if text:
                print(f"[OCR] 千问视觉识别完成，耗时{elapsed}s，文本长度={len(text)}")
                return text, elapsed, "qwen_vision"
            print("[OCR] 千问视觉识别失败/为空，回退到Paddle")
            # 回退到 Paddle 通道
            text2, elapsed2, src2 = self._recognize_paddle(image_path)
            return text2, elapsed2, src2
        
        return self._recognize_paddle(image_path)
    
    def _recognize_paddle(self, image_path: str) -> Tuple[str, float, str]:
        """PaddleOCR 双通道识别（API + 本地并行）"""
        start_total = time.time()
        results = {}  # source -> (text, time)
        
        # 两路并行
        futures = {}
        
        # 通道1: API（默认开启）
        futures[self._executor.submit(self._recognize_via_api, image_path)] = "api"
        
        # 通道2: 本地（默认关闭，可在config中开启）
        # 注意：启动时是异步预加载；不在请求路径里阻塞等待（避免首请求干等），
        # 就绪则并行参与，未就绪则本次仅走 API 通道，后续请求自然用上本地。
        if FEATURE_FLAGS.get("enable_local_ocr", False) and self._local_ready:
            futures[self._executor.submit(self._recognize_local, image_path)] = "local"
        elif FEATURE_FLAGS.get("enable_local_ocr", False):
            print("[OCR] 本地通道加载中，本次仅使用API通道")
        elif self._local_ready:
            print("[OCR] 本地通道已按配置关闭（enable_local_ocr=False），仅使用API通道")
        
        # 收集结果（无论本地通道是否就绪，都要收集API通道的结果，否则会白白丢弃）
        try:
            for future in as_completed(futures, timeout=120):
                source = futures[future]
                try:
                    text, elapsed = future.result()
                    results[source] = (text, elapsed)
                    print(f"[OCR] {source} 完成，耗时{elapsed}s，文本长度={len(text)}")
                except Exception as e:
                    print(f"[OCR] {source} 异常: {e}")
        except TimeoutError:
            print("[OCR] 部分识别通道超时，将使用已成功返回的结果继续处理")
        
        # 合并策略
        api_text = results.get("api", ("", 0))[0]
        local_text = results.get("local", ("", 0))[0]
        
        if api_text and local_text:
            # 两者都成功：合并（本地 PaddleOCR 对公式/符号识别通常更准，故先本地后 API）
            merged = self._merge_ocr_results(local_text, api_text)
            total_time = time.time() - start_total
            print(f"[OCR] 双通道合并完成，总耗时{total_time:.1f}s，合并长度={len(merged)}")
            return merged, round(total_time, 2), "merged"
        elif api_text:
            total_time = time.time() - start_total
            print(f"[OCR] 仅API结果，耗时{total_time:.1f}s")
            return api_text, round(total_time, 2), "api"
        elif local_text:
            total_time = time.time() - start_total
            print(f"[OCR] 仅本地结果，耗时{total_time:.1f}s")
            return local_text, round(total_time, 2), "local"
        else:
            total_time = time.time() - start_total
            return "OCR识别失败：API和本地均无结果", round(total_time, 2), "failed"
    
    def _merge_ocr_results(self, api_text: str, local_text: str) -> str:
        """
        合并API和本地OCR结果
        策略：取并集，以API文本为骨架，补充本地文本中API未识别到的部分
        """
        if not api_text:
            return local_text
        if not local_text:
            return api_text
        
        # 简单合并：两者都取，标注来源
        # 实际可以做得更智能（文本相似度去重），但先做简单合并
        merged = f"""【API识别结果】
{api_text}

【本地识别结果】
{local_text}

【综合识别结果】
{api_text}

{local_text}"""
        return merged
    
    def _recognize_via_api(self, image_path: str) -> Tuple[str, float]:
        """通过PaddleOCR-VL API识别"""
        start_time = time.time()
        
        try:
            with open(image_path, "rb") as f:
                file_bytes = f.read()
                file_data = base64.b64encode(file_bytes).decode("ascii")
            
            headers = {
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "file": file_data,
                "fileType": 1,
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            }
            
            response = requests.post(
                self.api_url, 
                json=payload, 
                headers=headers,
                timeout=self._api_timeout
            )
            
            if response.status_code != 200:
                raise Exception(f"OCR API错误: {response.status_code}")
            
            result = response.json()["result"]
            
            # 提取文本
            texts = []
            for res in result.get("layoutParsingResults", []):
                md_text = res.get("markdown", {}).get("text", "")
                if md_text:
                    texts.append(md_text)
            
            elapsed = time.time() - start_time
            return "\n\n".join(texts), round(elapsed, 2)
            
        except Exception as e:
            print(f"[OCR] API失败: {e}")
            return "", time.time() - start_time

    def recognize_with_boxes(self, image_path: str) -> Tuple[str, List[Dict], int, int, float]:
        """⑰ AI批注专用：返回 (文本, 文本块列表, 宽, 高, 耗时)

        文本块形如 {"text": "...", "bbox": [l, t, r, b]}，供批注定位使用。
        优先走 PaddleOCR API（它带回 layout 坐标）；失败时返回空块列表。
        """
        start_time = time.time()
        blocks: List[Dict] = []
        width = height = 0

        # 图片尺寸（用于给 LLM 提供坐标系）
        try:
            from PIL import Image as _Image
            with _Image.open(image_path) as _im:
                width, height = _im.size
        except Exception:
            pass

        try:
            with open(image_path, "rb") as f:
                file_data = base64.b64encode(f.read()).decode("ascii")
            headers = {"Authorization": f"token {self.token}", "Content-Type": "application/json"}
            payload = {
                "file": file_data,
                "fileType": 1,
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            }
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=self._api_timeout)
            if response.status_code != 200:
                raise Exception(f"OCR API错误: {response.status_code}")
            result = response.json()["result"]

            texts = []
            for res in result.get("layoutParsingResults", []):
                md_text = res.get("markdown", {}).get("text", "")
                if md_text:
                    texts.append(md_text)
                pruned = res.get("prunedResult") or {}
                if not width or not height:
                    width = int(pruned.get("width") or width or 0)
                    height = int(pruned.get("height") or height or 0)
                # parsing_res_list 里带 block_content + block_bbox，最适合做批注定位
                for blk in (pruned.get("parsing_res_list") or []):
                    txt = str(blk.get("block_content") or "").strip()
                    bb = blk.get("block_bbox")
                    if txt and isinstance(bb, (list, tuple)) and len(bb) >= 4:
                        blocks.append({"text": txt, "bbox": [float(v) for v in bb[:4]]})
            elapsed = time.time() - start_time
            return "\n\n".join(texts), blocks, width, height, round(elapsed, 2)
        except Exception as e:
            print(f"[OCR] 带坐标识别失败: {e}")
            return "", [], width, height, round(time.time() - start_time, 2)
    
    def _recognize_local(self, image_path: str) -> Tuple[str, float]:
        """本地PaddleOCR识别"""
        self._init_paddle_env()
        start_time = time.time()
        
        try:
            # if not self._local_ocr:
            #     from paddleocr import PaddleOCR
            #     self._local_ocr = PaddleOCR(
            #         text_detection_model_name="PP-OCRv5_mobile_det",
            #         text_recognition_model_name="PP-OCRv5_server_rec",
            #         use_doc_orientation_classify=False,
            #         use_doc_unwarping=False,
            #         use_textline_orientation=False,
            #     )
            result = self._local_ocr.predict(image_path)
            
            texts = result[0].get("rec_texts", [])
            elapsed = time.time() - start_time
            return "\n".join(texts), round(elapsed, 2)
            
        except ImportError:
            return "", time.time() - start_time
        except Exception as e:
            print(f"[OCR] 本地识别失败: {e}")
            return "", time.time() - start_time


# 全局单例
ocr_service = OCRService()
