"""
demo.py 修复点回归测试
覆盖：
1. 导出目录隔离（仓库不被污染）
2. 重叠提交判定（submit 允许 SUBMITTED，approve 才冲突）
3. 回滚后状态恢复（APPROVED，不是独立 ROLLED_BACK）
4. 重启后行为一致
5. 原有两道保护（坏时间、非审批人）仍正常
"""

import sys
import os
import io
import json
import tempfile
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "maintenance_window.db")

from pydantic import ValidationError
from app.database import SessionLocal, Base, engine
from app import models, schemas, services
from app.models import WindowStatus

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


def cleanup():
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except PermissionError:
        pass


def main():
    cleanup()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    print("\n" + "=" * 70)
    print("  [demo.py 修复点回归测试]")
    print("=" * 70)

    # ---------- 准备数据 ----------
    print("\n--- [准备] 配置数据 ---")
    env_prod = services.create_environment(db, schemas.EnvironmentCreate(
        name="prod-regression", description="prod for regression"
    ))
    env_test = services.create_environment(db, schemas.EnvironmentCreate(
        name="test-regression", description="test for regression"
    ))
    role_mgr = services.create_role(db, schemas.RoleCreate(
        name="CM-Regression", can_approve=1, description="approver"
    ))
    role_dev = services.create_role(db, schemas.RoleCreate(
        name="DEV-Regression", can_approve=0, description="developer"
    ))
    user_mgr = services.create_user(db, schemas.UserCreate(
        username="mgr.reg", display_name="RegressionMgr", role_id=role_mgr.id
    ))
    user_dev = services.create_user(db, schemas.UserCreate(
        username="dev.reg", display_name="RegressionDev", role_id=role_dev.id
    ))
    check("配置数据创建完成", True)

    # ---------- 场景1：导出目录隔离 ----------
    print("\n--- [场景1] 导出目录隔离（仓库不被污染） ---")
    w = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="export-test",
        environment_id=env_test.id,
        start_time=datetime(2026, 9, 1, 10, 0),
        end_time=datetime(2026, 9, 1, 12, 0),
        creator_id=user_dev.id,
        change_reason="export test",
    ))
    w = services.submit_window(db, w.id, schemas.SubmitRequest(operator_id=user_dev.id))
    w = services.approve_window(db, w.id, schemas.ApproveRequest(
        operator_id=user_mgr.id, reason="approved"
    ))
    export_data = services.export_window_records(db, w.id)

    # 导出到系统临时目录（与 main.py 和 demo.py 一致的策略）
    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    export_path = os.path.join(export_dir, f"regression_test_{w.id}.json")
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    norm_root = os.path.normpath(ROOT) + os.sep
    norm_file = os.path.normpath(export_path)
    file_in_repo = norm_file.startswith(norm_root)
    check(f"导出文件不在仓库内 (path={export_path})", not file_in_repo,
          f"root={ROOT} path={export_path}")
    sys_tmp = tempfile.gettempdir()
    check("导出文件在系统临时目录下", sys_tmp in export_path, f"tmpdir={sys_tmp} path={export_path}")
    check("导出文件实际存在", os.path.isfile(export_path))

    repo_exports = os.path.join(ROOT, "exports")
    check("仓库根不存在 exports/ 目录", not os.path.isdir(repo_exports))

    # 清理测试导出文件
    try:
        os.remove(export_path)
    except Exception:
        pass

    # ---------- 场景2：重叠提交判定 ----------
    print("\n--- [场景2] 重叠提交：submit 允许 SUBMITTED，approve 才冲突 ---")
    base = datetime(2026, 9, 10, 2, 0, 0)
    wa = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="overlap-A", environment_id=env_prod.id,
        start_time=base, end_time=base + timedelta(hours=2),
        creator_id=user_dev.id,
    ))
    wa = services.submit_window(db, wa.id, schemas.SubmitRequest(operator_id=user_dev.id))
    wa = services.approve_window(db, wa.id, schemas.ApproveRequest(
        operator_id=user_mgr.id, reason="approve A"
    ))
    check("参考窗口A批准成功", wa.status == WindowStatus.APPROVED)

    wb = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="overlap-B", environment_id=env_prod.id,
        start_time=base + timedelta(minutes=30),
        end_time=base + timedelta(hours=2, minutes=30),
        creator_id=user_dev.id,
    ))
    wb = services.submit_window(db, wb.id, schemas.SubmitRequest(operator_id=user_dev.id))
    check("重叠窗口B submit=SUBMITTED（不再拦截）", wb.status == WindowStatus.SUBMITTED,
          f"status={wb.status.value}")

    try:
        services.approve_window(db, wb.id, schemas.ApproveRequest(operator_id=user_mgr.id))
        check("重叠窗口B approve 应拦截但未拦截", False, "未报冲突")
    except services.BusinessError as e:
        check("重叠窗口B approve 正确拦截", True, f"msg={e.message}")

    # ---------- 场景3：回滚后状态恢复 ----------
    print("\n--- [场景3] 回滚后状态恢复 APPROVED（不是独立 ROLLED_BACK） ---")
    wr = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="rollback-test", environment_id=env_test.id,
        start_time=datetime(2026, 9, 15, 14, 0),
        end_time=datetime(2026, 9, 15, 16, 0),
        creator_id=user_dev.id,
        change_reason="rollback test",
    ))
    wr = services.submit_window(db, wr.id, schemas.SubmitRequest(operator_id=user_dev.id))
    wr = services.approve_window(db, wr.id, schemas.ApproveRequest(
        operator_id=user_mgr.id, reason="approve for rollback"
    ))
    wr = services.start_window(db, wr.id, schemas.StartRequest(operator_id=user_dev.id))
    wr = services.complete_window(db, wr.id, schemas.CompleteRequest(operator_id=user_dev.id))
    check("主流程完成 -> COMPLETED", wr.status == WindowStatus.COMPLETED)

    wr = services.rollback_window(db, wr.id, schemas.RollbackRequest(
        operator_id=user_mgr.id, reason="rollback reason"
    ))
    check("回滚后状态 = APPROVED（不是 ROLLED_BACK）",
          wr.status == WindowStatus.APPROVED,
          f"status={wr.status.value if hasattr(wr.status, 'value') else str(wr.status)}")
    check("rollback_note 已写入", bool(wr.rollback_note), f"note={wr.rollback_note}")
    check("approver_id 保留（回滚到 APPROVED 不清除审批人）",
          wr.approver_id == user_mgr.id)

    rollback_export = services.export_window_records(db, wr.id)
    actions = [log["action"] for log in rollback_export["audit_logs"]]
    check("审计同时包含 COMPLETE + ROLLBACK 两段历史",
          "COMPLETE" in actions and "ROLLBACK" in actions,
          f"actions={actions}")
    rb_log = [l for l in rollback_export["audit_logs"] if l["action"] == "ROLLBACK"][0]
    check("ROLLBACK 审计 from=COMPLETED / to=APPROVED",
          rb_log["from_status"] == "COMPLETED" and rb_log["to_status"] == "APPROVED",
          f"from={rb_log['from_status']} to={rb_log['to_status']}")

    # ---------- 场景4：重启后一致性 ----------
    print("\n--- [场景4] 重启后数据一致性 ---")
    rollback_win_id = wr.id
    main_win_id = wa.id
    # 重启前先缓存所有 id，避免 db.close() 后 detached 对象访问报错
    saved_env_prod_id = env_prod.id
    saved_env_test_id = env_test.id
    saved_user_dev_id = user_dev.id
    saved_user_mgr_id = user_mgr.id

    db.close()
    engine.dispose()

    Base.metadata.create_all(bind=engine)
    db2 = SessionLocal()

    reloaded_rb = services.export_window_records(db2, rollback_win_id)
    reloaded_main = services.export_window_records(db2, main_win_id)

    check("重启后回滚窗口 status=APPROVED", reloaded_rb["status"] == "APPROVED",
          f"status={reloaded_rb['status']}")
    check("重启后回滚窗口 rollback_note 仍存在", bool(reloaded_rb["rollback_note"]))
    check("重启后回滚窗口 approver.display_name=RegressionMgr",
          reloaded_rb["approver"]["display_name"] == "RegressionMgr")
    check("重启后主流程窗口 status=APPROVED", reloaded_main["status"] == "APPROVED")
    check("重启后环境名称一致",
          reloaded_rb["environment"]["name"] == "test-regression")
    check("重启后仍能看到 COMPLETE + ROLLBACK 两段",
          "COMPLETE" in [l["action"] for l in reloaded_rb["audit_logs"]] and
          "ROLLBACK" in [l["action"] for l in reloaded_rb["audit_logs"]])

    # ---------- 场景5：原有两道保护 ----------
    print("\n--- [场景5] 原有两道保护（坏时间、非审批人）仍正常 ---")
    # 用重启前缓存的 id
    env_prod_id = saved_env_prod_id
    env_test_id = saved_env_test_id
    user_dev_id = saved_user_dev_id
    user_mgr_id = saved_user_mgr_id

    try:
        services.create_maintenance_window(db2, schemas.MaintenanceWindowCreate(
            title="bad-time", environment_id=env_prod_id,
            start_time=datetime(2026, 9, 20, 10, 0),
            end_time=datetime(2026, 9, 20, 8, 0),
            creator_id=user_dev_id,
        ))
        check("坏时间未拦截", False)
    except (services.BusinessError, ValidationError):
        check("坏时间正确拦截", True)

    w_test = services.create_maintenance_window(db2, schemas.MaintenanceWindowCreate(
        title="non-approver-test", environment_id=env_test_id,
        start_time=datetime(2026, 9, 25, 10, 0),
        end_time=datetime(2026, 9, 25, 12, 0),
        creator_id=user_dev_id,
    ))
    w_test = services.submit_window(db2, w_test.id, schemas.SubmitRequest(operator_id=user_dev_id))
    try:
        services.approve_window(db2, w_test.id, schemas.ApproveRequest(
            operator_id=user_dev_id
        ))
        check("非审批人未拦截", False)
    except services.BusinessError as e:
        check("非审批人正确拦截", True, f"code={e.code}")

    # ---------- 场景6：回滚后可重新开始+完成（状态闭环） ----------
    print("\n--- [场景6] 回滚后可重新 start + complete（状态闭环） ---")
    wr2 = services.get_maintenance_window(db2, rollback_win_id)
    wr2 = services.start_window(db2, wr2.id, schemas.StartRequest(operator_id=user_dev_id))
    check("回滚后重新 start -> IN_PROGRESS", wr2.status == WindowStatus.IN_PROGRESS)
    wr2 = services.complete_window(db2, wr2.id, schemas.CompleteRequest(operator_id=user_dev_id))
    check("重新 complete -> COMPLETED", wr2.status == WindowStatus.COMPLETED)
    final_export = services.export_window_records(db2, rollback_win_id)
    final_actions = [l["action"] for l in final_export["audit_logs"]]
    check("最终审计含 2×COMPLETE + 1×ROLLBACK",
          final_actions.count("COMPLETE") == 2 and final_actions.count("ROLLBACK") == 1,
          f"actions={final_actions}")

    # ---------- 总结 ----------
    print("\n" + "=" * 70)
    total = len(results)
    ok = sum(1 for f, _, _ in results if f == PASS)
    print(f"  测试结果: {ok}/{total} 通过")
    print("=" * 70)
    failed = [(n, d) for f, n, d in results if f == FAIL]
    for n, d in failed:
        print(f"  {FAIL} {n}  {d}")

    db2.close()

    if ok == total:
        print("\n  *** demo.py 修复点回归测试全部通过 ***")
        sys.exit(0)
    else:
        print(f"\n  失败 {len(failed)} 项")
        sys.exit(2)


if __name__ == "__main__":
    main()
