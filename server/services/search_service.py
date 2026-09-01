import time
import json
import re
import requests
from typing import Optional, Tuple
from server.config import APIConfig, FEATURE_FLAGS

class SearchService:
    """题库搜索服务 - 带智能判断"""
    
    def __init__(self):
        self.access_key_id = APIConfig.SEARCH_ACCESS_KEY_ID
        self.access_key_secret = APIConfig.SEARCH_ACCESS_KEY_SECRET
        self.url = "https://openai.100tal.com/aiimage/search-questions"
        self.enabled = FEATURE_FLAGS["enable_question_search"]
    
    def should_search(self, ocr_text: str) -> bool:
        """
        智能判断是否需要调用题库搜索
        
        节省API调用费用的策略：
        1. 检查是否开启搜索功能
        2. 文本过短或过长都不适合搜索
        3. 包含明显的学习材料文本特征不搜索
        """
        if not self.enabled:
            return False
        
        text_len = len(ocr_text)
        
        # 太短或太长不适合搜索
        if text_len < 10 or text_len > 500:
            return False
        
        # 包含阅读材料特征
        reading_patterns = [
            r'阅读', r'材料', r'根据.*回答',
            r'第[一二三四五六七八九十\d]+段',
            r'\d{4}年',
        ]
        for pattern in reading_patterns:
            if re.search(pattern, ocr_text):
                return False
        
        # 包含明显题目特征
        question_patterns = [
            r'[（(]\s*\d+\s*分\s*[）)]',
            r'(求证|证明|求解|计算|化简|判断)',
            r'[ABCD][\.、．]',
            r'[①②③④⑤]',
            r'(正确的是|错误的是|属于|不属于)',
        ]
        for pattern in question_patterns:
            if re.search(pattern, ocr_text):
                return True
        
        # 默认决定：中等长度且无明显材料特征 -> 搜索
        return 50 < text_len < 300
    
    def search(self, ocr_text: str) -> Tuple[Optional[str], float, list]:
        """执行题库搜索
        Returns:
            (AI上下文用的Markdown摘要, 耗时秒数, 结构化结果列表[{question_md,hint_md,answer_md,subject,grade,point_name}])
        """
        if not self.should_search(ocr_text):
            return None, 0.0, []
        
        start_time = time.time()
        
        try:
            # 构建请求 (使用原有逻辑)
            from server.utils.signature_generator import generate_signature
            
            url_params = {
                "access_key_id": self.access_key_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "signature_nonce": str(time.time()),
            }
            
            body_params = {
                "words": ocr_text,
                "function": 0
            }
            
            signature = generate_signature(
                url_params, body_params, self.access_key_secret
            )
            url_params["signature"] = signature
            
            response = requests.post(
                self.url, 
                params=url_params, 
                json=body_params,
                timeout=15
            )
            
            result = response.json()
            elapsed = time.time() - start_time
            
            if result.get("code") != 20000:
                print(f"搜索失败: {result.get('msg')}")
                return None, elapsed, []
            
            # 格式化搜索结果
            questions = result["data"]["questionArr"]
            if not questions:
                return None, elapsed, []
            
            structured = []
            formatted = []
            for idx, q in enumerate(questions[:3]):  # 最多取3个结果
                item = {
                    "question_md": html_to_markdown(q.get("question", "")),
                    "hint_md": html_to_markdown(q.get("hint", "")),
                    "answer_md": html_to_markdown(q.get("answer", "")),
                    "subject": q.get("subject", ""),
                    "grade": q.get("grade", ""),
                    "point_name": q.get("point_name", ""),
                }
                structured.append(item)
                formatted.append(f"## 参考题目 {idx + 1}")
                if item["subject"]:
                    formatted.append(f"学科：{item['subject']}")
                formatted.append(item["question_md"])
                if item["hint_md"]:
                    formatted.append(f"**解析**：{item['hint_md']}")
                if item["answer_md"]:
                    formatted.append(f"**答案**：{item['answer_md']}")
                formatted.append("---")
            
            return "\n".join(formatted), elapsed, structured
            
        except Exception as e:
            print(f"题库搜索异常: {e}")
            elapsed = time.time() - start_time
            return None, elapsed, []


def html_to_markdown(html: str) -> str:
    """把好未来题库返回的HTML转成Markdown（保留图片）"""
    if not html:
        return ""
    import re as _re
    # 图片 <img src="..."> → ![图](url)
    def _img_repl(m):
        src = m.group(1) or ""
        return f"\n\n![图]({src})\n\n" if src else ""
    html = _re.sub(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', _img_repl, html)
    html = _re.sub(r'<img[^>]*>', '', html)
    # 去掉 style/script
    html = _re.sub(r'<style[^>]*>.*?</style>', '', html, flags=_re.S)
    html = _re.sub(r'<script[^>]*>.*?</script>', '', html, flags=_re.S)
    # 块级标签换行
    html = _re.sub(r'</?(?:p|div|br|li|tr|h\d)[^>]*>', '\n', html)
    # 去掉剩余标签
    html = _re.sub(r'<[^>]+>', '', html)
    # 清理多余空行与空白
    html = _re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()

search_service = SearchService()