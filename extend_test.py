# -*- coding: utf-8 -*-
"""单独验证知识延伸 SSE 流完整性"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion2.1.2_DeepSeek\history\03d655d1-38d8-4fcb-ac98-437138370950.jpg"

with open(img, "rb") as f:
    r = requests.post(f"{BASE}/extend", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
rid = r.json().get("request_id", "")
print("request_id:", rid)

stages = []
contents = {}
t0 = time.time()
with requests.get(f"{BASE}/extend/stream/{rid}", stream=True, timeout=None) as resp:
    for raw in resp.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            ev = json.loads(raw[5:].strip())
            st = ev.get("stage", "")
            stages.append(st)
            c = ev.get("content")
            if isinstance(c, str) and c:
                contents.setdefault(st, 0)
                contents[st] = max(contents[st], len(c))
            if st == "complete":
                break
print("耗时:", round(time.time()-t0, 1), "s")
print("阶段序列:", stages)
print("各阶段内容长度:", contents)
need = ["info", "ocr_complete", "question_info", "mistakes", "extension", "suggested_questions", "complete"]
ok = all(s in stages for s in need)
print("EXTEND TEST:", "PASS" if ok else "FAIL")
