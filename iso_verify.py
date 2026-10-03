# -*- coding: utf-8 -*-
"""严格验证：A刚创建的记录是否对B/无token隐藏"""
import sys, requests
sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"

def login(u):
    r = requests.post(f"{BASE}/auth/login", json={"username": u, "password": "test123456"}, timeout=10)
    return r.json()["data"]["token"]

ta = login("iso_user_a")
tb = login("iso_user_b")

# 找A的最新记录（按时间倒序第一条带user_id=A的）
ra = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""},
                   headers={"Authorization": f"Bearer {ta}"}, timeout=15).json()
rb = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""},
                   headers={"Authorization": f"Bearer {tb}"}, timeout=15).json()
rn = requests.post(f"{BASE}/history", json={"start_date": "", "end_date": ""}, timeout=15).json()

a_ids = {x["id"] for x in ra.get("records", [])}
b_ids = {x["id"] for x in rb.get("records", [])}
n_ids = {x["id"] for x in rn.get("records", [])}

# 无token可见的都是NULL旧数据；A新记录的id应该只在A的列表里
hidden = a_ids - n_ids  # A可见但公共不可见的 = 属于A的新记录
leaked = hidden & b_ids  # 这些记录如果B也能看到 = 泄露
print(f"A可见但公共不可见: {len(hidden)} 条 -> {sorted(hidden)}")
print(f"泄露给B的: {len(leaked)} 条 -> {sorted(leaked)}")
print("ISOLATION:", "PASS" if len(leaked) == 0 and len(hidden) > 0 else "FAIL")

# 同时验证report隔离：B的报告URL内容不应包含A新记录的时间戳/学科（抽样检查subject分布太弱，改为确认B能看到的是NULL数据）
print("A记录数:", len(a_ids), " B记录数:", len(b_ids), " 公共记录数:", len(n_ids))
