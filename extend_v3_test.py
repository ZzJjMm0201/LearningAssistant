# -*- coding: utf-8 -*-
"""验证知识延伸流式（mistakes_chunk/extension_chunk）"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion3.0.0_DeepSeek\history\03d655d1-38d8-4fcb-ac98-437138370950.jpg"

with open(img, "rb") as f:
    r = requests.post(f"{BASE}/extend", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
rid = r.json().get("request_id", "")
print("extend id:", rid)

stages = []
mistakes_chunks = 0
extension_chunks = 0
t0 = time.time()
with requests.get(f"{BASE}/extend/stream/{rid}", stream=True, timeout=None) as resp:
    for raw in resp.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            ev = json.loads(raw[5:].strip())
            st = ev.get("stage")
            stages.append(st)
            if st == "mistakes_chunk":
                mistakes_chunks += 1
            if st == "extension_chunk":
                extension_chunks += 1
            if st == "complete":
                break
print("耗时:", round(time.time()-t0, 1), "s")
print("mistakes_chunk:", mistakes_chunks, " extension_chunk:", extension_chunks)
print("阶段:", [s for s in stages if not s.endswith("_chunk") or True][:15], "...")
print("EXTEND STREAM:", "PASS" if mistakes_chunks >= 2 and extension_chunks >= 2 else "FAIL")
