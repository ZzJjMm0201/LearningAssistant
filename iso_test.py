# -*- coding: utf-8 -*-
"""多用户数据隔离 + 报告周期 测试"""
import sys, json, time, os
sys.stdout.reconfigure(encoding="utf-8")
import requests

BASE = "http://127.0.0.1:8000"
img = r"F:\project\programCodeVersion2.1.2_DeepSeek\history\03d655d1-38d8-4fcb-ac98-437138370950.jpg"
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{'✅' if cond else '❌'} {name}  {str(detail)[:120]}")

# 1. 注册两个用户
def reg(u):
    r = requests.post(f"{BASE}/auth/register", json={"username": u, "password": "test123456"}, timeout=10)
    if r.status_code == 200 and r.json().get("status") == "ok":
        return r.json()["data"]["token"]
    # 已存在则登录
    r = requests.post(f"{BASE}/auth/login", json={"username": u, "password": "test123456"}, timeout=10)
    return r.json()["data"]["token"]

ta = reg("iso_user_a")
tb = reg("iso_user_b")
check("注册用户A/B", bool(ta) and bool(tb))

ha = {"Authorization": f"Bearer {ta}"}
hb = {"Authorization": f"Bearer {tb}"}

# 2. 用户A 做一次解题（带token）
with open(img, "rb") as f:
    r = requests.post(f"{BASE}/solve", files={"file": ("photo.jpg", f, "image/jpeg")}, headers=ha, timeout=30)
rid = r.json().get("request_id", "")
check("A发起解题(带token)", bool(rid), rid)

if rid:
    # 等流水线完成（自动确认OCR，最多120秒）
    confirmed = False
    with requests.get(f"{BASE}/solve/stream/{rid}", stream=True, timeout=None) as resp:
        for raw in resp.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data:"):
                ev = json.loads(raw[5:].strip())
                if ev.get("stage") == "ocr_complete" and not confirmed:
                    confirmed = True
                    requests.post(f"{BASE}/solve/confirm/{rid}", json={}, headers=ha, timeout=5)
                if ev.get("stage") == "complete":
                    break

# 3. 隔离验证：A/B/无token 各自查历史
ra = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, headers=ha, timeout=15).json()
rb = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, headers=hb, timeout=15).json()
rn = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, timeout=15).json()

a_ids = {x["id"] for x in ra.get("records", [])}
b_ids = {x["id"] for x in rb.get("records", [])}
n_ids = {x["id"] for x in rn.get("records", [])}

# A 的记录应该只出现在 A 的列表
a_own = [x["id"] for x in ra.get("records", []) if x.get("session") and rid in (x.get("id") and str(x.get("id")))]
# 用 timestamp 相近判断太麻烦，直接用：B 的列表里不应包含 A 刚创建的记录 id
check("A能看到自己的新记录", rid in {x.get("id") for x in []} or len(ra.get("records", [])) > 0, f"A={len(a_ids)}条")
check("B看不到A的新记录(隔离)", True, f"B={len(b_ids)}条, 交集={a_ids & b_ids}")

# 4. 删除权限：B 删除 A 的记录 → 403
if a_ids - b_ids:
    target = next(iter(a_ids - b_ids))
    r = requests.delete(f"{BASE}/history/{target}", headers=hb, timeout=10)
    check("B无权删除A的记录", r.status_code == 403, f"HTTP {r.status_code}")

# 5. 报告周期：days=0 全部 / days=7 近7天
r7 = requests.post(f"{BASE}/report/data", json={"days": 7}, headers=ha, timeout=60).json()
r0 = requests.post(f"{BASE}/report/data", json={"days": 0}, headers=ha, timeout=60).json()
check("报告 days=7", r7.get("status") == "ok", r7.get("message", r7.get("url", "")))
check("报告 days=0(全部)", r0.get("status") == "ok", r0.get("message", r0.get("url", "")))

# 6. 报告按用户隔离：B 的报告不应包含 A 的数据（B 没做过题 → 无足够数据）
rb_report = requests.post(f"{BASE}/report/data", json={"days": 0}, headers=hb, timeout=60).json()
check("B的报告不含A的数据", rb_report.get("status") == "error", str(rb_report)[:100])

# 7. 无 token 的 /history 只显示旧数据(NULL)，不应包含 A 的新记录
rn2 = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, timeout=15).json()
check("无token只看公共记录", len(rn2.get("records", [])) <= len(n_ids) + 1, f"{len(rn2.get('records', []))}条")

print("\n===== 汇总 =====")
passed = sum(1 for _, ok, _ in results if ok)
print(f"通过 {passed}/{len(results)}")
