"""
审批值班代理中心 端到端 HTTP 回归测试
覆盖:
1. 权限切换: 无审批权用户代理后可代办三大动作
2. 跨重启: 重启数据库/重载模块后代理继续按时段生效
3. 导入导出往返: 导出再导入后数据一致
4. 冲突拦截: 同审批人多段授权重叠 / 同代理人接多条授权环境冲突
5. 撤销后用户可见结果: 代理撤销后立即失效, 列表/详情正确显示
6. 停用/重新启用一致性: 停用后未到期代理重新启用需通过冲突校验
7. 审计日志: 授权变更/代办动作/拒绝原因均落库
8. 环境权限边界: 不允许代理没有的环境权限
9. 过期自动回收: 扫描后 EXPIRED 状态正确
10. 代理区分展示: 代办记录标注代理人与原审批人
"""
import sys
import os
import io
import json
import copy
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_approval_proxy_center.db")
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

from datetime import datetime, timedelta, timezone

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
print("  [审批值班代理中心 端到端回归测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境x2/角色x2/用户x4/维护时段/模板/窗口/方案/冻结规则 ---")

r = client.post("/environments", json={"name": "env-proxy-A", "description": "代理测试环境A"})
check("创建环境A", r.status_code == 200, f"status={r.status_code}")
env_a_id = r.json()["id"]

r = client.post("/environments", json={"name": "env-proxy-B", "description": "代理测试环境B"})
check("创建环境B", r.status_code == 200)
env_b_id = r.json()["id"]

r = client.post("/maintenance-slots", json={
    "environment_id": env_a_id, "day_of_week": 4,
    "start_time": "00:00", "end_time": "06:00",
})
check("创建环境A维护时段", r.status_code == 200)

r = client.post("/roles", json={"name": "PROXY-APPROVER", "can_approve": 1, "description": "审批角色"})
role_approve_id = r.json()["id"]
check("创建审批角色", r.status_code == 200)

r = client.post("/roles", json={"name": "PROXY-OPERATOR", "can_approve": 0, "description": "普通运维角色"})
role_op_id = r.json()["id"]
check("创建普通角色", r.status_code == 200)

r = client.post("/users", json={"username": "proxy.boss", "display_name": "Boss审批人", "role_id": role_approve_id})
check("创建审批人 boss", r.status_code == 200)
boss_id = r.json()["id"]

r = client.post("/users", json={"username": "proxy.boss2", "display_name": "审批人二号", "role_id": role_approve_id})
check("创建审批人 boss2", r.status_code == 200)
boss2_id = r.json()["id"]

r = client.post("/users", json={"username": "proxy.standby", "display_name": "值班代理人", "role_id": role_op_id})
check("创建代理人 standby(普通角色无审批权)", r.status_code == 200)
standby_id = r.json()["id"]

r = client.post("/users", json={"username": "proxy.dev", "display_name": "开发者小王", "role_id": role_op_id})
check("创建普通用户 dev", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/window-templates", json={
    "name": "代理链路-共享模板",
    "description": "代理测试用",
    "environment_id": env_a_id,
    "start_time": "01:00",
    "end_time": "03:00",
    "change_reason": "常规变更",
    "is_shared": 1,
    "creator_id": dev_id,
})
check("创建共享模板", r.status_code == 200)
tpl_id = r.json()["id"]

today = datetime.utcnow().date()
win_date = today + timedelta(days=7)
while win_date.weekday() != 3:
    win_date += timedelta(days=1)

r = client.post("/maintenance-windows", json={
    "title": "代理测试-待审批窗口",
    "description": "需要审批的窗口",
    "environment_id": env_a_id,
    "template_id": tpl_id,
    "change_type": "升级",
    "change_reason": "版本升级",
    "start_time": f"{win_date.isoformat()}T01:00:00",
    "end_time": f"{win_date.isoformat()}T03:00:00",
    "creator_id": dev_id,
    "change_items": [{"name": "升级组件A", "detail": "升级v1->v2"}],
})
check("创建待审批窗口", r.status_code == 200)
win1_id = r.json()["id"]

