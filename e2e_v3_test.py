# -*- coding: utf-8 -*-
"""第三轮功能测试：流式扩展（steps/mistakes/extension/ai报告）+ LaTeX多图 + OCR开关"""
import sys, json, time, os, re
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion3.0.0_DeepSeek\history\03d655d1-38d8-4fcb-ac98-437138370950.jpg"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'OK ' if cond else 'MISS'} {name}  {str(detail)[:120]}")

# 1. 健康检查（OCR开关确认）
r = requests.get(f"{BASE}/health", timeout=5)
check("health", r.status_code == 200, str(r.json()))

# 2. 解题流程：验证新事件序列
with open(img, "rb") as f:
    r = requests.post(f"{BASE}/solve", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
rid = r.json().get("request_id", "")
check("POST /solve", bool(rid), rid)

stages = []
steps_chunks = 0
solution_chunks = 0
has_latex_extras = False
solution_rendered = ""
if rid:
    start = time.time()
    confirmed = False
    with requests.get(f"{BASE}/solve/stream/{rid}", stream=True, timeout=None) as resp:
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None or not raw.startswith("data:"):
                continue
            try:
                ev = json.loads(raw[5:].strip())
            except Exception:
                continue
            stage = ev.get("stage", "")
            stages.append(stage)
            if stage == "ocr_complete" and not confirmed:
                confirmed = True
                requests.post(f"{BASE}/solve/confirm/{rid}", json={}, timeout=5)
            if stage == "solution_steps_chunk":
                steps_chunks += 1
            if stage == "solution_chunk":
                solution_chunks += 1
            if stage == "latex_extras":
                has_latex_extras = True
            if stage in ("solution", "solution_rendered"):
                solution_rendered = ev.get("content", "")
            if stage == "complete":
                break
            if time.time() - start > 240:
                break

check("解题思路流式(steps_chunk)", steps_chunks >= 2, f"{steps_chunks} chunks")
check("完整解析流式(solution_chunk)", solution_chunks >= 2, f"{solution_chunks} chunks")
check("LaTeX补充轮(latex_extras)", has_latex_extras, "")
check("solution_rendered含图解辅助", "图解辅助" in solution_rendered or "图解" in solution_rendered, "")
imgs = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', solution_rendered)
check("图解数量>=1", len(imgs) >= 1, f"{len(imgs)} 张: {imgs[:4]}")
check("行内公式存在(客户端将转换$)", "$" in solution_rendered, "含$标记")
print("阶段序列(前20):", stages[:20])

# 3. 知识延伸：mistakes/extension 流式
with open(img, "rb") as f:
    r = requests.post(f"{BASE}/extend", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
ext_id = r.json().get("request_id", "")
ext_stages = []
mistakes_chunks = 0
extension_chunks = 0
if ext_id:
    with requests.get(f"{BASE}/extend/stream/{ext_id}", stream=True, timeout=None) as resp:
        for raw in resp.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data:"):
                ev = json.loads(raw[5:].strip())
                st = ev.get("stage")
                ext_stages.append(st)
                if st == "mistakes_chunk":
                    mistakes_chunks += 1
                if st == "extension_chunk":
                    extension_chunks += 1
                if st == "complete":
                    break
check("易错点流式(mistakes_chunk)", mistakes_chunks >= 2, f"{mistakes_chunks} chunks")
check("知识拓展流式(extension_chunk)", extension_chunks >= 2, f"{extension_chunks} chunks")

# 4. AI报告流式
r = requests.post(f"{BASE}/report/ai/stream", json={"days": 0}, timeout=15)
check("/report/ai/stream 可访问", r.status_code == 200, "")
report_chunks = 0
report_text = ""
if r.status_code == 200:
    for raw in r.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            try:
                ev = json.loads(raw[5:].strip())
            except Exception:
                continue
            if ev.get("stage") == "report_chunk":
                report_chunks += 1
                report_text = ev.get("content", "")
            if ev.get("stage") == "complete":
                break
check("AI报告流式(report_chunk)", report_chunks >= 3, f"{report_chunks} chunks, 文本{len(report_text)}字")

print("\n===== 汇总 =====")
passed = sum(1 for _, ok, _ in results if ok)
print(f"通过 {passed}/{len(results)}")
