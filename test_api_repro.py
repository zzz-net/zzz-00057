"""
真实 HTTP 接口复现测试：
- 指定日期模式下，/batch-records 列表是否 500
- /batch-records/{id} 明细是否拿得到 specific_dates
- 重启后最近参数是否稳定
"""
import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_api_repro.db")
os.environ["MAINTENANCE_WINDOW_DB_PATH"] = TEST_DB_PATH

# 清掉已有数据库
if os.path.exists(TEST_DB_PATH):
    try:
        os.remove(TEST_DB_PATH)
    except PermissionError:
        pass

# 在导入 FastAPI 之前先保证没有 engine 被创建过
import importlib
for mod in list(sys.modules.keys()):
    if mod.startswith("app.") or mod == "main":
        del sys.modules[mod]

# 打补丁：强制 app.database 用测试数据库
import app.database as db_mod
db_mod.DB_PATH = TEST_DB_PATH
from sqlalchemy import create_engine
db_mod.engine = create_engine(
    f"sqlite:///{TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
)
from sqlalchemy.orm import sessionmaker
db_mod.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_mod.engine)

from datetime import date

from fastapi.testclient import TestClient
from main import app
from app import schemas, services

# 重新建表
from app.database import Base, engine as e
Base.metadata.create_all(bind=e)

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def check(name, cond, detail=""):
    flag = PASS if cond else FAIL
    results.append((flag, name, detail))
    suffix = ""
    if detail:
        suffix = f"  ({detail})" if cond else f"  FAIL-INFO: {detail}"
    print(f"{flag} {name}{suffix}")


client = TestClient(app)

print("\n" + "=" * 70)
print("  [真实 HTTP 接口：最近一次生成参数查看链路 复现+验证]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户 ---")
r = client.post("/environments", json={"name": "env-api-test", "description": ""})
check("创建环境", r.status_code == 200, f"status={r.status_code} body={r.text}")
env_id = r.json()["id"]

r = client.post("/roles", json={"name": "CM-API", "can_approve": 1, "description": ""})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "DEV-API", "can_approve": 0, "description": ""})
check("创建开发角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "api.mgr", "display_name": "APIMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "api.dev", "display_name": "APIDev", "role_id": role_dev_id})
check("创建开发用户", r.status_code == 200)
dev_id = r.json()["id"]

# ---------- 创建模板 + 指定日期批量生成 ----------
print("\n--- [接口1] 创建模板 + 指定日期批量生成 ---")
r = client.post("/window-templates", json={
    "name": "API-指定日期模板",
    "description": "用于接口测试",
    "environment_id": env_id,
    "start_time": "01:00",
    "end_time": "02:00",
    "change_reason": "接口测试",
    "is_shared": 0,
    "creator_id": dev_id,
})
check("POST /window-templates 200", r.status_code == 200, f"body={r.text}")
tpl_id = r.json()["id"]

r = client.post("/window-templates/batch-generate", json={
    "template_id": tpl_id,
    "operator_id": dev_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-07-01", "2026-07-05", "2026-07-10"],
    "auto_create": True,
})
check("POST /window-templates/batch-generate 200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:500]}")
batch_id = r.json()["batch_id"]

# ---------- 场景1：列表接口 ----------
print("\n--- [场景1] GET /batch-records 列表 ---")
r = client.get("/batch-records", params={"template_id": tpl_id})
check("列表接口状态=200（不是 500）", r.status_code == 200,
      f"status={r.status_code} body={r.text[:500]}")

if r.status_code == 200:
    data = r.json()
    check("列表返回1条记录", len(data) == 1, f"count={len(data)}")
    if len(data) > 0:
        br = data[0]
        check("列表记录含 generate_mode", "generate_mode" in br)
        check("列表记录含 specific_dates", "specific_dates" in br,
              f"keys={list(br.keys())}")
        check("列表 specific_dates 为数组",
              isinstance(br.get("specific_dates"), list),
              f"type={type(br.get('specific_dates'))} val={br.get('specific_dates')}")
        if isinstance(br.get("specific_dates"), list):
            check("列表 specific_dates 长度=3", len(br["specific_dates"]) == 3,
                  f"val={br['specific_dates']}")

# ---------- 场景2：明细接口 ----------
print("\n--- [场景2] GET /batch-records/{id} 明细 ---")
r = client.get(f"/batch-records/{batch_id}")
check("明细接口状态=200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:500]}")

if r.status_code == 200:
    data = r.json()
    check("明细含 precheck_items", "precheck_items" in data)
    check("明细含 generate_mode", "generate_mode" in data)
    check("明细含 specific_dates", "specific_dates" in data,
          f"keys={list(data.keys())}")
    check("明细 specific_dates 为数组",
          isinstance(data.get("specific_dates"), list),
          f"type={type(data.get('specific_dates'))} val={data.get('specific_dates')}")
    if isinstance(data.get("specific_dates"), list):
        check("明细 specific_dates 长度=3", len(data["specific_dates"]) == 3)
    if data.get("precheck_items"):
        check("明细预检项=3", len(data["precheck_items"]) == 3,
              f"count={len(data.get('precheck_items', []))}")
        first_pc = data["precheck_items"][0]
        check("明细预检项含 conflict_type", "conflict_type" in first_pc)