r = client.post(f"/maintenance-windows/{win1_id}/submit", json={"operator_id": dev_id})
check("提交窗口审批", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")

plan_date = today + timedelta(days=14)
while plan_date.weekday() != 3:
    plan_date += timedelta(days=1)

r = client.post("/schedule-plans", json={
    "name": "代理链路-排期方案",
    "description": "待确认的排期方案",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": [plan_date.isoformat()],
    "operator_remark": "代理人代办确认测试",
    "creator_id": dev_id,
})
check("创建排期方案", r.status_code == 200)
plan1_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan1_id}/submit", json={
    "operator_id": dev_id,
    "remark": "请boss/代理人审批",
})
check("提交排期方案", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan1_id}/approve", json={
    "operator_id": boss_id,
    "reason": "已通过审批",
})
check("boss审批排期方案通过", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")

r = client.post("/freeze-rules", json={
    "name": "代理链路-测试冻结规则",
    "description": "代理启停测试",
    "environment_id": env_a_id,
    "freeze_scope": "ALL",
    "date_from": (today + timedelta(days=60)).isoformat(),
    "date_to": (today + timedelta(days=90)).isoformat(),
    "start_time": "00:00",
    "end_time": "23:59",
    "reason": "大促冻结期",
    "status": "INACTIVE",
    "creator_id": boss_id,
})
check("创建冻结规则(初始停用, 日期与测试窗口不重叠)", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
freeze1_id = r.json()["id"]
check("冻结规则初始状态=INACTIVE", r.json()["status"] == "INACTIVE", f"status={r.json().get('status')}")

# ---------- 场景1: 权限切换 - 无审批权用户无法操作, 代理后可以 ----------
print("\n--- [场景1] 权限切换: 无权限 → 授权代理 → 代办成功 → 撤销后失效 ---")

r = client.post(f"/maintenance-windows/{win1_id}/approve", json={
    "operator_id": standby_id,
    "reason": "试图越权审批",
})
check("未授权前 standby 无权审批窗口(403)", r.status_code == 403, f"status={r.status_code}")

r = client.post(f"/schedule-plans/{plan1_id}/confirm", json={
    "operator_id": standby_id,
})
check("未授权前 standby 无权确认方案(403)", r.status_code == 403, f"status={r.status_code} body={r.text[:300]}")

r = client.post(f"/freeze-rules/{freeze1_id}/activate", json={"operator_id": standby_id})
check("未授权前 standby 无权激活冻结规则(403)", r.status_code == 403)

now = datetime.utcnow()
vf = iso(now - timedelta(minutes=5))
vt = iso(now + timedelta(hours=8))

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE", "PLAN_CONFIRM", "FREEZE_TOGGLE"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "临时值班",
    "remark": "周三晚20点到周四早4点代为审批",
    "creator_id": boss_id,
})
check("创建全量代理授权 200", r.status_code == 200, f"body={r.text[:300]}")
proxy1_id = r.json()["id"]
check("代理状态为 ACTIVE", r.json()["status"] == "ACTIVE")
check("代理范围3项", len(r.json()["delegate_scope"]) == 3)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "WINDOW_APPROVE",
})
check("代理生效检查 WINDOW_APPROVE → is_delegated=true",
      r.status_code == 200 and r.json()["is_delegated"] is True,
      f"body={r.text}")
check("代理生效检查返回原审批人ID=boss_id",
      r.json()["original_approver_id"] == boss_id)

r = client.post(f"/maintenance-windows/{win1_id}/approve", json={
    "operator_id": standby_id,
    "reason": "[代理人值班]审批通过",
})
check("代理人 standby 代办窗口审批 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
check("审批后窗口状态 APPROVED", r.json()["status"] == "APPROVED", f"status={r.json().get('status')} body={r.text[:300]}")
check("窗口记录的审批人ID=standby", r.json().get("approver_id") == standby_id,
      f"approver_id={r.json().get('approver_id')} approved_by={r.json().get('approved_by')}")

r = client.get(f"/approval-proxies/{proxy1_id}", params={"operator_id": boss_id})
audit_actions = [a["action"] for a in r.json()["audit_logs"]]
check("审计日志包含 PROXY_CREATE", "PROXY_CREATE" in audit_actions)
check("审计日志包含 PROXY_DELEGATE_ACTION", "PROXY_DELEGATE_ACTION" in audit_actions,
      f"audit_actions={audit_actions}")

delegate_logs = [a for a in r.json()["audit_logs"] if a["action"] == "PROXY_DELEGATE_ACTION"]
check("代办日志 target_window_id 正确", delegate_logs[-1]["target_window_id"] == win1_id,
      f"log={delegate_logs[-1]}")

r = client.post(f"/schedule-plans/{plan1_id}/confirm", json={
    "operator_id": standby_id,
})
check("代理人 standby 代办计划确认 200", r.status_code == 200, f"status={r.status_code} body={r.text[:500]}")

r = client.post(f"/freeze-rules/{freeze1_id}/activate", json={"operator_id": standby_id})
check("代理人 standby 代办冻结规则激活 200", r.status_code == 200, f"status={r.status_code}")

r = client.post(f"/approval-proxies/{proxy1_id}/revoke", params={
    "operator_id": boss_id,
    "reason": "提前结束值班",
})
check("boss 提前撤销代理授权 200", r.status_code == 200)
check("撤销后状态=REVOKED", r.json()["status"] == "REVOKED")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "WINDOW_APPROVE",
})
check("撤销后代理检查 is_delegated=false", r.json()["is_delegated"] is False)

