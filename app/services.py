from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime
import json
from typing import List, Optional

from app import models, schemas
from app.models import WindowStatus, AuditAction


class BusinessError(Exception):
    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code


# ============== Environment ==============

def create_environment(db: Session, env_in: schemas.EnvironmentCreate) -> models.Environment:
    existing = db.query(models.Environment).filter(models.Environment.name == env_in.name).first()
    if existing:
        raise BusinessError(f"环境名称 '{env_in.name}' 已存在")
    db_env = models.Environment(**env_in.model_dump())
    db.add(db_env)
    db.commit()
    db.refresh(db_env)
    return db_env


def get_environment(db: Session, env_id: int) -> Optional[models.Environment]:
    return db.query(models.Environment).filter(models.Environment.id == env_id).first()


def get_environment_by_name(db: Session, name: str) -> Optional[models.Environment]:
    return db.query(models.Environment).filter(models.Environment.name == name).first()


def list_environments(db: Session) -> List[models.Environment]:
    return db.query(models.Environment).all()


def update_environment(db: Session, env_id: int, env_in: schemas.EnvironmentUpdate) -> models.Environment:
    db_env = get_environment(db, env_id)
    if not db_env:
        raise BusinessError(f"环境 ID={env_id} 不存在", 404)
    update_data = env_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(db_env, k, v)
    db_env.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_env)
    return db_env


def delete_environment(db: Session, env_id: int) -> None:
    db_env = get_environment(db, env_id)
    if not db_env:
        raise BusinessError(f"环境 ID={env_id} 不存在", 404)
    windows_count = db.query(models.MaintenanceWindow).filter(
        models.MaintenanceWindow.environment_id == env_id
    ).count()
    if windows_count > 0:
        raise BusinessError("该环境下存在维护窗口，无法删除")
    db.delete(db_env)
    db.commit()


# ============== Maintenance Slot ==============

def create_maintenance_slot(db: Session, slot_in: schemas.MaintenanceSlotCreate) -> models.MaintenanceSlot:
    env = get_environment(db, slot_in.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={slot_in.environment_id} 不存在", 404)
    db_slot = models.MaintenanceSlot(**slot_in.model_dump())
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return db_slot


def list_maintenance_slots(db: Session, environment_id: Optional[int] = None) -> List[models.MaintenanceSlot]:
    q = db.query(models.MaintenanceSlot)
    if environment_id is not None:
        q = q.filter(models.MaintenanceSlot.environment_id == environment_id)
    return q.all()


def delete_maintenance_slot(db: Session, slot_id: int) -> None:
    db_slot = db.query(models.MaintenanceSlot).filter(models.MaintenanceSlot.id == slot_id).first()
    if not db_slot:
        raise BusinessError(f"维护时段 ID={slot_id} 不存在", 404)
    db.delete(db_slot)
    db.commit()


# ============== Role ==============

def create_role(db: Session, role_in: schemas.RoleCreate) -> models.Role:
    existing = db.query(models.Role).filter(models.Role.name == role_in.name).first()
    if existing:
        raise BusinessError(f"角色名称 '{role_in.name}' 已存在")
    db_role = models.Role(**role_in.model_dump())
    db.add(db_role)
    db.commit()
    db.refresh(db_role)
    return db_role


def get_role(db: Session, role_id: int) -> Optional[models.Role]:
    return db.query(models.Role).filter(models.Role.id == role_id).first()


def list_roles(db: Session) -> List[models.Role]:
    return db.query(models.Role).all()


def update_role(db: Session, role_id: int, role_in: schemas.RoleUpdate) -> models.Role:
    db_role = get_role(db, role_id)
    if not db_role:
        raise BusinessError(f"角色 ID={role_id} 不存在", 404)
    update_data = role_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(db_role, k, v)
    db.commit()
    db.refresh(db_role)
    return db_role


def delete_role(db: Session, role_id: int) -> None:
    db_role = get_role(db, role_id)
    if not db_role:
        raise BusinessError(f"角色 ID={role_id} 不存在", 404)
    users_count = db.query(models.User).filter(models.User.role_id == role_id).count()
    if users_count > 0:
        raise BusinessError("该角色下存在用户，无法删除")
    db.delete(db_role)
    db.commit()


# ============== User ==============

def create_user(db: Session, user_in: schemas.UserCreate) -> models.User:
    existing = db.query(models.User).filter(models.User.username == user_in.username).first()
    if existing:
        raise BusinessError(f"用户名 '{user_in.username}' 已存在")
    role = get_role(db, user_in.role_id)
    if not role:
        raise BusinessError(f"角色 ID={user_in.role_id} 不存在", 404)
    db_user = models.User(**user_in.model_dump())
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_user(db: Session, user_id: int) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.id == user_id).first()


