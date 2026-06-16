"""
冻结规则影响恢复中心 完整链路回归测试：
1. 创建冻结规则 -> 命中方案 -> 命中记录落库 + 审计日志
2. 删除冻结规则 -> 自动恢复方案条目状态 + 恢复日志
3. 人工撤销冻结规则 -> 自动恢复 + 撤销后再次命中的一致性
4. 重启后状态一致：命中记录、恢复日志、方案状态均持久化
5. 权限校验：非审批角色不能撤销冻结规则
6. 日志可追踪：恢复日志、命中记录、审计日志完整可查
7. 多条规则影响同一方案时的状态回收顺序
8. 时间窗口重叠：不同冻结规则时间段重叠时恢复正确
9. 停用后恢复 + 启用后再次拦截
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_freeze_recovery_center.db")
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
print("  [冻结规则影响恢复中心 完整链路回归测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户/模板 ---")
r = client.post("/environments", json={"name": "env-recovery-center", "description": "恢复中心测试环境"})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/roles", json={"name": "RC-Admin", "can_approve": 1, "description": "审批角色"})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "RC-Dev", "can_approve": 0, "description": "开发角色"})
check("创建开发角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "rc.mgr", "display_name": "RCMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "rc.dev", "display_name": "RCDev", "role_id": role_dev_id})
check("创建开发用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "RC-测试模板",
    "description": "恢复中心测试模板",
    "environment_id": env_id,
    "start_time": "02:00",
    "end_time": "04:00",
    "change_reason": "常规维护",
    "is_shared": 1,
    "creator_id": mgr_id,
})
check("创建共享模板", r.status_code == 200)
tpl_id = r.json()["id"]

# ========== 场景1：创建冻结规则 + 命中方案 + 命中记录落库 ==========
print("\n--- [场景1] 创建冻结规则 -> 排期方案提交被拦截 -> 命中记录落库 ---")

freeze_start = datetime(2026, 7, 1, 0, 0, 0).isoformat()
freeze_end = datetime(2026, 7, 15, 23, 59, 59).isoformat()

r = client.post("/freeze-rules", json={
    "name": "7月保障冻结",
    "description": "7月保障期冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": freeze_start,
    "date_to": freeze_end,
    "reason": "7月保障期禁止变更",
    "creator_id": mgr_id,
})
check("创建冻结规则 200", r.status_code == 200, f"body={r.text[:200]}")
rule1_id = r.json()["id"]
check("规则状态 ACTIVE", r.json()["status"] == "ACTIVE")

r = client.post("/schedule-plans", json={
    "name": "恢复中心测试方案",
    "description": "测试冻结命中和恢复",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-07-05", "2026-07-06", "2026-08-01"],
    "creator_id": dev_id,
})
check("创建含冻结日期的方案 200", r.status_code == 200)
plan1_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan1_id}/submit", json={"operator_id": dev_id})
check("提交被冻结拦截 403", r.status_code == 403, f"status={r.status_code}")

r = client.get("/freeze-hit-records", params={"plan_id": plan1_id})
check("命中记录查询 200", r.status_code == 200)
hit_records = r.json()
check("有命中记录", len(hit_records) >= 1, f"count={len(hit_records)}")

active_hits = [h for h in hit_records if h["status"] == "ACTIVE"]
check("命中记录状态 ACTIVE", len(active_hits) >= 1, f"active={len(active_hits)}")

if active_hits:
    check("命中记录含规则ID", active_hits[0]["rule_id"] == rule1_id)
    check("命中记录含规则名称", active_hits[0]["rule_name"] is not None)
    check("命中记录含命中原因", active_hits[0]["hit_reason"] is not None)

r = client.get(f"/schedule-plans/{plan1_id}/audit-logs")
check("方案审计日志 200", r.status_code == 200)
audit_actions = [log["action"] for log in r.json()]
check("含 PLAN_FREEZE_HIT 日志", "PLAN_FREEZE_HIT" in audit_actions,
      f"actions={audit_actions}")

# ========== 场景2：删除冻结规则 -> 自动恢复方案条目状态 ==========
print("\n--- [场景2] 删除冻结规则 -> 自动恢复 + 恢复日志 ---")

r = client.delete(f"/freeze-rules/{rule1_id}", params={"operator_id": mgr_id})
check("删除冻结规则 200", r.status_code == 200)

r = client.get("/freeze-recovery-logs", params={"plan_id": plan1_id})
check("恢复日志查询 200", r.status_code == 200)
recovery_logs = r.json()
check("有恢复日志记录", len(recovery_logs) >= 1, f"count={len(recovery_logs)}")

if recovery_logs:
    check("恢复日志含触发动作", recovery_logs[0]["trigger_action"] is not None)
    check("恢复日志含状态变更", recovery_logs[0]["status_before"] != recovery_logs[0]["status_after"]
          or recovery_logs[0]["detail"] is not None)

r = client.get("/freeze-hit-records", params={"plan_id": plan1_id, "status": "RECOVERED"})
check("已恢复的命中记录 200", r.status_code == 200)
recovered_records = r.json()
check("命中记录已变为 RECOVERED", len(recovered_records) >= 1,
      f"recovered={len(recovered_records)}")

if recovered_records:
    check("恢复记录含恢复时间", recovered_records[0]["recovered_at"] is not None)
    check("恢复记录含恢复原因", recovered_records[0]["recovery_reason"] is not None)

r = client.post(f"/schedule-plans/{plan1_id}/submit", json={"operator_id": dev_id})
check("删除冻结后方案可提交 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

r = client.post(f"/schedule-plans/{plan1_id}/approve", json={"operator_id": mgr_id})
check("删除冻结后方案可审批 200", r.status_code == 200)

# ========== 场景3：撤销冻结规则 -> 自动恢复 + 再次命中一致性 ==========
print("\n--- [场景3] 撤销冻结规则 -> 自动恢复 -> 再次命中一致性 ---")

r = client.post("/freeze-rules", json={
    "name": "8月保障冻结",
    "description": "8月保障期冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 8, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 8, 15, 23, 59, 59).isoformat(),
    "reason": "8月保障期禁止变更",
    "creator_id": mgr_id,
})
check("创建8月冻结规则 200", r.status_code == 200)
rule2_id = r.json()["id"]

r = client.post("/schedule-plans", json={
    "name": "撤销恢复测试方案",
    "description": "测试撤销后恢复和再拦截",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-08-05", "2026-08-06"],
    "creator_id": dev_id,
})
check("创建8月方案 200", r.status_code == 200)
plan2_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan2_id}/submit", json={"operator_id": dev_id})
check("8月方案提交被拦截 403", r.status_code == 403)

r = client.get("/freeze-hit-records", params={"plan_id": plan2_id, "status": "ACTIVE"})
check("有8月命中记录", len(r.json()) >= 1)

r = client.post(f"/freeze-rules/{rule2_id}/revoke", json={
    "operator_id": mgr_id,
    "reason": "保障期提前结束",
})
check("撤销冻结规则 200", r.status_code == 200, f"body={r.text[:300]}")
revoke_result = r.json()
check("撤销结果含规则ID", revoke_result["rule_id"] == rule2_id)
check("撤销结果含恢复条目数", "recovered_items" in revoke_result)
check("撤销恢复条目>0", revoke_result["recovered_items"] >= 1,
      f"recovered={revoke_result['recovered_items']}")

r = client.post(f"/schedule-plans/{plan2_id}/submit", json={"operator_id": dev_id})
check("撤销后方案可提交 200", r.status_code == 200, f"status={r.status_code}")

r = client.post(f"/schedule-plans/{plan2_id}/approve", json={"operator_id": mgr_id})
check("撤销后方案可审批 200", r.status_code == 200)

r = client.post("/freeze-rules", json={
    "name": "8月保障冻结-再次",
    "description": "8月保障期再次冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 8, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 8, 15, 23, 59, 59).isoformat(),
    "reason": "8月保障期再次冻结",
    "creator_id": mgr_id,
})
check("再次创建8月冻结规则 200", r.status_code == 200)
rule2b_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan2_id}/detect-changes", params={"operator_id": mgr_id})
check("再次冻结后检测变更 200", r.status_code == 200)
detect_result = r.json()
check("检测到变更条目>0", detect_result["changed_items"] > 0,
      f"changed={detect_result['changed_items']}")

r = client.get(f"/schedule-plans/{plan2_id}")
plan2_detail = r.json()
changed_items = [item for item in plan2_detail["items"] if item["status"] == "CHANGED"]
check("有条目变为 CHANGED", len(changed_items) >= 1)

r = client.get("/freeze-hit-records", params={"plan_id": plan2_id, "status": "ACTIVE"})
check("再次命中有新命中记录", len(r.json()) >= 1,
      f"active_hits={len(r.json())}")

r = client.get(f"/freeze-rules/{rule2_id}/audit-logs")
check("撤销规则审计日志 200", r.status_code == 200)
rule2_audit = r.json()
check("含 FREEZE_REVOKE 日志", any(l["action"] == "FREEZE_REVOKE" for l in rule2_audit),
      f"actions={[l['action'] for l in rule2_audit]}")

# ========== 场景4：重启后状态一致 ==========
print("\n--- [场景4] 服务重启后状态一致 ---")

before_hit_records = client.get("/freeze-hit-records").json()
before_recovery_logs = client.get("/freeze-recovery-logs").json()
before_plan2 = client.get(f"/schedule-plans/{plan2_id}").json()
before_plan1 = client.get(f"/schedule-plans/{plan1_id}").json()
before_summary = client.get("/freeze-recovery-center/summary").json()

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

r = client2.get("/freeze-hit-records")
check("重启后命中记录查询 200", r.status_code == 200)
check("重启后命中记录数量一致", len(r.json()) == len(before_hit_records),
      f"before={len(before_hit_records)} after={len(r.json())}")

r = client2.get("/freeze-recovery-logs")
check("重启后恢复日志查询 200", r.status_code == 200)
check("重启后恢复日志数量一致", len(r.json()) == len(before_recovery_logs),
      f"before={len(before_recovery_logs)} after={len(r.json())}")

r = client2.get(f"/schedule-plans/{plan2_id}")
check("重启后方案2状态一致", r.json()["status"] == before_plan2["status"])

r = client2.get(f"/schedule-plans/{plan1_id}")
check("重启后方案1状态一致", r.json()["status"] == before_plan1["status"])

r = client2.get("/freeze-recovery-center/summary")
check("重启后恢复中心概览 200", r.status_code == 200)
after_summary = r.json()
check("重启后active_hits一致", after_summary["total_active_hits"] == before_summary["total_active_hits"],
      f"before={before_summary['total_active_hits']} after={after_summary['total_active_hits']}")

r = client2.get(f"/freeze-rules/{rule2b_id}")
check("重启后冻结规则仍生效", r.json()["status"] == "ACTIVE")

r = client2.post("/maintenance-windows", json={
    "title": "重启后验证冻结生效",
    "environment_id": env_id,
    "start_time": "2026-08-05T02:00:00",
    "end_time": "2026-08-05T04:00:00",
    "change_reason": "重启后验证",
    "creator_id": dev_id,
})
check("重启后冻结仍生效 403", r.status_code == 403)

# ========== 场景5：权限校验 - 非审批角色不能撤销 ==========
print("\n--- [场景5] 权限校验：非审批角色不能撤销冻结规则 ---")

r = client2.post("/freeze-rules", json={
    "name": "权限测试规则",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 9, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 9, 10, 23, 59, 59).isoformat(),
    "reason": "权限测试",
    "creator_id": mgr_id,
})
check("创建权限测试规则 200", r.status_code == 200)
perm_rule_id = r.json()["id"]

r = client2.post(f"/freeze-rules/{perm_rule_id}/revoke", json={
    "operator_id": dev_id,
    "reason": "普通用户尝试撤销",
})
check("普通用户撤销冻结规则 403", r.status_code == 403, f"status={r.status_code}")

r = client2.put(f"/freeze-rules/{perm_rule_id}", params={"operator_id": dev_id}, json={
    "description": "普通用户修改",
})
check("普通用户修改冻结规则 403", r.status_code == 403)

r = client2.delete(f"/freeze-rules/{perm_rule_id}", params={"operator_id": dev_id})
check("普通用户删除冻结规则 403", r.status_code == 403)

r = client2.get("/freeze-hit-records")
check("普通用户可查看命中记录 200", r.status_code == 200)

r = client2.get("/freeze-recovery-logs")
check("普通用户可查看恢复日志 200", r.status_code == 200)

r = client2.get("/freeze-recovery-center/summary")
check("普通用户可查看恢复中心概览 200", r.status_code == 200)

r = client2.delete(f"/freeze-rules/{perm_rule_id}", params={"operator_id": mgr_id})
check("审批用户可删除冻结规则 200", r.status_code == 200)

# ========== 场景6：日志可追踪 ==========
print("\n--- [场景6] 日志可追踪：命中记录+恢复日志+审计日志完整 ---")

r = client2.get("/freeze-hit-records", params={"plan_id": plan2_id})
check("方案2命中记录 200", r.status_code == 200)
plan2_hits = r.json()
check("方案2命中记录>=1", len(plan2_hits) >= 1)

recovered_hits = [h for h in plan2_hits if h["status"] == "RECOVERED"]
check("有已恢复的命中记录", len(recovered_hits) >= 1,
      f"recovered={len(recovered_hits)}")

if recovered_hits:
    rh = recovered_hits[0]
    check("恢复记录含 recovered_at", rh["recovered_at"] is not None)
    check("恢复记录含 recovered_by", rh["recovered_by"] is not None)
    check("恢复记录含 recovery_reason", rh["recovery_reason"] is not None)

r = client2.get("/freeze-recovery-logs", params={"plan_id": plan2_id})
check("方案2恢复日志 200", r.status_code == 200)
plan2_recovery = r.json()
check("方案2恢复日志>=1", len(plan2_recovery) >= 1)

if plan2_recovery:
    rl = plan2_recovery[0]
    check("恢复日志含触发动作", rl["trigger_action"] is not None)
    check("恢复日志含操作人", rl["operator_id"] is not None)
    check("恢复日志含详情", rl["detail"] is not None)

r = client2.get(f"/schedule-plans/{plan2_id}/audit-logs")
check("方案2审计日志 200", r.status_code == 200)
plan2_audit = r.json()
audit_action_values = [log["action"] for log in plan2_audit]
check("含 PLAN_FREEZE_HIT 审计日志", "PLAN_FREEZE_HIT" in audit_action_values,
      f"actions={audit_action_values}")

r = client2.get(f"/freeze-rules/{rule2_id}/audit-logs")
check("规则审计日志 200", r.status_code == 200)
rule_audit = r.json()
rule_audit_actions = [log["action"] for log in rule_audit]
check("含 FREEZE_RECOVER 审计日志", "FREEZE_RECOVER" in rule_audit_actions,
      f"actions={rule_audit_actions}")

# ========== 场景7：多条规则影响同一方案时的状态回收顺序 ==========
print("\n--- [场景7] 多条规则影响同一方案：回收顺序正确 ---")

r = client2.post("/freeze-rules", json={
    "name": "10月冻结A",
    "description": "10月上旬冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 10, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 10, 10, 23, 59, 59).isoformat(),
    "reason": "10月上旬保障",
    "creator_id": mgr_id,
})
check("创建10月冻结A 200", r.status_code == 200)
rule_a_id = r.json()["id"]

r = client2.post("/freeze-rules", json={
    "name": "10月冻结B",
    "description": "10月中旬冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 10, 5, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 10, 15, 23, 59, 59).isoformat(),
    "reason": "10月中旬保障",
    "creator_id": mgr_id,
})
check("创建10月冻结B 200", r.status_code == 200)
rule_b_id = r.json()["id"]

r = client2.post("/schedule-plans", json={
    "name": "多规则影响方案",
    "description": "测试多规则回收顺序",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-10-05", "2026-10-06", "2026-10-11"],
    "creator_id": dev_id,
})
check("创建多规则影响方案 200", r.status_code == 200)
plan3_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan3_id}/submit", json={"operator_id": dev_id})
check("多规则方案提交被拦截 403", r.status_code == 403)

r = client2.get("/freeze-hit-records", params={"plan_id": plan3_id, "status": "ACTIVE"})
multi_hits = r.json()
check("多规则命中记录>1", len(multi_hits) >= 2,
      f"hits={len(multi_hits)}")

r = client2.post(f"/freeze-rules/{rule_a_id}/deactivate", json={"operator_id": mgr_id})
check("停用规则A 200", r.status_code == 200)

r = client2.get("/freeze-hit-records", params={"plan_id": plan3_id, "status": "ACTIVE"})
after_deact_a = r.json()
still_active = [h for h in after_deact_a if h["rule_id"] == rule_b_id]
check("规则A停用后B的命中记录仍ACTIVE", len(still_active) >= 1,
      f"active_b={len(still_active)}")

r = client2.post(f"/schedule-plans/{plan3_id}/submit", json={"operator_id": dev_id})
check("规则A停用后提交仍被B拦截 403", r.status_code == 403,
      f"status={r.status_code}")

r = client2.post(f"/freeze-rules/{rule_b_id}/deactivate", json={"operator_id": mgr_id})
check("停用规则B 200", r.status_code == 200)

r = client2.get("/freeze-recovery-logs", params={"plan_id": plan3_id})
check("多规则恢复日志 200", r.status_code == 200)
multi_recovery = r.json()
check("多规则恢复日志>=2", len(multi_recovery) >= 2,
      f"recovery_count={len(multi_recovery)}")

r = client2.post(f"/schedule-plans/{plan3_id}/submit", json={"operator_id": dev_id})
check("两条规则都停用后方案可提交 200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:200]}")

# ========== 场景8：时间窗口重叠 - 不同规则时间段重叠时恢复正确 ==========
print("\n--- [场景8] 时间窗口重叠：不同冻结规则时间段重叠时恢复正确 ---")

r = client2.post("/freeze-rules", json={
    "name": "11月全月冻结",
    "description": "11月全月冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 11, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 11, 30, 23, 59, 59).isoformat(),
    "reason": "11月保障",
    "creator_id": mgr_id,
})
check("创建11月全月冻结 200", r.status_code == 200)
rule_nov_full_id = r.json()["id"]

r = client2.post("/freeze-rules", json={
    "name": "11月上旬冻结",
    "description": "11月上旬冻结(重叠)",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 11, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 11, 10, 23, 59, 59).isoformat(),
    "reason": "11月上旬保障",
    "creator_id": mgr_id,
})
check("创建11月上旬冻结(重叠) 200", r.status_code == 200)
rule_nov_early_id = r.json()["id"]

r = client2.post("/schedule-plans", json={
    "name": "时间窗口重叠方案",
    "description": "测试重叠规则恢复",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-11-05", "2026-11-20"],
    "creator_id": dev_id,
})
check("创建重叠方案 200", r.status_code == 200)
plan4_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan4_id}/submit", json={"operator_id": dev_id})
check("重叠方案提交被拦截 403", r.status_code == 403)

r = client2.post(f"/freeze-rules/{rule_nov_early_id}/deactivate", json={"operator_id": mgr_id})
check("停用11月上旬冻结 200", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan4_id}/submit", json={"operator_id": dev_id})
check("停用上旬后仍被全月冻结拦截 403", r.status_code == 403,
      f"status={r.status_code}")

r = client2.get("/freeze-hit-records", params={"plan_id": plan4_id, "status": "ACTIVE"})
overlap_active = r.json()
overlap_blocked_by_full = [h for h in overlap_active if h["rule_id"] == rule_nov_full_id]
check("仍被全月规则命中", len(overlap_blocked_by_full) >= 1,
      f"blocked_by_full={len(overlap_blocked_by_full)}")

r = client2.post(f"/freeze-rules/{rule_nov_full_id}/deactivate", json={"operator_id": mgr_id})
check("停用11月全月冻结 200", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan4_id}/submit", json={"operator_id": dev_id})
check("两条重叠规则都停用后可提交 200", r.status_code == 200,
      f"status={r.status_code} body={r.text[:200]}")

# ========== 场景9：停用后恢复 + 启用后再次拦截 ==========
print("\n--- [场景9] 停用后恢复 + 启用后再次拦截 ---")

r = client2.post("/freeze-rules", json={
    "name": "12月冻结",
    "description": "12月保障冻结",
    "environment_id": env_id,
    "freeze_scope": "ALL",
    "date_from": datetime(2026, 12, 1, 0, 0, 0).isoformat(),
    "date_to": datetime(2026, 12, 10, 23, 59, 59).isoformat(),
    "reason": "12月保障期",
    "creator_id": mgr_id,
})
check("创建12月冻结 200", r.status_code == 200)
rule_dec_id = r.json()["id"]

r = client2.post("/schedule-plans", json={
    "name": "停用启用测试方案",
    "description": "测试停用恢复后启用再次拦截",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-12-05"],
    "creator_id": dev_id,
})
check("创建12月方案 200", r.status_code == 200)
plan5_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan5_id}/submit", json={"operator_id": dev_id})
check("12月方案提交被拦截 403", r.status_code == 403)

r = client2.post(f"/freeze-rules/{rule_dec_id}/deactivate", json={"operator_id": mgr_id})
check("停用12月冻结 200", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan5_id}/submit", json={"operator_id": dev_id})
check("停用后方案可提交 200", r.status_code == 200, f"body={r.text[:200]}")

r = client2.post(f"/schedule-plans/{plan5_id}/approve", json={"operator_id": mgr_id})
check("停用后方案可审批 200", r.status_code == 200)

r = client2.post(f"/freeze-rules/{rule_dec_id}/activate", json={"operator_id": mgr_id})
check("重新启用12月冻结 200", r.status_code == 200)

r = client2.post(f"/schedule-plans/{plan5_id}/detect-changes", params={"operator_id": mgr_id})
check("启用后检测变更 200", r.status_code == 200)
detect5 = r.json()
check("启用后检测到变更>0", detect5["changed_items"] > 0,
      f"changed={detect5['changed_items']}")

r = client2.get(f"/schedule-plans/{plan5_id}")
plan5_detail = r.json()
changed5 = [item for item in plan5_detail["items"] if item["status"] == "CHANGED"]
check("启用后有条目变为CHANGED", len(changed5) >= 1)

r = client2.get("/freeze-hit-records", params={"plan_id": plan5_id, "status": "ACTIVE"})
check("启用后有新的命中记录", len(r.json()) >= 1,
      f"active={len(r.json())}")

# ========== 场景10：恢复中心概览 ==========
print("\n--- [场景10] 恢复中心概览 ---")

r = client2.get("/freeze-recovery-center/summary")
check("恢复中心概览 200", r.status_code == 200)
summary = r.json()
check("概览含 total_active_hits", "total_active_hits" in summary)
check("概览含 total_recovered", "total_recovered" in summary)
check("概览含 total_still_blocked", "total_still_blocked" in summary)
check("概览含 by_rule", "by_rule" in summary)
check("概览含 by_plan", "by_plan" in summary)
check("total_active_hits > 0", summary["total_active_hits"] > 0,
      f"active={summary['total_active_hits']}")
check("total_recovered > 0", summary["total_recovered"] > 0,
      f"recovered={summary['total_recovered']}")

# ========== 场景11：命中原因返回 ==========
print("\n--- [场景11] 命中原因返回完整 ---")

r = client2.get("/freeze-hit-records", params={"rule_id": rule_dec_id, "status": "ACTIVE"})
dec_active = r.json()
if dec_active:
    check("命中记录含命中原因文本", dec_active[0]["hit_reason"] is not None)
    check("命中原因含规则名或原因",
          "12月" in str(dec_active[0].get("hit_reason", "")) or
          "冻结" in str(dec_active[0].get("hit_reason", "")),
          f"reason={dec_active[0].get('hit_reason', '')}")
    check("命中记录含 overlap_type", dec_active[0].get("overlap_type") is not None)

# ========== 总结 ----------
print("\n" + "=" * 70)
total = len(results)
ok = sum(1 for f, _, _ in results if f == PASS)
print(f"  测试结果: {ok}/{total} 通过")
print("=" * 70)
failed = [(n, d) for f, n, d in results if f == FAIL]
for n, d in failed:
    print(f"  {FAIL} {n}  {d}")

if ok == total:
    print("\n  *** 冻结规则影响恢复中心完整链路回归测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
