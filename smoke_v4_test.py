# -*- coding: utf-8 -*-
"""第四轮冒烟测试：思维导图流式(mindmap_chunk) + 预测问题带答案(suggested_questions 对象数组)"""
import sys, json, time, os
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion3.0.0_DeepSeek\history\565a3029-0f7c-41d5-a410-517e685f6ed0.jpg"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'OK ' if cond else 'MISS'} {name}  {str(detail)[:150]}")

r = requests.get(f"{BASE}/health", timeout=5)
check("health", r.status_code == 200, str(r.json()))

with open(img, "rb") as f:
    r = requests.post(f"{BASE}/solve", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
rid = r.json().get("request_id", "")
check("POST /solve", bool(rid), rid)

stages = []
mindmap_chunks = 0
mindmap_final = ""
suggested = None
suggested_has_answer = False
rendered = ""
if rid:
    start = time.time()
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
            if stage == "ocr_complete":
                requests.post(f"{BASE}/solve/confirm/{rid}", json={}, timeout=5)
            elif stage == "mindmap_chunk":
                mindmap_chunks += 1
                mindmap_final = ev.get("content", "")
            elif stage == "mindmap":
                mindmap_final = ev.get("content", "")
            elif stage == "suggested_questions":
                suggested = ev.get("content")
            elif stage == "solution_rendered":
                rendered = ev.get("content", "")
            elif stage == "complete":
                break
            if time.time() - start > 240:
                break

check("思维导图流式(mindmap_chunk)", mindmap_chunks >= 2, f"{mindmap_chunks} chunks")
check("思维导图围栏规范化", mindmap_final.strip().startswith("```") and mindmap_final.rstrip().endswith("```"), f"len={len(mindmap_final)}")
check("预测问题为对象数组", isinstance(suggested, list) and len(suggested) > 0, f"{json.dumps(suggested, ensure_ascii=False)[:150]}")
if isinstance(suggested, list) and suggested and isinstance(suggested[0], dict):
    suggested_has_answer = all("question" in it and "answer" in it for it in suggested if isinstance(it, dict))
check("问题含answer字段", suggested_has_answer, "")
check("完整流程到达complete", "complete" in stages, "")
check("渲染含图解", "图解" in rendered, "")
print("阶段序列:", stages)
print("预测问题示例:", json.dumps(suggested, ensure_ascii=False)[:300] if suggested else None)

print("\n===== 汇总 =====")
passed = sum(1 for _, ok, _ in results if ok)
print(f"通过 {passed}/{len(results)}")
