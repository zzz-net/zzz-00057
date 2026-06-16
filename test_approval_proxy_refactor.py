"""
审批代理重构专项回归测试
覆盖:
1. 未来生效跨重启自动激活: valid_from 到达后自动可代办，服务重启后规则一致
2. 冲突拦截后的拒绝审计: 代办操作被业务规则拒绝时写 PROXY_DELEGATE_REJECT，不能写成成功
3. 授权撤销后结果恢复一致: REVOKED 状态重启后仍然不可代办
4. 导入导出后状态与日志仍然一致: 四种状态(ACTIVE/INACTIVE/REVOKED/EXPIRED)和审计日志往返一致
"""
import sys
import os
import io
import json
import copy

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_approval_proxy_refactor.db")
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

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from main import app
from app import schemas, services, models

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


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def reload_app():
    """模拟服务重启: 重载数据库模块 + 重建 FastAPI 实例."""
    global client
    for mod in list(sys.modules.keys()):
        if mod.startswith("app.") or mod == "main":
            del sys.modules[mod]

    import app.database as db_mod2
    db_mod2.DB_PATH = TEST_DB_PATH
    db_mod2.engine = create_engine(
        f"sqlite:///{TEST_DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    db_mod2.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_mod2.engine)

    from app.database import Base, engine as e2
    Base.metadata.create_all(bind=e2)

    import main as main_mod
    main_mod.engine = e2
    Base.metadata.create_all(bind=e2)
    client = TestClient(main_mod.app)
    return client


client = TestClient(app)

print("\n" + "=" * 70)
print("  [审批代理重构专项 端到端回归测试]")
print("=" * 70)

# ---------- 准备基础数据 ----------
print("\n--- [准备] 建环境x3/角色x2/用户x5/维护时段/模板/窗口/方案/冻结规则 ---")

r = client.post("/environments", json={"name": "env-refactor-A", "description": "重构测试环境A"})
check("创建环境A", r.status_code == 200)
env_a_id = r.json()["id"]

r = client.post("/environments", json={"name": "env-refactor-B", "description": "重构测试环境B"})
check("创建环境B", r.status_code == 200)
env_b_id = r.json()["id"]

r = client.post("/environments", json={"name": "env-refactor-IMPORT", "description": "重构导入测试环境"})
check("创建导入目标环境", r.status_code == 200)
env_import_id = r.json()["id"]

r = client.post("/roles", json={"name": "RefactorApprover", "description": "重构审批角色", "can_approve": True})
check("创建审批角色", r.status_code == 200)
approver_role_id = r.json()["id"]

r = client.post("/roles", json={"name": "RefactorRegular", "description": "重构普通角色", "can_approve": False})
check("创建普通角色", r.status_code == 200)
regular_role_id = r.json()["id"]

r = client.post("/users", json={"username": "refactor.boss", "display_name": "重构-审批人Boss", "role_id": approver_role_id})
check("创建审批人 boss", r.status_code == 200)
boss_id = r.json()["id"]

r = client.post("/users", json={"username": "refactor.boss2", "display_name": "重构-审批人Boss2", "role_id": approver_role_id})
check("创建审批人 boss2", r.status_code == 200)
boss2_id = r.json()["id"]

r = client.post("/users", json={"username": "refactor.standby", "display_name": "重构-代理人Standby", "role_id": regular_role_id})
check("创建代理人 standby(普通角色无审批权)", r.status_code == 200)
standby_id = r.json()["id"]

r = client.post("/users", json={"username": "refactor.dev", "display_name": "重构-开发者Dev", "role_id": regular_role_id})
check("创建普通用户 dev", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/users", json={"username": "refactor.boss_import", "display_name": "重构-导入环境审批人", "role_id": approver_role_id})
check("创建导入环境审批人", r.status_code == 200)
boss_import_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "refactor-shared",
    "description": "重构测试用共享模板",
    "environment_id": env_a_id,
    "start_time": "01:00",
    "end_time": "03:00",
    "change_reason": "常规变更",
    "is_shared": 1,
    "creator_id": dev_id,
})
check("创建共享模板", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
template_id = r.json()["id"]

today = datetime.utcnow().date()
tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
r = client.post("/maintenance-windows", json={
    "title": "重构-待审批窗口(冲突测试用)",
    "description": "窗口1: 先审批通过，然后让代理人再审批一次触发冲突",
    "environment_id": env_a_id,
    "start_time": f"{tomorrow_str}T01:00:00",
    "end_time": f"{tomorrow_str}T03:00:00",
    "change_reason": "版本升级",
    "creator_id": dev_id,
})
check("创建待审批窗口1", r.status_code == 200, f"status={r.status_code} body={r.text}")
win1_id = r.json()["id"]

r = client.post(f"/maintenance-windows/{win1_id}/submit", json={"operator_id": dev_id, "remark": "提交审批"})
check("提交窗口1审批", r.status_code == 200, f"status={r.status_code} body={r.text}")

r = client.post("/schedule-plans", json={
    "name": "重构-待确认方案",
    "description": "排期方案",
    "environment_id": env_a_id,
    "template_id": template_id,
    "generate_mode": "specific_dates",
    "specific_dates": [(today + timedelta(days=7)).strftime("%Y-%m-%d")],
    "creator_id": dev_id,
})
check("创建排期方案", r.status_code == 200, f"status={r.status_code} body={r.text}")
plan1_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan1_id}/submit", json={"operator_id": dev_id})
check("提交排期方案", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan1_id}/approve", json={"approver_id": boss_id, "operator_id": boss_id})
check("boss审批排期方案通过", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")

r = client.post("/freeze-rules", json={
    "name": "重构-测试冻结规则",
    "description": "冻结启停用测试",
    "environment_id": env_a_id,
    "freeze_scope": "ALL",
    "date_from": (today + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00"),
    "date_to": (today + timedelta(days=90)).strftime("%Y-%m-%dT00:00:00"),
    "start_time": "00:00",
    "end_time": "23:59",
    "reason": "大促冻结期",
    "status": "INACTIVE",
    "creator_id": boss_id,
})
check("创建冻结规则(初始停用, 日期与测试窗口不重叠)", r.status_code == 200, f"status={r.status_code} body={r.text[:80]}")
freeze1_id = r.json()["id"]


# =====================================================================
# 场景1: 未来生效跨重启自动激活
# =====================================================================
print("\n--- [场景1] 未来生效跨重启: 未到 valid_from 不可代办, 到点后自动生效, 重启后规则一致 ---")

now = datetime.utcnow()
future_from = iso(now + timedelta(hours=2))
future_to = iso(now + timedelta(hours=8))

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["PLAN_CONFIRM"],
    "valid_from": future_from,
    "valid_to": future_to,
    "reason": "未来生效测试",
    "creator_id": boss_id,
})
check("创建未来生效代理(2小时后开始, PLAN_CONFIRM范围)", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")
future_proxy_id = r.json()["id"]
check("未来生效代理状态=ACTIVE(正常生命周期)", r.json()["status"] == "ACTIVE", f'status={r.json()["status"]}')

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("未到生效时间: check_delegation 返回 is_delegated=false", r.json()["is_delegated"] is False, f"body={r.json()}")

print("  --- 模拟服务重启 ---")
client = reload_app()
check("重启后应用可用", client is not None)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("重启后未到生效时间: is_delegated 仍=false", r.json()["is_delegated"] is False, f"body={r.json()}")

r = client.get(f"/approval-proxies/{future_proxy_id}", params={"operator_id": boss_id})
check("重启后详情查询状态仍=ACTIVE", r.json()["status"] == "ACTIVE", f'status={r.json()["status"]}')

db = db_mod.SessionLocal()
proxy_obj = db.query(models.ApprovalProxy).filter(models.ApprovalProxy.id == future_proxy_id).first()
proxy_obj.valid_from = now - timedelta(minutes=10)
db.commit()
db.close()
check("手动调整数据库: 将 valid_from 改为 10 分钟前(模拟时间流逝)", True)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("valid_from 到达后: check_delegation 自动返回 is_delegated=true", r.json()["is_delegated"] is True, f"body={r.json()}")
check("自动生效后返回正确的 proxy_id", r.json().get("proxy_id") == future_proxy_id, f"proxy_id={r.json().get('proxy_id')}")

print("  --- 再次模拟服务重启(验证生效后状态保持) ---")
client = reload_app()

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("重启后代办权限保持有效: is_delegated=true", r.json()["is_delegated"] is True, f"body={r.json()}")

r = client.get(f"/approval-proxies/{future_proxy_id}/audit-logs", params={"operator_id": boss_id})
actions = [log["action"] for log in r.json()]
check("自动生效不产生额外审计日志(通过查询层过滤而非状态变更)", "PROXY_REACTIVATE" not in actions, f"actions={actions}")


# =====================================================================
# 场景2: 冲突拦截后的拒绝审计
# =====================================================================
print("\n--- [场景2] 冲突拦截拒绝审计: 代办操作被业务规则拒绝时写 PROXY_DELEGATE_REJECT, 不写成功日志 ---")

now = datetime.utcnow()
active_from = iso(now - timedelta(minutes=30))
active_to = iso(now + timedelta(hours=8))

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE", "FREEZE_TOGGLE"],
    "valid_from": active_from,
    "valid_to": active_to,
    "reason": "冲突拦截测试代理",
    "creator_id": boss2_id,
})
check("创建冲突测试用全量代理授权(boss2→standby, WINDOW+FREEZE)", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
conflict_proxy_id = r.json()["id"]

r = client.post(f"/maintenance-windows/{win1_id}/approve", json={"operator_id": boss_id, "reason": "Boss先审批通过"})
check("Boss先审批窗口1通过(制造冲突)", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")
check("窗口1状态=APPROVED", r.json()["status"] == "APPROVED")

r = client.get(f"/approval-proxies/{conflict_proxy_id}/audit-logs", params={"operator_id": boss_id})
before_actions = [log["action"] for log in r.json()]
check("代办前审计日志不含 PROXY_DELEGATE_REJECT", "PROXY_DELEGATE_REJECT" not in before_actions, f"before={before_actions}")
check("代办前审计日志不含 PROXY_DELEGATE_ACTION(针对此proxy)", before_actions.count("PROXY_DELEGATE_ACTION") == 0, f"before={before_actions}")

r = client.post(f"/maintenance-windows/{win1_id}/approve", json={"operator_id": standby_id, "reason": "代理人重复审批触发冲突"})
check("代理人重复审批已通过的窗口 → 被业务规则拒绝(400)", r.status_code == 400, f"status={r.status_code} body={r.text}")
check("拒绝响应包含错误详情", "仅已提交状态可以审批" in r.json().get("detail", ""), f'detail={r.json().get("detail")}')

r = client.get(f"/approval-proxies/{conflict_proxy_id}/audit-logs", params={"operator_id": boss_id})
after_logs = r.json()
after_actions = [log["action"] for log in after_logs]
check("拒绝后审计日志包含 PROXY_DELEGATE_REJECT", "PROXY_DELEGATE_REJECT" in after_actions, f"after={after_actions}")

reject_logs = [log for log in after_logs if log["action"] == "PROXY_DELEGATE_REJECT"]
check("有且仅有 1 条拒绝审计", len(reject_logs) == 1, f"count={len(reject_logs)}")
if reject_logs:
    check("拒绝审计 operator_id=standby", reject_logs[0]["operator_id"] == standby_id, f'op={reject_logs[0]["operator_id"]}')
    check("拒绝审计包含 target_window_id", reject_logs[0]["target_window_id"] == win1_id, f'target={reject_logs[0]["target_window_id"]}')
    check("拒绝审计 detail 包含拒绝原因", "仅已提交状态可以审批" in reject_logs[0]["detail"], f'detail={reject_logs[0]["detail"]}')

check("拒绝审计不包含 PROXY_DELEGATE_ACTION(未虚写成功)", after_actions.count("PROXY_DELEGATE_ACTION") == 0, f"actions={after_actions}")


# =====================================================================
# 场景3: 授权撤销后结果恢复一致
# =====================================================================
print("\n--- [场景3] 授权撤销后结果一致: REVOKED 状态跨重启保持不可代办, 过期/停用同理 ---")

now = datetime.utcnow()
active_from2 = iso(now - timedelta(minutes=30))
active_to2 = iso(now + timedelta(hours=8))

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "delegate_scope": ["WINDOW_APPROVE", "PLAN_CONFIRM"],
    "valid_from": active_from2,
    "valid_to": active_to2,
    "reason": "撤销一致性测试",
    "creator_id": boss_id,
})
check("创建撤销测试代理(环境B)", r.status_code == 200)
revoke_proxy_id = r.json()["id"]

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "WINDOW_APPROVE",
})
check("撤销前: 环境B可代办", r.json()["is_delegated"] is True)

