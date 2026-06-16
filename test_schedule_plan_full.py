"""
排期方案完整流程测试：
- 锁版机制：生成候选窗口并保存为待审批方案
- 审批流程：提交、审批通过/驳回
- 变更检测：模板变更、时段变更、冲突变更、窗口状态变更
- 二次确认：单条预检、剔除、批量确认
- 执行创建：生成维护窗口
- 重启恢复：服务重启后数据完整
- 导入导出：方案 JSON 导入导出回放
- 权限控制：非审批角色不能替别人确认已共享方案
"""
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_DB_PATH = os.path.join(ROOT, "test_schedule_plan_full.db")
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
print("  [排期方案完整流程 端到端测试]")
print("=" * 70)

# ---------- 准备 ----------
print("\n--- [准备] 建环境/角色/用户/维护时段 ---")
r = client.post("/environments", json={"name": "env-plan-test", "description": "测试环境"})
check("创建环境", r.status_code == 200, f"status={r.status_code}")
env_id = r.json()["id"]

r = client.post("/maintenance-slots", json={
    "environment_id": env_id,
    "day_of_week": 2,
    "start_time": "01:00",
    "end_time": "05:00",
})
check("创建维护时段(周三凌晨)", r.status_code == 200)

r = client.post("/roles", json={"name": "CM-Plan", "can_approve": 1, "description": "审批角色"})
check("创建审批角色", r.status_code == 200)
role_mgr_id = r.json()["id"]

r = client.post("/roles", json={"name": "DEV-Plan", "can_approve": 0, "description": "开发角色"})
check("创建开发角色", r.status_code == 200)
role_dev_id = r.json()["id"]

r = client.post("/users", json={"username": "plan.mgr", "display_name": "PlanMgr", "role_id": role_mgr_id})
check("创建审批用户", r.status_code == 200)
mgr_id = r.json()["id"]

r = client.post("/users", json={"username": "plan.dev", "display_name": "PlanDev", "role_id": role_dev_id})
check("创建开发用户", r.status_code == 200)
dev_id = r.json()["id"]

r = client.post("/users", json={"username": "other.dev", "display_name": "OtherDev", "role_id": role_dev_id})
check("创建其他开发用户", r.status_code == 200)
other_dev_id = r.json()["id"]

# ---------- 场景1：创建模板 ----------
print("\n--- [场景1] 创建模板 ---")
r = client.post("/window-templates", json={
    "name": "Plan-测试模板",
    "description": "用于排期方案测试",
    "environment_id": env_id,
    "start_time": "02:00",
    "end_time": "04:00",
    "change_reason": "方案测试",
    "is_shared": 1,
    "creator_id": dev_id,
})
check("创建共享模板", r.status_code == 200, f"body={r.text[:300]}")
tpl_id = r.json()["id"]

# ---------- 场景2：创建排期方案（锁版机制） ----------
print("\n--- [场景2] 创建排期方案（锁版机制） ---")
r = client.post("/schedule-plans", json={
    "name": "2026年Q3维护方案",
    "description": "第三季度批量维护",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-07-01", "2026-07-08", "2026-07-15"],
    "operator_remark": "按周三维护窗执行",
    "creator_id": dev_id,
})
check("创建排期方案 200", r.status_code == 200, f"status={r.status_code} body={r.text[:500]}")
plan_id = r.json()["id"]
plan_data = r.json()

check("方案状态为 DRAFT", plan_data["status"] == "DRAFT")
check("方案锁定模板快照", "template_version_snapshot" in plan_data and plan_data["template_version_snapshot"] is not None)
check("方案锁定环境时段快照", "environment_slots_snapshot" in plan_data and plan_data["environment_slots_snapshot"] is not None)
check("方案总数=3", plan_data["total_count"] == 3, f"count={plan_data['total_count']}")

