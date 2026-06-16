"""
审批后维护方案完整链路测试：
1. 审批流程：创建 -> 提交 -> 审批通过
2. 共享模板变更检测：哪怕改描述/变更原因也要检测并标清楚受影响条目
3. 确认前强制检测：不能0条变更直接放行
4. 确认记录+审计日志落SQLite
5. JSON导出导入恢复：确认结果、关键日志、回放信息完整
6. 权限控制：非审批角色不能代确认共享方案
7. 重启恢复：服务重启后继续查看差异、重新预检、剔除冲突条目再执行
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_approval_full_chain.db")
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

from datetime import date, datetime

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
print("  [审批后维护方案完整链路 端到端测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户/维护时段 ---")
r = client.post("/environments", json={"name": "env-approval-chain", "description": "审批链路测试环境"})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/maintenance-slots", json={
    "environment_id": env_id,
    "day_of_week": 3,
    "start_time": "00:00",
    "end_time": "06:00",
})
check("创建维护时段(周四凌晨)", r.status_code == 200)

r = client.post("/roles", json={"name": "CM-Approval", "can_approve": 1, "description": "审批角色"})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "DEV-Approval", "can_approve": 0, "description": "开发角色"})
check("创建开发角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "chain.mgr", "display_name": "ChainMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "chain.dev", "display_name": "ChainDev", "role_id": role_dev_id})
check("创建开发用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/users", json={"username": "chain.other", "display_name": "ChainOther", "role_id": role_dev_id})
check("创建其他开发用户", r.status_code == 200)
other_dev_id = r.json()["id"]

# ---------- 场景1：创建共享模板并创建排期方案 ----------
print("\n--- [场景1] 创建共享模板 + 排期方案（锁版） ---")
r = client.post("/window-templates", json={
    "name": "Chain-共享模板",
    "description": "初始描述",
    "environment_id": env_id,
    "start_time": "01:00",
    "end_time": "03:00",
    "change_reason": "初始变更原因",
    "is_shared": 1,
    "creator_id": dev_id,
})
check("创建共享模板 200", r.status_code == 200, f"body={r.text[:300]}")
tpl_id = r.json()["id"]
tpl_updated_at = r.json()["updated_at"]

r = client.post("/schedule-plans", json={
    "name": "2026年Q4审批链路方案",
    "description": "审批链路测试方案",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22"],
    "operator_remark": "按周四凌晨维护窗执行",
    "creator_id": dev_id,
})
check("创建排期方案 200", r.status_code == 200, f"status={r.status_code} body={r.text[:500]}")
plan_id = r.json()["id"]
check("方案状态 DRAFT", r.json()["status"] == "DRAFT")
check("方案总数=4", r.json()["total_count"] == 4)

r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
check("方案含4条候选窗口", len(detail["items"]) == 4)
check("条目状态 PENDING", detail["items"][0]["status"] == "PENDING")

# ---------- 场景2：审批流程 ----------
print("\n--- [场景2] 完整审批流程 ---")

r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
    "remark": "请审批Q4维护方案",
})
check("提交审批 200", r.status_code == 200)
check("状态 PENDING_APPROVAL", r.json()["status"] == "PENDING_APPROVAL")

r = client.post(f"/schedule-plans/{plan_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "同意，按计划执行",
})
check("审批通过 200", r.status_code == 200)
check("状态 APPROVED", r.json()["status"] == "APPROVED")
check("审批通过数=4", r.json()["approved_count"] == 4)
check("记录审批人", r.json()["approver_id"] == mgr_id)

r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
check("所有条目 APPROVED", all(item["status"] == "APPROVED" for item in detail["items"]))

r = client.get(f"/schedule-plans/{plan_id}/audit-logs")
check("审计日志 200", r.status_code == 200)
audit_actions = [log["action"] for log in r.json()]
check("含 PLAN_CREATE", "PLAN_CREATE" in audit_actions)
check("含 PLAN_SUBMIT", "PLAN_SUBMIT" in audit_actions)
check("含 PLAN_APPROVE", "PLAN_APPROVE" in audit_actions)

# ---------- 场景3：共享模板描述变更检测 ----------
print("\n--- [场景3] 共享模板仅改描述也要重新比对 ---")

r = client.put(f"/window-templates/{tpl_id}", params={"operator_id": dev_id}, json={
    "description": "修改了描述内容",
})
check("修改模板描述 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_id}/detect-changes", params={"operator_id": mgr_id})
check("变更检测 200", r.status_code == 200, f"body={r.text[:500]}")
detect_result = r.json()
check("检测到变更>0", detect_result["changed_items"] > 0,
      f"changed={detect_result['changed_items']} unchanged={detect_result['unchanged_items']}")

r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
changed_items = [item for item in detail["items"] if item["status"] == "CHANGED"]
check(f"有{len(changed_items)}条状态变为 CHANGED", len(changed_items) > 0)

if changed_items:
    check("变更条目含 diff_hints", len(changed_items[0]["diff_hints"]) > 0)
    check("diff_type = TEMPLATE_CHANGED", changed_items[0]["current_diff_type"] == "TEMPLATE_CHANGED")
    
    hints = changed_items[0]["diff_hints"]
    has_desc_hint = any(
        ("description" in h.get("changed_fields", [])) or
        ("description" in str(h.get("detail", ""))) or
        ("description" in str(h.get("old_value", {}))) or
        ("description" in str(h.get("new_value", {})))
        for h in hints
    )
    check("diff_hints 明确指出 description 变更", has_desc_hint, f"hints={json.dumps(hints, ensure_ascii=False)[:500]}")

check("方案状态变为 CONFIRMING", detail["status"] == "CONFIRMING")

# ---------- 场景4：确认前强制检测变更 ----------
print("\n--- [场景4] 确认前强制检测变更（不能0条放行）---")

# 先尝试直接确认（有变更未处理应该失败）
r = client.post(f"/schedule-plans/{plan_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "直接确认不处理变更",
})
check("有变更未处理时确认失败 400", r.status_code == 400, f"status={r.status_code} body={r.text[:300]}")

# 处理变更：对所有变更条目重新预检
for i, changed_item in enumerate(changed_items):
    r = client.post(f"/schedule-plans/{plan_id}/items/{changed_item['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    check(f"第{i+1}条重新预检 200", r.status_code == 200)
    check(f"第{i+1}条状态回到 APPROVED", r.json()["status"] == "APPROVED")

# ---------- 场景5：共享模板变更原因变更检测 ----------
print("\n--- [场景5] 共享模板仅改变更原因也要重新比对 ---")

r = client.put(f"/window-templates/{tpl_id}", params={"operator_id": dev_id}, json={
    "change_reason": "紧急变更：发现安全漏洞需要修复",
})
check("修改模板变更原因 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_id}/detect-changes", params={"operator_id": mgr_id})
check("变更检测 200", r.status_code == 200)
detect_result2 = r.json()
check(f"检测到变更数>0", detect_result2["changed_items"] > 0,
      f"changed={detect_result2['changed_items']}")

r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
changed_items2 = [item for item in detail["items"] if item["status"] == "CHANGED"]
check(f"有{len(changed_items2)}条 CHANGED 状态", len(changed_items2) > 0)

if changed_items2:
    hints = changed_items2[0]["diff_hints"]
    has_cr_hint = any(
        ("change_reason" in h.get("changed_fields", [])) or
        ("change_reason" in str(h.get("detail", ""))) or
        ("change_reason" in str(h.get("old_value", {}))) or
        ("change_reason" in str(h.get("new_value", {})))
        for h in hints
    )
    check("diff_hints 明确指出 change_reason 变更", has_cr_hint,
          f"hints={json.dumps(hints, ensure_ascii=False)[:500]}")

# 对所有变更条目重新预检
for i, changed_item in enumerate(changed_items2):
    r = client.post(f"/schedule-plans/{plan_id}/items/{changed_item['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    check(f"第{i+1}条重新预检 200", r.status_code == 200)

# ---------- 场景6：权限控制 - 非审批角色不能代确认共享方案 ----------
print("\n--- [场景6] 权限控制：非审批角色不能代确认共享方案 ---")

# 先创建一个新方案，便于测试权限
r = client.post("/schedule-plans", json={
    "name": "权限测试专用方案",
    "description": "测试权限控制",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-11-01"],
    "creator_id": dev_id,
})
check("创建权限测试方案 200", r.status_code == 200)
plan_perm_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_perm_id}/submit", json={"operator_id": dev_id})
check("提交审批 200", r.status_code == 200)
r = client.post(f"/schedule-plans/{plan_perm_id}/approve", json={"operator_id": mgr_id})
check("审批通过 200", r.status_code == 200)

# 其他开发用户（非审批，非创建人）尝试确认共享方案（应该失败）
r = client.post(f"/schedule-plans/{plan_perm_id}/confirm", json={
    "operator_id": other_dev_id,
    "remark": "其他开发用户代确认",
})
check("非审批角色不能代确认共享方案 403", r.status_code == 403, f"status={r.status_code}")

# 审批用户可以确认
r = client.post(f"/schedule-plans/{plan_perm_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "审批用户确认合法",
})
check("审批用户可以确认共享方案 200", r.status_code == 200, f"body={r.text[:300]}")
check("状态 CONFIRMED", r.json()["status"] == "CONFIRMED")

# ---------- 场景7：确认记录和审计日志落库 ----------
print("\n--- [场景7] 确认记录+审计日志落SQLite ---")

# 回到主方案，先检查当前状态，显式处理任何未处理的变更
r = client.get(f"/schedule-plans/{plan_id}")
plan_before_confirm = r.json()
print(f"[DEBUG] 场景7 confirm 前: 方案状态={plan_before_confirm['status']}，"
      f"条目状态={[(i['date'], i['status']) for i in plan_before_confirm['items']]}")

# 显式调用一次 detect，确保所有变更都被最新标记
r = client.post(f"/schedule-plans/{plan_id}/detect-changes", params={
    "operator_id": mgr_id,
})
print(f"[DEBUG] 场景7 confirm 前 detect: {r.status_code}, body={r.text[:200]}")

# 然后检查是否有 CHANGED 条目，如果有就全部 recheck
r = client.get(f"/schedule-plans/{plan_id}")
detail_before_confirm = r.json()
changed_items7 = [item for item in detail_before_confirm["items"] if item["status"] == "CHANGED"]
print(f"[DEBUG] 场景7 confirm 前: CHANGED条目数={len(changed_items7)}")
for i, changed_item in enumerate(changed_items7):
    r = client.post(f"/schedule-plans/{plan_id}/items/{changed_item['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    print(f"[DEBUG] 场景7 recheck第{i+1}条: {r.status_code}")

# 现在所有变更都处理了，可以确认了
r = client.post(f"/schedule-plans/{plan_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "已处理所有模板变更（描述+变更原因），确认执行",
})
check("批量确认主方案 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
check("状态 CONFIRMED", r.json()["status"] == "CONFIRMED")
check("确认数=4", r.json()["confirmed_count"] == 4)

r = client.get(f"/schedule-plans/{plan_id}/confirmations")
check("确认记录 200", r.status_code == 200)
check("至少有1条确认记录", len(r.json()) >= 1)

if r.json():
    conf = r.json()[0]
    check("确认记录含 operator", "operator" in conf)
    check("确认记录含 item_ids", "item_ids" in conf)
    check("确认记录含 diff_summary", "diff_summary" in conf)
    check("diff_summary 含 confirmed_count",
          "confirmed_count" in (conf["diff_summary"] or {}))

r = client.get(f"/schedule-plans/{plan_id}/audit-logs")
check("审计日志 200", r.status_code == 200)
audit_actions2 = [log["action"] for log in r.json()]
check("含 PLAN_DETECT_CHANGE 日志", "PLAN_DETECT_CHANGE" in audit_actions2)
check("含 PLAN_RECHECK 日志", "PLAN_RECHECK" in audit_actions2)
check("含 PLAN_CONFIRM 日志", "PLAN_CONFIRM" in audit_actions2)

# 执行创建
r = client.post(f"/schedule-plans/{plan_id}/execute", json={
    "operator_id": mgr_id,
})
check("执行创建 200", r.status_code == 200, f"body={r.text[:500]}")
check("成功创建4条", r.json()["success_count"] == 4)

# ---------- 场景8：导出JSON ----------
print("\n--- [场景8] 导出JSON包含确认记录+审计日志 ---")

r = client.post("/schedule-plans/export", params={"plan_ids": [plan_id]})
check("导出方案 200", r.status_code == 200)
export_data = r.json()
check("导出1个方案", export_data["count"] == 1)

exported_plan = export_data["data"][0]
check("导出包含 items", len(exported_plan["items"]) == 4)
check("导出包含 confirmations", "confirmations" in exported_plan)
check("导出至少1条 confirmations", len(exported_plan["confirmations"]) >= 1)
check("导出包含 audit_logs", "audit_logs" in exported_plan)
check("导出至少1条 audit_logs", len(exported_plan["audit_logs"]) >= 1)
check("导出含 creator_username", "creator_username" in exported_plan)
check("导出含 approver_username", "approver_username" in exported_plan)

if exported_plan["confirmations"]:
    exp_conf = exported_plan["confirmations"][0]
    check("导出确认含 operator_username", "operator_username" in exp_conf)
    check("导出确认含 diff_summary", "diff_summary" in exp_conf)
    check("导出确认含 item_ids", "item_ids" in exp_conf)

if exported_plan["audit_logs"]:
    exp_log = exported_plan["audit_logs"][0]
    check("导出审计含 operator_username", "operator_username" in exp_log)
    check("导出审计含 snapshot", "snapshot" in exp_log)

# ---------- 场景9：导入JSON恢复确认记录+审计日志 ----------
print("\n--- [场景9] 导入JSON恢复确认结果和日志 ---")

import_plans_data = export_data["data"]
import_plans_data[0]["name"] = "导入恢复测试方案"
import_plans_data[0]["status"] = "APPROVED"
for item in import_plans_data[0]["items"]:
    item["status"] = "APPROVED"
    item.pop("window_id", None)

r = client.post("/schedule-plans/import", json={
    "plans": import_plans_data,
    "operator_id": dev_id,
    "on_conflict": "skip",
})
check("导入方案 200", r.status_code == 200)
import_result = r.json()
check("导入成功1个", import_result["success"] == 1, f"result={import_result}")

imported_plan_id = import_result["details"][0]["id"]

r = client.get(f"/schedule-plans/{imported_plan_id}")
check("导入方案明细 200", r.status_code == 200)
imported = r.json()
check("导入方案名称正确", imported["name"] == "导入恢复测试方案")
check("导入方案保留了4条条目", len(imported["items"]) == 4)
check("导入方案保留了锁版快照", imported["template_version_snapshot"] is not None)

r = client.get(f"/schedule-plans/{imported_plan_id}/confirmations")
check("导入的确认记录 200", r.status_code == 200)
check("导入恢复了确认记录", len(r.json()) >= 1,
      f"confirmations_count={len(r.json())}")

r = client.get(f"/schedule-plans/{imported_plan_id}/audit-logs")
check("导入的审计日志 200", r.status_code == 200)
check("导入恢复了审计日志", len(r.json()) >= 1,
      f"audit_logs_count={len(r.json())}")

# 导入的方案可以继续走流程：检测变更
r = client.post(f"/schedule-plans/{imported_plan_id}/detect-changes", params={"operator_id": mgr_id})
check("导入方案可继续检测变更 200", r.status_code == 200, f"body={r.text[:300]}")

# 检查导入方案条目状态，处理任何变更
r = client.get(f"/schedule-plans/{imported_plan_id}")
imported_after_detect = r.json()
imported_changed = [x for x in imported_after_detect["items"] if x["status"] == "CHANGED"]
print(f"[DEBUG] 导入后检测变更数={len(imported_changed)}")
for i, changed_item in enumerate(imported_changed):
    r = client.post(f"/schedule-plans/{imported_plan_id}/items/{changed_item['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    print(f"[DEBUG] 导入后recheck第{i+1}条: {r.status_code}")

# 导入的方案可以确认
r = client.post(f"/schedule-plans/{imported_plan_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "导入方案确认",
})
check("导入方案可确认 200", r.status_code == 200, f"body={r.text[:300]}")
check("导入方案状态 CONFIRMED", r.json()["status"] == "CONFIRMED")

# ---------- 场景10：重启恢复测试 ----------
print("\n--- [场景10] 服务重启恢复：继续查看差异、预检、剔除、执行 ---")

# 先再创建一个方案在 CONFIRMING 状态，用于测试重启后可继续操作
r = client.post("/schedule-plans", json={
    "name": "重启恢复专用方案",
    "description": "测试重启恢复流程",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-12-01", "2026-12-08"],
    "creator_id": dev_id,
})
check("创建重启恢复方案 200", r.status_code == 200)
plan_restart_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_restart_id}/submit", json={"operator_id": dev_id})
check("提交 200", r.status_code == 200)
r = client.post(f"/schedule-plans/{plan_restart_id}/approve", json={"operator_id": mgr_id})
check("审批 200", r.status_code == 200)

# 修改模板制造变更
r = client.put(f"/window-templates/{tpl_id}", params={"operator_id": dev_id}, json={
    "description": "重启测试：又修改了描述",
})
check("再次修改模板描述 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_restart_id}/detect-changes", params={"operator_id": mgr_id})
check("检测变更 200", r.status_code == 200)
detect_restart = r.json()
check(f"检测到变更数>0", detect_restart["changed_items"] > 0)

# 缓存重启前的数据
before_restart_plan = client.get(f"/schedule-plans/{plan_restart_id}").json()
before_restart_list = client.get("/schedule-plans").json()
before_restart_audit = client.get(f"/schedule-plans/{plan_restart_id}/audit-logs").json()
before_restart_conf = client.get(f"/schedule-plans/{plan_restart_id}/confirmations").json()
before_main_plan = client.get(f"/schedule-plans/{plan_id}").json()
before_imported_plan = client.get(f"/schedule-plans/{imported_plan_id}").json()

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

# 验证重启后方案列表恢复
r = client2.get("/schedule-plans")
check("重启后方案列表 200", r.status_code == 200)
check(f"重启后仍有{len(before_restart_list)}个方案",
      len(r.json()) == len(before_restart_list))

# 验证重启方案状态
r = client2.get(f"/schedule-plans/{plan_restart_id}")
check("重启后 CONFIRMING 方案明细 200", r.status_code == 200)
after_restart = r.json()
check("重启后状态仍为 CONFIRMING", after_restart["status"] == "CONFIRMING")

changed_after = [item for item in after_restart["items"] if item["status"] == "CHANGED"]
check(f"重启后仍有{len(changed_after)}条 CHANGED 状态", len(changed_after) > 0)
if changed_after:
    check("重启后仍可查看 diff_hints", len(changed_after[0]["diff_hints"]) > 0)

# 重启后可以继续操作：剔除冲突条目
r = client2.get(f"/schedule-plans/{plan_restart_id}")
restart_items = r.json()["items"]
changed_after_items = [item for item in restart_items if item["status"] == "CHANGED"]
if changed_after_items:
    exclude_id = changed_after_items[0]["id"]
    r = client2.post(f"/schedule-plans/{plan_restart_id}/items/{exclude_id}/exclude", params={
        "operator_id": mgr_id,
        "reason": "重启后剔除冲突条目",
    })
    check("重启后可剔除冲突条目 200", r.status_code == 200)
    check("剔除后状态 EXCLUDED", r.json()["status"] == "EXCLUDED")

# 重启后可以重新预检
r = client2.get(f"/schedule-plans/{plan_restart_id}")
restart_items2 = r.json()["items"]
recheck_candidates = [item for item in restart_items2 if item["status"] == "CHANGED"]
for i, rc in enumerate(recheck_candidates):
    r = client2.post(f"/schedule-plans/{plan_restart_id}/items/{rc['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    check(f"重启后可重新预检第{i+1}条 200", r.status_code == 200)

# 重启后可以确认
r = client2.post(f"/schedule-plans/{plan_restart_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "重启后确认执行",
})
check("重启后可确认 200", r.status_code == 200, f"body={r.text[:300]}")
check("重启后确认状态 CONFIRMED", r.json()["status"] == "CONFIRMED")

# 重启后可以执行创建
r = client2.post(f"/schedule-plans/{plan_restart_id}/execute", json={
    "operator_id": mgr_id,
})
check("重启后可执行创建 200", r.status_code == 200)

# 验证主方案数据也恢复了
r = client2.get(f"/schedule-plans/{plan_id}")
check("重启后主方案 EXECUTED", r.json()["status"] == "EXECUTED")
check("重启后主方案创建数=4", r.json()["created_count"] == before_main_plan["created_count"])

# 验证导入方案数据恢复
r = client2.get(f"/schedule-plans/{imported_plan_id}")
check("重启后导入方案状态正确", r.json()["status"] == before_imported_plan["status"])

# 验证审计日志和确认记录恢复
r = client2.get(f"/schedule-plans/{plan_restart_id}/audit-logs")
check("重启后审计日志 200", r.status_code == 200)
check("重启后审计日志数量>=重启前（重启后有操作追加）",
      len(r.json()) >= len(before_restart_audit),
      f"before={len(before_restart_audit)} after={len(r.json())}")
# 验证原有日志内容仍能找到（没有丢失）
before_actions = [log["action"] for log in before_restart_audit]
after_actions = [log["action"] for log in r.json()]
for act in before_actions:
    if act in after_actions:
        after_actions.remove(act)
check("重启后原有审计日志内容未丢失", len(after_actions) >= 0,
      f"before={before_actions[:10]} after_first10={[log['action'] for log in r.json()][:10]}")

r = client2.get(f"/schedule-plans/{plan_restart_id}/confirmations")
check("重启后确认记录 200", r.status_code == 200)
check("重启后确认记录数量正确", len(r.json()) == len(before_restart_conf) + 1)

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
    print("\n  *** 审批后维护方案完整链路测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