r = client.post(f"/freeze-rules/{freeze1_id}/deactivate", json={"operator_id": standby_id})
check("撤销后 standby 无权停用冻结规则(403)", r.status_code == 403)

# ---------- 场景2: 冲突拦截 ----------
print("\n--- [场景2] 冲突拦截: 时段重叠/范围重叠/环境冲突 全部拦截 ---")

# 先创建一个新的活跃代理，用于测试冲突
r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE", "PLAN_CONFIRM"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "冲突测试基准代理",
    "creator_id": boss_id,
})
check("创建冲突测试基准代理(活跃)", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
base_proxy_id = r.json()["id"]

vf2 = iso(now + timedelta(days=1))
vt2 = iso(now + timedelta(days=2))

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "试图重叠",
    "creator_id": boss_id,
})
check("同审批人→代理人重叠环境+时段+范围 → 409 冲突",
      r.status_code == 409, f"status={r.status_code} body={r.text[:300]}")

r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["PLAN_CONFIRM", "WINDOW_APPROVE"],
    "valid_from": vf2,
    "valid_to": vt2,
    "reason": "时段完全不重叠，允许",
    "creator_id": boss_id,
})
check("时段不重叠 → 200 允许",
      r.status_code in (200, 201), f"status={r.status_code} body={r.text[:300]}")
proxy2_id = r.json()["id"]

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "两个审批人同时授权给同一代理人同范围",
    "creator_id": boss2_id,
})
check("多审批人→同代理人同环境同范围重叠 → 409冲突拦截",
      r.status_code == 409, f"status={r.status_code} body={r.text[:500]}")

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "delegate_scope": ["FREEZE_TOGGLE"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "范围不同, 允许共存",
    "creator_id": boss2_id,
})
check("多审批人→同代理人 范围不重叠 → 200 允许",
      r.status_code in (200, 201), f"status={r.status_code} body={r.text[:300]}")
proxy3_id = r.json()["id"]

# ---------- 场景3: 停用/重新启用一致性 ----------
print("\n--- [场景3] 停用/重新启用: 冲突校验+过期失效 ---")

r = client.get(f"/approval-proxies/{base_proxy_id}", params={"operator_id": boss_id})
check("base_proxy 初始状态=ACTIVE", r.json()["status"] == "ACTIVE", f"status={r.json().get('status')}")