r = client.post(f"/approval-proxies/{revoke_proxy_id}/revoke", params={"operator_id": boss_id, "reason": "提前撤销测试"})
check("Boss撤销代理授权", r.status_code == 200)
check("撤销后状态=REVOKED", r.json()["status"] == "REVOKED")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "WINDOW_APPROVE",
})
check("撤销后: 环境B不可代办", r.json()["is_delegated"] is False)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "PLAN_CONFIRM",
})
check("撤销后: PLAN_CONFIRM 也不可代办(范围级联失效)", r.json()["is_delegated"] is False)

print("  --- 模拟服务重启 ---")
client = reload_app()

r = client.get(f"/approval-proxies/{revoke_proxy_id}", params={"operator_id": boss_id})
check("重启后详情: 状态仍=REVOKED", r.json()["status"] == "REVOKED", f'status={r.json()["status"]}')

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "WINDOW_APPROVE",
})
check("重启后: WINDOW_APPROVE 仍不可代办", r.json()["is_delegated"] is False)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "PLAN_CONFIRM",
})
check("重启后: PLAN_CONFIRM 仍不可代办", r.json()["is_delegated"] is False)

r = client.get(f"/approval-proxies", params={"proxy_user_id": standby_id, "status": "REVOKED", "operator_id": boss_id})
visible_ids = [p["id"] for p in r.json()]
check("按REVOKED过滤能看到已撤销代理", revoke_proxy_id in visible_ids, f"visible={visible_ids}")

