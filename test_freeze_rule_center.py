"""
维护冻结规则中心完整链路测试：
1. 冻结规则增删改查、启停、备注
2. 时间重叠判断：短时段在长窗口内(NESTED)、跨天(CROSS_DAY)、相邻边界不误拦、重复规则识别
3. 嵌套重叠拦截：短维护窗口完全落在长冻结时段内必须被拦截
4. 预检接口返回重叠类型分类
5. 导入导出恢复：状态、备注、审计日志完整
6. 重启后仍生效
7. 无权限拒绝：非审批角色不能管理冻结规则
8. 停用后放行：停用冻结规则后重新校验可继续操作
9. 命中和变更审计日志
10. 规则重叠检测：创建/更新时检测重叠规则
11. 重新校验接口
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_freeze_rule_center.db")
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
print("  [维护冻结规则中心 完整链路测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户/模板 ---")
r = client.post("/environments", json={"name": "env-freeze-center", "description": "冻结规则中心测试环境"})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/roles", json={"name": "FreezeCenter-Admin", "can_approve": 1, "description": "审批角色"})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "FreezeCenter-User", "can_approve": 0, "description": "普通角色"})
check("创建普通角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "fc.mgr", "display_name": "FCMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "fc.dev", "display_name": "FCDev", "role_id": role_dev_id})
check("创建普通用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "FC-测试模板",
    "description": "冻结规则中心测试模板",
    "environment_id": env_id,
    "start_time": "02:00",
    "end_time": "04:00",
    "change_reason": "常规维护",
    "is_shared": 1,
    "creator_id": mgr_id,
})
check("创建共享模板", r.status_code == 200)
tpl_id = r.json()["id"]

# ---------- 场景1：冻结规则 CRUD + 备注 ----------
print("\n--- [场景1] 冻结规则增删改查+备注 ---")

freeze_start = datetime(2026, 7, 1, 0, 0, 0).isoformat()
freeze_end = datetime(2026, 7, 10, 23, 59, 59).isoformat()

r = client.post("/freeze-rules", json={
    "name": "7月冻结",
    "description": "7月维护冻结期",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "reason": "7月保障期，禁止所有变更",
    "remark": "重要保障期",
    "creator_id": mgr_id,
})
check("创建冻结规则 200", r.status_code == 200, f"body={r.text[:300]}")
rule_id = r.json()["id"]
check("规则状态 ACTIVE", r.json()["status"] == "ACTIVE")
check("规则备注正确", r.json()["remark"] == "重要保障期")

r = client.get(f"/freeze-rules/{rule_id}")
check("查询规则详情 200", r.status_code == 200)
check("详情含审计日志", len(r.json()["audit_logs"]) >= 1)
check("审计含 FREEZE_CREATE", any(
    log["action"] == "FREEZE_CREATE" for log in r.json()["audit_logs"]
))

r = client.put(f"/freeze-rules/{rule_id}", params={"operator_id": mgr_id}, json={
    "remark": "更新备注：经CTO审批",
})
check("更新备注 200", r.status_code == 200)
check("备注已更新", r.json()["remark"] == "更新备注：经CTO审批")

r = client.get(f"/freeze-rules/{rule_id}/audit-logs")
check("更新后审计日志 200", r.status_code == 200)
check("审计含 FREEZE_UPDATE", any(
    log["action"] == "FREEZE_UPDATE" for log in r.json()
))

# ---------- 场景2：短时段在长冻结窗口内（NESTED） ----------
print("\n--- [场景2] 短时段在长冻结窗口内（NESTED）必须拦截 ---")

r = client.post("/maintenance-windows", json={
    "title": "短窗口嵌套在冻结期内",
    "environment_id": env_id,
    "start_time": "2026-07-05T02:00:00",
    "end_time": "2026-07-05T04:00:00",
    "change_reason": "测试嵌套拦截",
    "creator_id": dev_id,
})
check("短窗口在冻结期内被拦截 403", r.status_code == 403, f"status={r.status_code} body={r.text[:200]}")
check("错误含冻结拦截", "冻结" in r.json().get("detail", ""))

# ---------- 场景3：冻结规则带每日时段 - 短时段在长时段内 ----------
print("\n--- [场景3] 每日时段冻结：短时段嵌套在长时段内 ---")

r = client.post("/freeze-rules", json={
    "name": "凌晨冻结",
    "description": "每日0-6点冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 8, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 8, 31, 23, 59, 59).isoformat(),
    "start_time": "00:00",
    "end_time": "06:00",
    "reason": "凌晨维护冻结",
    "remark": "每日时段冻结测试",
    "creator_id": mgr_id,
})
check("创建每日时段冻结规则 200", r.status_code == 200)
daily_rule_id = r.json()["id"]

r = client.post("/maintenance-windows", json={
    "title": "02-04嵌套在00-06冻结内",
    "environment_id": env_id,
    "start_time": "2026-08-10T02:00:00",
    "end_time": "2026-08-10T04:00:00",
    "change_reason": "短时段嵌套",
    "creator_id": dev_id,
})
check("短时段嵌套在长冻结时段内被拦截 403", r.status_code == 403, f"status={r.status_code}")

r = client.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-08-10T02:00:00",
    "end_time": "2026-08-10T04:00:00",
    "scope": "ALL",
})
check("预检短时段嵌套 200", r.status_code == 200)
check("预检有冲突", r.json()["has_conflict"] == True)
if r.json()["conflicts"]:
    overlap_type = r.json()["conflicts"][0].get("overlap_type", "")
    check(f"重叠类型为NESTED", overlap_type == "NESTED", f"overlap_type={overlap_type}")

# ---------- 场景4：相邻边界不误拦 ----------
print("\n--- [场景4] 相邻边界不误拦 ---")

r = client.post("/maintenance-windows", json={
    "title": "06:00开始与冻结期06:00结束相邻",
    "environment_id": env_id,
    "start_time": "2026-08-10T06:00:00",
    "end_time": "2026-08-10T08:00:00",
    "change_reason": "相邻边界测试",
    "creator_id": dev_id,
})
check("冻结06:00结束，窗口06:00开始不误拦 200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:200]}")

r = client.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-08-10T06:00:00",
    "end_time": "2026-08-10T08:00:00",
    "scope": "ALL",
})
check("相邻边界预检无冲突", r.json()["has_conflict"] == False, f"result={r.json()}")

# ---------- 场景5：跨天冻结时段 ----------
print("\n--- [场景5] 跨天冻结时段（如22:00-02:00）---")

r = client.post("/freeze-rules", json={
    "name": "跨天冻结",
    "description": "每日22:00-02:00冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 9, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 30, 23, 59, 59).isoformat(),
    "start_time": "22:00",
    "end_time": "02:00",
    "reason": "跨天冻结测试",
    "creator_id": mgr_id,
})
check("创建跨天冻结规则 200", r.status_code == 200)
cross_day_rule_id = r.json()["id"]

r = client.post("/maintenance-windows", json={
    "title": "23:00-01:00在跨天冻结内",
    "environment_id": env_id,
    "start_time": "2026-09-10T23:00:00",
    "end_time": "2026-09-11T01:00:00",
    "change_reason": "跨天冻结拦截测试",
    "creator_id": dev_id,
})
check("跨天冻结拦截 403", r.status_code == 403, f"status={r.status_code}")

r = client.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-09-10T23:00:00",
    "end_time": "2026-09-11T01:00:00",
    "scope": "ALL",
})
check("跨天预检有冲突", r.json()["has_conflict"] == True)
if r.json()["conflicts"]:
    cross_overlap = [c for c in r.json()["conflicts"] if c["rule_name"] == "跨天冻结"]
    if cross_overlap:
        check("跨天重叠类型为CROSS_DAY", cross_overlap[0].get("overlap_type") == "CROSS_DAY",
              f"overlap_type={cross_overlap[0].get('overlap_type')}")

r = client.post("/maintenance-windows", json={
    "title": "02:00-04:00与跨天冻结02:00结束相邻",
    "environment_id": env_id,
    "start_time": "2026-09-10T02:00:00",
    "end_time": "2026-09-10T04:00:00",
    "change_reason": "跨天相邻边界测试",
    "creator_id": dev_id,
})
check("跨天冻结02:00结束，窗口02:00开始不误拦 200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:200]}")

# ---------- 场景6：重复规则检测 ----------
print("\n--- [场景6] 重复/重叠规则检测 ---")

r = client.post("/freeze-rules", json={
    "name": "7月冻结副本",
    "description": "与7月冻结完全重叠",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "reason": "重复规则测试",
    "creator_id": mgr_id,
})
check("创建重叠规则 200 (允许但检测)", r.status_code == 200)
dup_rule_id = r.json()["id"]

r = client.get(f"/freeze-rules/{dup_rule_id}/audit-logs")
check("创建重叠规则有审计日志", r.status_code == 200)
overlap_create_logs = [log for log in r.json()
                       if log["action"] == "FREEZE_CREATE" and "重叠" in (log.get("detail") or "")]
check("创建日志含重叠提示", len(overlap_create_logs) >= 1,
      f"logs={[l.get('detail') for l in r.json() if l['action'] == 'FREEZE_CREATE']}")

# ---------- 场景7：维护窗口创建/提交/审批统一拦截 ----------
print("\n--- [场景7] 新建、提交、审批维护窗口时统一拦截 ---")

r = client.post("/maintenance-windows", json={
    "title": "冻结期创建被拦截",
    "environment_id": env_id,
    "start_time": "2026-07-05T10:00:00",
    "end_time": "2026-07-05T12:00:00",
    "change_reason": "测试创建拦截",
    "creator_id": dev_id,
})
check("冻结期创建窗口被拦截 403", r.status_code == 403)

r = client.post("/maintenance-windows", json={
    "title": "非冻结期窗口",
    "environment_id": env_id,
    "start_time": "2026-06-20T02:00:00",
    "end_time": "2026-06-20T04:00:00",
    "change_reason": "非冻结期",
    "creator_id": dev_id,
})
check("非冻结期创建窗口 200", r.status_code == 200)
win_id = r.json()["id"]

r = client.post(f"/maintenance-windows/{win_id}/submit", json={
    "operator_id": dev_id,
    "reason": "提交审批",
})
check("非冻结期提交 200", r.status_code == 200)

r = client.post(f"/maintenance-windows/{win_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "审批通过",
})
check("非冻结期审批 200", r.status_code == 200)

# ---------- 场景8：排期方案冻结拦截 ----------
print("\n--- [场景8] 排期方案创建/提交/审批冻结拦截 ---")

r = client.post("/schedule-plans", json={
    "name": "含冻结日期的方案",
    "description": "测试方案冻结",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-07-05", "2026-07-06"],
    "creator_id": dev_id,
})
check("创建含冻结日期方案 200", r.status_code == 200, f"body={r.text[:300]}")
plan_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
})
check("含冻结日期方案提交被拦截 403", r.status_code == 403, f"status={r.status_code}")

r = client.post("/schedule-plans", json={
    "name": "非冻结期方案",
    "description": "测试非冻结期",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-06-20", "2026-06-21"],
    "creator_id": dev_id,
})
check("创建非冻结期方案 200", r.status_code == 200)
plan_ok_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_ok_id}/submit", json={
    "operator_id": dev_id,
})
check("非冻结期方案可提交 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_ok_id}/approve", json={
    "operator_id": mgr_id,
})
check("非冻结期方案可审批 200", r.status_code == 200)

# ---------- 场景9：权限控制 ----------
print("\n--- [场景9] 权限控制：非审批角色不能管理冻结规则 ---")

r = client.post("/freeze-rules", json={
    "name": "普通用户尝试创建",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "creator_id": dev_id,
})
check("普通用户创建冻结规则 403", r.status_code == 403, f"status={r.status_code}")
check("错误含审批角色", "审批角色" in r.json().get("detail", ""))

r = client.put(f"/freeze-rules/{rule_id}", params={"operator_id": dev_id}, json={
    "description": "普通用户修改",
})
check("普通用户修改冻结规则 403", r.status_code == 403)

r = client.post(f"/freeze-rules/{rule_id}/deactivate", json={"operator_id": dev_id})
check("普通用户停用冻结规则 403", r.status_code == 403)

r = client.delete(f"/freeze-rules/{rule_id}", params={"operator_id": dev_id})
check("普通用户删除冻结规则 403", r.status_code == 403)

r = client.post(f"/freeze-rules/{rule_id}/revalidate", params={"operator_id": dev_id})
check("普通用户重新校验 403", r.status_code == 403, f"status={r.status_code}")

r = client.get("/freeze-rules")
check("普通用户可查看列表 200", r.status_code == 200)

r = client.get(f"/freeze-rules/{rule_id}")
check("普通用户可查看详情 200", r.status_code == 200)

# ---------- 场景10：停用后放行+重新校验 ----------
print("\n--- [场景10] 停用冻结规则后放行+重新校验 ---")

r = client.post("/freeze-rules", json={
    "name": "停用测试规则",
    "description": "测试停用后放行",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 10, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 10, 10, 23, 59, 59).isoformat(),
    "reason": "停用测试",
    "creator_id": mgr_id,
})
check("创建停用测试规则 200", r.status_code == 200)
deact_rule_id = r.json()["id"]

r = client.post("/maintenance-windows", json={
    "title": "停用前被冻结",
    "environment_id": env_id,
    "start_time": "2026-10-05T02:00:00",
    "end_time": "2026-10-05T04:00:00",
    "change_reason": "停用前验证",
    "creator_id": dev_id,
})
check("停用前冻结生效 403", r.status_code == 403)

r = client.post(f"/freeze-rules/{deact_rule_id}/deactivate", json={"operator_id": mgr_id})
check("停用冻结规则 200", r.status_code == 200)
check("停用后状态 INACTIVE", r.json()["status"] == "INACTIVE")

r = client.post("/maintenance-windows", json={
    "title": "停用后可创建",
    "environment_id": env_id,
    "start_time": "2026-10-05T02:00:00",
    "end_time": "2026-10-05T04:00:00",
    "change_reason": "停用后验证",
    "creator_id": dev_id,
})
check("停用后可正常创建 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

# ---------- 场景11：停用后重新校验排期方案 ----------
print("\n--- [场景11] 停用冻结规则后重新校验排期方案 ---")

r = client.post("/schedule-plans", json={
    "name": "受冻结影响方案",
    "description": "测试停用后方案恢复",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-10-05", "2026-10-06"],
    "creator_id": dev_id,
})
check("创建受冻结影响方案 200", r.status_code == 200)
affected_plan_id = r.json()["id"]

r = client.post(f"/freeze-rules/{deact_rule_id}/activate", json={"operator_id": mgr_id})
check("重新启用冻结规则 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{affected_plan_id}/submit", json={
    "operator_id": dev_id,
})
check("启用后方案提交被拦截 403", r.status_code == 403)

r = client.post(f"/freeze-rules/{deact_rule_id}/deactivate", json={"operator_id": mgr_id})
check("再次停用 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{affected_plan_id}/submit", json={
    "operator_id": dev_id,
})
check("停用后方案可提交 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

r = client.post(f"/schedule-plans/{affected_plan_id}/approve", json={
    "operator_id": mgr_id,
})
check("停用后方案可审批 200", r.status_code == 200)

# ---------- 场景12：重新校验接口 ----------
print("\n--- [场景12] 重新校验接口 ---")

r = client.post(f"/freeze-rules/{rule_id}/revalidate", params={"operator_id": mgr_id})
check("重新校验接口 200", r.status_code == 200, f"body={r.text[:300]}")
reval_result = r.json()
check("重新校验含rule_id", "rule_id" in reval_result)
check("重新校验含affected_plans", "affected_plans" in reval_result)
check("重新校验含affected_windows", "affected_windows" in reval_result)

# ---------- 场景13：命中审计日志 ----------
print("\n--- [场景13] 命中和变更审计日志 ---")

r = client.get(f"/freeze-rules/{rule_id}/audit-logs")
check("冻结规则审计日志 200", r.status_code == 200)
audit_actions = [log["action"] for log in r.json()]
check("含 FREEZE_CREATE", "FREEZE_CREATE" in audit_actions)
check("含 FREEZE_UPDATE", "FREEZE_UPDATE" in audit_actions)

r = client.get(f"/freeze-rules/{deact_rule_id}/audit-logs")
check("停用规则审计日志 200", r.status_code == 200)
deact_audit_actions = [log["action"] for log in r.json()]
check("含 FREEZE_ACTIVATE", "FREEZE_ACTIVATE" in deact_audit_actions)
check("含 FREEZE_DEACTIVATE", "FREEZE_DEACTIVATE" in deact_audit_actions)

r = client.get(f"/freeze-rules/{rule_id}/audit-logs")
hit_logs = [log for log in r.json() if log["action"] == "FREEZE_HIT_WINDOW"]
check("有窗口命中日志", len(hit_logs) >= 1, f"hit_count={len(hit_logs)}")

hit_plan_logs = [log for log in r.json() if log["action"] == "FREEZE_HIT_PLAN"]
check("有方案命中日志", len(hit_plan_logs) >= 1, f"hit_count={len(hit_plan_logs)}")

# ---------- 场景14：导入导出恢复 ----------
print("\n--- [场景14] 冻结规则导入导出恢复 ---")

r = client.post("/freeze-rules/export", params={"rule_ids": [rule_id, daily_rule_id]})
check("导出冻结规则 200", r.status_code == 200)
export_data = r.json()
check("导出2条", export_data["count"] == 2, f"count={export_data['count']}")

for exp_rule in export_data["data"]:
    check(f"导出含名称 {exp_rule.get('name')}", exp_rule.get("name") is not None)
    check(f"导出含状态", "status" in exp_rule)
    check(f"导出含备注", "remark" in exp_rule)
    check(f"导出含审计日志", len(exp_rule.get("audit_logs", [])) >= 1)

import_data = export_data["data"]
import_data[0]["name"] = "导入的7月冻结"
import_data[1]["name"] = "导入的凌晨冻结"

r = client.post("/freeze-rules/import", json={
    "rules": import_data,
    "operator_id": mgr_id,
    "on_conflict": "skip",
})
check("导入冻结规则 200", r.status_code == 200)
import_result = r.json()
check("导入成功2条", import_result["success"] == 2, f"result={import_result}")

imported_ids = [d["id"] for d in import_result["details"] if d.get("status") == "created"]

for imp_id in imported_ids:
    r = client.get(f"/freeze-rules/{imp_id}")
    check(f"导入规则详情 200", r.status_code == 200)
    imported = r.json()
    check("导入规则状态 ACTIVE", imported["status"] == "ACTIVE")
    check("导入规则含备注", imported.get("remark") is not None)

    r = client.get(f"/freeze-rules/{imp_id}/audit-logs")
    check("导入恢复了审计日志", len(r.json()) >= 1, f"logs_count={len(r.json())}")

if imported_ids:
    r = client.post("/maintenance-windows", json={
        "title": "验证导入冻结生效",
        "environment_id": env_id,
        "start_time": "2026-07-05T10:00:00",
        "end_time": "2026-07-05T12:00:00",
        "change_reason": "验证导入冻结",
        "creator_id": dev_id,
    })
    check("导入的冻结规则立即生效拦截 403", r.status_code == 403)

# ---------- 场景15：重启后仍生效 ----------
print("\n--- [场景15] 服务重启后冻结规则仍生效 ---")

before_restart_rules = client.get("/freeze-rules").json()
before_rule_detail = client.get(f"/freeze-rules/{rule_id}").json()
before_audit = client.get(f"/freeze-rules/{rule_id}/audit-logs").json()

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
check("重启后状态仍为 ACTIVE", after_detail["status"] == "ACTIVE")
check("重启后备注保留", after_detail.get("remark") == before_rule_detail.get("remark"))

r = client2.get(f"/freeze-rules/{rule_id}/audit-logs")
check("重启后审计日志保留", len(r.json()) == len(before_audit),
      f"before={len(before_audit)} after={len(r.json())}")

r = client2.post("/maintenance-windows", json={
    "title": "重启后验证冻结生效",
    "environment_id": env_id,
    "start_time": "2026-07-05T10:00:00",
    "end_time": "2026-07-05T12:00:00",
    "change_reason": "重启后验证",
    "creator_id": dev_id,
})
check("重启后冻结仍生效 403", r.status_code == 403)

r = client2.post("/maintenance-windows", json={
    "title": "重启后非冻结期正常",
    "environment_id": env_id,
    "start_time": "2026-06-20T02:00:00",
    "end_time": "2026-06-20T04:00:00",
    "change_reason": "重启后正常创建",
    "creator_id": dev_id,
})
check("重启后非冻结期可正常创建 200", r.status_code == 200)

# ---------- 场景16：重启后停用规则放行 ----------
print("\n--- [场景16] 重启后停用规则放行 ---")

r = client2.get(f"/freeze-rules/{deact_rule_id}/audit-logs")
check("停用规则审计日志 200", r.status_code == 200)

r = client2.post("/maintenance-windows", json={
    "title": "重启后停用规则期间可创建",
    "environment_id": env_id,
    "start_time": "2026-10-05T02:00:00",
    "end_time": "2026-10-05T04:00:00",
    "change_reason": "重启后停用规则放行",
    "creator_id": dev_id,
})
check("重启后停用规则放行 200", r.status_code == 200)

# ---------- 场景17：按作用域冻结 ----------
print("\n--- [场景17] 按作用域冻结（只冻结审批） ---")

r = client2.post("/freeze-rules", json={
    "name": "仅审批冻结-中心测试",
    "description": "只禁止审批操作",
    "environment_id": env_id,
    "freeze_scope": "APPROVE",
    "date_from": datetime(2026, 11, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 11, 5, 23, 59, 59).isoformat(),
    "reason": "只冻结审批环节",
    "creator_id": mgr_id,
})
check("创建仅审批冻结规则 200", r.status_code == 200)
scope_rule_id = r.json()["id"]

r = client2.post("/maintenance-windows", json={
    "title": "仅审批冻结期可创建",
    "environment_id": env_id,
    "start_time": "2026-11-03T10:00:00",
    "end_time": "2026-11-03T12:00:00",
    "change_reason": "仅审批冻结测试",
    "creator_id": dev_id,
})
check("仅审批冻结期可创建 200", r.status_code == 200)
scope_win_id = r.json()["id"]

r = client2.post(f"/maintenance-windows/{scope_win_id}/submit", json={
    "operator_id": dev_id,
})
check("仅审批冻结期可提交 200", r.status_code == 200)

r = client2.post(f"/maintenance-windows/{scope_win_id}/approve", json={
    "operator_id": mgr_id,
})
check("仅审批冻结期审批被拦截 403", r.status_code == 403)

# ---------- 场景18：删除冻结规则后放行 ----------
print("\n--- [场景18] 删除冻结规则后放行 ---")

r = client2.post("/freeze-rules", json={
    "name": "待删除的规则",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 12, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 12, 5, 23, 59, 59).isoformat(),
    "reason": "测试删除",
    "creator_id": mgr_id,
})
check("创建待删除规则 200", r.status_code == 200)
del_rule_id = r.json()["id"]

r = client2.post("/maintenance-windows", json={
    "title": "删除前被冻结",
    "environment_id": env_id,
    "start_time": "2026-12-03T02:00:00",
    "end_time": "2026-12-03T04:00:00",
    "change_reason": "验证删除前被冻结",
    "creator_id": dev_id,
})
check("删除前冻结生效 403", r.status_code == 403)

r = client2.delete(f"/freeze-rules/{del_rule_id}", params={"operator_id": mgr_id})
check("删除冻结规则 200", r.status_code == 200)

r = client2.post("/maintenance-windows", json={
    "title": "删除后可创建",
    "environment_id": env_id,
    "start_time": "2026-12-03T02:00:00",
    "end_time": "2026-12-03T04:00:00",
    "change_reason": "删除后验证",
    "creator_id": dev_id,
})
check("删除冻结规则后可正常创建 200", r.status_code == 200)

# ---------- 场景19：重叠类型分类完整性 ----------
print("\n--- [场景19] 重叠类型分类完整性验证 ---")

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-07-05T00:00:00",
    "end_time": "2026-07-05T23:59:59",
    "scope": "ALL",
})
check("全天冻结期预检 200", r.status_code == 200)
check("全天冻结期有冲突", r.json()["has_conflict"] == True)
if r.json()["conflicts"]:
    full_day_conflict = [c for c in r.json()["conflicts"] if c["rule_name"] == "7月冻结"]
    if full_day_conflict:
        check("全天冻结重叠类型 FULL_DAY", full_day_conflict[0].get("overlap_type") == "FULL_DAY",
              f"type={full_day_conflict[0].get('overlap_type')}")

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-08-10T01:00:00",
    "end_time": "2026-08-10T03:00:00",
    "scope": "ALL",
})
check("凌晨冻结预检 200", r.status_code == 200)
if r.json()["conflicts"]:
    daily_conflict = [c for c in r.json()["conflicts"] if c["rule_name"] == "凌晨冻结"]
    if daily_conflict:
        check("凌晨冻结重叠类型 NESTED", daily_conflict[0].get("overlap_type") == "NESTED",
              f"type={daily_conflict[0].get('overlap_type')}")

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-09-10T23:30:00",
    "end_time": "2026-09-11T00:30:00",
    "scope": "ALL",
})
check("跨天冻结预检 200", r.status_code == 200)
if r.json()["conflicts"]:
    cross_conflict = [c for c in r.json()["conflicts"] if c["rule_name"] == "跨天冻结"]
    if cross_conflict:
        check("跨天重叠类型 CROSS_DAY", cross_conflict[0].get("overlap_type") == "CROSS_DAY",
              f"type={cross_conflict[0].get('overlap_type')}")

r = client2.post("/freeze-rules/check", params={
    "environment_id": env_id,
    "start_time": "2026-08-10T05:00:00",
    "end_time": "2026-08-10T07:00:00",
    "scope": "ALL",
})
check("部分重叠预检 200", r.status_code == 200)
if r.json()["conflicts"]:
    partial_conflict = [c for c in r.json()["conflicts"] if c["rule_name"] == "凌晨冻结"]
    if partial_conflict:
        check("部分重叠类型 PARTIAL", partial_conflict[0].get("overlap_type") == "PARTIAL",
              f"type={partial_conflict[0].get('overlap_type')}")

# ---------- 场景20：启停操作审计日志 ----------
print("\n--- [场景20] 启停操作审计日志 ---")

r = client2.get(f"/freeze-rules/{deact_rule_id}/audit-logs")
check("停用规则审计日志 200", r.status_code == 200)
deact_logs = r.json()
check("含 FREEZE_DEACTIVATE", any(l["action"] == "FREEZE_DEACTIVATE" for l in deact_logs))
check("含 FREEZE_ACTIVATE", any(l["action"] == "FREEZE_ACTIVATE" for l in deact_logs))

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
    print("\n  *** 维护冻结规则中心完整链路测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