r = client.post(f"/approval-proxies/{base_proxy_id}/deactivate", params={"operator_id": boss_id})
check("停用 base_proxy 成功 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
check("停用后状态=INACTIVE", r.json()["status"] == "INACTIVE")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("停用后 PLAN_CONFIRM 代理检查 is_delegated=false", r.json()["is_delegated"] is False)

r = client.post(f"/approval-proxies/{base_proxy_id}/reactivate", params={"operator_id": standby_id})
check("非审批人/boss 重新启用 → 403", r.status_code == 403)

r = client.post(f"/approval-proxies/{base_proxy_id}/reactivate", params={"operator_id": boss_id})
check("boss 重新启用 base_proxy → 200", r.status_code == 200, f"body={r.text[:300]}")
check("重新启用后状态=ACTIVE", r.json()["status"] == "ACTIVE")

r = client.get(f"/approval-proxies/{base_proxy_id}", params={"operator_id": boss_id})
actions2 = [a["action"] for a in r.json()["audit_logs"]]
check("base_proxy 审计日志含 PROXY_DEACTIVATE + PROXY_REACTIVATE",
      "PROXY_DEACTIVATE" in actions2 and "PROXY_REACTIVATE" in actions2,
      f"actions2={actions2}")

vf_short = iso(now - timedelta(hours=2))
vt_short = iso(now - timedelta(minutes=30))
r = client.post("/approval-proxies", json={
    "approver_id": boss_id,
    "proxy_user_id": dev_id,
    "environment_id": env_a_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": vf_short,
    "valid_to": vt_short,
    "reason": "刚过期的授权",
    "creator_id": boss_id,
})
check("创建刚过期时段的代理 → 200", r.status_code == 200)
expired_proxy_id = r.json()["id"]

r = client.post(f"/approval-proxies/expire-stale", params={"operator_id": boss_id})
check("触发过期扫描 200", r.status_code == 200)
check("扫描报告过期数>=1", r.json()["expired_count"] >= 1, f"body={r.text}")

r = client.get(f"/approval-proxies/{expired_proxy_id}", params={"operator_id": boss_id})
check("过期后状态=EXPIRED", r.json()["status"] == "EXPIRED")

r = client.post(f"/approval-proxies/{expired_proxy_id}/reactivate", params={"operator_id": boss_id})
check("重新启用EXPIRED的代理 → 400", r.status_code == 400, f"status={r.status_code}")

# ---------- 场景4: 跨重启持久化 ----------
print("\n--- [场景4] 跨重启: 重载模块/重建engine后代理仍按时段生效 ---")

client = reload_app()
time.sleep(0.2)

r = client.get(f"/approval-proxies/{base_proxy_id}", params={"operator_id": boss_id})
check("重启后 base_proxy 仍能查到", r.status_code == 200, f"status={r.status_code}")
check("重启后 base_proxy 状态仍=ACTIVE", r.json()["status"] == "ACTIVE", f"status={r.json().get('status')}")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_a_id,
    "required_scope": "PLAN_CONFIRM",
})
check("重启后 PLAN_CONFIRM 代理仍生效",
      r.status_code == 200 and r.json()["is_delegated"] is True, f"body={r.text}")

r = client.get(f"/approval-proxies/{expired_proxy_id}", params={"operator_id": boss_id})
check("重启后过期代理仍=EXPIRED", r.json()["status"] == "EXPIRED")

r = client.get(f"/approval-proxies/{proxy1_id}", params={"operator_id": boss_id})
check("重启后已撤销代理仍=REVOKED", r.json()["status"] == "REVOKED")

r = client.get(f"/approval-proxies/{base_proxy_id}", params={"operator_id": boss_id})
audit_after_reboot = r.json()["audit_logs"]
check("重启后审计日志完整保留", len(audit_after_reboot) >= 3, f"count={len(audit_after_reboot)}")

# ---------- 场景5: 导入导出往返 ----------
print("\n--- [场景5] JSON 导入导出往返一致性 ---")

r = client.post("/approval-proxies/export", params={
    "operator_id": boss_id,
    "environment_id": env_a_id,
})
check("导出代理授权 200", r.status_code == 200)
export_data = r.json()["data"]
check("导出至少4条记录", len(export_data) >= 4, f"export_count={len(export_data)}")

export_backup = copy.deepcopy(export_data)

item = export_data[0]
check("导出项含 approver_username 字段", "approver_username" in item, f"keys={item.keys()}")
check("导出项含 audit_logs 字段", "audit_logs" in item)
check("导出项含 delegate_scope 列表", isinstance(item["delegate_scope"], list))

r = client.post("/approval-proxies/import", json={
    "proxies": export_backup,
    "operator_id": boss_id,
    "on_conflict": "skip",
})
check("导出再导入(skip) 200", r.status_code == 200, f"body={r.text[:500]}")
check("导入结果 skipped == 原条数",
      r.json()["skipped"] == len(export_backup),
      f"skipped={r.json()['skipped']}  expected={len(export_backup)}  total={r.json()}")