def list_users(db: Session) -> List[models.User]:
    return db.query(models.User).all()


def user_can_approve(db: Session, user_id: int) -> bool:
    user = get_user(db, user_id)
    if not user:
        return False
    return user.role and user.role.can_approve == 1


# ============== Audit Log Helper ==============

def _window_snapshot(window: models.MaintenanceWindow) -> str:
    return json.dumps({
        "id": window.id,
        "title": window.title,
        "description": window.description,
        "environment_id": window.environment_id,
        "start_time": window.start_time.isoformat() if window.start_time else None,
        "end_time": window.end_time.isoformat() if window.end_time else None,
        "status": window.status.value if window.status else None,
        "creator_id": window.creator_id,
        "approver_id": window.approver_id,
        "approval_reason": window.approval_reason,
        "change_reason": window.change_reason,
        "rollback_note": window.rollback_note,
    }, ensure_ascii=False)


def _add_audit_log(
    db: Session,
    window: models.MaintenanceWindow,
    action: AuditAction,
    operator_id: int,
    from_status: Optional[WindowStatus] = None,
    to_status: Optional[WindowStatus] = None,
    reason: Optional[str] = None,
):
    log = models.AuditLog(
        window_id=window.id,
        action=action,
        operator_id=operator_id,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        snapshot=_window_snapshot(window),
    )
    db.add(log)


# ============== Conflict Detection ==============

def check_time_overlap(
    db: Session,
    environment_id: int,
    start_time: datetime,
    end_time: datetime,
    exclude_window_id: Optional[int] = None,
) -> List[models.MaintenanceWindow]:
    active_statuses = [WindowStatus.SUBMITTED, WindowStatus.APPROVED, WindowStatus.IN_PROGRESS]
    q = db.query(models.MaintenanceWindow).filter(
        models.MaintenanceWindow.environment_id == environment_id,
        models.MaintenanceWindow.status.in_(active_statuses),
        or_(
            and_(
                models.MaintenanceWindow.start_time < end_time,
                models.MaintenanceWindow.end_time > start_time,
            )
        ),
    )
    if exclude_window_id is not None:
        q = q.filter(models.MaintenanceWindow.id != exclude_window_id)
    return q.all()


# ============== Maintenance Window Core ==============

def _validate_time_range(start_time: datetime, end_time: datetime):
    if end_time <= start_time:
        raise BusinessError("结束时间必须晚于开始时间")


