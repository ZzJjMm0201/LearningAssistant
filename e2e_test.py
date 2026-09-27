# -*- coding: utf-8 -*-
"""端到端功能测试：验证修复后的流式输出、LaTeX图形、认证、历史、追问等"""
import json, sys, time, io, os
import requests

# 强制UTF-8输出，避免Windows GBK控制台报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'✅' if cond else '❌'} {name}  {detail}"[:160])

# 1. 健康检查
try:
    r = requests.get(f"{BASE}/health", timeout=5)
    check("GET /health", r.status_code == 200 and r.json().get("status") == "healthy", str(r.json()))
except Exception as e:
    check("GET /health", False, str(e))

# 2. 注册/登录
import random
uname = f"tester_{random.randint(10000, 99999)}"
try:
    r = requests.post(f"{BASE}/auth/register", json={"username": uname, "password": "test123456"}, timeout=10)
    reg_ok = r.status_code == 200 and r.json().get("status") == "ok"
    check("POST /auth/register", reg_ok, r.text[:120])
    token = r.json().get("data", {}).get("token", "") if reg_ok else ""
    r = requests.post(f"{BASE}/auth/login", json={"username": uname, "password": "test123456"}, timeout=10)
    login_ok = r.status_code == 200 and r.json().get("status") == "ok"
    check("POST /auth/login", login_ok, r.text[:120])
    if login_ok and not token:
        token = r.json().get("data", {}).get("token", "")
    if token:
        r = requests.post(f"{BASE}/auth/verify", json={"token": token}, timeout=10)
        check("POST /auth/verify", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])
except Exception as e:
    check("注册/登录", False, str(e))

# 3. 解题主流程（SSE流式）
img = r"F:\project\programCodeVersion3.0.0_DeepSeek\history\03d655d1-38d8-4fcb-ac98-437138370950.jpg"
if not os.path.exists(img):
    # 换一个存在的历史图片
    hdir = r"F:\project\programCodeVersion3.0.0_DeepSeek\history"
    cands = [os.path.join(hdir, f) for f in os.listdir(hdir) if f.endswith(".jpg")]
    img = cands[0] if cands else None
check("测试图片存在", img is not None and os.path.exists(img), img or "无")

request_id = None
stages_seen = []
chunk_count = 0
solution_rendered = ""
if img:
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with open(img, "rb") as f:
            r = requests.post(f"{BASE}/solve", files={"file": ("photo.jpg", f, "image/jpeg")}, headers=headers, timeout=30)
        check("POST /solve", r.status_code == 200 and r.json().get("request_id"), r.text[:120])
        request_id = r.json().get("request_id")

        # SSE流读取（最长等待240秒）
        if request_id:
            start = time.time()
            confirmed = False
            try:
                with requests.get(f"{BASE}/solve/stream/{request_id}", stream=True, timeout=None) as resp:
                    for raw in resp.iter_lines(decode_unicode=True):
                        if raw is None or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        try:
                            ev = json.loads(data)
                        except Exception:
                            continue
                        stage = ev.get("stage", "")
                        stages_seen.append(stage)
                        if stage == "ocr_complete" and not confirmed:
                            # 模拟客户端点击“确认”，跳过30秒等待
                            confirmed = True
                            try:
                                requests.post(f"{BASE}/solve/confirm/{request_id}", json={}, timeout=5)
                            except Exception:
                                pass
                        if stage == "solution_chunk":
                            chunk_count += 1
                        if stage in ("solution", "solution_rendered"):
                            solution_rendered = ev.get("content", "")
                        if stage == "complete":
                            break
                        if time.time() - start > 240:
                            break
            except Exception as e:
                check("SSE流读取", False, str(e))
    except Exception as e:
        check("POST /solve", False, str(e))

# 4. 校验SSE事件序列与流式
check("SSE事件包含 solution_chunk（流式）", chunk_count >= 2, f"{chunk_count} 个chunk")
expect_seq = ["ocr_complete", "solution_steps", "solution_chunk", "solution", "solution_rendered", "mindmap", "suggested_questions", "complete"]
for s in expect_seq:
    check(f"SSE阶段出现: {s}", s in stages_seen, f"总阶段: {stages_seen[:20]}...")

# 5. 校验LaTeX图形URL与文件真实性
import re
urls = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', solution_rendered)
check("solution_rendered 含图片URL", len(urls) > 0, f"{len(urls)} 张图: {urls[:3]}")
png_real = False
if urls:
    u = urls[0]
    if u.startswith("http://"):
        # 换成本机地址验证（Host头动态生成）
        u = u.replace("http://10.100.55.231:8000", BASE).replace("http://127.0.0.1:8000", BASE)
        try:
            r = requests.get(u, timeout=10)
            body = r.content
            png_real = r.status_code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"
            check("LaTeX图片可下载且为真PNG", png_real, f"HTTP {r.status_code}, 文件头: {body[:8].hex()}, 大小: {len(body)}")
        except Exception as e:
            check("LaTeX图片下载", False, str(e))
    else:
        check("图片URL为相对路径(待客户端解析)", True, u)

# 6. 追问（验证上下文来自数据库）
if request_id:
    try:
        r = requests.post(f"{BASE}/ask", json={"session_id": request_id, "question": "请用一句话总结这道题的思路"}, timeout=120)
        check("POST /ask（追问有上下文）", r.status_code == 200 and len(r.json().get("answer", "")) > 10, r.text[:150])
    except Exception as e:
        check("POST /ask", False, str(e))

# 7. 历史记录
try:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, headers=headers, timeout=15)
    recs = r.json().get("records", [])
    check("POST /history", r.status_code == 200, f"{len(recs)} 条记录")
except Exception as e:
    check("POST /history", False, str(e))

# 8. 知识延伸（OCR较慢，仅当有图片时测）
if img:
    try:
        with open(img, "rb") as f:
            r = requests.post(f"{BASE}/extend", files={"file": ("photo.jpg", f, "image/jpeg")}, timeout=30)
        ext_id = r.json().get("request_id", "")
        check("POST /extend", r.status_code == 200 and ext_id, r.text[:120])
        if ext_id:
            ext_stages = []
            with requests.get(f"{BASE}/extend/stream/{ext_id}", stream=True, timeout=5) as resp:
                for raw in resp.iter_lines(decode_unicode=True):
                    if raw and raw.startswith("data:"):
                        ev = json.loads(raw[5:].strip())
                        ext_stages.append(ev.get("stage"))
                        if ev.get("stage") == "complete":
                            break
            check("知识延伸SSE完成", "complete" in ext_stages, f"阶段: {ext_stages}")
    except Exception as e:
        check("POST /extend", False, str(e))

# 9. 报告
try:
    r = requests.post(f"{BASE}/report/data", json={"days": 7}, timeout=60)
    check("POST /report/data", r.status_code == 200, r.text[:120])
except Exception as e:
    check("POST /report/data", False, str(e))
try:
    r = requests.post(f"{BASE}/report/ai", json={"days": 7}, timeout=120)
    check("POST /report/ai", r.status_code == 200 and len(r.json().get("report", "")) > 20, r.text[:150])
except Exception as e:
    check("POST /report/ai", False, str(e))

print("\n===== 测试汇总 =====")
passed = sum(1 for _, ok, _ in results if ok)
print(f"通过 {passed}/{len(results)}")
for name, ok, detail in results:
    print(f"  {'✅' if ok else '❌'} {name}")