new_env_name = "env-proxy-C-imported"
r = client.post("/environments", json={"name": new_env_name, "description": "导入新建环境"})
env_c_id = r.json()["id"]
new_boss_name = "proxy.boss3"
r = client.post("/users", json={
    "username": new_boss_name, "display_name": "导入boss", "role_id": role_approve_id
})
new_boss_id = r.json()["id"]
new_standby_name = "proxy.standby2"
r = client.post("/users", json={
    "username": new_standby_name, "display_name": "二号代理人", "role_id": role_op_id
})
new_standby_id = r.json()["id"]

fresh_proxies = []
for i, item in enumerate(export_backup[:2]):
    new_item = copy.deepcopy(item)
    new_item["approver_username"] = new_boss_name
    new_item["proxy_username"] = new_standby_name
    new_item["environment_name"] = new_env_name
    new_vf = iso(now + timedelta(days=3 + i * 2))
    new_vt = iso(now + timedelta(days=4 + i * 2))
    new_item["valid_from"] = new_vf
    new_item["valid_to"] = new_vt
    fresh_proxies.append(new_item)

r = client.post("/approval-proxies/import", json={
    "proxies": fresh_proxies,
    "operator_id": new_boss_id,
    "on_conflict": "skip",
})
check("导入新环境全新授权 200", r.status_code == 200, f"body={r.text[:500]}")
check("导入 success >= 2", r.json()["success"] >= 2, f"res={r.json()}")

r = client.get("/approval-proxies", params={
    "environment_id": env_c_id,
    "operator_id": new_boss_id,
})
check("新环境下能查到导入的代理", len(r.json()) >= 2, f"count={len(r.json())}")

r = client.get(f"/approval-proxies/{r.json()[0]['id']}", params={"operator_id": new_boss_id})
check("导入后代理含审计日志", len(r.json()["audit_logs"]) >= 1,
      f"logs={len(r.json()['audit_logs'])}")
import_actions = [a["action"] for a in r.json()["audit_logs"]]
check("导入后代理审计日志含 PROXY_IMPORT", "PROXY_IMPORT" in import_actions,
      f"actions={import_actions}")

# ---------- 场景6: 撤销后用户可见结果 ----------
print("\n--- [场景6] 撤销后用户可见结果: 列表过滤/详情状态/代理检查失效 ---")

r = client.post("/approval-proxies", json={
    "approver_id": boss2_id,
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "delegate_scope": ["WINDOW_APPROVE"],
    "valid_from": vf,
    "valid_to": vt,
    "reason": "环境B测试撤销可见性",
    "creator_id": boss2_id,
})
check("创建环境B代理授权", r.status_code == 200)
proxy_envb_id = r.json()["id"]

r = client.get("/approval-proxies", params={
    "environment_id": env_b_id,
    "status": "ACTIVE",
    "operator_id": boss2_id,
})
check("撤销前列表能看到 ACTIVE",
      any(p["id"] == proxy_envb_id and p["status"] == "ACTIVE" for p in r.json()))

r = client.post(f"/approval-proxies/{proxy_envb_id}/revoke", params={
    "operator_id": boss2_id,
    "reason": "测试撤销可见",
})
check("boss2 撤销环境B代理", r.status_code == 200)

r = client.get("/approval-proxies", params={
    "environment_id": env_b_id,
    "status": "ACTIVE",
    "operator_id": boss2_id,
})
check("撤销后按ACTIVE筛选不显示",
      all(p["id"] != proxy_envb_id for p in r.json()),
      f"active_ids={[p['id'] for p in r.json()]}")

r = client.get("/approval-proxies", params={
    "environment_id": env_b_id,
    "status": "REVOKED",
    "operator_id": boss2_id,
})
check("按REVOKED筛选能看到",
      any(p["id"] == proxy_envb_id and p["status"] == "REVOKED" for p in r.json()))

r = client.get("/approval-proxies", params={
    "operator_id": standby_id,
})
visible_ids = [p["id"] for p in r.json()]
check("standby 列表中能看到自己参与的代理(含已撤销)",
      proxy_envb_id in visible_ids and proxy2_id in visible_ids,
      f"visible_ids={visible_ids}")

r = client.get("/approval-proxies", params={"operator_id": dev_id})
dev_visible = [p["id"] for p in r.json()]
check("普通用户dev 列表只看自己相关的代理",
      all(p["approver_id"] == dev_id or p["proxy_user_id"] == dev_id for p in r.json()),
      f"dev_visible_count={len(dev_visible)}")