# 查看方案明细
r = client.get(f"/schedule-plans/{plan_id}")
check("获取方案明细 200", r.status_code == 200)
detail = r.json()
check("明细包含3条候选窗口", len(detail["items"]) == 3, f"count={len(detail['items'])}")

items = detail["items"]
check("条目1含预检快照", items[0]["conflict_type_snapshot"] == "OK")
check("条目锁定了时间段", items[0]["start_time"] == "02:00" and items[0]["end_time"] == "04:00")
check("条目状态为 PENDING", items[0]["status"] == "PENDING")

# 查看方案列表
r = client.get("/schedule-plans")
check("方案列表 200", r.status_code == 200)
check("列表有1条方案", len(r.json()) == 1)

# 查看审计日志
r = client.get(f"/schedule-plans/{plan_id}/audit-logs")
check("审计日志 200", r.status_code == 200)
check("有创建日志", len(r.json()) >= 1)
check("日志动作为 PLAN_CREATE", r.json()[0]["action"] == "PLAN_CREATE")

# ---------- 场景3：提交审批 ----------
print("\n--- [场景3] 提交审批 ---")
r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
    "remark": "请审批Q3维护方案",
})
check("提交审批 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
check("状态变为 PENDING_APPROVAL", r.json()["status"] == "PENDING_APPROVAL")

# 开发用户尝试审批（应该失败）
r = client.post(f"/schedule-plans/{plan_id}/approve", json={
    "operator_id": dev_id,
    "reason": "我来审批",
})
check("开发用户无审批权限 403", r.status_code == 403, f"status={r.status_code}")

# 审批用户驳回
r = client.post(f"/schedule-plans/{plan_id}/reject", json={
    "operator_id": mgr_id,
    "reason": "日期需要调整，请重新选择",
})
check("审批驳回 200", r.status_code == 200)
check("状态变为 REJECTED", r.json()["status"] == "REJECTED")
check("记录了审批人", r.json()["approver_id"] == mgr_id)

# 重新提交
r = client.post(f"/schedule-plans/{plan_id}/submit", json={
    "operator_id": dev_id,
    "remark": "已按要求调整日期",
})
check("重新提交审批 200", r.status_code == 200)
check("状态回到 PENDING_APPROVAL", r.json()["status"] == "PENDING_APPROVAL")

# 审批用户通过
r = client.post(f"/schedule-plans/{plan_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "同意，按计划执行",
})
check("审批通过 200", r.status_code == 200)
check("状态变为 APPROVED", r.json()["status"] == "APPROVED")
check("审批通过数=3", r.json()["approved_count"] == 3)

# 检查条目状态变为 APPROVED
r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
all_approved = all(item["status"] == "APPROVED" for item in detail["items"])
check("所有条目状态变为 APPROVED", all_approved)

# ---------- 场景4：变更检测 ----------
print("\n--- [场景4] 变更检测 ---")

# 先创建一个冲突窗口
r = client.post("/maintenance-windows", json={
    "title": "已存在的维护窗口",
    "description": "用于测试冲突",
    "environment_id": env_id,
    "start_time": "2026-07-08T02:30:00",
    "end_time": "2026-07-08T03:30:00",
    "creator_id": mgr_id,
    "change_reason": "冲突测试",
})
check("创建冲突窗口 200", r.status_code == 200)
conflict_win_id = r.json()["id"]

# 提交冲突窗口审批
r = client.post(f"/maintenance-windows/{conflict_win_id}/submit", json={
    "operator_id": mgr_id,
    "reason": "测试冲突",
})
check("提交冲突窗口审批 200", r.status_code == 200)

# 审批冲突窗口
r = client.post(f"/maintenance-windows/{conflict_win_id}/approve", json={
    "operator_id": mgr_id,
    "reason": "通过",
})
check("审批冲突窗口 200", r.status_code == 200)

# 变更检测
r = client.post(f"/schedule-plans/{plan_id}/detect-changes", params={"operator_id": mgr_id})
check("变更检测 200", r.status_code == 200, f"body={r.text[:500]}")
detect_result = r.json()

