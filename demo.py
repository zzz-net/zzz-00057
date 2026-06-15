"""
维护窗口编排 API 综合演示脚本
演示内容：
1. 配置初始化（环境、维护时段、角色、用户）
2. 主流程：草稿 -> 已提交 -> 已批准 -> 执行中 -> 完成 -> 导出
3. 失败链路：
   - 结束时间早于开始时间（直接拦截）
   - 同环境时间重叠（允许SUBMITTED，审批时才冲突）
   - 非审批角色无法批准（403）
4. 回滚/撤销：状态恢复到上一可操作状态（不是独立回滚态），完整历史不抹除
5. 服务重启一致性：关闭数据库后重新连接，验证数据一致
6. 导出隔离：导出产物落到系统临时目录，不污染仓库
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

from pydantic import ValidationError

from app.database import SessionLocal, Base, engine, DB_PATH
from app import models, schemas, services
from app.models import WindowStatus


def separator(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_json(label, obj):
    print(f"\n[{label}]")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"[INIT] Cleared old database: {DB_PATH}")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # ================================================================
    # Part 1: Configuration
    # ================================================================
    separator("[1/6] Configuration - Environments, Slots, Roles, Users")

    env_prod = services.create_environment(db, schemas.EnvironmentCreate(
        name="production",
        description="production env, strict approval required"
    ))
    print(f"[OK] Environment created: {env_prod.name} (id={env_prod.id})")

    env_test = services.create_environment(db, schemas.EnvironmentCreate(
        name="test",
        description="test env"
    ))
    print(f"[OK] Environment created: {env_test.name} (id={env_test.id})")

    slot = services.create_maintenance_slot(db, schemas.MaintenanceSlotCreate(
        environment_id=env_prod.id,
        day_of_week=6,
        start_time="02:00",
        end_time="06:00",
    ))
    print(f"[OK] Maintenance slot: Saturday 02:00-06:00 for {env_prod.name}")

    role_admin = services.create_role(db, schemas.RoleCreate(
        name="ChangeManager",
        can_approve=1,
        description="Change manager with approval permission"
    ))
    print(f"[OK] Role created: {role_admin.name} (can_approve={role_admin.can_approve}, id={role_admin.id})")

    role_dev = services.create_role(db, schemas.RoleCreate(
        name="Developer",
        can_approve=0,
        description="Developer without approval permission"
    ))
    print(f"[OK] Role created: {role_dev.name} (can_approve={role_dev.can_approve}, id={role_dev.id})")

    user_approver = services.create_user(db, schemas.UserCreate(
        username="manager.zhang",
        display_name="Zhang Manager",
        role_id=role_admin.id,
    ))
    print(f"[OK] User created: {user_approver.display_name} ({user_approver.username}) role={role_admin.name}")

    user_dev = services.create_user(db, schemas.UserCreate(
        username="dev.li",
        display_name="Li Developer",
        role_id=role_dev.id,
    ))
    print(f"[OK] User created: {user_dev.display_name} ({user_dev.username}) role={role_dev.name}")

    # ================================================================
    # Part 2: Main Flow
    # ================================================================
    separator("[2/6] Main Flow - Draft -> Submitted -> Approved -> InProgress -> Completed -> Export")

    base_time = datetime(2026, 6, 20, 2, 0, 0)
    start_1 = base_time
    end_1 = base_time + timedelta(hours=2)

    window = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="Production DB upgrade",
        description="Upgrade MySQL 8.0.30 to 8.0.36",
        environment_id=env_prod.id,
        start_time=start_1,
        end_time=end_1,
        creator_id=user_dev.id,
        change_reason="Fix security vulnerability CVE-2024-XXXX",
    ))
    print(f"[OK] [DRAFT] Window created: id={window.id} status={window.status.value}")

    window = services.submit_window(db, window.id, schemas.SubmitRequest(
        operator_id=user_dev.id,
        reason="Change ready, requesting approval"
    ))
    print(f"[OK] [SUBMITTED] Submitted for approval: status={window.status.value}")

    window = services.approve_window(db, window.id, schemas.ApproveRequest(
        operator_id=user_approver.id,
        reason="Risk assessed, approved for maintenance window"
    ))
    print(f"[OK] [APPROVED] Approved by: {window.approver.display_name} status={window.status.value}")

    window = services.start_window(db, window.id, schemas.StartRequest(
        operator_id=user_dev.id,
    ))
    print(f"[OK] [IN_PROGRESS] Execution started: status={window.status.value}")

    window = services.complete_window(db, window.id, schemas.CompleteRequest(
        operator_id=user_dev.id,
    ))
    print(f"[OK] [COMPLETED] Maintenance completed: status={window.status.value}")

    export_data = services.export_window_records(db, window.id)
    print_json(f"Export change record (window_id={window.id})", {
        "title": export_data["title"],
        "status": export_data["status"],
        "environment": export_data["environment"],
        "approver": export_data["approver"],
        "time_range": export_data["time_range"],
        "audit_log_count": len(export_data["audit_logs"]),
        "audit_actions": [log["action"] for log in export_data["audit_logs"]],
    })

    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    export_path = os.path.join(export_dir, f"window_{window.id}_demo.json")
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Export file saved: {export_path}")
    print(f"[OK] Storage: {export_path}")

    verified_window_id = window.id

    # ================================================================
    # Part 3: Failure - end_time before start_time
    # ================================================================
    separator("[3/6] Failure Path 1 - end_time earlier than start_time")

    try:
        bad_window = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
            title="Invalid time window",
            environment_id=env_prod.id,
            start_time=datetime(2026, 6, 21, 10, 0),
            end_time=datetime(2026, 6, 21, 8, 0),
            creator_id=user_dev.id,
        ))
        print("FAIL: Should have been blocked!")
    except (services.BusinessError, ValidationError) as e:
        msg = e.message if isinstance(e, services.BusinessError) else str(e)
        print(f"[OK] Blocked as expected: {msg}")

    # ================================================================
    # Part 4: Failure - Same env time overlap
    # ================================================================
    separator("[4/6] Failure Path 2 - Overlapping window in same environment")

    overlap_base_start = datetime(2026, 6, 27, 2, 0, 0)
    overlap_base_end = datetime(2026, 6, 27, 4, 0, 0)

    window_approved_ref = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="Approved window for overlap test",
        environment_id=env_prod.id,
        start_time=overlap_base_start,
        end_time=overlap_base_end,
        creator_id=user_dev.id,
    ))
    window_approved_ref = services.submit_window(db, window_approved_ref.id, schemas.SubmitRequest(
        operator_id=user_dev.id,
    ))
    window_approved_ref = services.approve_window(db, window_approved_ref.id, schemas.ApproveRequest(
        operator_id=user_approver.id,
        reason="Approved for overlap test"
    ))
    print(f"[OK] Reference window APPROVED: id={window_approved_ref.id} "
          f"time={overlap_base_start.strftime('%H:%M')}-{overlap_base_end.strftime('%H:%M')}")

    overlap_start = overlap_base_start + timedelta(minutes=30)
    overlap_end = overlap_base_end + timedelta(minutes=30)

    window_overlap = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="Overlapping window",
        description="Overlaps with approved window",
        environment_id=env_prod.id,
        start_time=overlap_start,
        end_time=overlap_end,
        creator_id=user_dev.id,
    ))
    print(f"[OK] Draft created: id={window_overlap.id} status={window_overlap.status.value}")

    window_overlap = services.submit_window(db, window_overlap.id, schemas.SubmitRequest(
        operator_id=user_dev.id,
    ))
    print(f"[OK] Overlap window SUBMITTED: status={window_overlap.status.value} (submit no longer blocks)")

    try:
        services.approve_window(db, window_overlap.id, schemas.ApproveRequest(
            operator_id=user_approver.id,
        ))
        print("FAIL: Should have been blocked on approve!")
    except services.BusinessError as e:
        print(f"[OK] Overlap blocked on APPROVE (correct behavior): {e.message}")

    # ================================================================
    # Part 5: Failure - Non-approver cannot approve
    # ================================================================
    separator("[5/6] Failure Path 3 - Non-approver role cannot approve")

    window_no_approve = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="Test non-approver",
        environment_id=env_test.id,
        start_time=datetime(2026, 7, 1, 10, 0),
        end_time=datetime(2026, 7, 1, 12, 0),
        creator_id=user_dev.id,
    ))
    window_no_approve = services.submit_window(db, window_no_approve.id, schemas.SubmitRequest(
        operator_id=user_dev.id,
    ))
    print(f"[OK] Window submitted: id={window_no_approve.id}")

    try:
        services.approve_window(db, window_no_approve.id, schemas.ApproveRequest(
            operator_id=user_dev.id,
            reason="Self-approval attempt"
        ))
        print("FAIL: Should have been blocked!")
    except services.BusinessError as e:
        print(f"[OK] Non-approver blocked: {e.message} (code=403)")

    # ================================================================
    # Part 6: Rollback + Restart Consistency
    # ================================================================
    separator("[6/6] Rollback + Restart Data Consistency")

    window_rollback = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="Window for rollback test",
        environment_id=env_test.id,
        start_time=datetime(2026, 7, 5, 14, 0),
        end_time=datetime(2026, 7, 5, 16, 0),
        creator_id=user_dev.id,
    ))
    window_rollback = services.submit_window(db, window_rollback.id, schemas.SubmitRequest(
        operator_id=user_dev.id,
    ))
    window_rollback = services.approve_window(db, window_rollback.id, schemas.ApproveRequest(
        operator_id=user_approver.id,
    ))
    window_rollback = services.start_window(db, window_rollback.id, schemas.StartRequest(
        operator_id=user_dev.id,
    ))
    print(f"[OK] Window id={window_rollback.id} status={window_rollback.status.value}")

    window_rollback = services.rollback_window(db, window_rollback.id, schemas.RollbackRequest(
        operator_id=user_approver.id,
        reason="Compatibility issue found, rollback required",
    ))
    print(f"[OK] Rollback executed: status={window_rollback.status.value}, note={window_rollback.rollback_note}")

    rollback_export = services.export_window_records(db, window_rollback.id)
    print_json("Audit logs after rollback (history preserved)", [
        {
            "action": log["action"],
            "from": log["from_status"],
            "to": log["to_status"],
            "operator": log["operator_name"],
            "reason": log["reason"],
        } for log in rollback_export["audit_logs"]
    ])
    print(f"[OK] {len(rollback_export['audit_logs'])} audit log entries preserved after rollback")

    # ---- Simulate service restart ----
    print("\n--- Simulating service restart: closing DB connection ---")
    db.close()
    engine.dispose()

    print("--- Reconnecting to database and loading data ---")
    Base.metadata.create_all(bind=engine)
    db2 = SessionLocal()

    reloaded = services.export_window_records(db2, verified_window_id)
    print_json("Reloaded main flow window (consistency check)", {
        "window_id": reloaded["window_id"],
        "title": reloaded["title"],
        "status": reloaded["status"],
        "environment_name": reloaded["environment"]["name"],
        "approver_name": reloaded["approver"]["display_name"],
        "approval_reason": reloaded["approval_reason"],
        "change_reason": reloaded["change_reason"],
        "time_range": reloaded["time_range"],
        "audit_log_count": len(reloaded["audit_logs"]),
    })

    reloaded_rollback = services.export_window_records(db2, window_rollback.id)

    passed = True
    checks = [
        (reloaded_rollback["status"] == "APPROVED", "Rollback status inconsistent after restart (should be APPROVED, not ROLLED_BACK)"),
        (reloaded_rollback["rollback_note"] is not None, "Rollback note lost after restart"),
        (reloaded["status"] == "COMPLETED", "Main flow status inconsistent after restart"),
        (reloaded["approver"]["display_name"] == "Zhang Manager", "Approver name inconsistent after restart"),
        (reloaded["environment"]["name"] == "production", "Environment name inconsistent after restart"),
        (reloaded["approval_reason"] == "Risk assessed, approved for maintenance window", "Approval reason inconsistent"),
        (reloaded["change_reason"] == "Fix security vulnerability CVE-2024-XXXX", "Change reason inconsistent"),
    ]

    for condition, desc in checks:
        if not condition:
            print(f"[FAIL] {desc}")
            passed = False

    if passed:
        print("\n[OK] ALL consistency checks PASSED!")
        print("[OK] After restart: approver, environment, status, remarks all consistent with DB")
    else:
        print("\n[FAIL] Some consistency checks FAILED!")

    print(f"[OK] Database file: {DB_PATH}")

    db2.close()

    print("\n" + "=" * 80)
    print("  ALL DEMOS COMPLETED [OK]")
    print("=" * 80)


if __name__ == "__main__":
    main()