r = client.get(f"/approval-proxies", params={"proxy_user_id": standby_id, "status": "ACTIVE", "operator_id": boss_id})
active_ids = [p["id"] for p in r.json()]
check("按ACTIVE过滤看不到已撤销代理", revoke_proxy_id not in active_ids, f"active={active_ids}")

r = client.get(f"/approval-proxies/{revoke_proxy_id}/audit-logs", params={"operator_id": boss_id})
revoke_actions = [log["action"] for log in r.json()]
check("撤销审计包含 PROXY_REVOKE", "PROXY_REVOKE" in revoke_actions, f"actions={revoke_actions}")

expired_from = iso(now - timedelta(days=7))
expired_to = iso(now - timedelta(days=1))
r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "delegate_scope": ["FREEZE_TOGGLE"],
    "valid_from": expired_from,
    "valid_to": expired_to,
    "reason": "过期一致性测试",
    "creator_id": boss2_id,
})
check("创建过期测试代理(valid_to已过)", r.status_code == 200)
expired_proxy_id = r.json()["id"]

r = client.post("/approval-proxies/expire-stale", params={"operator_id": boss_id})
check("触发过期扫描", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "FREEZE_TOGGLE",
})
check("过期后: FREEZE_TOGGLE 不可代办", r.json()["is_delegated"] is False)