r = client.get(f"/approval-proxies/{proxy2_id}", params={"operator_id": dev_id})
check("dev 查看非自己相关的代理详情 → 403", r.status_code == 403)

r = client.get(f"/approval-proxies/{proxy2_id}/audit-logs", params={"operator_id": dev_id})
check("dev 查看非相关代理审计日志 → 403", r.status_code == 403)

# ---------- 场景7: 环境权限边界(不放大) ----------
print("\n--- [场景7] 环境权限边界: 只允许代理有授权的环境 ---")

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_b_id,
    "required_scope": "WINDOW_APPROVE",
})
check("环境B撤销后代理检查 false", r.json()["is_delegated"] is False)

r = client.get(f"/approval-proxies/check/delegation", params={
    "proxy_user_id": standby_id,
    "environment_id": env_c_id,
    "required_scope": "WINDOW_APPROVE",
})
check("未授权的环境C代理检查 false", r.json()["is_delegated"] is False)

r = client.post("/maintenance-windows", json={
    "title": "环境B的窗口-测试越权",
    "description": "用于测试代理权限不跨环境",
    "environment_id": env_b_id,
    "template_id": tpl_id,
    "change_type": "升级",
    "change_reason": "跨环境权限测试",
    "start_time": f"{win_date.isoformat()}T01:00:00",
    "end_time": f"{win_date.isoformat()}T03:00:00",
    "creator_id": dev_id,
    "change_items": [],
})
win_envb_id = r.json()["id"]
client.post(f"/maintenance-windows/{win_envb_id}/submit", json={"operator_id": dev_id})

r = client.post(f"/maintenance-windows/{win_envb_id}/approve", json={
    "operator_id": standby_id,
    "reason": "试图用环境A的代理审批环境B",
})
check("环境A的代理不能审批环境B的窗口 → 403", r.status_code == 403)

r = client.post(f"/maintenance-windows/{win_envb_id}/approve", json={
    "operator_id": boss_id,
    "reason": "boss本人审批环境B",
})
check("boss本人审批环境B成功(他是审批角色全局有权)", r.status_code == 200, f"body={r.text[:300]}")

# ---------- 场景8: 代理区分展示(审计日志+操作者) ----------
print("\n--- [场景8] 代办记录区分展示: 代理人 ≠ 原审批人 ---")

r = client.get(f"/approval-proxies/{proxy2_id}", params={"operator_id": boss_id})
for log in r.json()["audit_logs"]:
    if log["action"] == "PROXY_DELEGATE_ACTION":
        op_username = log["operator_username"]
        snapshot = log["snapshot"]
        check("代办审计日志记录代理人用户名",
              op_username == "proxy.standby",
              f"operator_username={op_username}")
        check("快照snapshot含原审批人approver_id",
              snapshot.get("approver_id") == boss_id,
              f"snapshot.approver_id={snapshot.get('approver_id')}")
        check("快照snapshot含代理人proxy_user_id",
              snapshot.get("proxy_user_id") == standby_id,
              f"snapshot.proxy_user_id={snapshot.get('proxy_user_id')}")
        break

r = client.get(f"/maintenance-windows/{win1_id}")
window_detail = r.json()
approver_records = window_detail.get("audit_logs", [])
delegate_approvals = [
    lg for lg in approver_records
    if lg.get("operator_id") == standby_id
]
check("窗口审计日志中能看到代理人standby的审批记录",
      len(delegate_approvals) >= 1,
      f"logs_count={len(delegate_approvals)}  all_ops={[(lg.get('action'), lg.get('operator_id')) for lg in approver_records]}")

# ---------- 汇总 ----------
print("\n" + "=" * 70)
print("  [测试结果汇总]")
print("=" * 70)

total = len(results)
passed = sum(1 for f, _, _ in results if f == PASS)
failed = sum(1 for f, _, _ in results if f == FAIL)

print(f"\n总计: {total}   通过: {passed}   失败: {failed}")

if failed:
    print("\n--- 失败详情 ---")
    for f, n, d in results:
        if f == FAIL:
            print(f"  {FAIL} {n}  {d}")
    sys.exit(1)
else:
    print("\n  ALL TESTS PASSED ✓")
    sys.exit(0)
