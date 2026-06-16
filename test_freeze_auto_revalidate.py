"""
冻结规则自动重校验中心 回归测试

覆盖场景：
1. 新建即拦截：创建冻结规则后，已审批的方案自动转为待确认
2. 重新启用后自动重校验：停用再启用规则，自动重新校验所有方案
3. 配置修改后命中变化：修改规则时间范围，命中条目自动增减
4. 失效/删除/撤销后自动恢复：规则失效后条目自动恢复状态
5. 多规则同时命中：同一方案被多条规则命中时的状态收回顺序
6. 人工解除后再次命中：recheck 后再次创建规则仍能正确拦截
7. 服务重启后状态一致：数据完整落库，重启后状态不变
8. 无权限操作拒绝：非审批角色不能管理冻结规则
9. 审计日志可追踪：所有操作都有完整审计记录
10. 时间窗口重叠：重叠规则的处理逻辑正确
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_freeze_auto_revalidate.db")
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
from app.database import Base, engine

Base.metadata.create_all(bind=engine)

client = TestClient(app)

total = 0
passed = 0
failed = 0


def check(name, cond, detail=""):
    global total, passed, failed
    total += 1
    if cond:
        passed += 1
        print(f"[PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {name}  FAIL-INFO: {detail}")
    return cond


print("=" * 70)
print("  [冻结规则自动重校验中心 回归测试]")
print("=" * 70)

print("\n--- [准备] 建环境、角色/用户/模板 ---")

r = client.post("/environments", json={
    "name": "env-revalidate",
    "description": "自动重校验测试环境",
})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/roles", json={
    "name": "审批角色-重校验",
    "can_approve": True,
    "can_manage_window": True,
})
check("创建审批角色", r.status_code == 200)
approve_role_id = r.json()["id"]

r = client.post("/roles", json={
    "name": "开发角色-重校验",
    "can_approve": False,
    "can_manage_window": True,
})
check("创建普通角色", r.status_code == 200)
dev_role_id = r.json()["id"]

r = client.post("/users", json={
    "username": "mgr_revalidate",
    "display_name": "MgrRevalidate",
    "role_id": approve_role_id,
})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={
    "username": "dev_revalidate",
    "display_name": "DevRevalidate",
    "role_id": dev_role_id,
})
check("创建普通用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "自动重校验测试模板",
    "description": "用于测试自动重校验",
    "environment_id": env_id,
    "start_time": "02:00",
    "end_time": "04:00",
    "change_reason": "例行维护",
    "is_shared": 1,
    "creator_id": mgr_id,
})
check("创建共享模板", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
template_id = r.json()["id"]


# ---------- 场景1：新建即拦截 ----------
print("\n--- [场景1] 新建即拦截：已审批方案被新规则自动拦截 ---")

r = client.post("/schedule-plans", json={
    "name": "新建即拦截测试方案",
    "description": "测试创建冻结规则后自动拦截",
    "template_id": template_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-09-10", "2026-09-11", "2026-09-12"],
    "creator_id": mgr_id,
})
check("创建排期方案", r.status_code == 200)
plan_1_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan_1_id}/submit", json={"operator_id": mgr_id})
check("提交审批", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_1_id}/approve", json={"operator_id": mgr_id})
check("审批通过", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
check("审批后方案状态为 APPROVED", r.json()["status"] == "APPROVED")
approved_items_before = [i for i in r.json()["items"] if i["status"] == "APPROVED"]
check(f"审批通过 {len(approved_items_before)} 条", len(approved_items_before) == 3)

r = client.post("/freeze-rules", json={
    "name": "9月中旬冻结",
    "description": "测试新建即拦截",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 9, 10, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 12, 23, 59, 59).isoformat(),
    "reason": "9月中旬保障期",
    "creator_id": mgr_id,
})
check("创建冻结规则（新建即拦截）", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
rule_1_id = r.json()["id"]

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_create = r.json()
check("新建规则后方案状态变为 CONFIRMING", 
      plan_1_after_create["status"] == "CONFIRMING",
      f"status={plan_1_after_create['status']}")

changed_items = [i for i in plan_1_after_create["items"] if i["status"] == "CHANGED"]
check(f"新建规则后 {len(changed_items)} 条变为 CHANGED", 
      len(changed_items) == 3,
      f"changed={len(changed_items)}")

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id})
hit_records_1 = r.json()
check(f"生成 {len(hit_records_1)} 条命中记录", len(hit_records_1) == 3)
check("命中记录状态为 ACTIVE", all(h["status"] == "ACTIVE" for h in hit_records_1))

r = client.get(f"/schedule-plans/{plan_1_id}/audit-logs")
plan_1_audit_logs = r.json()
plan_freeze_hit_logs = [l for l in plan_1_audit_logs if l["action"] == "PLAN_FREEZE_HIT"]
check(f"有 {len(plan_freeze_hit_logs)} 条 PLAN_FREEZE_HIT 审计日志", 
      len(plan_freeze_hit_logs) >= 1)

r = client.get(f"/freeze-rules/{rule_1_id}/audit-logs")
rule_1_audit = r.json()
has_create_log = any(l["action"] == "FREEZE_CREATE" for l in rule_1_audit)
has_recover_log = any(l["action"] == "FREEZE_RECOVER" for l in rule_1_audit)
check("规则审计包含 FREEZE_CREATE", has_create_log)
check("规则审计包含 FREEZE_RECOVER", has_recover_log)


# ---------- 场景2：配置修改后命中变化 ----------
print("\n--- [场景2] 配置修改后命中变化：修改规则时间，命中条目自动增减 ---")

r = client.put(f"/freeze-rules/{rule_1_id}", json={
    "date_from": datetime(2026, 9, 11, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 11, 23, 59, 59).isoformat(),
}, params={"operator_id": mgr_id})
check("修改冻结规则（缩短到只有1天）", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_update = r.json()
changed_items_after = [i for i in plan_1_after_update["items"] if i["status"] == "CHANGED"]
check(f"修改后还剩 {len(changed_items_after)} 条 CHANGED", 
      len(changed_items_after) == 1,
      f"changed={len(changed_items_after)}")

approved_items_after = [i for i in plan_1_after_update["items"] if i["status"] == "APPROVED"]
check(f"修改后有 {len(approved_items_after)} 条恢复为 APPROVED", 
      len(approved_items_after) == 2,
      f"approved={len(approved_items_after)}")

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
active_hits = r.json()
check(f"活跃命中记录变为 {len(active_hits)} 条", len(active_hits) == 1)

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "RECOVERED"})
recovered_hits = r.json()
check(f"已恢复命中记录有 {len(recovered_hits)} 条", len(recovered_hits) == 2)

r = client.get("/freeze-recovery-logs", params={"plan_id": plan_1_id})
recovery_logs = r.json()
check(f"生成 {len(recovery_logs)} 条恢复日志", len(recovery_logs) >= 2)

r = client.put(f"/freeze-rules/{rule_1_id}", json={
    "date_from": datetime(2026, 9, 9, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 13, 23, 59, 59).isoformat(),
}, params={"operator_id": mgr_id})
check("修改冻结规则（扩大到5天）", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_widen = r.json()
changed_items_widen = [i for i in plan_1_after_widen["items"] if i["status"] == "CHANGED"]
check(f"扩大后有 {len(changed_items_widen)} 条 CHANGED", 
      len(changed_items_widen) == 3,
      f"changed={len(changed_items_widen)}")

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
active_hits_widen = r.json()
check(f"扩大后活跃命中记录有 {len(active_hits_widen)} 条", len(active_hits_widen) == 3)


# ---------- 场景3：失效后自动恢复 ----------
print("\n--- [场景3] 失效后自动恢复：停用规则后条目自动恢复 ---")

r = client.post(f"/freeze-rules/{rule_1_id}/deactivate", json={"operator_id": mgr_id})
check("停用冻结规则", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_deactivate = r.json()
check("停用后方案状态变回 APPROVED", 
      plan_1_after_deactivate["status"] == "APPROVED",
      f"status={plan_1_after_deactivate['status']}")

all_approved = all(i["status"] == "APPROVED" for i in plan_1_after_deactivate["items"]
                   if i["status"] != "EXCLUDED")
check("停用后所有条目恢复为 APPROVED", all_approved)

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
active_after_deact = r.json()
check("停用后没有活跃命中记录", len(active_after_deact) == 0)

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "RECOVERED"})
recovered_after_deact = r.json()
check(f"停用后有 {len(recovered_after_deact)} 条 RECOVERED 记录", len(recovered_after_deact) >= 3)

r = client.get("/freeze-recovery-logs", params={"rule_id": rule_1_id})
recovery_deact = r.json()
check(f"停用后生成恢复日志", len(recovery_deact) >= 3)


# ---------- 场景4：重新启用后自动重校验 ----------
print("\n--- [场景4] 重新启用后自动重校验：重新启用规则自动拦截 ---")

r = client.post(f"/freeze-rules/{rule_1_id}/activate", json={"operator_id": mgr_id})
check("重新启用冻结规则", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_activate = r.json()
check("重新启用后方案状态变为 CONFIRMING", 
      plan_1_after_activate["status"] == "CONFIRMING",
      f"status={plan_1_after_activate['status']}")

changed_after_activate = [i for i in plan_1_after_activate["items"] if i["status"] == "CHANGED"]
check(f"重新启用后有 {len(changed_after_activate)} 条 CHANGED", 
      len(changed_after_activate) == 3)

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
active_after_activate = r.json()
check(f"重新启用后有 {len(active_after_activate)} 条活跃命中", len(active_after_activate) == 3)


# ---------- 场景5：多规则同时命中 ----------
print("\n--- [场景5] 多规则同时命中：状态收回顺序正确 ---")

r = client.post("/freeze-rules", json={
    "name": "9月上旬冻结",
    "description": "测试多规则同时命中",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 9, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 15, 23, 59, 59).isoformat(),
    "reason": "9月上旬保障期",
    "creator_id": mgr_id,
})
check("创建第二条冻结规则（范围更大）", r.status_code == 200)
rule_2_id = r.json()["id"]

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
multi_active = r.json()
check(f"多规则命中时有 {len(multi_active)} 条活跃记录", len(multi_active) == 6)

r = client.post(f"/freeze-rules/{rule_2_id}/deactivate", json={"operator_id": mgr_id})
check("停用第二条规则（范围更大的）", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_deact2 = r.json()
check("停用一条规则后方案仍为 CONFIRMING", 
      plan_1_after_deact2["status"] == "CONFIRMING",
      f"status={plan_1_after_deact2['status']}")

still_changed = [i for i in plan_1_after_deact2["items"] if i["status"] == "CHANGED"]
check(f"停用一条规则后仍有 {len(still_changed)} 条 CHANGED", 
      len(still_changed) == 3)

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id, "status": "ACTIVE"})
still_active = r.json()
check(f"仍有 {len(still_active)} 条活跃命中（来自第一条规则）", 
      len(still_active) == 3)

r = client.post(f"/freeze-rules/{rule_1_id}/deactivate", json={"operator_id": mgr_id})
check("停用第一条规则", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_after_deact_all = r.json()
check("两条规则都停用后方案变回 APPROVED", 
      plan_1_after_deact_all["status"] == "APPROVED",
      f"status={plan_1_after_deact_all['status']}")

all_ok = all(i["status"] == "APPROVED" for i in plan_1_after_deact_all["items"]
             if i["status"] != "EXCLUDED")
check("两条规则都停用后所有条目恢复 APPROVED", all_ok)


# ---------- 场景6：人工解除后再次命中 ----------
print("\n--- [场景6] 人工解除后再次命中：一致性保证 ---")

r = client.post(f"/freeze-rules/{rule_1_id}/activate", json={"operator_id": mgr_id})
check("重新启用规则1（准备人工解除测试）", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_1_before_recheck = r.json()
changed_for_recheck = [i for i in plan_1_before_recheck["items"] if i["status"] == "CHANGED"]
check(f"激活后有 {len(changed_for_recheck)} 条 CHANGED", len(changed_for_recheck) > 0)

item_to_recheck = changed_for_recheck[0]
r = client.post(f"/schedule-plans/{plan_1_id}/items/{item_to_recheck['id']}/recheck", 
                params={"operator_id": mgr_id})
check("人工重新预检单条（应该仍命中）", r.status_code == 200)
check("重新预检后状态仍为 CHANGED", r.json()["status"] == "CHANGED")

r = client.post(f"/freeze-rules/{rule_1_id}/deactivate", json={"operator_id": mgr_id})
check("停用规则1", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan_1_id}/items/{item_to_recheck['id']}/recheck", 
                params={"operator_id": mgr_id})
check("停用后人工重新预检（应该恢复）", r.status_code == 200)
check("重新预检后状态变为 APPROVED", r.json()["status"] == "APPROVED")

r = client.get("/freeze-hit-records", params={
    "plan_id": plan_1_id, 
    "item_id": item_to_recheck["id"],
})
item_hit_records = r.json()
recovered_record = [h for h in item_hit_records if h["status"] == "RECOVERED"]
check(f"该条目有 {len(recovered_record)} 条已恢复的命中记录", len(recovered_record) >= 1)

r = client.post(f"/freeze-rules/{rule_1_id}/activate", json={"operator_id": mgr_id})
check("再次启用规则1", r.status_code == 200)

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_after_reactivate = r.json()
item_after_reactivate = [i for i in plan_after_reactivate["items"] 
                         if i["id"] == item_to_recheck["id"]][0]
check("再次启用后条目又变为 CHANGED", item_after_reactivate["status"] == "CHANGED")

r = client.get("/freeze-hit-records", params={
    "plan_id": plan_1_id, 
    "item_id": item_to_recheck["id"],
    "status": "ACTIVE",
})
item_active_hits = r.json()
check(f"再次启用后该条目有 {len(item_active_hits)} 条活跃命中", len(item_active_hits) >= 1)


# ---------- 场景7：服务重启后状态一致 ----------
print("\n--- [场景7] 服务重启后状态一致：数据完整落库 ---")

r = client.get(f"/schedule-plans/{plan_1_id}")
plan_before_restart = r.json()
plan_status_before = plan_before_restart["status"]
items_before = [(i["date"], i["status"]) for i in plan_before_restart["items"]]

r = client.get("/freeze-hit-records", params={"plan_id": plan_1_id})
hits_before_restart = r.json()
active_count_before = len([h for h in hits_before_restart if h["status"] == "ACTIVE"])
recovered_count_before = len([h for h in hits_before_restart if h["status"] == "RECOVERED"])

r = client.get("/freeze-recovery-logs", params={"plan_id": plan_1_id})
recovery_before_restart = r.json()
recovery_count_before = len(recovery_before_restart)

r = client.get("/freeze-rules")
rules_before_restart = r.json()
rules_count_before = len(rules_before_restart)

print("[DEBUG] 重启前:", 
      f"方案状态={plan_status_before},",
      f"活跃命中={active_count_before},",
      f"已恢复命中={recovered_count_before},",
      f"恢复日志={recovery_count_before},",
      f"规则数={rules_count_before}")

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

r = client2.get(f"/schedule-plans/{plan_1_id}")
plan_after_restart = r.json()
check("重启后方案状态一致", plan_after_restart["status"] == plan_status_before)

items_after = [(i["date"], i["status"]) for i in plan_after_restart["items"]]
check("重启后所有条目状态一致", items_before == items_after)

r = client2.get("/freeze-hit-records", params={"plan_id": plan_1_id})
hits_after_restart = r.json()
active_count_after = len([h for h in hits_after_restart if h["status"] == "ACTIVE"])
recovered_count_after = len([h for h in hits_after_restart if h["status"] == "RECOVERED"])

check(f"重启后活跃命中数一致 ({active_count_before})", active_count_after == active_count_before)
check(f"重启后已恢复命中数一致 ({recovered_count_before})", 
      recovered_count_after == recovered_count_before)

r = client2.get("/freeze-recovery-logs", params={"plan_id": plan_1_id})
recovery_after_restart = r.json()
check(f"重启后恢复日志数一致 ({recovery_count_before})", 
      len(recovery_after_restart) == recovery_count_before)

r = client2.get("/freeze-rules")
rules_after_restart = r.json()
check(f"重启后规则数一致 ({rules_count_before})", 
      len(rules_after_restart) == rules_count_before)

r = client2.post("/maintenance-windows", json={
    "title": "重启后验证冻结仍生效",
    "environment_id": env_id,
    "start_time": "2026-09-10T02:00:00",
    "end_time": "2026-09-10T04:00:00",
    "change_reason": "重启后验证",
    "creator_id": dev_id,
})
check("重启后冻结仍生效（403）", r.status_code == 403)


# ---------- 场景8：无权限操作拒绝 ----------
print("\n--- [场景8] 无权限操作拒绝：非审批角色不能管理冻结规则 ---")

r = client2.post("/freeze-rules", json={
    "name": "无权限测试规则",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 10, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 10, 10, 23, 59, 59).isoformat(),
    "reason": "无权限测试",
    "creator_id": dev_id,
})
check("普通用户不能创建冻结规则（403）", r.status_code == 403)
check("错误信息包含权限相关", "权限" in r.json().get("detail", "") or 
      "审批" in r.json().get("detail", ""))

r = client2.put(f"/freeze-rules/{rule_1_id}", json={
    "reason": "无权修改",
}, params={"operator_id": dev_id})
check("普通用户不能修改冻结规则（403）", r.status_code == 403)

r = client2.post(f"/freeze-rules/{rule_1_id}/activate", json={"operator_id": dev_id})
check("普通用户不能启用冻结规则（403）", r.status_code == 403)

r = client2.post(f"/freeze-rules/{rule_1_id}/deactivate", json={"operator_id": dev_id})
check("普通用户不能停用冻结规则（403）", r.status_code == 403)

r = client2.delete(f"/freeze-rules/{rule_1_id}", params={"operator_id": dev_id})
check("普通用户不能删除冻结规则（403）", r.status_code == 403)

r = client2.get("/freeze-rules", params={"operator_id": dev_id})
check("普通用户可以查看冻结规则列表（200）", r.status_code == 200)

r = client2.get(f"/freeze-rules/{rule_1_id}", params={"operator_id": dev_id})
check("普通用户可以查看冻结规则详情（200）", r.status_code == 200)

r = client2.get(f"/freeze-rules/{rule_1_id}/audit-logs", params={"operator_id": dev_id})
check("普通用户可以查看冻结规则审计日志（200）", r.status_code == 200)

r = client2.get("/freeze-hit-records", params={"operator_id": dev_id})
check("普通用户可以查看命中记录（200）", r.status_code == 200)

r = client2.get("/freeze-recovery-logs", params={"operator_id": dev_id})
check("普通用户可以查看恢复日志（200）", r.status_code == 200)

r = client2.get("/freeze-recovery-center/summary", params={"operator_id": dev_id})
check("普通用户可以查看恢复中心概览（200）", r.status_code == 200)


# ---------- 场景9：审计日志可追踪 ----------
print("\n--- [场景9] 审计日志可追踪：所有操作都有完整记录 ---")

r = client2.get(f"/freeze-rules/{rule_1_id}/audit-logs")
rule_audit = r.json()
audit_actions = [a["action"] for a in rule_audit]

check("规则审计包含 FREEZE_CREATE", "FREEZE_CREATE" in audit_actions)
check("规则审计包含 FREEZE_UPDATE", "FREEZE_UPDATE" in audit_actions)
check("规则审计包含 FREEZE_DEACTIVATE", "FREEZE_DEACTIVATE" in audit_actions)
check("规则审计包含 FREEZE_ACTIVATE", "FREEZE_ACTIVATE" in audit_actions)
check("规则审计包含 FREEZE_RECOVER", "FREEZE_RECOVER" in audit_actions)

r = client2.get(f"/schedule-plans/{plan_1_id}/audit-logs")
plan_audit = r.json()
plan_audit_actions = [a["action"] for a in plan_audit]

check("方案审计包含 PLAN_CREATE", "PLAN_CREATE" in plan_audit_actions)
check("方案审计包含 PLAN_SUBMIT", "PLAN_SUBMIT" in plan_audit_actions)
check("方案审计包含 PLAN_APPROVE", "PLAN_APPROVE" in plan_audit_actions)
check("方案审计包含 PLAN_FREEZE_HIT", "PLAN_FREEZE_HIT" in plan_audit_actions)
check("方案审计包含 PLAN_FREEZE_RECOVER", "PLAN_FREEZE_RECOVER" in plan_audit_actions)

r = client2.get("/freeze-hit-records", params={"plan_id": plan_1_id})
all_hits = r.json()
check("命中记录包含规则ID", all(h.get("rule_id") is not None for h in all_hits))
check("命中记录包含规则名称", all(h.get("rule_name") is not None for h in all_hits))
check("命中记录包含命中原因", all(h.get("hit_reason") is not None for h in all_hits))
check("命中记录包含创建时间", all(h.get("created_at") is not None for h in all_hits))
check("已恢复的命中包含恢复时间", 
      all(h.get("recovered_at") is not None for h in all_hits if h["status"] == "RECOVERED"))
check("已恢复的命中包含恢复人", 
      all(h.get("recovered_by") is not None for h in all_hits if h["status"] == "RECOVERED"))
check("已恢复的命中包含恢复原因", 
      all(h.get("recovery_reason") is not None for h in all_hits if h["status"] == "RECOVERED"))

r = client2.get("/freeze-recovery-logs", params={"plan_id": plan_1_id})
all_recovery = r.json()
check("恢复日志包含触发动作", all(l.get("trigger_action") is not None for l in all_recovery))
check("恢复日志包含操作人", all(l.get("operator_id") is not None for l in all_recovery))
check("恢复日志包含状态变更", 
      all(l.get("status_before") is not None and l.get("status_after") is not None 
          for l in all_recovery))
check("恢复日志包含详情描述", all(l.get("detail") is not None for l in all_recovery))


# ---------- 场景10：删除后自动恢复 ----------
print("\n--- [场景10] 删除后自动恢复：删除规则后条目自动恢复 ---")

r = client2.post("/schedule-plans", json={
    "name": "删除恢复测试方案",
    "description": "测试删除规则后自动恢复",
    "template_id": template_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-11-01", "2026-11-02"],
    "creator_id": mgr_id,
})
check("创建删除测试方案", r.status_code == 200)
plan_del_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan_del_id}/submit", json={"operator_id": mgr_id})
check("提交审批", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan_del_id}/approve", json={"operator_id": mgr_id})
check("审批通过", r.status_code == 200)

r = client2.post("/freeze-rules", json={
    "name": "11月删除测试冻结",
    "description": "测试删除后自动恢复",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 11, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 11, 10, 23, 59, 59).isoformat(),
    "reason": "11月测试",
    "creator_id": mgr_id,
})
check("创建删除测试冻结规则", r.status_code == 200)
rule_del_id = r.json()["id"]

r = client2.get(f"/schedule-plans/{plan_del_id}")
plan_del_after = r.json()
check("创建规则后方案变为 CONFIRMING", plan_del_after["status"] == "CONFIRMING")

r = client2.delete(f"/freeze-rules/{rule_del_id}", params={"operator_id": mgr_id})
check("删除冻结规则", r.status_code == 200)

r = client2.get(f"/schedule-plans/{plan_del_id}")
plan_del_deleted = r.json()
check("删除规则后方案恢复为 APPROVED", 
      plan_del_deleted["status"] == "APPROVED",
      f"status={plan_del_deleted['status']}")

all_items_ok = all(i["status"] == "APPROVED" for i in plan_del_deleted["items"]
                   if i["status"] != "EXCLUDED")
check("删除规则后所有条目恢复 APPROVED", all_items_ok)

r = client2.get("/freeze-hit-records", params={"plan_id": plan_del_id})
del_hits = r.json()
all_recovered = all(h["status"] == "RECOVERED" for h in del_hits)
check("删除规则后所有命中记录变为 RECOVERED", all_recovered)

r = client2.get("/freeze-recovery-logs", params={"plan_id": plan_del_id})
del_recovery = r.json()
check("删除规则后生成恢复日志", len(del_recovery) >= 2)


# ---------- 场景11：时间窗口重叠 ----------
print("\n--- [场景11] 时间窗口重叠：重叠规则处理正确 ---")

r = client2.post("/schedule-plans", json={
    "name": "重叠测试方案",
    "description": "测试时间窗口重叠场景",
    "template_id": template_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-12-15", "2026-12-25"],
    "creator_id": mgr_id,
})
check("创建重叠测试方案", r.status_code == 200)
plan_overlap_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan_overlap_id}/submit", json={"operator_id": mgr_id})
check("提交审批", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan_overlap_id}/approve", json={"operator_id": mgr_id})
check("审批通过", r.status_code == 200)

r = client2.post("/freeze-rules", json={
    "name": "全月冻结-重叠测试",
    "description": "测试时间窗口重叠",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 12, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 12, 31, 23, 59, 59).isoformat(),
    "reason": "全月保障",
    "creator_id": mgr_id,
})
check("创建全月冻结规则", r.status_code == 200)
rule_full_month_id = r.json()["id"]

r = client2.get(f"/schedule-plans/{plan_overlap_id}")
plan_after_full_month = r.json()
check("创建全月冻结后方案变为 CONFIRMING", 
      plan_after_full_month["status"] == "CONFIRMING")

r = client2.get("/freeze-hit-records", params={"plan_id": plan_overlap_id, "status": "ACTIVE"})
full_month_hits = r.json()
check(f"全月冻结后有 {len(full_month_hits)} 条活跃命中", len(full_month_hits) == 2)

r = client2.post("/freeze-rules", json={
    "name": "圣诞冻结-重叠测试",
    "description": "嵌套在全月冻结中",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 12, 24, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 12, 26, 23, 59, 59).isoformat(),
    "reason": "圣诞节保障",
    "creator_id": mgr_id,
})
check("创建圣诞冻结规则（嵌套）", r.status_code == 200)
rule_xmas_id = r.json()["id"]

r = client2.get(f"/schedule-plans/{plan_overlap_id}")
plan_overlap_after = r.json()
check("两条重叠规则都命中，方案仍为 CONFIRMING", 
      plan_overlap_after["status"] == "CONFIRMING")

r = client2.get("/freeze-hit-records", params={"plan_id": plan_overlap_id, "status": "ACTIVE"})
overlap_hits = r.json()
check(f"重叠场景有 {len(overlap_hits)} 条活跃命中（2条日期 + 1条额外规则）", 
      len(overlap_hits) == 3)

xmas_item = [i for i in plan_overlap_after["items"] if i["date"] == "2026-12-25"][0]
mid_month_item = [i for i in plan_overlap_after["items"] if i["date"] == "2026-12-15"][0]

check("12-25 被两条规则同时命中（CHANGED）", xmas_item["status"] == "CHANGED")
check("12-15 只被全月规则命中（CHANGED）", mid_month_item["status"] == "CHANGED")

r = client2.post(f"/freeze-rules/{rule_full_month_id}/deactivate", json={"operator_id": mgr_id})
check("停用全月冻结规则", r.status_code == 200)

r = client2.get(f"/schedule-plans/{plan_overlap_id}")
plan_overlap_deact_full = r.json()
check("停用全月规则后方案仍为 CONFIRMING", 
      plan_overlap_deact_full["status"] == "CONFIRMING")

xmas_item_2 = [i for i in plan_overlap_deact_full["items"] if i["date"] == "2026-12-25"][0]
mid_month_item_2 = [i for i in plan_overlap_deact_full["items"] if i["date"] == "2026-12-15"][0]

check("12-25 仍被圣诞规则命中（CHANGED）", xmas_item_2["status"] == "CHANGED")
check("12-15 不再命中（恢复 APPROVED）", mid_month_item_2["status"] == "APPROVED")

r = client2.get("/freeze-hit-records", params={"plan_id": plan_overlap_id, "status": "ACTIVE"})
after_deact_full_hits = r.json()
check(f"停用全月规则后有 {len(after_deact_full_hits)} 条活跃命中（只剩圣诞规则）", 
      len(after_deact_full_hits) == 1)

r = client2.post(f"/freeze-rules/{rule_xmas_id}/deactivate", json={"operator_id": mgr_id})
check("停用圣诞冻结规则", r.status_code == 200)

r = client2.get(f"/schedule-plans/{plan_overlap_id}")
plan_overlap_deact_all = r.json()
check("两条规则都停用后方案恢复 APPROVED", 
      plan_overlap_deact_all["status"] == "APPROVED")


# ---------- 测试总结 ----------
print("\n" + "=" * 70)
print(f"  测试结果: {passed}/{total} 通过")
print("=" * 70)

if failed == 0:
    print("\n  *** 冻结规则自动重校验中心回归测试全部通过 ***")
else:
    print(f"\n  失败 {failed} 项")
    sys.exit(1)
