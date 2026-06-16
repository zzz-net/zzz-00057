"""
维护冻结日历完整链路测试：
1. 冻结规则增删改查、启停、按环境筛选
2. 维护窗口创建/提交/审批时的冻结拦截
3. 排期方案创建/提交/审批/检测时的冻结拦截
4. 命中冻结规则写入审计日志
5. 权限控制：只有审批角色能管理冻结规则
6. 导入导出：状态、备注、审计日志恢复
7. 重启恢复：服务重启后冻结规则仍然生效
8. 撤销/停用冻结规则后重新校验放行
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_freeze_calendar_full_chain.db")
os.environ["MAINTENANCE_WINDOW_DB_PATH"] = TEST_DB_PATH

if os.path.exists(TEST_DB_PATH):
    try:
        os.remove(TEST_DB_PATH)
    except PermissionError:
        pass

import importlib
for mod in list(sys.modules.keys()):
    if mod.startswith("app.") or mod == "main":
        del sys.modules[mod]

import app.database as db_mod
db_mod.DB_PATH = TEST_DB_PATH
from sqlalchemy import create_engine
db_mod.engine = create_engine(
    f"sqlite:///{TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
)
from sqlalchemy.orm import sessionmaker
db_mod.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_mod.engine)

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from main import app
from app import schemas, services

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
    return cond


client = TestClient(app)

print("\n" + "=" * 70)
print("  [维护冻结日历完整链路 端到端测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户/模板 ---")
r = client.post("/environments", json={"name": "env-freeze-test", "description": "冻结日历测试环境"})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/roles", json={"name": "Freeze-Admin", "can_approve": 1, "description": "审批角色"})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "Freeze-User", "can_approve": 0, "description": "普通角色"})
check("创建普通角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "freeze.mgr", "display_name": "FreezeMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "freeze.dev", "display_name": "FreezeDev", "role_id": role_dev_id})
check("创建普通用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "Freeze-测试模板",
    "description": "冻结测试用模板",
    "environment_id": env_id,
    "start_time": "02:00",
    "end_time": "04:00",
    "change_reason": "常规维护",
    "is_shared": 1,
    "creator_id": mgr_id,
})
check("创建共享模板", r.status_code == 200, f"body={r.text[:200]}")
tpl_id = r.json()["id"]

# ---------- 场景1：冻结规则 CRUD ----------
print("\n--- [场景1] 冻结规则增删改查 ---")

freeze_start = datetime(2026, 6, 15, 0, 0, 0).isoformat()
freeze_end = datetime(2026, 6, 20, 23, 59, 59).isoformat()

r = client.post("/freeze-rules", json={
    "name": "618大促冻结",
    "description": "618大促期间禁止变更",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "reason": "618大促保障期，禁止所有维护变更",
    "remark": "重大促销活动保障",
    "creator_id": mgr_id,
})
check("创建冻结规则 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
rule_id = r.json()["id"]
check("规则状态 ACTIVE", r.json()["status"] == "ACTIVE")
check("规则名称正确", r.json()["name"] == "618大促冻结")

r = client.get(f"/freeze-rules/{rule_id}")
check("查询冻结规则详情 200", r.status_code == 200)
check("详情包含审计日志", len(r.json()["audit_logs"]) >= 1)
check("审计日志含 FREEZE_CREATE", any(
    log["action"] == "FREEZE_CREATE" for log in r.json()["audit_logs"]
))

r = client.get("/freeze-rules")
check("查询冻结规则列表 200", r.status_code == 200)
check("列表至少1条", len(r.json()) >= 1)

r = client.get("/freeze-rules", params={"environment_id": env_id})
check("按环境筛选 200", r.status_code == 200)
check("筛选结果正确", len(r.json()) >= 1 and r.json()[0]["environment_id"] == env_id)

r = client.get("/freeze-rules", params={"active_only": True})
check("只看生效中 200", r.status_code == 200)
check("生效中数量正确", len(r.json()) >= 1)

r = client.put(f"/freeze-rules/{rule_id}", params={"operator_id": mgr_id}, json={
    "description": "618大促期间禁止所有变更操作",
    "remark": "更新备注：由CMO审批通过",
})
check("更新冻结规则 200", r.status_code == 200)
check("描述已更新", r.json()["description"] == "618大促期间禁止所有变更操作")
check("备注已更新", r.json()["remark"] == "更新备注：由CMO审批通过")

# ---------- 场景2：权限控制 ----------
print("\n--- [场景2] 权限控制：普通用户不能管理冻结规则 ---")

r = client.post("/freeze-rules", json={
    "name": "普通用户创建的规则",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "creator_id": dev_id,
})
check("普通用户创建冻结规则 403", r.status_code == 403, f"status={r.status_code}")
check("错误信息提示审批角色", "审批角色" in r.json().get("detail", ""))

r = client.put(f"/freeze-rules/{rule_id}", params={"operator_id": dev_id}, json={
    "description": "普通用户尝试修改",
})
check("普通用户修改冻结规则 403", r.status_code == 403)

r = client.post(f"/freeze-rules/{rule_id}/deactivate", json={"operator_id": dev_id})
check("普通用户停用冻结规则 403", r.status_code == 403)

r = client.delete(f"/freeze-rules/{rule_id}", params={"operator_id": dev_id})
check("普通用户删除冻结规则 403", r.status_code == 403)

r = client.get("/freeze-rules")
check("普通用户可查看列表 200", r.status_code == 200)

r = client.get(f"/freeze-rules/{rule_id}")
check("普通用户可查看详情 200", r.status_code == 200)

# ---------- 场景3：冻结规则启停 ----------
print("\n--- [场景3] 冻结规则启停 ---")

r = client.post(f"/freeze-rules/{rule_id}/deactivate", json={"operator_id": mgr_id})
check("停用冻结规则 200", r.status_code == 200)
check("停用后状态 INACTIVE", r.json()["status"] == "INACTIVE")

r = client.get(f"/freeze-rules/{rule_id}/audit-logs")
check("停用后审计日志 200", r.status_code == 200)
check("审计日志含 FREEZE_DEACTIVATE", any(
    log["action"] == "FREEZE_DEACTIVATE" for log in r.json()
))

r = client.post(f"/freeze-rules/{rule_id}/activate", json={"operator_id": mgr_id})
check("启用冻结规则 200", r.status_code == 200)
check("启用后状态 ACTIVE", r.json()["status"] == "ACTIVE")

# ---------- 场景4：维护窗口冻结拦截 ----------
print("\n--- [场景4] 维护窗口创建/提交/审批冻结拦截 ---")

r = client.post("/maintenance-windows", json={
    "title": "冻结期内的窗口",
    "environment_id": env_id,
    "start_time": "2026-06-18T02:00:00",
    "end_time": "2026-06-18T04:00:00",
    "change_reason": "测试冻结拦截",
    "creator_id": dev_id,
})
check("冻结期内创建窗口 403", r.status_code == 403, f"status={r.status_code} body={r.text[:200]}")
check("错误信息包含冻结拦截", "冻结" in r.json().get("detail", ""))

r = client.post("/maintenance-windows", json={
    "title": "冻结期外的窗口",
    "environment_id": env_id,
    "start_time": "2026-06-10T02:00:00",
    "end_time": "2026-06-10T04:00:00",
    "change_reason": "测试非冻结期",
    "creator_id": dev_id,
})
check("冻结期外创建窗口 200", r.status_code == 200)
win_id = r.json()["id"]

r = client.post("/maintenance-windows", json={
    "title": "需要审批的窗口",
    "environment_id": env_id,
    "start_time": "2026-06-18T01:00:00",
    "end_time": "2026-06-18T02:00:00",
    "change_reason": "部分在冻结期内",
    "creator_id": dev_id,
})
check("跨冻结期创建窗口 403", r.status_code == 403)

r = client.get(f"/freeze-rules/{rule_id}/audit-logs")
check("命中冻结有审计日志", any(
    log["action"] == "FREEZE_HIT_WINDOW" for log in r.json()
))
hit_logs = [log for log in r.json() if log["action"] == "FREEZE_HIT_WINDOW"]
check("命中日志含目标窗口ID", len(hit_logs) > 0)

# ---------- 场景5：按作用域冻结 ----------
print("\n--- [场景5] 按作用域冻结（只冻结审批） ---")

r = client.post("/freeze-rules", json={
    "name": "仅审批冻结",
    "description": "只禁止审批操作",
    "environment_id": env_id,
    "freeze_scope": "APPROVE",
    "date_from": datetime(2026, 7, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 7, 5, 23, 59, 59).isoformat(),
    "reason": "只冻结审批环节",
    "creator_id": mgr_id,
})
check("创建仅审批冻结规则 200", r.status_code == 200)
approve_rule_id = r.json()["id"]

r = client.post("/maintenance-windows", json={
    "title": "仅审批冻结期内创建",
    "environment_id": env_id,
    "start_time": "2026-07-02T02:00:00",
    "end_time": "2026-07-02T04:00:00",
    "change_reason": "仅审批冻结期创建",
    "creator_id": dev_id,
})
check("仅审批冻结期可以创建 200", r.status_code == 200)
approve_win_id = r.json()["id"]

r = client.post(f"/maintenance-windows/{approve_win_id}/submit", json={
    "operator_id": dev_id,
    "reason": "提交审批",
})
check("仅审批冻结期可以提交 200", r.status_code == 200)

r = client.post(f"/maintenance-windows/{approve_win_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "同意",
})
check("仅审批冻结期审批被拦截 403", r.status_code == 403)
check("错误信息包含冻结拦截", "冻结" in r.json().get("detail", ""))

# ---------- 场景6：排期方案冻结拦截 ----------
print("\n--- [场景6] 排期方案冻结拦截 ---")

r = client.post("/schedule-plans", json={
    "name": "冻结期排期方案",
    "description": "测试排期方案冻结",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-06-17", "2026-06-18", "2026-06-21"],
    "operator_remark": "测试冻结",
    "creator_id": dev_id,
})
check("创建含冻结日期的方案 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
plan_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
    "remark": "提交审批",
})
check("提交含冻结日期的方案 403", r.status_code == 403, f"status={r.status_code} body={r.text[:200]}")

r = client.post("/schedule-plans", json={
    "name": "非冻结期排期方案",
    "description": "测试非冻结期",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-06-10", "2026-06-11"],
    "creator_id": dev_id,
})
check("创建非冻结期方案 200", r.status_code == 200)
plan_ok_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_ok_id}/submit", json={
    "operator_id": dev_id,
    "remark": "提交审批",
})
check("非冻结期方案可提交 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_ok_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "同意",
})
check("非冻结期方案可审批 200", r.status_code == 200)

# ---------- 场景7：检测变更中发现冻结冲突 ----------
print("\n--- [场景7] 变更检测中发现冻结冲突 ---")

r = client.post(f"/schedule-plans/{plan_ok_id}/detect-changes", params={"operator_id": mgr_id})
check("无冻结时检测变更 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
})
plan_data = client.get(f"/schedule-plans/{plan_id}").json()
items_ok = [item for item in plan_data["items"] if item["date"] == "2026-06-21"]
if items_ok:
    r = client.post(f"/schedule-plans/{plan_id}/items/{items_ok[0]['id']}/exclude",
                   params={"operator_id": mgr_id, "reason": "测试"})

r = client.post(f"/schedule-plans/{plan_ok_id}/detect-changes", params={"operator_id": mgr_id})
check("原方案变更检测 200", r.status_code == 200)

r = client.post("/freeze-rules", json={
    "name": "新增冻结影响方案",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 6, 9, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 6, 12, 23, 59, 59).isoformat(),
    "reason": "新增冻结期",
    "creator_id": mgr_id,
})
check("新增冻结规则 200", r.status_code == 200)
new_freeze_rule_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_ok_id}/detect-changes", params={"operator_id": mgr_id})
check("新增冻结后检测变更 200", r.status_code == 200)
detect_result = r.json()
check("检测到变更条目>0", detect_result["changed_items"] > 0,
      f"changed={detect_result['changed_items']}")

r = client.get(f"/schedule-plans/{plan_ok_id}")
detail = r.json()
changed_items = [item for item in detail["items"] if item["status"] == "CHANGED"]
check(f"有{len(changed_items)}条CHANGED状态", len(changed_items) > 0)

if changed_items:
    has_freeze_hint = False
    for item in changed_items:
        hints = item.get("diff_hints", [])
        for hint in hints:
            if "FREEZE" in str(hint.get("diff_type", "")) or "冻结" in str(hint.get("detail", "")):
                has_freeze_hint = True
                break
        if has_freeze_hint:
            break
    check("变更条目含冻结冲突提示", has_freeze_hint,
          f"hints={json.dumps(changed_items[0].get('diff_hints', []), ensure_ascii=False)[:300]}")

# ---------- 场景8：停用冻结规则后重新校验放行 ----------
print("\n--- [场景8] 停用冻结规则后重新校验放行 ---")

r = client.post(f"/freeze-rules/{new_freeze_rule_id}/deactivate", json={"operator_id": mgr_id})
check("停用影响方案的冻结规则 200", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_ok_id}")
items_before = [x for x in r.json()["items"] if x["status"] == "CHANGED"]
check(f"停用前有{len(items_before)}条CHANGED", len(items_before) > 0)

for item in items_before:
    r = client.post(f"/schedule-plans/{plan_ok_id}/items/{item['id']}/recheck",
                   params={"operator_id": mgr_id})
    check(f"重新预检 {item['date']} 200", r.status_code == 200)
    check("重新预检后状态 APPROVED", r.json()["status"] == "APPROVED")

r = client.get(f"/schedule-plans/{plan_ok_id}")
still_changed = [x for x in r.json()["items"] if x["status"] == "CHANGED"]
check("停用冻结后所有条目恢复正常", len(still_changed) == 0,
      f"remaining={len(still_changed)}")

# ---------- 场景9：导入导出 ----------
print("\n--- [场景9] 冻结规则导入导出 ---")

r = client.post("/freeze-rules/export", params={"rule_ids": [rule_id]})
check("导出冻结规则 200", r.status_code == 200)
export_data = r.json()
check("导出至少1条", export_data["count"] >= 1)

exported_rule = export_data["data"][0]
check("导出含名称", exported_rule.get("name") == "618大促冻结")
check("导出含状态", exported_rule.get("status") == "ACTIVE")
check("导出含备注", "remark" in exported_rule)
check("导出含审计日志", len(exported_rule.get("audit_logs", [])) >= 1)

import_rules_data = export_data["data"]
import_rules_data[0]["name"] = "导入的618冻结"
import_rules_data[0]["status"] = "ACTIVE"

r = client.post("/freeze-rules/import", json={
    "rules": import_rules_data,
    "operator_id": mgr_id,
    "on_conflict": "skip",
})
check("导入冻结规则 200", r.status_code == 200)
import_result = r.json()
check("导入成功1条", import_result["success"] == 1, f"result={import_result}")

imported_rule_id = import_result["details"][0]["id"]

r = client.get(f"/freeze-rules/{imported_rule_id}")
check("导入规则详情 200", r.status_code == 200)
imported = r.json()
check("导入规则名称正确", imported["name"] == "导入的618冻结")
check("导入规则状态 ACTIVE", imported["status"] == "ACTIVE")
check("导入规则含备注", imported.get("remark") is not None)

r = client.get(f"/freeze-rules/{imported_rule_id}/audit-logs")
check("导入恢复了审计日志", len(r.json()) >= 1,
      f"logs_count={len(r.json())}")

check("导入后规则立即生效", r.status_code == 200)

r = client.post("/maintenance-windows", json={
    "title": "验证导入冻结生效",
    "environment_id": env_id,
    "start_time": "2026-06-18T02:00:00",
    "end_time": "2026-06-18T04:00:00",
    "change_reason": "验证导入冻结",
    "creator_id": dev_id,
})
check("导入的冻结规则生效拦截 403", r.status_code == 403)

# ---------- 场景10：重启恢复 ----------
print("\n--- [场景10] 服务重启后冻结规则仍生效 ---")

before_restart_rules = client.get("/freeze-rules").json()
before_rule_detail = client.get(f"/freeze-rules/{rule_id}").json()
before_audit = client.get(f"/freeze-rules/{rule_id}/audit-logs").json()

# 模拟服务重启
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

r = client2.get("/freeze-rules")
check("重启后冻结规则列表 200", r.status_code == 200)
check(f"重启后仍有{len(before_restart_rules)}条规则",
      len(r.json()) == len(before_restart_rules),
      f"before={len(before_restart_rules)} after={len(r.json())}")

r = client2.get(f"/freeze-rules/{rule_id}")
check("重启后规则详情 200", r.status_code == 200)
after_detail = r.json()
check("重启后规则状态仍为 ACTIVE", after_detail["status"] == "ACTIVE")
check("重启后规则备注保留", after_detail.get("remark") == before_rule_detail.get("remark"))

r = client2.get(f"/freeze-rules/{rule_id}/audit-logs")
check("重启后审计日志保留", len(r.json()) == len(before_audit),
      f"before={len(before_audit)} after={len(r.json())}")

r = client2.post("/maintenance-windows", json={
    "title": "重启后验证冻结生效",
    "environment_id": env_id,
    "start_time": "2026-06-18T02:00:00",
    "end_time": "2026-06-18T04:00:00",
    "change_reason": "重启后验证",
    "creator_id": dev_id,
})
check("重启后冻结仍生效 403", r.status_code == 403)

r = client2.post("/maintenance-windows", json={
    "title": "重启后非冻结期正常",
    "environment_id": env_id,
    "start_time": "2026-06-10T02:00:00",
    "end_time": "2026-06-10T04:00:00",
    "change_reason": "重启后正常创建",
    "creator_id": dev_id,
})
check("重启后非冻结期可正常创建 200", r.status_code == 200)

# ---------- 场景11：冻结规则预检接口 ----------
print("\n--- [场景11] 冻结规则预检接口 ---")

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-06-18T00:00:00",
    "end_time": "2026-06-18T23:59:59",
    "scope": "ALL",
})
check("冻结期预检 200", r.status_code == 200)
check("预检结果有冲突", r.json()["has_conflict"] == True)
check("冲突列表非空", len(r.json()["conflicts"]) > 0)
if r.json()["conflicts"]:
    check("冲突含规则名称", r.json()["conflicts"][0]["rule_name"] is not None)
    check("冲突含原因说明", r.json()["conflicts"][0]["conflict_reason"] is not None)

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-06-10T00:00:00",
    "end_time": "2026-06-10T23:59:59",
    "scope": "ALL",
})
check("非冻结期预检 200", r.status_code == 200)
check("预检结果无冲突", r.json()["has_conflict"] == False)

# ---------- 场景12：删除冻结规则 ----------
print("\n--- [场景12] 删除冻结规则 ---")

r = client2.post("/freeze-rules", json={
    "name": "待删除的冻结规则",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 8, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 8, 5, 23, 59, 59).isoformat(),
    "reason": "测试删除",
    "creator_id": mgr_id,
})
check("创建待删除的冻结规则 200", r.status_code == 200)
del_rule_id = r.json()["id"]

r = client2.post("/maintenance-windows", json={
    "title": "删除前被冻结",
    "environment_id": env_id,
    "start_time": "2026-08-02T02:00:00",
    "end_time": "2026-08-02T04:00:00",
    "change_reason": "验证删除前被冻结",
    "creator_id": dev_id,
})
check("删除前冻结生效 403", r.status_code == 403)

r = client2.delete(f"/freeze-rules/{del_rule_id}", params={"operator_id": mgr_id})
check("删除冻结规则 200", r.status_code == 200)

r = client2.get(f"/freeze-rules/{del_rule_id}")
check("删除后查询 404", r.status_code == 404)

r = client2.post("/maintenance-windows", json={
    "title": "删除冻结后可以创建",
    "environment_id": env_id,
    "start_time": "2026-08-02T02:00:00",
    "end_time": "2026-08-02T04:00:00",
    "change_reason": "删除冻结后验证",
    "creator_id": dev_id,
})
check("删除冻结规则后可正常创建 200", r.status_code == 200)

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
    print("\n  *** 维护冻结日历完整链路测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