check("检测到1条变更", detect_result["changed_items"] == 1, f"changed={detect_result['changed_items']}")
check("2条无变化", detect_result["unchanged_items"] == 2, f"unchanged={detect_result['unchanged_items']}")

# 检查变更条目状态
r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
changed_items = [item for item in detail["items"] if item["status"] == "CHANGED"]
check("有1条状态变为 CHANGED", len(changed_items) == 1)
check("变更条目含 diff_hints", len(changed_items[0]["diff_hints"]) > 0)
check("diff_type 为 CONFLICT_CHANGED", changed_items[0]["current_diff_type"] == "CONFLICT_CHANGED")

# 方案状态变为 CONFIRMING
check("方案状态变为 CONFIRMING", detail["status"] == "CONFIRMING")

# ---------- 场景5：二次确认 - 单条预检、剔除 ----------
print("\n--- [场景5] 二次确认 - 单条预检、剔除 ---")

# 尝试直接确认（应该失败，因为有变更未处理）
r = client.post(f"/schedule-plans/{plan_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "直接确认",
})
check("有变更未处理时确认失败 400", r.status_code == 400, f"status={r.status_code}")

# 先剔除有冲突的条目
changed_item_id = changed_items[0]["id"]
r = client.post(f"/schedule-plans/{plan_id}/items/{changed_item_id}/exclude", params={
    "operator_id": mgr_id,
    "reason": "存在冲突，跳过本次",
})
check("剔除冲突条目 200", r.status_code == 200)
check("条目状态变为 EXCLUDED", r.json()["status"] == "EXCLUDED")
check("记录了剔除人", r.json()["excluded_by"] == mgr_id)

# 检查方案审批数减少
r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
check("审批数变为2", detail["approved_count"] == 2)

# 模拟另一种变更：修改模板时间
r = client.put(f"/window-templates/{tpl_id}", params={"operator_id": dev_id}, json={
    "start_time": "03:00",
    "end_time": "05:00",
})
check("修改模板时间 200", r.status_code == 200)

# 再次检测变更
r = client.post(f"/schedule-plans/{plan_id}/detect-changes", params={"operator_id": mgr_id})
check("再次变更检测 200", r.status_code == 200)
detect_result2 = r.json()
check("检测到2条 TEMPLATE_CHANGED", detect_result2["changed_items"] == 2)

# 对其中一条重新预检
r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
changed_items2 = [item for item in detail["items"] if item["status"] == "CHANGED"]
check("有2条 CHANGED 状态", len(changed_items2) == 2)

# 对两条都重新预检
for i, changed_item in enumerate(changed_items2):
    r = client.post(f"/schedule-plans/{plan_id}/items/{changed_item['id']}/recheck", params={
        "operator_id": mgr_id,
    })
    check(f"第{i+1}条重新预检 200", r.status_code == 200)
    check(f"第{i+1}条预检后状态回到 APPROVED", r.json()["status"] == "APPROVED")
    check(f"第{i+1}条 diff_type 变为 NO_CHANGE", r.json()["current_diff_type"] == "NO_CHANGE")