print("  --- 重启验证过期状态 ---")
client = reload_app()

r = client.get(f"/approval-proxies/{expired_proxy_id}", params={"operator_id": boss_id})
check("重启后过期代理仍=EXPIRED", r.json()["status"] == "EXPIRED", f'status={r.json()["status"]}')

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "FREEZE_TOGGLE",
})
check("重启后过期仍不可代办", r.json()["is_delegated"] is False)


# =====================================================================
# 场景4: 导入导出后状态与日志仍然一致
# =====================================================================
print("\n--- [场景4] 导入导出状态与审计一致: 四种状态 + 代办成功/拒绝审计往返一致 ---")

now = datetime.utcnow()
from_a = iso(now - timedelta(hours=1))
to_a = iso(now + timedelta(hours=8))

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_import_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": from_a,
    "valid_to": to_a,
    "reason": "导出测试-ACTIVE",
    "creator_id": boss_id,
})
check("导出组: 创建 ACTIVE 代理", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
export_active_id = r.json()["id"]

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_import_id,
    "delegate_scope": ["PLAN_CONFIRM"],
    "valid_from": from_a,
    "valid_to": to_a,
    "reason": "导出测试-INACTIVE",
    "creator_id": boss2_id,
})
check("导出组: 创建第二个代理(后续停用)", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
export_inactive_id = r.json()["id"]

r = client.post(f"/approval-proxies/{export_inactive_id}/deactivate", params={"operator_id": boss2_id})
check("导出组: 停用第二个代理 → INACTIVE", r.status_code == 200, f"status={r.status_code} body={r.text[:80]}")

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_import_id,
    "delegate_scope": ["FREEZE_TOGGLE"],
    "valid_from": from_a,
    "valid_to": to_a,
    "reason": "导出测试-REVOKED",
    "creator_id": boss_id,
})
check("导出组: 创建第三个代理(后续撤销)", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
export_revoked_id = r.json()["id"]

r = client.post(f"/approval-proxies/{export_revoked_id}/revoke", params={"operator_id": boss_id, "reason": "导出测试撤销"})
check("导出组: 撤销第三个代理 → REVOKED", r.status_code == 200)

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_import_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": iso(now - timedelta(days=7)),
    "valid_to": iso(now - timedelta(days=1)),
    "reason": "导出测试-EXPIRED",
    "creator_id": boss2_id,
})
check("导出组: 创建已过期代理", r.status_code == 200, f"status={r.status_code} body={r.text[:150]}")
export_expired_id = r.json()["id"]
r = client.post("/approval-proxies/expire-stale", params={"operator_id": boss_id})

