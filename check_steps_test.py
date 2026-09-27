# -*- coding: utf-8 -*-
"""验证①：解题思路不再是JSON；顺带验证完整流程"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion3.0.0_DeepSeek\history\565a3029-0f7c-41d5-a410-517e685f6ed0.jpg"

with open(img, "rb") as f:
    r = requests.post(f"{BASE}/solve", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
rid = r.json().get("request_id", "")
print("request_id:", rid)

steps_text = ""
is_json_steps = False
mindmap_chunks = 0
suggested = None
stages = []
if rid:
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
            elif stage == "solution_steps":
                steps_text = ev.get("content", "")
            elif stage == "mindmap_chunk":
                mindmap_chunks += 1
            elif stage == "suggested_questions":
                suggested = ev.get("content")
            elif stage == "complete":
                break

print("=== 解题思路(前300字) ===")
print(steps_text[:300])
print("=== 检查 ===")
print("steps是JSON?", steps_text.strip().startswith("{"))
print("mindmap_chunk数:", mindmap_chunks)
print("suggested类型:", type(suggested).__name__, json.dumps(suggested, ensure_ascii=False)[:120] if suggested else None)
print("流程完成:", "complete" in stages)
