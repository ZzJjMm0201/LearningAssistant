# -*- coding: utf-8 -*-
"""验证②③⑥：/ask/stream 流式回答 + /geogebra 官方Applet + 引擎切换"""
import sys, json, sqlite3
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"

# ===== ② /ask/stream =====
con = sqlite3.connect(r"F:\project\programCodeVersion2.1.2_DeepSeek\data\learning_assistant.db")
row = con.execute("SELECT session_id FROM conversation_history ORDER BY id DESC LIMIT 1").fetchone()
con.close()
sid = row[0] if row else None
print("session:", sid)

if sid:
    r = requests.post(f"{BASE}/ask/stream",
                      json={"session_id": sid, "question": "请用一句话总结这道题的解题关键"},
                      headers={"X-Engine": "deepseek"}, stream=True, timeout=90)
    print("ask/stream status:", r.status_code)
    chunks = 0
    answer = ""
    for raw in r.iter_lines(decode_unicode=True):
        if raw and raw.startswith("data:"):
            try:
                ev = json.loads(raw[5:].strip())
            except Exception:
                continue
            st = ev.get("stage")
            if st == "answer_chunk":
                chunks += 1
                answer = ev.get("content", "")
            if st == "complete":
                break
    print("chunks:", chunks)
    print("answer前200字:", answer[:200])
    print("是JSON?", answer.strip().startswith("{") or answer.strip().startswith("["))

# ===== ③ /geogebra =====
r = requests.post(f"{BASE}/geogebra",
                  json={"ocr_text": "已知函数 f(x)=x^2-2x+3，求它的顶点坐标和对称轴。"},
                  headers={"X-Engine": "deepseek"}, timeout=90)
j = r.json()
print("\ngeogebra status:", j.get("status"))
url = j.get("url", "")
print("geogebra url:", url)
if url:
    html = requests.get(f"{BASE}{url}", timeout=10).text
    print("HTML含deployggb.js?", "deployggb.js" in html)
    print("HTML含evalCommand?", "evalCommand" in html)
    print("命令数:", len(j.get("commands", [])))
    print("命令示例:", j.get("commands", [])[:4])