tomorrow_str2 = (today + timedelta(days=2)).strftime("%Y-%m-%d")
r = client.post("/maintenance-windows", json={
    "title": "导出审计-代办成功窗口",
    "description": "窗口代办成功审计",
    "environment_id": env_import_id,
    "start_time": f"{tomorrow_str2}T01:00:00",
    "end_time": f"{tomorrow_str2}T03:00:00",
    "change_reason": "版本升级",
    "creator_id": dev_id,
    "approver_id": boss_id,
})
check("导出组: 创建窗口2(代办成功)", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")
win2_id = r.json()["id"]
r = client.post(f"/maintenance-windows/{win2_id}/submit", json={"operator_id": dev_id})
r = client.post(f"/maintenance-windows/{win2_id}/approve", json={"operator_id": standby_id, "reason": "[导出测试]代理人代办成功"})
check("导出组: standby代办窗口2成功", r.status_code == 200, f"status={r.status_code} body={r.text[:80]}")

tomorrow_str3 = (today + timedelta(days=3)).strftime("%Y-%m-%d")
r = client.post("/maintenance-windows", json={
    "title": "导出审计-代办拒绝窗口",
    "description": "窗口代办拒绝审计",
    "environment_id": env_import_id,
    "start_time": f"{tomorrow_str3}T01:00:00",
    "end_time": f"{tomorrow_str3}T03:00:00",
    "change_reason": "版本升级",
    "creator_id": dev_id,
    "approver_id": boss_id,
})
check("导出组: 创建窗口3(代办拒绝)", r.status_code == 200, f"status={r.status_code} body={r.text[:120]}")
win3_id = r.json()["id"]
r = client.post(f"/maintenance-windows/{win3_id}/submit", json={"operator_id": dev_id})
r = client.post(f"/maintenance-windows/{win3_id}/approve", json={"operator_id": boss_id, "reason": "Boss先审批"})
r = client.post(f"/maintenance-windows/{win3_id}/approve", json={"operator_id": standby_id, "reason": "[导出测试]代理人重复审批应被拒"})
check("导出组: standby重复审批窗口3被拒(产生拒绝审计)", r.status_code == 400)

r = client.post("/approval-proxies/export", params={"environment_id": env_import_id, "operator_id": boss_id})
check("导出环境IMPORT所有代理", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
export_data = r.json().get("data", [])
check("导出至少4条代理记录(ACTIVE+INACTIVE+REVOKED+EXPIRED)", len(export_data) >= 4, f"count={len(export_data)} data_keys={list(r.json().keys())}")

status_map = {}
audit_counts = {}
for item in export_data:
    status_map[item["reason"]] = item["status"]
    audit_counts[item["reason"]] = len(item.get("audit_logs", []))

check("导出ACTIVE状态正确", status_map.get("导出测试-ACTIVE") == "ACTIVE", f"map={status_map}")
check("导出INACTIVE状态正确", status_map.get("导出测试-INACTIVE") == "INACTIVE", f"map={status_map}")
check("导出REVOKED状态正确", status_map.get("导出测试-REVOKED") == "REVOKED", f"map={status_map}")
check("导出EXPIRED状态正确", status_map.get("导出测试-EXPIRED") == "EXPIRED", f"map={status_map}")

active_proxy_audit = None
for item in export_data:
    if item["reason"] == "导出测试-ACTIVE":
        active_proxy_audit = [log["action"] for log in item.get("audit_logs", [])]
        break
check("ACTIVE代理导出包含代办成功审计日志", active_proxy_audit and "PROXY_DELEGATE_ACTION" in active_proxy_audit, f"audit={active_proxy_audit}")
check("ACTIVE代理导出包含代办拒绝审计日志", active_proxy_audit and "PROXY_DELEGATE_REJECT" in active_proxy_audit, f"audit={active_proxy_audit}")

export_data_for_import = []
for item in export_data:
    if item["reason"] and item["reason"].startswith("导出测试-"):
        new_item = copy.deepcopy(item)
        new_item["environment_name"] = "env-refactor-IMPORT"
        new_item["approver_username"] = "refactor.boss_import"
        if new_item.get("creator_username") and new_item["creator_username"] not in ["refactor.boss", "refactor.boss2"]:
            new_item["creator_username"] = "refactor.boss_import"
        elif new_item.get("creator_username"):
            new_item["creator_username"] = "refactor.boss_import"
        export_data_for_import.append(new_item)

check("为导入准备4条目标代理(四种状态各一)", len(export_data_for_import) == 4, f"count={len(export_data_for_import)}")

r = client.post("/approval-proxies/import", json={
    "proxies": export_data_for_import,
    "operator_id": boss_import_id,
    "on_conflict": "skip",
})
check("导入到 env-refactor-IMPORT 环境", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
import_result = r.json()
check("导入总数4(success+skipped)=4", import_result["success"] + import_result["skipped"] == 4, f"result={import_result}")

r = client.get("/approval-proxies", params={"environment_id": env_import_id, "operator_id": boss_import_id})
imported = r.json()
check("导入后环境IMPORT有6条代理(原4+新导入2)", len(imported) == 6, f"count={len(imported)}")

imported_status = {p["reason"]: p["status"] for p in imported}
check("导入后ACTIVE状态保持", imported_status.get("导出测试-ACTIVE") == "ACTIVE", f"imported={imported_status}")
check("导入后INACTIVE状态保持", imported_status.get("导出测试-INACTIVE") == "INACTIVE", f"imported={imported_status}")
check("导入后REVOKED状态保持", imported_status.get("导出测试-REVOKED") == "REVOKED", f"imported={imported_status}")
check("导入后EXPIRED状态保持(导入时规范化)", imported_status.get("导出测试-EXPIRED") == "EXPIRED", f"imported={imported_status}")

active_imported = next((p for p in imported if p["reason"] == "导出测试-ACTIVE"), None)
if active_imported:
    r = client.get(f"/approval-proxies/{active_imported['id']}/audit-logs", params={"operator_id": boss_import_id})
    imported_audits = [log["action"] for log in r.json()]
    check("导入后代办成功审计日志保留", "PROXY_DELEGATE_ACTION" in imported_audits, f"audits={imported_audits}")
    check("导入后代办拒绝审计日志保留", "PROXY_DELEGATE_REJECT" in imported_audits, f"audits={imported_audits}")

new_created_ids = [d["id"] for d in import_result.get("details", []) if d.get("status") == "created"]
if new_created_ids:
    r = client.get(f"/approval-proxies/{new_created_ids[0]}/audit-logs", params={"operator_id": boss_import_id})
    new_imported_audits = [log["action"] for log in r.json()]
    check("新创建的导入代理有 PROXY_IMPORT 审计", "PROXY_IMPORT" in new_imported_audits, f"audits={new_imported_audits}")

print("  --- 重启验证导入后状态 ---")
client = reload_app()

r = client.get("/approval-proxies", params={"environment_id": env_import_id, "operator_id": boss_import_id})
restarted = r.json()
check("重启后导入的代理仍=6条(原4+新导入2)", len(restarted) == 6, f"count={len(restarted)}")
restarted_status = {p["reason"]: p["status"] for p in restarted}
check("重启后ACTIVE状态一致", restarted_status.get("导出测试-ACTIVE") == "ACTIVE")
check("重启后INACTIVE状态一致", restarted_status.get("导出测试-INACTIVE") == "INACTIVE")
check("重启后REVOKED状态一致", restarted_status.get("导出测试-REVOKED") == "REVOKED")
check("重启后EXPIRED状态一致", restarted_status.get("导出测试-EXPIRED") == "EXPIRED")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_import_id,
    "required_scope": "WINDOW_APPROVE",
})
check("重启后导入的ACTIVE代理仍可代办(环境IMPORT)", r.json()["is_delegated"] is True, f"body={r.json()}")


# =====================================================================
# 最终汇总
# =====================================================================
passed = sum(1 for f, _, _ in results if f == PASS)
failed = sum(1 for f, _, _ in results if f == FAIL)

print("\n" + "=" * 70)
print("  [测试结果汇总]")
print("=" * 70)
print(f"总计: {len(results)}   通过: {passed}   失败: {failed}")
print()

if failed:
    print("  FAILURES:")
    for flag, name, detail in results:
        if flag == FAIL:
            print(f"    - {name}  {detail}")
    print()
    print("  SOME TESTS FAILED ✗")
    sys.exit(1)
else:
    print("  ALL TESTS PASSED ✓")