def create_maintenance_window(
    db: Session, win_in: schemas.MaintenanceWindowCreate
) -> models.MaintenanceWindow:
    _validate_time_range(win_in.start_time, win_in.end_time)
    env = get_environment(db, win_in.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={win_in.environment_id} 不存在", 404)
    creator = get_user(db, win_in.creator_id)
    if not creator:
        raise BusinessError(f"创建人 ID={win_in.creator_id} 不存在", 404)

    db_win = models.MaintenanceWindow(**win_in.model_dump(), status=WindowStatus.DRAFT)
    db.add(db_win)
    db.flush()
    _add_audit_log(
        db, db_win, AuditAction.CREATE, win_in.creator_id,
        to_status=WindowStatus.DRAFT, reason=win_in.change_reason or "创建维护窗口",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def get_maintenance_window(db: Session, win_id: int) -> Optional[models.MaintenanceWindow]:
    return db.query(models.MaintenanceWindow).filter(models.MaintenanceWindow.id == win_id).first()


def list_maintenance_windows(
    db: Session,
    environment_id: Optional[int] = None,
    status: Optional[WindowStatus] = None,
) -> List[models.MaintenanceWindow]:
    q = db.query(models.MaintenanceWindow)
    if environment_id is not None:
        q = q.filter(models.MaintenanceWindow.environment_id == environment_id)
    if status is not None:
        q = q.filter(models.MaintenanceWindow.status == status)
    return q.order_by(models.MaintenanceWindow.created_at.desc()).all()


def update_maintenance_window(
    db: Session, win_id: int, win_in: schemas.MaintenanceWindowUpdate, operator_id: int
) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status != WindowStatus.DRAFT:
        raise BusinessError("仅草稿状态可以修改")

    update_data = win_in.model_dump(exclude_unset=True)
    new_start = update_data.get("start_time", db_win.start_time)
    new_end = update_data.get("end_time", db_win.end_time)
    _validate_time_range(new_start, new_end)

    if "start_time" in update_data or "end_time" in update_data or "environment_id" in update_data:
        env_id = update_data.get("environment_id", db_win.environment_id)
        overlaps = check_time_overlap(db, env_id, new_start, new_end, exclude_window_id=win_id)
        if overlaps:
            titles = ", ".join([w.title for w in overlaps])
            raise BusinessError(f"同一环境下存在重叠的已审批窗口: {titles}")

    for k, v in update_data.items():
        setattr(db_win, k, v)
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.UPDATE, operator_id,
        from_status=db_win.status, to_status=db_win.status,
        reason=update_data.get("change_reason") or "更新维护窗口内容",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def submit_window(db: Session, win_id: int, req: schemas.SubmitRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status != WindowStatus.DRAFT:
        raise BusinessError("仅草稿状态可以提交审批")

    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    _validate_time_range(db_win.start_time, db_win.end_time)

    overlaps = check_time_overlap(db, db_win.environment_id, db_win.start_time, db_win.end_time, exclude_window_id=win_id)
    if overlaps:
        titles = ", ".join([w.title for w in overlaps])
        raise BusinessError(f"同一环境下存在重叠的已审批/待审批窗口: {titles}")

    old_status = db_win.status
    db_win.status = WindowStatus.SUBMITTED
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.SUBMIT, req.operator_id,
        from_status=old_status, to_status=WindowStatus.SUBMITTED,
        reason=req.reason or "提交审批",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def approve_window(db: Session, win_id: int, req: schemas.ApproveRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status != WindowStatus.SUBMITTED:
        raise BusinessError("仅已提交状态可以审批")

    if not user_can_approve(db, req.operator_id):
        raise BusinessError("当前用户无审批权限", 403)

    approver = get_user(db, req.operator_id)
    if not approver:
        raise BusinessError(f"审批人 ID={req.operator_id} 不存在", 404)

    _validate_time_range(db_win.start_time, db_win.end_time)

    overlaps = check_time_overlap(db, db_win.environment_id, db_win.start_time, db_win.end_time, exclude_window_id=win_id)
    if overlaps:
        titles = ", ".join([w.title for w in overlaps])
        raise BusinessError(f"审批冲突：同一环境下存在重叠的已生效窗口: {titles}")

    old_status = db_win.status
    db_win.status = WindowStatus.APPROVED
    db_win.approver_id = req.operator_id
    db_win.approval_reason = req.reason
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.APPROVE, req.operator_id,
        from_status=old_status, to_status=WindowStatus.APPROVED,
        reason=req.reason or "审批通过",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def start_window(db: Session, win_id: int, req: schemas.StartRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status != WindowStatus.APPROVED:
        raise BusinessError("仅已批准状态可以开始执行")
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    old_status = db_win.status
    db_win.status = WindowStatus.IN_PROGRESS
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.START, req.operator_id,
        from_status=old_status, to_status=WindowStatus.IN_PROGRESS,
        reason="开始执行维护",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def complete_window(db: Session, win_id: int, req: schemas.CompleteRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status != WindowStatus.IN_PROGRESS:
        raise BusinessError("仅执行中状态可以完成")
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    old_status = db_win.status
    db_win.status = WindowStatus.COMPLETED
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.COMPLETE, req.operator_id,
        from_status=old_status, to_status=WindowStatus.COMPLETED,
        reason="维护完成",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def withdraw_window(db: Session, win_id: int, req: schemas.WithdrawRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status not in [WindowStatus.DRAFT, WindowStatus.SUBMITTED, WindowStatus.APPROVED]:
        raise BusinessError("仅草稿、已提交或已批准状态可以撤回")
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    old_status = db_win.status
    db_win.status = WindowStatus.WITHDRAWN
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.WITHDRAW, req.operator_id,
        from_status=old_status, to_status=WindowStatus.WITHDRAWN,
        reason=req.reason or "撤回申请",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


def rollback_window(db: Session, win_id: int, req: schemas.RollbackRequest) -> models.MaintenanceWindow:
    db_win = get_maintenance_window(db, win_id)
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)
    if db_win.status not in [WindowStatus.COMPLETED, WindowStatus.IN_PROGRESS, WindowStatus.APPROVED]:
        raise BusinessError("仅已批准、执行中或已完成状态可以回滚")
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    old_status = db_win.status
    db_win.status = WindowStatus.ROLLED_BACK
    db_win.rollback_note = req.reason
    db_win.updated_at = datetime.utcnow()

    _add_audit_log(
        db, db_win, AuditAction.ROLLBACK, req.operator_id,
        from_status=old_status, to_status=WindowStatus.ROLLED_BACK,
        reason=req.reason or "执行回滚",
    )
    db.commit()
    db.refresh(db_win)
    return db_win


# ============== Export ==============

def export_window_records(db: Session, win_id: int) -> dict:
    db_win = db.query(models.MaintenanceWindow).filter(
        models.MaintenanceWindow.id == win_id
    ).first()
    if not db_win:
        raise BusinessError(f"维护窗口 ID={win_id} 不存在", 404)

    env = db_win.environment
    creator = db_win.creator
    approver = db_win.approver

    audit_records = []
    for log in db_win.audit_logs:
        operator = log.operator
        audit_records.append({
            "id": log.id,
            "action": log.action.value if log.action else None,
            "operator_id": log.operator_id,
            "operator_name": operator.display_name if operator else None,
            "operator_username": operator.username if operator else None,
            "from_status": log.from_status.value if log.from_status else None,
            "to_status": log.to_status.value if log.to_status else None,
            "reason": log.reason,
            "snapshot": json.loads(log.snapshot) if log.snapshot else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {
        "window_id": db_win.id,
        "title": db_win.title,
        "description": db_win.description,
        "status": db_win.status.value if db_win.status else None,
        "environment": {
            "id": env.id if env else None,
            "name": env.name if env else None,
            "description": env.description if env else None,
        },
        "time_range": {
            "start_time": db_win.start_time.isoformat() if db_win.start_time else None,
            "end_time": db_win.end_time.isoformat() if db_win.end_time else None,
        },
        "creator": {
            "id": creator.id if creator else None,
            "username": creator.username if creator else None,
            "display_name": creator.display_name if creator else None,
        },
        "approver": {
            "id": approver.id if approver else None,
            "username": approver.username if approver else None,
            "display_name": approver.display_name if approver else None,
        },
        "approval_reason": db_win.approval_reason,
        "change_reason": db_win.change_reason,
        "rollback_note": db_win.rollback_note,
        "created_at": db_win.created_at.isoformat() if db_win.created_at else None,
        "updated_at": db_win.updated_at.isoformat() if db_win.updated_at else None,
        "audit_logs": audit_records,
    }