# ---------- 场景3：日期范围模式的列表/明细 ----------
print("\n--- [场景3] 日期范围模式 列表/明细 ---")
r = client.post("/window-templates/batch-generate", json={
    "template_id": tpl_id,
    "operator_id": dev_id,
    "generate_mode": "date_range",
    "date_from": "2026-08-01",
    "date_to": "2026-08-03",
    "auto_create": True,
})
check("日期范围批量生成 200", r.status_code == 200, f"body={r.text[:300]}")
range_batch_id = r.json()["batch_id"]

r = client.get("/batch-records", params={"template_id": tpl_id})
check("列表状态=200", r.status_code == 200)
if r.status_code == 200:
    all_records = r.json()
    range_br = [b for b in all_records if b.get("generate_mode") == "date_range"]
    check("列表有1条日期范围记录", len(range_br) == 1, f"count={len(range_br)}")
    if range_br:
        rb = range_br[0]
        check("日期范围记录含 date_from", "date_from" in rb and rb["date_from"] is not None)
        check("日期范围记录含 date_to", "date_to" in rb and rb["date_to"] is not None)

r = client.get(f"/batch-records/{range_batch_id}")
check("日期范围明细 200", r.status_code == 200)
if r.status_code == 200:
    d = r.json()
    check("明细 date_from 非空", d.get("date_from") is not None)
    check("明细 date_to 非空", d.get("date_to") is not None)

# ---------- 场景4：模拟重启后，参数稳定性 ----------
print("\n--- [场景4] 模拟重启后最近参数稳定性 ---")
del client

import importlib as il
from app.database import engine as eng
eng.dispose()

for mod in list(sys.modules.keys()):
    if mod.startswith("app.") or mod == "main":
        del sys.modules[mod]

import app.database as db_mod2
db_mod2.DB_PATH = TEST_DB_PATH
from sqlalchemy import create_engine as ce2
db_mod2.engine = ce2(
    f"sqlite:///{TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
)
from sqlalchemy.orm import sessionmaker as sm2
db_mod2.SessionLocal = sm2(autocommit=False, autoflush=False, bind=db_mod2.engine)

from fastapi.testclient import TestClient as TC2
from main import app as app2
from app.database import Base, engine as eng2
Base.metadata.create_all(bind=eng2)

client2 = TC2(app2)

r = client2.get("/batch-records", params={"template_id": tpl_id})
check("重启后列表状态=200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:500]}")

if r.status_code == 200:
    data = r.json()
    check("重启后列表仍有2条记录", len(data) == 2, f"count={len(data)}")
    specific_records = [b for b in data if b.get("generate_mode") == "specific_dates"]
    if specific_records:
        sr = specific_records[0]
        check("重启后 specific_dates 仍为数组",
              isinstance(sr.get("specific_dates"), list),
              f"type={type(sr.get('specific_dates'))} val={sr.get('specific_dates')}")
        if isinstance(sr.get("specific_dates"), list):
            check("重启后 specific_dates 长度=3", len(sr["specific_dates"]) == 3)

r = client2.get(f"/batch-records/{batch_id}")
check("重启后明细状态=200", r.status_code == 200)
if r.status_code == 200:
    d = r.json()
    check("重启后 specific_dates 非空", d.get("specific_dates") is not None)
    check("重启后 precheck_items 非空", d.get("precheck_items") is not None)
    if isinstance(d.get("specific_dates"), list):
        check("重启后明细 specific_dates=3", len(d["specific_dates"]) == 3)

# ---------- 场景5：从批量记录再生成 ----------
print("\n--- [场景5] POST /batch-records/{id}/regenerate 再生成 ---")
r = client2.post(f"/batch-records/{batch_id}/regenerate", params={"operator_id": dev_id})
check("再生成接口状态=200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:500]}")

if r.status_code == 200:
    d = r.json()
    check("再生成总数=3", d.get("total_count") == 3, f"total={d.get('total_count')}")
    check("再生成含 precheck_items", "precheck_items" in d and len(d["precheck_items"]) == 3)

r = client2.get("/batch-records", params={"template_id": tpl_id})
if r.status_code == 200:
    check("再生成后列表有3条记录", len(r.json()) == 3, f"count={len(r.json())}")

# ---------- 总结 ----------
print("\n" + "=" * 70)
total = len(results)
ok = sum(1 for f, _, _ in results if f == PASS)
print(f"  测试结果: {ok}/{total} 通过")
print("=" * 70)
failed = [(n, d) for f, n, d in results if f == FAIL]
for n, d in failed:
    print(f"  {FAIL} {n}  {d}")

if ok == total:
    print("\n  *** 真实 HTTP 接口：最近参数链路测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