# 批量确认剩余条目
r = client.post(f"/schedule-plans/{plan_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "已处理变更，确认执行",
})
check("批量确认 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
check("状态变为 CONFIRMED", r.json()["status"] == "CONFIRMED")
check("确认数=2", r.json()["confirmed_count"] == 2)

# 查看确认记录
r = client.get(f"/schedule-plans/{plan_id}/confirmations")
check("确认记录 200", r.status_code == 200)
check("有1条确认记录", len(r.json()) == 1)
check("确认记录含 diff_summary", "diff_summary" in r.json()[0])

# ---------- 场景6：执行创建 ----------
print("\n--- [场景6] 执行创建维护窗口 ---")
r = client.post(f"/schedule-plans/{plan_id}/execute", json={
    "operator_id": mgr_id,
})
check("执行创建 200", r.status_code == 200, f"body={r.text[:500]}")
exec_result = r.json()

check("成功创建2条", exec_result["success_count"] == 2, f"success={exec_result['success_count']}")
check("状态为 EXECUTED", exec_result["status"] == "EXECUTED")

# 检查方案状态和条目
r = client.get(f"/schedule-plans/{plan_id}")
detail = r.json()
check("方案状态变为 EXECUTED", detail["status"] == "EXECUTED")
check("已创建数=2", detail["created_count"] == 2)

created_items = [item for item in detail["items"] if item["status"] == "CREATED"]
check("2条条目状态为 CREATED", len(created_items) == 2)
check("条目关联了 window_id", all(item["window_id"] is not None for item in created_items))

# 验证实际创建了窗口
r = client.get("/maintenance-windows", params={"environment_id": env_id})
check("环境下有3条窗口（1条冲突+2条方案创建）", len(r.json()) == 3)

# ---------- 场景7：权限控制测试 ----------
print("\n--- [场景7] 权限控制测试 ---")

# 创建一个新方案，共享模板
r = client.post("/schedule-plans", json={
    "name": "权限测试方案",
    "description": "测试权限控制",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-08-01"],
    "creator_id": dev_id,
})
check("创建权限测试方案 200", r.status_code == 200)
plan2_id = r.json()["id"]

r = client.post(f"/schedule-plans/{plan2_id}/submit", json={"operator_id": dev_id})
check("提交审批 200", r.status_code == 200)

r = client.post(f"/schedule-plans/{plan2_id}/approve", json={"operator_id": mgr_id})
check("审批通过 200", r.status_code == 200)

# 其他开发用户尝试确认共享方案（应该失败）
r = client.post(f"/schedule-plans/{plan2_id}/confirm", json={
    "operator_id": other_dev_id,
    "remark": "我来确认别人的方案",
})
check("非审批角色不能替别人确认已共享方案 403", r.status_code == 403, f"status={r.status_code}")

# 审批用户可以确认
r = client.post(f"/schedule-plans/{plan2_id}/confirm", json={
    "operator_id": mgr_id,
    "remark": "审批用户确认",
})
check("审批用户可以确认 200", r.status_code == 200)

# ---------- 场景8：重启恢复测试 ----------
print("\n--- [场景8] 重启恢复测试 ---")

# 保存重启前的数据
before_restart = client.get(f"/schedule-plans/{plan_id}").json()
before_list = client.get("/schedule-plans").json()
before_audit = client.get(f"/schedule-plans/{plan_id}/audit-logs").json()
before_conf = client.get(f"/schedule-plans/{plan_id}/confirmations").json()

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

# 验证方案列表恢复
r = client2.get("/schedule-plans")
check("重启后方案列表 200", r.status_code == 200)
check("重启后仍有2个方案", len(r.json()) == 2)

# 验证方案明细恢复
r = client2.get(f"/schedule-plans/{plan_id}")
check("重启后方案明细 200", r.status_code == 200)
after_restart = r.json()
check("重启后状态仍为 EXECUTED", after_restart["status"] == "EXECUTED")
check("重启后条目数不变", len(after_restart["items"]) == len(before_restart["items"]))
check("重启后创建数不变", after_restart["created_count"] == before_restart["created_count"])

# 验证审计日志恢复
r = client2.get(f"/schedule-plans/{plan_id}/audit-logs")
check("重启后审计日志 200", r.status_code == 200)
check("重启后审计日志数量不变", len(r.json()) == len(before_audit))

# 验证确认记录恢复
r = client2.get(f"/schedule-plans/{plan_id}/confirmations")
check("重启后确认记录 200", r.status_code == 200)
check("重启后确认记录数量不变", len(r.json()) == len(before_conf))

# 验证可以继续操作
r = client2.post("/schedule-plans", json={
    "name": "重启后创建方案",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-09-01"],
    "creator_id": dev_id,
})
check("重启后可正常创建新方案", r.status_code == 200)

# ---------- 场景9：导入导出测试 ----------
print("\n--- [场景9] 导入导出测试 ---")

# 导出方案
r = client2.post("/schedule-plans/export", params={"plan_ids": [plan_id]})
check("导出方案 200", r.status_code == 200)
export_data = r.json()
check("导出1个方案", export_data["count"] == 1)
check("导出包含明细", len(export_data["data"][0]["items"]) == 3)
check("导出包含确认记录", len(export_data["data"][0]["confirmations"]) >= 1)
check("导出包含审计日志", len(export_data["data"][0]["audit_logs"]) >= 1)

# 准备导入数据
import_plans = export_data["data"]
# 修改名称避免冲突
import_plans[0]["name"] = "导入回放方案"
import_plans[0]["status"] = "DRAFT"  # 改为草稿状态以测试审批流程
for item in import_plans[0]["items"]:
    item["status"] = "PENDING"  # 条目标为待处理
    item["window_id"] = None    # 清除已创建的窗口ID

r = client2.post("/schedule-plans/import", json={
    "plans": import_plans,
    "operator_id": dev_id,
    "on_conflict": "skip",
})
check("导入方案 200", r.status_code == 200)
import_result = r.json()
check("导入成功1个", import_result["success"] == 1, f"result={import_result}")

imported_plan_id = import_result["details"][0]["id"]

# 验证导入的方案
r = client2.get(f"/schedule-plans/{imported_plan_id}")
check("导入方案明细 200", r.status_code == 200)
imported = r.json()
check("导入方案名称正确", imported["name"] == "导入回放方案")
check("导入方案保留了3条条目", len(imported["items"]) == 3)
check("导入方案保留了锁版快照", imported["template_version_snapshot"] is not None)

# 验证导入的方案可以继续审批流程
r = client2.post(f"/schedule-plans/{imported_plan_id}/submit", json={"operator_id": dev_id})
check("导入方案可提交审批", r.status_code == 200)

r = client2.post(f"/schedule-plans/{imported_plan_id}/approve", json={"operator_id": mgr_id})
check("导入方案可审批通过", r.status_code == 200)

# 测试冲突处理
r = client2.post("/schedule-plans/import", json={
    "plans": import_plans,
    "operator_id": dev_id,
    "on_conflict": "skip",
})
check("同名方案 skip 模式跳过", r.status_code == 200)
check("跳过1个", r.json()["skipped"] == 1)

r = client2.post("/schedule-plans/import", json={
    "plans": import_plans,
    "operator_id": dev_id,
    "on_conflict": "overwrite",
})
check("同名方案 overwrite 模式覆盖", r.status_code == 200)
check("覆盖成功1个", r.json()["success"] == 1)

r = client2.post("/schedule-plans/import", json={
    "plans": import_plans,
    "operator_id": dev_id,
    "on_conflict": "error",
})
check("同名方案 error 模式失败", r.status_code == 200)
check("失败1个", r.json()["failed"] == 1)

# ---------- 场景10：取消方案测试 ----------
print("\n--- [场景10] 取消方案测试 ---")
r = client2.post("/schedule-plans", json={
    "name": "待取消方案",
    "template_id": tpl_id,
    "generate_mode": "specific_dates",
    "specific_dates": ["2026-10-01"],
    "creator_id": dev_id,
})
plan3_id = r.json()["id"]

r = client2.post(f"/schedule-plans/{plan3_id}/cancel", params={"operator_id": dev_id})
check("取消草稿方案 200", r.status_code == 200)
check("状态变为 CANCELLED", r.json()["status"] == "CANCELLED")

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
    print("\n  *** 排期方案完整流程测试全部通过 ***")
    sys.exit(0)
else:
    print(f"\n  失败 {len(failed)} 项")
    sys.exit(2)
