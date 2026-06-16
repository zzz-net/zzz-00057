from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, date, timedelta
import json
from typing import List, Optional

from app import models, schemas
from app.models import WindowStatus, AuditAction, TemplateAction, ConflictType


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

    check_window_freeze_and_raise(
        db, win_in.environment_id, win_in.start_time, win_in.end_time,
        models.FreezeRuleScope.CREATE, win_in.creator_id,
    )

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

    check_window_freeze_and_raise(
        db, db_win.environment_id, db_win.start_time, db_win.end_time,
        models.FreezeRuleScope.SUBMIT, req.operator_id, window_id=db_win.id,
    )

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

    delegation = can_user_act_as_approver(db, req.operator_id, db_win.environment_id, "WINDOW_APPROVE")
    if not user_can_approve(db, req.operator_id) and not delegation.is_delegated:
        raise BusinessError("当前用户无审批权限", 403)

    approver = get_user(db, req.operator_id)
    if not approver:
        raise BusinessError(f"审批人 ID={req.operator_id} 不存在", 404)

    _validate_time_range(db_win.start_time, db_win.end_time)

    check_window_freeze_and_raise(
        db, db_win.environment_id, db_win.start_time, db_win.end_time,
        models.FreezeRuleScope.APPROVE, req.operator_id, window_id=db_win.id,
    )

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

    _ROLLBACK_TARGET = {
        WindowStatus.COMPLETED: WindowStatus.APPROVED,
        WindowStatus.IN_PROGRESS: WindowStatus.APPROVED,
        WindowStatus.APPROVED: WindowStatus.SUBMITTED,
    }
    old_status = db_win.status
    target_status = _ROLLBACK_TARGET[old_status]

    db_win.status = target_status
    db_win.rollback_note = req.reason
    db_win.updated_at = datetime.utcnow()
    if target_status != WindowStatus.APPROVED:
        db_win.approver_id = None
        db_win.approval_reason = None

    _add_audit_log(
        db, db_win, AuditAction.ROLLBACK, req.operator_id,
        from_status=old_status, to_status=target_status,
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


# ============== Window Template ==============

def _template_snapshot(template: models.WindowTemplate) -> str:
    return json.dumps({
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "environment_id": template.environment_id,
        "start_time": template.start_time,
        "end_time": template.end_time,
        "change_reason": template.change_reason,
        "is_shared": template.is_shared,
        "creator_id": template.creator_id,
    }, ensure_ascii=False)


def _add_template_audit_log(
    db: Session,
    template: models.WindowTemplate,
    action: TemplateAction,
    operator_id: int,
    detail: Optional[str] = None,
):
    log = models.TemplateAuditLog(
        template_id=template.id,
        action=action,
        operator_id=operator_id,
        detail=detail,
        snapshot=_template_snapshot(template),
    )
    db.add(log)


def _validate_template_time(start_time: str, end_time: str):
    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        if end_minutes <= start_minutes:
            raise BusinessError("模板结束时间必须晚于开始时间")
    except ValueError:
        raise BusinessError("时间格式不正确，应为 HH:MM")


def _check_template_permission(
    db: Session,
    template: models.WindowTemplate,
    operator_id: int,
    action: str = "modify",
):
    operator = get_user(db, operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={operator_id} 不存在", 404)
    
    is_owner = template.creator_id == operator_id
    is_approver = user_can_approve(db, operator_id)
    is_shared = template.is_shared == 1
    
    if is_owner:
        return True
    
    if is_shared:
        if action == "view":
            return True
        if action == "use":
            return True
        if is_approver:
            return True
        raise BusinessError(
            f"无权限{action_description(action)}该共享模板（非审批角色不能修改他人共享模板）",
            403,
        )
    
    raise BusinessError(f"无权限{action_description(action)}该私有模板", 403)


def action_description(action: str) -> str:
    mapping = {
        "view": "查看",
        "use": "使用",
        "modify": "修改",
        "delete": "删除",
        "share": "分享",
    }
    return mapping.get(action, action)


def create_window_template(
    db: Session, tpl_in: schemas.WindowTemplateCreate
) -> models.WindowTemplate:
    _validate_template_time(tpl_in.start_time, tpl_in.end_time)
    
    existing = db.query(models.WindowTemplate).filter(
        models.WindowTemplate.name == tpl_in.name,
        models.WindowTemplate.creator_id == tpl_in.creator_id,
    ).first()
    if existing:
        raise BusinessError(f"模板名称 '{tpl_in.name}' 已存在")
    
    env = get_environment(db, tpl_in.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={tpl_in.environment_id} 不存在", 404)
    
    creator = get_user(db, tpl_in.creator_id)
    if not creator:
        raise BusinessError(f"创建人 ID={tpl_in.creator_id} 不存在", 404)
    
    db_tpl = models.WindowTemplate(**tpl_in.model_dump())
    db.add(db_tpl)
    db.flush()
    
    _add_template_audit_log(
        db, db_tpl, TemplateAction.TEMPLATE_CREATE, tpl_in.creator_id,
        detail=f"创建模板: {tpl_in.name}",
    )
    
    db.commit()
    db.refresh(db_tpl)
    return db_tpl


def get_window_template(db: Session, tpl_id: int) -> Optional[models.WindowTemplate]:
    return db.query(models.WindowTemplate).filter(models.WindowTemplate.id == tpl_id).first()


def list_window_templates(
    db: Session,
    user_id: Optional[int] = None,
    environment_id: Optional[int] = None,
    is_shared: Optional[int] = None,
) -> List[models.WindowTemplate]:
    q = db.query(models.WindowTemplate)
    if user_id is not None:
        q = q.filter(
            or_(
                models.WindowTemplate.creator_id == user_id,
                models.WindowTemplate.is_shared == 1,
            )
        )
    if environment_id is not None:
        q = q.filter(models.WindowTemplate.environment_id == environment_id)
    if is_shared is not None:
        q = q.filter(models.WindowTemplate.is_shared == is_shared)
    return q.order_by(models.WindowTemplate.updated_at.desc()).all()


def update_window_template(
    db: Session, tpl_id: int, tpl_in: schemas.WindowTemplateUpdate, operator_id: int
) -> models.WindowTemplate:
    db_tpl = get_window_template(db, tpl_id)
    if not db_tpl:
        raise BusinessError(f"模板 ID={tpl_id} 不存在", 404)
    
    _check_template_permission(db, db_tpl, operator_id, "modify")
    
    update_data = tpl_in.model_dump(exclude_unset=True)
    
    if "name" in update_data:
        existing = db.query(models.WindowTemplate).filter(
            models.WindowTemplate.name == update_data["name"],
            models.WindowTemplate.creator_id == db_tpl.creator_id,
            models.WindowTemplate.id != tpl_id,
        ).first()
        if existing:
            raise BusinessError(f"模板名称 '{update_data['name']}' 已存在")
    
    new_start = update_data.get("start_time", db_tpl.start_time)
    new_end = update_data.get("end_time", db_tpl.end_time)
    _validate_template_time(new_start, new_end)
    
    if "environment_id" in update_data:
        env = get_environment(db, update_data["environment_id"])
        if not env:
            raise BusinessError(f"环境 ID={update_data['environment_id']} 不存在", 404)
    
    old_shared = db_tpl.is_shared
    for k, v in update_data.items():
        setattr(db_tpl, k, v)
    db_tpl.updated_at = datetime.utcnow()
    
    action = TemplateAction.TEMPLATE_UPDATE
    detail = "更新模板内容"
    if "is_shared" in update_data:
        if old_shared == 0 and update_data["is_shared"] == 1:
            action = TemplateAction.TEMPLATE_SHARE
            detail = "分享模板"
        elif old_shared == 1 and update_data["is_shared"] == 0:
            action = TemplateAction.TEMPLATE_UNSHARE
            detail = "取消分享模板"
    
    _add_template_audit_log(db, db_tpl, action, operator_id, detail=detail)
    
    db.commit()
    db.refresh(db_tpl)
    return db_tpl


def delete_window_template(db: Session, tpl_id: int, operator_id: int) -> None:
    db_tpl = get_window_template(db, tpl_id)
    if not db_tpl:
        raise BusinessError(f"模板 ID={tpl_id} 不存在", 404)
    
    _check_template_permission(db, db_tpl, operator_id, "delete")
    
    db.delete(db_tpl)
    db.commit()


# ============== Batch Generate & Precheck ==============

def _parse_dates_from_request(req: schemas.BatchGenerateRequest) -> List[date]:
    if req.generate_mode == "date_range":
        if not req.date_from or not req.date_to:
            raise BusinessError("日期范围模式需要提供 date_from 和 date_to")
        if req.date_to < req.date_from:
            raise BusinessError("date_to 不能早于 date_from")
        dates = []
        current = req.date_from
        while current <= req.date_to:
            dates.append(current)
            current += timedelta(days=1)
        return dates
    elif req.generate_mode == "specific_dates":
        if not req.specific_dates or len(req.specific_dates) == 0:
            raise BusinessError("指定日期模式需要提供 specific_dates")
        return sorted(set(req.specific_dates))
    else:
        raise BusinessError(f"不支持的生成模式: {req.generate_mode}")


def _combine_datetime(d: date, time_str: str) -> datetime:
    h, m = map(int, time_str.split(":"))
    return datetime(d.year, d.month, d.day, h, m)


def precheck_batch_windows(
    db: Session,
    template: models.WindowTemplate,
    dates: List[date],
) -> List[schemas.PreCheckItem]:
    results = []
    
    for d in dates:
        start_dt = _combine_datetime(d, template.start_time)
        end_dt = _combine_datetime(d, template.end_time)
        
        item = schemas.PreCheckItem(
            date=d.isoformat(),
            start_time=template.start_time,
            end_time=template.end_time,
            conflict_type=ConflictType.OK,
            message="可创建",
        )
        
        overlaps = check_time_overlap(db, template.environment_id, start_dt, end_dt)
        
        if overlaps:
            w = overlaps[0]
            item.conflict_window_id = w.id
            item.conflict_window_title = w.title
            item.conflict_window_status = w.status.value if w.status else None
            
            if w.status == WindowStatus.SUBMITTED:
                item.conflict_type = ConflictType.PENDING_APPROVAL
                item.message = f"存在审批中窗口: {w.title}（不可覆盖）"
            else:
                item.conflict_type = ConflictType.TIME_OVERLAP
                item.message = f"时间重叠: {w.title}（状态: {w.status.value if w.status else '未知'}）"
        
        results.append(item)
    
    return results


def batch_generate_windows(
    db: Session, req: schemas.BatchGenerateRequest
) -> schemas.BatchGenerateResult:
    template = get_window_template(db, req.template_id)
    if not template:
        raise BusinessError(f"模板 ID={req.template_id} 不存在", 404)
    
    _check_template_permission(db, template, req.operator_id, "use")
    
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)
    
    dates = _parse_dates_from_request(req)
    if len(dates) == 0:
        raise BusinessError("没有可生成的日期")
    
    precheck_items = precheck_batch_windows(db, template, dates)
    
    date_from = min(dates)
    date_to = max(dates)
    
    batch_record = models.BatchGenerateRecord(
        template_id=template.id,
        template_name=template.name,
        creator_id=req.operator_id,
        environment_id=template.environment_id,
        generate_mode=req.generate_mode,
        date_from=_combine_datetime(date_from, template.start_time),
        date_to=_combine_datetime(date_to, template.end_time),
        specific_dates=json.dumps([d.isoformat() for d in dates], ensure_ascii=False),
        total_count=len(dates),
        success_count=0,
        skip_count=0,
        fail_count=0,
        precheck_result=json.dumps([item.model_dump() for item in precheck_items], ensure_ascii=False),
        status="PRECHECKED",
    )
    db.add(batch_record)
    db.flush()
    
    created_windows = []
    success_count = 0
    skip_count = 0
    fail_count = 0
    
    if req.auto_create:
        for item in precheck_items:
            if item.conflict_type == ConflictType.OK:
                try:
                    d = date.fromisoformat(item.date)
                    start_dt = _combine_datetime(d, template.start_time)
                    end_dt = _combine_datetime(d, template.end_time)
                    
                    win = create_maintenance_window(db, schemas.MaintenanceWindowCreate(
                        title=f"{template.name} - {d.isoformat()}",
                        description=template.description or "",
                        environment_id=template.environment_id,
                        start_time=start_dt,
                        end_time=end_dt,
                        creator_id=req.operator_id,
                        change_reason=template.change_reason or f"批量生成自模板: {template.name}",
                    ))
                    created_windows.append(win)
                    success_count += 1
                except Exception:
                    fail_count += 1
            else:
                skip_count += 1
        
        batch_record.success_count = success_count
        batch_record.skip_count = skip_count
        batch_record.fail_count = fail_count
        batch_record.status = "COMPLETED"
        
        _add_template_audit_log(
            db, template, TemplateAction.BATCH_GENERATE, req.operator_id,
            detail=f"批量生成 {len(dates)} 条窗口，成功 {success_count}，跳过 {skip_count}，失败 {fail_count}",
        )
    
    db.commit()
    db.refresh(batch_record)
    
    return schemas.BatchGenerateResult(
        batch_id=batch_record.id,
        total_count=len(dates),
        success_count=success_count,
        skip_count=skip_count,
        fail_count=fail_count,
        status=batch_record.status,
        precheck_items=precheck_items,
        created_windows=created_windows,
    )


def confirm_batch_generate(
    db: Session, batch_id: int, operator_id: int
) -> schemas.BatchGenerateResult:
    batch = db.query(models.BatchGenerateRecord).filter(
        models.BatchGenerateRecord.id == batch_id
    ).first()
    if not batch:
        raise BusinessError(f"批量生成记录 ID={batch_id} 不存在", 404)
    
    if batch.status == "COMPLETED":
        precheck_items = [
            schemas.PreCheckItem(**item) for item in json.loads(batch.precheck_result)
        ]
        return schemas.BatchGenerateResult(
            batch_id=batch.id,
            total_count=batch.total_count,
            success_count=batch.success_count,
            skip_count=batch.skip_count,
            fail_count=batch.fail_count,
            status=batch.status,
            precheck_items=precheck_items,
            created_windows=[],
        )
    
    if batch.status != "PRECHECKED":
        raise BusinessError(f"当前状态 {batch.status} 不能确认生成")
    
    template = get_window_template(db, batch.template_id) if batch.template_id else None
    if not template:
        raise BusinessError(f"关联模板不存在", 404)
    
    _check_template_permission(db, template, operator_id, "use")
    
    precheck_items = [
        schemas.PreCheckItem(**item) for item in json.loads(batch.precheck_result)
    ]
    
    created_windows = []
    success_count = 0
    skip_count = 0
    fail_count = 0
    
    for item in precheck_items:
        if item.conflict_type == ConflictType.OK:
            try:
                d = date.fromisoformat(item.date)
                start_dt = _combine_datetime(d, template.start_time)
                end_dt = _combine_datetime(d, template.end_time)
                
                win = create_maintenance_window(db, schemas.MaintenanceWindowCreate(
                    title=f"{template.name} - {d.isoformat()}",
                    description=template.description or "",
                    environment_id=template.environment_id,
                    start_time=start_dt,
                    end_time=end_dt,
                    creator_id=operator_id,
                    change_reason=template.change_reason or f"批量生成自模板: {template.name}",
                ))
                created_windows.append(win)
                success_count += 1
            except Exception:
                fail_count += 1
        else:
            skip_count += 1
    
    batch.success_count = success_count
    batch.skip_count = skip_count
    batch.fail_count = fail_count
    batch.status = "COMPLETED"
    
    if template:
        _add_template_audit_log(
            db, template, TemplateAction.BATCH_GENERATE, operator_id,
            detail=f"批量生成 {len(precheck_items)} 条窗口，成功 {success_count}，跳过 {skip_count}，失败 {fail_count}",
        )
    
    db.commit()
    db.refresh(batch)
    
    return schemas.BatchGenerateResult(
        batch_id=batch.id,
        total_count=batch.total_count,
        success_count=success_count,
        skip_count=skip_count,
        fail_count=fail_count,
        status=batch.status,
        precheck_items=precheck_items,
        created_windows=created_windows,
    )


def list_batch_records(
    db: Session,
    template_id: Optional[int] = None,
    creator_id: Optional[int] = None,
) -> List[models.BatchGenerateRecord]:
    q = db.query(models.BatchGenerateRecord)
    if template_id is not None:
        q = q.filter(models.BatchGenerateRecord.template_id == template_id)
    if creator_id is not None:
        q = q.filter(models.BatchGenerateRecord.creator_id == creator_id)
    return q.order_by(models.BatchGenerateRecord.created_at.desc()).all()


def get_batch_record(db: Session, batch_id: int) -> Optional[models.BatchGenerateRecord]:
    return db.query(models.BatchGenerateRecord).filter(
        models.BatchGenerateRecord.id == batch_id
    ).first()


# ============== Template Import/Export ==============

def _serialize_batch_record_for_export(batch: models.BatchGenerateRecord) -> dict:
    precheck_items = []
    if batch.precheck_result:
        precheck_items = json.loads(batch.precheck_result)
    
    specific_dates = None
    if batch.specific_dates:
        specific_dates = json.loads(batch.specific_dates)
    
    return {
        "generate_mode": batch.generate_mode,
        "date_from": batch.date_from.isoformat() if batch.date_from else None,
        "date_to": batch.date_to.isoformat() if batch.date_to else None,
        "specific_dates": specific_dates,
        "total_count": batch.total_count,
        "success_count": batch.success_count,
        "skip_count": batch.skip_count,
        "fail_count": batch.fail_count,
        "status": batch.status,
        "precheck_items": precheck_items,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def export_templates(
    db: Session,
    template_ids: Optional[List[int]] = None,
    user_id: Optional[int] = None,
) -> List[dict]:
    q = db.query(models.WindowTemplate)
    if template_ids:
        q = q.filter(models.WindowTemplate.id.in_(template_ids))
    if user_id:
        q = q.filter(models.WindowTemplate.creator_id == user_id)
    
    templates = q.all()
    result = []
    
    for tpl in templates:
        env = tpl.environment
        creator = tpl.creator
        
        batch_records = db.query(models.BatchGenerateRecord).filter(
            models.BatchGenerateRecord.template_id == tpl.id
        ).order_by(models.BatchGenerateRecord.created_at.desc()).all()
        
        batch_data = [_serialize_batch_record_for_export(b) for b in batch_records]
        
        result.append({
            "name": tpl.name,
            "description": tpl.description,
            "environment_name": env.name if env else None,
            "start_time": tpl.start_time,
            "end_time": tpl.end_time,
            "change_reason": tpl.change_reason,
            "is_shared": tpl.is_shared,
            "creator_username": creator.username if creator else None,
            "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
            "batch_records": batch_data,
        })
    
    return result


def _restore_batch_record(
    db: Session,
    tpl: models.WindowTemplate,
    batch_data: dict,
    operator_id: int,
) -> models.BatchGenerateRecord:
    precheck_items_raw = batch_data.get("precheck_items", [])
    specific_dates_raw = batch_data.get("specific_dates")
    
    date_from = None
    date_to = None
    if batch_data.get("date_from"):
        date_from = datetime.fromisoformat(batch_data["date_from"])
    if batch_data.get("date_to"):
        date_to = datetime.fromisoformat(batch_data["date_to"])
    
    record = models.BatchGenerateRecord(
        template_id=tpl.id,
        template_name=tpl.name,
        creator_id=operator_id,
        environment_id=tpl.environment_id,
        generate_mode=batch_data.get("generate_mode", "date_range"),
        date_from=date_from,
        date_to=date_to,
        specific_dates=json.dumps(specific_dates_raw, ensure_ascii=False) if specific_dates_raw else None,
        total_count=batch_data.get("total_count", 0),
        success_count=batch_data.get("success_count", 0),
        skip_count=batch_data.get("skip_count", 0),
        fail_count=batch_data.get("fail_count", 0),
        precheck_result=json.dumps(precheck_items_raw, ensure_ascii=False) if precheck_items_raw else None,
        status=batch_data.get("status", "PRECHECKED"),
    )
    db.add(record)
    return record


def import_templates(
    db: Session, req: schemas.TemplateImportRequest
) -> schemas.TemplateImportResult:
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)
    
    total = len(req.templates)
    success = 0
    skipped = 0
    failed = 0
    details = []
    
    for idx, item in enumerate(req.templates):
        try:
            env = get_environment_by_name(db, item.environment_name)
            if not env:
                failed += 1
                details.append({
                    "index": idx,
                    "name": item.name,
                    "status": "failed",
                    "reason": f"环境 '{item.environment_name}' 不存在",
                })
                continue
            
            existing = db.query(models.WindowTemplate).filter(
                models.WindowTemplate.name == item.name,
                models.WindowTemplate.creator_id == req.operator_id,
            ).first()
            
            if existing:
                if req.on_conflict == "skip":
                    if not req.re_generate_on_conflict:
                        skipped += 1
                        details.append({
                            "index": idx,
                            "name": item.name,
                            "status": "skipped",
                            "reason": "同名模板已存在，跳过",
                        })
                        continue
                    else:
                        if item.batch_records:
                            for br_data in item.batch_records:
                                _restore_batch_record(db, existing, br_data.model_dump(), req.operator_id)
                            success += 1
                            details.append({
                                "index": idx,
                                "name": item.name,
                                "status": "regenerated",
                                "template_id": existing.id,
                                "batch_records_restored": len(item.batch_records),
                            })
                            continue
                        else:
                            skipped += 1
                            details.append({
                                "index": idx,
                                "name": item.name,
                                "status": "skipped",
                                "reason": "同名模板已存在，且无批量记录可再生成",
                            })
                            continue
                elif req.on_conflict == "overwrite":
                    tpl_in = schemas.WindowTemplateUpdate(
                        description=item.description,
                        environment_id=env.id,
                        start_time=item.start_time,
                        end_time=item.end_time,
                        change_reason=item.change_reason,
                        is_shared=item.is_shared,
                    )
                    update_window_template(db, existing.id, tpl_in, req.operator_id)
                    
                    if req.restore_batch_records and item.batch_records:
                        for br_data in item.batch_records:
                            _restore_batch_record(db, existing, br_data.model_dump(), req.operator_id)
                    
                    success += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "overwritten",
                        "id": existing.id,
                        "batch_records_restored": len(item.batch_records) if item.batch_records else 0,
                    })
                    continue
                else:
                    failed += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "failed",
                        "reason": "同名模板已存在",
                    })
                    continue
            
            tpl_create = schemas.WindowTemplateCreate(
                name=item.name,
                description=item.description,
                environment_id=env.id,
                start_time=item.start_time,
                end_time=item.end_time,
                change_reason=item.change_reason,
                is_shared=item.is_shared,
                creator_id=req.operator_id,
            )
            tpl = create_window_template(db, tpl_create)
            
            if req.restore_batch_records and item.batch_records:
                for br_data in item.batch_records:
                    _restore_batch_record(db, tpl, br_data.model_dump(), req.operator_id)
            
            success += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "created",
                "id": tpl.id,
                "batch_records_restored": len(item.batch_records) if item.batch_records else 0,
            })
            
            _add_template_audit_log(
                db, tpl, TemplateAction.TEMPLATE_IMPORT, req.operator_id,
                detail=f"导入模板: {item.name}" + (f"，恢复 {len(item.batch_records)} 条批量记录" if item.batch_records else ""),
            )
            
        except BusinessError as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": e.message,
            })
        except Exception as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": str(e),
            })
    
    db.commit()
    
    return schemas.TemplateImportResult(
        total=total,
        success=success,
        skipped=skipped,
        failed=failed,
        details=details,
    )


def regenerate_from_batch_record(
    db: Session, batch_id: int, operator_id: int
) -> schemas.BatchGenerateResult:
    batch = db.query(models.BatchGenerateRecord).filter(
        models.BatchGenerateRecord.id == batch_id
    ).first()
    if not batch:
        raise BusinessError(f"批量生成记录 ID={batch_id} 不存在", 404)
    
    template = get_window_template(db, batch.template_id) if batch.template_id else None
    if not template:
        raise BusinessError("关联模板不存在，无法再生成", 404)
    
    _check_template_permission(db, template, operator_id, "use")
    
    dates = []
    if batch.specific_dates:
        dates = [date.fromisoformat(d) for d in json.loads(batch.specific_dates)]
    elif batch.date_from and batch.date_to:
        current = batch.date_from.date()
        end = batch.date_to.date()
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)
    
    if not dates:
        raise BusinessError("批量记录中没有日期信息，无法再生成")
    
    precheck_items = precheck_batch_windows(db, template, dates)
    
    new_batch = models.BatchGenerateRecord(
        template_id=template.id,
        template_name=template.name,
        creator_id=operator_id,
        environment_id=template.environment_id,
        generate_mode=batch.generate_mode,
        date_from=batch.date_from,
        date_to=batch.date_to,
        specific_dates=batch.specific_dates,
        total_count=len(dates),
        success_count=0,
        skip_count=0,
        fail_count=0,
        precheck_result=json.dumps([item.model_dump() for item in precheck_items], ensure_ascii=False),
        status="PRECHECKED",
    )
    db.add(new_batch)
    db.flush()
    
    created_windows = []
    success_count = 0
    skip_count = 0
    fail_count = 0
    
    for item in precheck_items:
        if item.conflict_type == ConflictType.OK:
            try:
                d = date.fromisoformat(item.date)
                start_dt = _combine_datetime(d, template.start_time)
                end_dt = _combine_datetime(d, template.end_time)
                
                win = create_maintenance_window(db, schemas.MaintenanceWindowCreate(
                    title=f"{template.name} - {d.isoformat()}",
                    description=template.description or "",
                    environment_id=template.environment_id,
                    start_time=start_dt,
                    end_time=end_dt,
                    creator_id=operator_id,
                    change_reason=template.change_reason or f"再生成自模板: {template.name}",
                ))
                created_windows.append(win)
                success_count += 1
            except Exception:
                fail_count += 1
        else:
            skip_count += 1
    
    new_batch.success_count = success_count
    new_batch.skip_count = skip_count
    new_batch.fail_count = fail_count
    new_batch.status = "COMPLETED"
    
    _add_template_audit_log(
        db, template, TemplateAction.BATCH_GENERATE, operator_id,
        detail=f"再生成 {len(dates)} 条窗口，成功 {success_count}，跳过 {skip_count}，失败 {fail_count}",
    )
    
    db.commit()
    db.refresh(new_batch)
    
    return schemas.BatchGenerateResult(
        batch_id=new_batch.id,
        total_count=len(dates),
        success_count=success_count,
        skip_count=skip_count,
        fail_count=fail_count,
        status=new_batch.status,
        precheck_items=precheck_items,
        created_windows=created_windows,
    )


# ============== Schedule Plan Service ==============

def _plan_snapshot(plan: models.SchedulePlan) -> str:
    return json.dumps({
        "id": plan.id,
        "name": plan.name,
        "description": plan.description,
        "template_id": plan.template_id,
        "environment_id": plan.environment_id,
        "generate_mode": plan.generate_mode,
        "status": plan.status.value if plan.status else None,
        "creator_id": plan.creator_id,
        "total_count": plan.total_count,
        "approved_count": plan.approved_count,
        "confirmed_count": plan.confirmed_count,
    }, ensure_ascii=False)


def _add_plan_audit_log(
    db: Session,
    plan: models.SchedulePlan,
    action: models.PlanAction,
    operator_id: int,
    item_id: Optional[int] = None,
    detail: Optional[str] = None,
):
    log = models.PlanAuditLog(
        plan_id=plan.id,
        action=action,
        operator_id=operator_id,
        item_id=item_id,
        detail=detail,
        snapshot=_plan_snapshot(plan),
    )
    db.add(log)


def _snapshot_template(template: models.WindowTemplate) -> str:
    return json.dumps({
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "environment_id": template.environment_id,
        "start_time": template.start_time,
        "end_time": template.end_time,
        "change_reason": template.change_reason,
        "is_shared": template.is_shared,
        "creator_id": template.creator_id,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }, ensure_ascii=False)


def _snapshot_environment_slots(db: Session, environment_id: int) -> str:
    slots = list_maintenance_slots(db, environment_id)
    return json.dumps([{
        "id": s.id,
        "day_of_week": s.day_of_week,
        "start_time": s.start_time,
        "end_time": s.end_time,
    } for s in slots], ensure_ascii=False)


def _check_plan_permission(
    db: Session,
    plan: models.SchedulePlan,
    operator_id: int,
    action: str = "view",
):
    operator = get_user(db, operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={operator_id} 不存在", 404)
    
    is_owner = plan.creator_id == operator_id
    is_approver = user_can_approve(db, operator_id)
    template = plan.template
    is_shared_template = template and template.is_shared == 1
    
    if action == "view":
        if is_owner or is_approver:
            return True
        if is_shared_template:
            return True
        raise BusinessError("无权限查看该方案", 403)
    
    if action == "submit":
        if is_owner or is_approver:
            return True
        raise BusinessError("只有创建人或审批人可以提交审批", 403)
    
    if action == "approve":
        if is_approver:
            return True
        delegation = can_user_act_as_approver(db, operator_id, plan.environment_id, "PLAN_CONFIRM")
        if delegation.is_delegated:
            return True
        raise BusinessError("只有审批角色可以审批方案", 403)
    
    if action == "confirm":
        if is_owner:
            return True
        if is_approver:
            return True
        delegation = can_user_act_as_approver(db, operator_id, plan.environment_id, "PLAN_CONFIRM")
        if delegation.is_delegated:
            return True
        if is_shared_template:
            if plan.creator_id != operator_id:
                raise BusinessError("非审批角色不能替别人确认已共享方案", 403)
            return True
        raise BusinessError("无权限确认该方案", 403)
    
    if action in ["modify", "exclude", "recheck", "execute", "cancel"]:
        if is_owner or is_approver:
            return True
        raise BusinessError(f"无权限{action_description(action)}该方案", 403)
    
    raise BusinessError(f"无权限执行该操作", 403)


def create_schedule_plan(
    db: Session, plan_in: schemas.SchedulePlanCreate
) -> models.SchedulePlan:
    template = get_window_template(db, plan_in.template_id)
    if not template:
        raise BusinessError(f"模板 ID={plan_in.template_id} 不存在", 404)
    
    _check_template_permission(db, template, plan_in.creator_id, "use")
    
    creator = get_user(db, plan_in.creator_id)
    if not creator:
        raise BusinessError(f"创建人 ID={plan_in.creator_id} 不存在", 404)
    
    env = get_environment(db, template.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={template.environment_id} 不存在", 404)
    
    dates = []
    if plan_in.generate_mode == "date_range":
        if not plan_in.date_from or not plan_in.date_to:
            raise BusinessError("日期范围模式需要提供 date_from 和 date_to")
        if plan_in.date_to < plan_in.date_from:
            raise BusinessError("date_to 不能早于 date_from")
        current = plan_in.date_from
        while current <= plan_in.date_to:
            dates.append(current)
            current += timedelta(days=1)
    elif plan_in.generate_mode == "specific_dates":
        if not plan_in.specific_dates or len(plan_in.specific_dates) == 0:
            raise BusinessError("指定日期模式需要提供 specific_dates")
        dates = sorted(set(plan_in.specific_dates))
    else:
        raise BusinessError(f"不支持的生成模式: {plan_in.generate_mode}")
    
    if len(dates) == 0:
        raise BusinessError("没有可生成的日期")
    
    precheck_items = precheck_batch_windows(db, template, dates)
    
    plan = models.SchedulePlan(
        name=plan_in.name,
        description=plan_in.description,
        template_id=template.id,
        template_version_snapshot=_snapshot_template(template),
        environment_id=template.environment_id,
        environment_slots_snapshot=_snapshot_environment_slots(db, template.environment_id),
        generate_mode=plan_in.generate_mode,
        date_from=_combine_datetime(min(dates), template.start_time) if dates else None,
        date_to=_combine_datetime(max(dates), template.end_time) if dates else None,
        specific_dates=json.dumps([d.isoformat() for d in dates], ensure_ascii=False),
        operator_remark=plan_in.operator_remark,
        status=models.PlanStatus.DRAFT,
        creator_id=plan_in.creator_id,
        total_count=len(dates),
        approved_count=0,
        confirmed_count=0,
        created_count=0,
    )
    db.add(plan)
    db.flush()
    
    for item in precheck_items:
        plan_item = models.SchedulePlanItem(
            plan_id=plan.id,
            date=item.date,
            start_time=item.start_time,
            end_time=item.end_time,
            precheck_snapshot=json.dumps(item.model_dump(), ensure_ascii=False),
            conflict_type_snapshot=item.conflict_type.value if item.conflict_type else None,
            conflict_window_id_snapshot=item.conflict_window_id,
            conflict_window_title_snapshot=item.conflict_window_title,
            conflict_window_status_snapshot=item.conflict_window_status,
            message_snapshot=item.message,
            status=models.PlanItemStatus.PENDING,
        )
        db.add(plan_item)
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_CREATE, plan_in.creator_id,
        detail=f"创建方案: {plan_in.name}，共 {len(dates)} 条候选窗口",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def get_schedule_plan(db: Session, plan_id: int) -> Optional[models.SchedulePlan]:
    return db.query(models.SchedulePlan).filter(models.SchedulePlan.id == plan_id).first()


def list_schedule_plans(
    db: Session,
    template_id: Optional[int] = None,
    creator_id: Optional[int] = None,
    status: Optional[models.PlanStatus] = None,
) -> List[models.SchedulePlan]:
    q = db.query(models.SchedulePlan)
    if template_id is not None:
        q = q.filter(models.SchedulePlan.template_id == template_id)
    if creator_id is not None:
        q = q.filter(models.SchedulePlan.creator_id == creator_id)
    if status is not None:
        q = q.filter(models.SchedulePlan.status == status)
    return q.order_by(models.SchedulePlan.created_at.desc()).all()


def submit_schedule_plan(
    db: Session, plan_id: int, req: schemas.SchedulePlanSubmit
) -> models.SchedulePlan:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "submit")
    
    if plan.status not in [models.PlanStatus.DRAFT, models.PlanStatus.REJECTED]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能提交审批")
    
    if plan.total_count == 0:
        raise BusinessError("方案中没有候选窗口，无法提交审批")
    
    freeze_conflicts = check_plan_freeze_for_items(
        db, plan_id, plan.environment_id, plan.items,
        models.FreezeRuleScope.SUBMIT, req.operator_id
    )
    if freeze_conflicts:
        conflict_names = list(set([c["rule_name"] for c in freeze_conflicts]))
        raise BusinessError(
            f"提交被冻结规则拦截，涉及 {len(freeze_conflicts)} 条窗口，"
            f"冻结规则: {', '.join(conflict_names)}",
            403,
        )
    
    old_status = plan.status
    plan.status = models.PlanStatus.PENDING_APPROVAL
    plan.updated_at = datetime.utcnow()
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_SUBMIT, req.operator_id,
        detail=f"提交审批: {req.remark or '无备注'}",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def approve_schedule_plan(
    db: Session, plan_id: int, req: schemas.SchedulePlanApprove
) -> models.SchedulePlan:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "approve")
    
    if plan.status != models.PlanStatus.PENDING_APPROVAL:
        raise BusinessError(f"当前状态 {plan.status.value} 不能审批")
    
    freeze_conflicts = check_plan_freeze_for_items(
        db, plan_id, plan.environment_id, plan.items,
        models.FreezeRuleScope.APPROVE, req.operator_id
    )
    if freeze_conflicts:
        conflict_names = list(set([c["rule_name"] for c in freeze_conflicts]))
        raise BusinessError(
            f"审批被冻结规则拦截，涉及 {len(freeze_conflicts)} 条窗口，"
            f"冻结规则: {', '.join(conflict_names)}",
            403,
        )
    
    old_status = plan.status
    plan.status = models.PlanStatus.APPROVED
    plan.approver_id = req.operator_id
    plan.approval_reason = req.reason
    plan.approved_at = datetime.utcnow()
    plan.approved_count = plan.total_count
    plan.updated_at = datetime.utcnow()
    
    for item in plan.items:
        if item.status == models.PlanItemStatus.PENDING:
            item.status = models.PlanItemStatus.APPROVED
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_APPROVE, req.operator_id,
        detail=f"审批通过: {req.reason or '无备注'}",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def reject_schedule_plan(
    db: Session, plan_id: int, req: schemas.SchedulePlanReject
) -> models.SchedulePlan:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "approve")
    
    if plan.status != models.PlanStatus.PENDING_APPROVAL:
        raise BusinessError(f"当前状态 {plan.status.value} 不能驳回")
    
    old_status = plan.status
    plan.status = models.PlanStatus.REJECTED
    plan.approver_id = req.operator_id
    plan.approval_reason = req.reason
    plan.updated_at = datetime.utcnow()
    
    for item in plan.items:
        if item.status == models.PlanItemStatus.PENDING:
            item.status = models.PlanItemStatus.PENDING
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_REJECT, req.operator_id,
        detail=f"审批驳回: {req.reason}",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def detect_plan_changes(
    db: Session, plan_id: int, operator_id: int
) -> schemas.SchedulePlanDetectChangeResult:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, operator_id, "view")
    
    if plan.status not in [models.PlanStatus.APPROVED, models.PlanStatus.CONFIRMING]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能检测变更")
    
    template = plan.template
    if not template:
        raise BusinessError("关联模板不存在", 404)
    
    current_template_snapshot = json.loads(_snapshot_template(template))
    original_template_snapshot = json.loads(plan.template_version_snapshot)
    
    current_slots_snapshot = json.loads(_snapshot_environment_slots(db, plan.environment_id))
    original_slots_snapshot = json.loads(plan.environment_slots_snapshot)
    
    template_time_changed = (
        current_template_snapshot["start_time"] != original_template_snapshot["start_time"] or
        current_template_snapshot["end_time"] != original_template_snapshot["end_time"] or
        current_template_snapshot["environment_id"] != original_template_snapshot["environment_id"]
    )
    
    template_meta_changed = (
        current_template_snapshot.get("description") != original_template_snapshot.get("description") or
        current_template_snapshot.get("change_reason") != original_template_snapshot.get("change_reason") or
        current_template_snapshot.get("is_shared") != original_template_snapshot.get("is_shared") or
        current_template_snapshot.get("name") != original_template_snapshot.get("name")
    )
    
    template_changed = template_time_changed or template_meta_changed
    
    slots_changed = current_slots_snapshot != original_slots_snapshot
    
    changed_count = 0
    unchanged_count = 0
    excluded_count = 0
    details = []
    
    for item in plan.items:
        if item.status == models.PlanItemStatus.EXCLUDED:
            excluded_count += 1
            continue
        
        diff_hints = []
        current_diff_type = models.DiffType.NO_CHANGE
        
        if template_changed:
            current_diff_type = models.DiffType.TEMPLATE_CHANGED
            template_diff_hint = {
                "diff_type": models.DiffType.TEMPLATE_CHANGED.value,
                "detail": "模板内容已变更",
                "changed_fields": [],
                "old_value": {},
                "new_value": {},
            }
            if template_time_changed:
                if current_template_snapshot["start_time"] != original_template_snapshot["start_time"]:
                    template_diff_hint["changed_fields"].append("start_time")
                    template_diff_hint["old_value"]["start_time"] = original_template_snapshot["start_time"]
                    template_diff_hint["new_value"]["start_time"] = current_template_snapshot["start_time"]
                if current_template_snapshot["end_time"] != original_template_snapshot["end_time"]:
                    template_diff_hint["changed_fields"].append("end_time")
                    template_diff_hint["old_value"]["end_time"] = original_template_snapshot["end_time"]
                    template_diff_hint["new_value"]["end_time"] = current_template_snapshot["end_time"]
                if current_template_snapshot["environment_id"] != original_template_snapshot["environment_id"]:
                    template_diff_hint["changed_fields"].append("environment_id")
                    template_diff_hint["old_value"]["environment_id"] = original_template_snapshot["environment_id"]
                    template_diff_hint["new_value"]["environment_id"] = current_template_snapshot["environment_id"]
            if template_meta_changed:
                if current_template_snapshot.get("description") != original_template_snapshot.get("description"):
                    template_diff_hint["changed_fields"].append("description")
                    template_diff_hint["old_value"]["description"] = original_template_snapshot.get("description")
                    template_diff_hint["new_value"]["description"] = current_template_snapshot.get("description")
                if current_template_snapshot.get("change_reason") != original_template_snapshot.get("change_reason"):
                    template_diff_hint["changed_fields"].append("change_reason")
                    template_diff_hint["old_value"]["change_reason"] = original_template_snapshot.get("change_reason")
                    template_diff_hint["new_value"]["change_reason"] = current_template_snapshot.get("change_reason")
                if current_template_snapshot.get("is_shared") != original_template_snapshot.get("is_shared"):
                    template_diff_hint["changed_fields"].append("is_shared")
                    template_diff_hint["old_value"]["is_shared"] = original_template_snapshot.get("is_shared")
                    template_diff_hint["new_value"]["is_shared"] = current_template_snapshot.get("is_shared")
                if current_template_snapshot.get("name") != original_template_snapshot.get("name"):
                    template_diff_hint["changed_fields"].append("name")
                    template_diff_hint["old_value"]["name"] = original_template_snapshot.get("name")
                    template_diff_hint["new_value"]["name"] = current_template_snapshot.get("name")
                template_diff_hint["detail"] = f"模板关键字段已变更: {', '.join(template_diff_hint['changed_fields'])}"
            diff_hints.append(template_diff_hint)
        
        if slots_changed:
            current_diff_type = models.DiffType.SLOT_CHANGED
            diff_hints.append({
                "diff_type": models.DiffType.SLOT_CHANGED.value,
                "detail": "环境维护时段已变更",
                "old_value": original_slots_snapshot,
                "new_value": current_slots_snapshot,
            })
        
        item_date = date.fromisoformat(item.date)
        start_dt = _combine_datetime(item_date, template.start_time)
        end_dt = _combine_datetime(item_date, template.end_time)
        
        current_overlaps = check_time_overlap(db, plan.environment_id, start_dt, end_dt)
        
        old_conflict_type = item.conflict_type_snapshot
        new_conflict_type = models.ConflictType.OK
        new_conflict_window_id = None
        new_conflict_window_title = None
        new_conflict_window_status = None
        new_message = "可创建"
        
        if current_overlaps:
            w = current_overlaps[0]
            new_conflict_window_id = w.id
            new_conflict_window_title = w.title
            new_conflict_window_status = w.status.value if w.status else None
            
            if w.status == models.WindowStatus.SUBMITTED:
                new_conflict_type = models.ConflictType.PENDING_APPROVAL
                new_message = f"存在审批中窗口: {w.title}"
            else:
                new_conflict_type = models.ConflictType.TIME_OVERLAP
                new_message = f"时间重叠: {w.title}"
        
        conflict_changed = (
            old_conflict_type != new_conflict_type.value or
            item.conflict_window_id_snapshot != new_conflict_window_id
        )
        
        if conflict_changed:
            current_diff_type = models.DiffType.CONFLICT_CHANGED
            diff_hints.append({
                "diff_type": models.DiffType.CONFLICT_CHANGED.value,
                "detail": "冲突检测结果已变更",
                "old_value": {
                    "conflict_type": old_conflict_type,
                    "conflict_window_id": item.conflict_window_id_snapshot,
                    "message": item.message_snapshot,
                },
                "new_value": {
                    "conflict_type": new_conflict_type.value,
                    "conflict_window_id": new_conflict_window_id,
                    "message": new_message,
                },
            })
        
        old_status = item.conflict_window_status_snapshot
        if old_status and item.conflict_window_id_snapshot:
            old_window = get_maintenance_window(db, item.conflict_window_id_snapshot)
            if old_window:
                new_status = old_window.status.value if old_window.status else None
                if old_status != new_status:
                    current_diff_type = models.DiffType.WINDOW_STATUS_CHANGED
                    diff_hints.append({
                        "diff_type": models.DiffType.WINDOW_STATUS_CHANGED.value,
                        "detail": "冲突窗口状态已变更",
                        "old_value": old_status,
                        "new_value": new_status,
                    })
        
        latest_precheck = schemas.PlanItemSnapshot(
            conflict_type=new_conflict_type,
            conflict_window_id=new_conflict_window_id,
            conflict_window_title=new_conflict_window_title,
            conflict_window_status=new_conflict_window_status,
            message=new_message,
        )
        
        freeze_conflicts = check_freeze_conflicts(
            db, plan.environment_id, start_dt, end_dt, models.FreezeRuleScope.ALL
        )
        
        if freeze_conflicts:
            freeze_rule = freeze_conflicts[0]
            freeze_reason = _build_freeze_conflict_reason(freeze_rule, "ALL", start_dt, end_dt)
            overlap_type = _classify_overlap_type(start_dt, end_dt, freeze_rule)
            current_diff_type = models.DiffType.CONFLICT_CHANGED
            diff_hints.append({
                "diff_type": "FREEZE_CONFLICT",
                "detail": freeze_reason,
                "rule_id": freeze_rule.id,
                "rule_name": freeze_rule.name,
                "rule_reason": freeze_rule.reason,
            })
            record_freeze_hit(
                db, freeze_rule, plan.id, item, operator_id,
                hit_reason=freeze_reason,
                overlap_type=overlap_type,
            )
        
        item.current_diff_type = current_diff_type
        item.current_diff_detail = json.dumps(diff_hints, ensure_ascii=False) if diff_hints else None
        item.latest_precheck = json.dumps(latest_precheck.model_dump(), ensure_ascii=False)
        
        if current_diff_type != models.DiffType.NO_CHANGE:
            item.status = models.PlanItemStatus.CHANGED
            changed_count += 1
        else:
            unchanged_count += 1
        
        details.append({
            "item_id": item.id,
            "date": item.date,
            "diff_type": current_diff_type.value,
            "diff_hints": diff_hints,
        })
    
    if changed_count > 0:
        plan.status = models.PlanStatus.CONFIRMING
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_DETECT_CHANGE, operator_id,
        detail=f"检测变更: 共 {plan.total_count} 条，变更 {changed_count} 条，无变化 {unchanged_count} 条，已剔除 {excluded_count} 条",
    )
    
    db.commit()
    db.refresh(plan)
    
    return schemas.SchedulePlanDetectChangeResult(
        plan_id=plan.id,
        total_items=plan.total_count,
        changed_items=changed_count,
        unchanged_items=unchanged_count,
        excluded_items=excluded_count,
        details=details,
    )


def recheck_plan_item(
    db: Session, plan_id: int, req: schemas.SchedulePlanRecheckItem
) -> models.SchedulePlanItem:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "recheck")
    
    if plan.status not in [models.PlanStatus.APPROVED, models.PlanStatus.CONFIRMING]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能重新预检")
    
    item = db.query(models.SchedulePlanItem).filter(
        models.SchedulePlanItem.id == req.item_id,
        models.SchedulePlanItem.plan_id == plan_id,
    ).first()
    
    if not item:
        raise BusinessError(f"方案条目 ID={req.item_id} 不存在", 404)
    
    if item.status == models.PlanItemStatus.EXCLUDED:
        raise BusinessError("该条目已被剔除，不能重新预检")
    
    template = plan.template
    if not template:
        raise BusinessError("关联模板不存在", 404)
    
    item_date = date.fromisoformat(item.date)
    start_dt = _combine_datetime(item_date, template.start_time)
    end_dt = _combine_datetime(item_date, template.end_time)
    
    overlaps = check_time_overlap(db, plan.environment_id, start_dt, end_dt)
    
    new_conflict_type = models.ConflictType.OK
    new_conflict_window_id = None
    new_conflict_window_title = None
    new_conflict_window_status = None
    new_message = "可创建"
    
    if overlaps:
        w = overlaps[0]
        new_conflict_window_id = w.id
        new_conflict_window_title = w.title
        new_conflict_window_status = w.status.value if w.status else None
        
        if w.status == models.WindowStatus.SUBMITTED:
            new_conflict_type = models.ConflictType.PENDING_APPROVAL
            new_message = f"存在审批中窗口: {w.title}"
        else:
            new_conflict_type = models.ConflictType.TIME_OVERLAP
            new_message = f"时间重叠: {w.title}"
    
    latest_precheck = schemas.PlanItemSnapshot(
        conflict_type=new_conflict_type,
        conflict_window_id=new_conflict_window_id,
        conflict_window_title=new_conflict_window_title,
        conflict_window_status=new_conflict_window_status,
        message=new_message,
    )
    
    old_status = item.status
    
    freeze_conflicts = check_freeze_conflicts(
        db, plan.environment_id, start_dt, end_dt, models.FreezeRuleScope.ALL
    )
    
    if freeze_conflicts:
        diff_hints = []
        for freeze_rule in freeze_conflicts:
            freeze_reason = _build_freeze_conflict_reason(
                freeze_rule, "ALL", start_dt, end_dt
            )
            overlap_type = _classify_overlap_type(start_dt, end_dt, freeze_rule)
            diff_hints.append({
                "diff_type": "FREEZE_CONFLICT",
                "detail": freeze_reason,
                "rule_id": freeze_rule.id,
                "rule_name": freeze_rule.name,
                "rule_reason": freeze_rule.reason,
            })
            record_freeze_hit(
                db, freeze_rule, plan.id, item, req.operator_id,
                hit_reason=freeze_reason,
                overlap_type=overlap_type,
            )
        
        item.current_diff_type = models.DiffType.CONFLICT_CHANGED
        item.current_diff_detail = json.dumps(diff_hints, ensure_ascii=False)
        item.latest_precheck = json.dumps(latest_precheck.model_dump(), ensure_ascii=False)
        item.status = models.PlanItemStatus.CHANGED
        item.updated_at = datetime.utcnow()
        
        if old_status != models.PlanItemStatus.CHANGED:
            _add_plan_audit_log(
                db, plan, models.PlanAction.PLAN_FREEZE_HIT, req.operator_id,
                item_id=item.id,
                detail=f"重新预检条目 {item.date}: 命中{len(freeze_conflicts)}条冻结规则",
            )
        else:
            _add_plan_audit_log(
                db, plan, models.PlanAction.PLAN_RECHECK, req.operator_id,
                item_id=item.id,
                detail=f"重新预检条目 {item.date}: 仍命中{len(freeze_conflicts)}条冻结规则",
            )
        
        if plan.status == models.PlanStatus.APPROVED:
            plan.status = models.PlanStatus.CONFIRMING
            plan.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(item)
        return item
    
    item.latest_precheck = json.dumps(latest_precheck.model_dump(), ensure_ascii=False)
    
    if old_status == models.PlanItemStatus.CHANGED:
        all_active_hits = db.query(models.FreezeHitRecord).filter(
            models.FreezeHitRecord.item_id == item.id,
            models.FreezeHitRecord.plan_id == plan.id,
            models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
        ).all()
        
        if not freeze_conflicts:
            item.status = models.PlanItemStatus.APPROVED
            item.current_diff_type = models.DiffType.NO_CHANGE
            item.current_diff_detail = None
            
            for hit in all_active_hits:
                hit.status = models.FreezeHitRecordStatus.RECOVERED
                hit.recovered_at = datetime.utcnow()
                hit.recovered_by = req.operator_id
                hit.recovery_reason = "人工重新预检后恢复"
                hit.updated_at = datetime.utcnow()
                
                recovery_log = models.FreezeRecoveryLog(
                    rule_id=hit.rule_id,
                    rule_name=hit.rule_name,
                    trigger_action="recheck",
                    plan_id=plan.id,
                    item_id=item.id,
                    item_date=item.date,
                    status_before="CHANGED",
                    status_after="APPROVED",
                    still_blocked_by_rule_ids=None,
                    operator_id=req.operator_id,
                    detail=f"人工重新预检: 条目{item.date}状态 CHANGED->APPROVED",
                )
                db.add(recovery_log)
            
            _add_plan_audit_log(
                db, plan, models.PlanAction.PLAN_FREEZE_RECOVER, req.operator_id,
                item_id=item.id,
                detail=f"重新预检条目 {item.date}: 冻结冲突已恢复",
            )
        else:
            item.current_diff_type = models.DiffType.CONFLICT_CHANGED
            item.status = models.PlanItemStatus.CHANGED
    else:
        item.current_diff_type = models.DiffType.NO_CHANGE
        item.current_diff_detail = None
        item.status = models.PlanItemStatus.APPROVED
    
    item.updated_at = datetime.utcnow()
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_RECHECK, req.operator_id,
        item_id=item.id,
        detail=f"重新预检条目 {item.date}: 结果={new_conflict_type.value}",
    )
    
    db.commit()
    db.refresh(item)
    return item


def exclude_plan_item(
    db: Session, plan_id: int, req: schemas.SchedulePlanExcludeItem
) -> models.SchedulePlanItem:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "exclude")
    
    if plan.status not in [models.PlanStatus.APPROVED, models.PlanStatus.CONFIRMING]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能剔除条目")
    
    item = db.query(models.SchedulePlanItem).filter(
        models.SchedulePlanItem.id == req.item_id,
        models.SchedulePlanItem.plan_id == plan_id,
    ).first()
    
    if not item:
        raise BusinessError(f"方案条目 ID={req.item_id} 不存在", 404)
    
    if item.status == models.PlanItemStatus.EXCLUDED:
        raise BusinessError("该条目已被剔除")
    
    old_status = item.status
    item.status = models.PlanItemStatus.EXCLUDED
    item.excluded_at = datetime.utcnow()
    item.excluded_by = req.operator_id
    item.updated_at = datetime.utcnow()
    
    plan.approved_count -= 1
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_EXCLUDE, req.operator_id,
        item_id=item.id,
        detail=f"剔除条目 {item.date}: {req.reason or '无备注'}",
    )
    
    db.commit()
    db.refresh(item)
    return item


def confirm_schedule_plan(
    db: Session, plan_id: int, req: schemas.SchedulePlanConfirm
) -> models.SchedulePlan:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "confirm")
    
    if plan.status not in [models.PlanStatus.APPROVED, models.PlanStatus.CONFIRMING]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能确认")
    
    if plan.status == models.PlanStatus.APPROVED:
        _ = detect_plan_changes(db, plan_id, req.operator_id)
    
    changed_items = [
        item for item in plan.items
        if item.status == models.PlanItemStatus.CHANGED
    ]
    if changed_items:
        changed_dates = ", ".join([item.date for item in changed_items])
        raise BusinessError(
            f"检测到 {len(changed_items)} 条变更未处理（日期: {changed_dates}），"
            f"请先对变更条目执行重新预检或剔除后再确认。可调用 detect-changes 查看详情。"
        )
    
    items_to_confirm = []
    if req.item_ids:
        for item in plan.items:
            if item.id in req.item_ids and item.status not in [
                models.PlanItemStatus.EXCLUDED,
                models.PlanItemStatus.CONFIRMED,
                models.PlanItemStatus.CREATED,
            ]:
                if item.status == models.PlanItemStatus.CHANGED:
                    raise BusinessError(
                        f"条目 {item.date} 存在变更未处理，请先重新预检或剔除后再确认"
                    )
                items_to_confirm.append(item)
    else:
        for item in plan.items:
            if item.status not in [
                models.PlanItemStatus.EXCLUDED,
                models.PlanItemStatus.CONFIRMED,
                models.PlanItemStatus.CREATED,
            ]:
                if item.status == models.PlanItemStatus.CHANGED:
                    raise BusinessError(
                        f"条目 {item.date} 存在变更未处理，请先重新预检或剔除后再确认"
                    )
                items_to_confirm.append(item)
    
    if not items_to_confirm:
        raise BusinessError("没有可确认的条目")
    
    for item in items_to_confirm:
        item.status = models.PlanItemStatus.CONFIRMED
        item.confirmed_at = datetime.utcnow()
        item.confirmed_by = req.operator_id
        item.updated_at = datetime.utcnow()
    
    plan.confirmed_count += len(items_to_confirm)
    
    all_confirmed = True
    for item in plan.items:
        if item.status not in [
            models.PlanItemStatus.CONFIRMED,
            models.PlanItemStatus.CREATED,
            models.PlanItemStatus.EXCLUDED,
        ]:
            all_confirmed = False
            break
    
    if all_confirmed:
        plan.status = models.PlanStatus.CONFIRMED
    
    plan.updated_at = datetime.utcnow()
    
    changed_items = [i for i in plan.items if i.status == models.PlanItemStatus.CHANGED]
    excluded_items = [i for i in plan.items if i.status == models.PlanItemStatus.EXCLUDED]
    
    diff_summary = {
        "confirmed_count": len(items_to_confirm),
        "changed_count": len(changed_items),
        "excluded_count": len(excluded_items),
        "changed_items": [{"id": i.id, "date": i.date} for i in changed_items],
        "excluded_items": [{"id": i.id, "date": i.date} for i in excluded_items],
    }
    
    confirmation = models.PlanConfirmation(
        plan_id=plan.id,
        operator_id=req.operator_id,
        confirmation_type="BATCH_CONFIRM",
        item_ids=json.dumps([i.id for i in items_to_confirm], ensure_ascii=False),
        excluded_item_ids=json.dumps([i.id for i in excluded_items], ensure_ascii=False),
        diff_summary=json.dumps(diff_summary, ensure_ascii=False),
        remark=req.remark,
    )
    db.add(confirmation)
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_CONFIRM, req.operator_id,
        detail=f"确认执行 {len(items_to_confirm)} 条: {req.remark or '无备注'}",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def execute_schedule_plan(
    db: Session, plan_id: int, req: schemas.SchedulePlanExecute
) -> schemas.BatchGenerateResult:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, req.operator_id, "execute")
    
    if plan.status != models.PlanStatus.CONFIRMED:
        raise BusinessError(f"当前状态 {plan.status.value} 不能执行创建")
    
    template = plan.template
    if not template:
        raise BusinessError("关联模板不存在", 404)
    
    items_to_create = [
        item for item in plan.items
        if item.status == models.PlanItemStatus.CONFIRMED
    ]
    
    if not items_to_create:
        raise BusinessError("没有可创建的条目")
    
    created_windows = []
    success_count = 0
    skip_count = 0
    fail_count = 0
    
    for item in items_to_create:
        try:
            d = date.fromisoformat(item.date)
            start_dt = _combine_datetime(d, template.start_time)
            end_dt = _combine_datetime(d, template.end_time)
            
            win = create_maintenance_window(db, schemas.MaintenanceWindowCreate(
                title=f"{template.name} - {d.isoformat()}",
                description=template.description or "",
                environment_id=template.environment_id,
                start_time=start_dt,
                end_time=end_dt,
                creator_id=req.operator_id,
                change_reason=template.change_reason or f"方案执行: {plan.name}",
            ))
            
            item.window_id = win.id
            item.status = models.PlanItemStatus.CREATED
            item.updated_at = datetime.utcnow()
            created_windows.append(win)
            success_count += 1
        except Exception as e:
            fail_count += 1
    
    plan.created_count += success_count
    plan.status = models.PlanStatus.EXECUTED
    plan.updated_at = datetime.utcnow()
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_EXECUTE, req.operator_id,
        detail=f"执行创建 {len(items_to_create)} 条，成功 {success_count}，失败 {fail_count}",
    )
    
    precheck_items = []
    for item in plan.items:
        precheck_data = json.loads(item.precheck_snapshot)
        precheck_items.append(schemas.PreCheckItem(**precheck_data))
    
    db.commit()
    db.refresh(plan)
    
    return schemas.BatchGenerateResult(
        batch_id=plan.id,
        total_count=len(items_to_create),
        success_count=success_count,
        skip_count=skip_count,
        fail_count=fail_count,
        status="EXECUTED",
        precheck_items=precheck_items,
        created_windows=created_windows,
    )


def cancel_schedule_plan(
    db: Session, plan_id: int, operator_id: int
) -> models.SchedulePlan:
    plan = get_schedule_plan(db, plan_id)
    if not plan:
        raise BusinessError(f"方案 ID={plan_id} 不存在", 404)
    
    _check_plan_permission(db, plan, operator_id, "cancel")
    
    if plan.status in [models.PlanStatus.EXECUTED, models.PlanStatus.CANCELLED]:
        raise BusinessError(f"当前状态 {plan.status.value} 不能取消")
    
    old_status = plan.status
    plan.status = models.PlanStatus.CANCELLED
    plan.updated_at = datetime.utcnow()
    
    _add_plan_audit_log(
        db, plan, models.PlanAction.PLAN_CANCEL, operator_id,
        detail=f"取消方案: 原状态={old_status.value}",
    )
    
    db.commit()
    db.refresh(plan)
    return plan


def get_plan_confirmations(
    db: Session, plan_id: int
) -> List[models.PlanConfirmation]:
    return db.query(models.PlanConfirmation).filter(
        models.PlanConfirmation.plan_id == plan_id
    ).order_by(models.PlanConfirmation.created_at.desc()).all()


def get_plan_audit_logs(
    db: Session, plan_id: int
) -> List[models.PlanAuditLog]:
    return db.query(models.PlanAuditLog).filter(
        models.PlanAuditLog.plan_id == plan_id
    ).order_by(models.PlanAuditLog.created_at.desc()).all()


# ============== Plan Import/Export ==============

def export_schedule_plans(
    db: Session,
    plan_ids: Optional[List[int]] = None,
    user_id: Optional[int] = None,
) -> List[dict]:
    q = db.query(models.SchedulePlan)
    if plan_ids:
        q = q.filter(models.SchedulePlan.id.in_(plan_ids))
    if user_id:
        q = q.filter(models.SchedulePlan.creator_id == user_id)
    
    plans = q.all()
    result = []
    
    for plan in plans:
        env = plan.environment
        template = plan.template
        creator = plan.creator
        approver = plan.approver
        
        items_data = []
        for item in plan.items:
            items_data.append({
                "date": item.date,
                "start_time": item.start_time,
                "end_time": item.end_time,
                "precheck_snapshot": json.loads(item.precheck_snapshot) if item.precheck_snapshot else {},
                "conflict_type_snapshot": item.conflict_type_snapshot,
                "conflict_window_id_snapshot": item.conflict_window_id_snapshot,
                "conflict_window_title_snapshot": item.conflict_window_title_snapshot,
                "conflict_window_status_snapshot": item.conflict_window_status_snapshot,
                "message_snapshot": item.message_snapshot,
                "status": item.status.value if item.status else None,
            })
        
        confirmations_data = []
        for conf in plan.confirmations:
            operator = conf.operator
            confirmations_data.append({
                "confirmation_type": conf.confirmation_type,
                "operator_username": operator.username if operator else None,
                "operator_name": operator.display_name if operator else None,
                "item_ids": json.loads(conf.item_ids) if conf.item_ids else [],
                "excluded_item_ids": json.loads(conf.excluded_item_ids) if conf.excluded_item_ids else [],
                "diff_summary": json.loads(conf.diff_summary) if conf.diff_summary else {},
                "remark": conf.remark,
                "created_at": conf.created_at.isoformat() if conf.created_at else None,
            })
        
        audit_data = []
        for log in plan.audit_logs:
            operator = log.operator
            audit_data.append({
                "action": log.action.value if log.action else None,
                "operator_username": operator.username if operator else None,
                "operator_name": operator.display_name if operator else None,
                "item_id": log.item_id,
                "detail": log.detail,
                "snapshot": json.loads(log.snapshot) if log.snapshot else {},
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })
        
        specific_dates = None
        if plan.specific_dates:
            specific_dates = json.loads(plan.specific_dates)
        
        result.append({
            "name": plan.name,
            "description": plan.description,
            "template_name": template.name if template else None,
            "template_version_snapshot": json.loads(plan.template_version_snapshot),
            "environment_name": env.name if env else None,
            "environment_slots_snapshot": json.loads(plan.environment_slots_snapshot),
            "generate_mode": plan.generate_mode,
            "date_from": plan.date_from.isoformat() if plan.date_from else None,
            "date_to": plan.date_to.isoformat() if plan.date_to else None,
            "specific_dates": specific_dates,
            "operator_remark": plan.operator_remark,
            "status": plan.status.value if plan.status else None,
            "approval_reason": plan.approval_reason,
            "items": items_data,
            "confirmations": confirmations_data,
            "audit_logs": audit_data,
            "creator_username": creator.username if creator else None,
            "approver_username": approver.username if approver else None,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
        })
    
    return result


def import_schedule_plans(
    db: Session, req: schemas.PlanImportRequest
) -> schemas.PlanImportResult:
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)
    
    total = len(req.plans)
    success = 0
    skipped = 0
    failed = 0
    details = []
    
    for idx, item in enumerate(req.plans):
        try:
            env = get_environment_by_name(db, item.environment_name)
            if not env:
                failed += 1
                details.append({
                    "index": idx,
                    "name": item.name,
                    "status": "failed",
                    "reason": f"环境 '{item.environment_name}' 不存在",
                })
                continue
            
            template = db.query(models.WindowTemplate).filter(
                models.WindowTemplate.name == item.template_name,
                models.WindowTemplate.creator_id == req.operator_id,
            ).first()
            
            if not template:
                failed += 1
                details.append({
                    "index": idx,
                    "name": item.name,
                    "status": "failed",
                    "reason": f"模板 '{item.template_name}' 不存在",
                })
                continue
            
            existing = db.query(models.SchedulePlan).filter(
                models.SchedulePlan.name == item.name,
                models.SchedulePlan.creator_id == req.operator_id,
            ).first()
            
            if existing:
                if req.on_conflict == "skip":
                    skipped += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "skipped",
                        "reason": "同名方案已存在，跳过",
                    })
                    continue
                elif req.on_conflict == "overwrite":
                    db.delete(existing)
                    db.flush()
                else:
                    failed += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "failed",
                        "reason": "同名方案已存在",
                    })
                    continue
            
            dates = []
            if item.generate_mode == "date_range":
                if item.date_from and item.date_to:
                    d_from = date.fromisoformat(item.date_from[:10])
                    d_to = date.fromisoformat(item.date_to[:10])
                    current = d_from
                    while current <= d_to:
                        dates.append(current)
                        current += timedelta(days=1)
            elif item.generate_mode == "specific_dates":
                if item.specific_dates:
                    dates = [date.fromisoformat(d[:10]) for d in item.specific_dates]
            
            date_from = _combine_datetime(min(dates), template.start_time) if dates else None
            date_to = _combine_datetime(max(dates), template.end_time) if dates else None
            
            status_map = {s.value: s for s in models.PlanStatus}
            plan_status = status_map.get(item.status, models.PlanStatus.DRAFT)
            
            plan = models.SchedulePlan(
                name=item.name,
                description=item.description,
                template_id=template.id,
                template_version_snapshot=json.dumps(item.template_version_snapshot, ensure_ascii=False),
                environment_id=env.id,
                environment_slots_snapshot=json.dumps(item.environment_slots_snapshot, ensure_ascii=False),
                generate_mode=item.generate_mode,
                date_from=date_from,
                date_to=date_to,
                specific_dates=json.dumps(item.specific_dates, ensure_ascii=False) if item.specific_dates else None,
                operator_remark=item.operator_remark,
                status=plan_status,
                creator_id=req.operator_id,
                approval_reason=item.approval_reason,
                total_count=len(item.items),
                approved_count=0,
                confirmed_count=0,
                created_count=0,
            )
            db.add(plan)
            db.flush()
            
            item_status_map = {s.value: s for s in models.PlanItemStatus}
            for plan_item_data in item.items:
                if isinstance(plan_item_data, dict):
                    item_dict = plan_item_data
                else:
                    item_dict = plan_item_data.model_dump()
                item_status = item_status_map.get(item_dict.get("status"), models.PlanItemStatus.PENDING)
                plan_item = models.SchedulePlanItem(
                    plan_id=plan.id,
                    date=item_dict["date"],
                    start_time=item_dict["start_time"],
                    end_time=item_dict["end_time"],
                    precheck_snapshot=json.dumps(item_dict["precheck_snapshot"], ensure_ascii=False),
                    conflict_type_snapshot=item_dict.get("conflict_type_snapshot"),
                    conflict_window_id_snapshot=item_dict.get("conflict_window_id_snapshot"),
                    conflict_window_title_snapshot=item_dict.get("conflict_window_title_snapshot"),
                    conflict_window_status_snapshot=item_dict.get("conflict_window_status_snapshot"),
                    message_snapshot=item_dict.get("message_snapshot"),
                    status=item_status,
                )
                db.add(plan_item)
                
                if item_status in [models.PlanItemStatus.APPROVED, models.PlanItemStatus.CONFIRMED, models.PlanItemStatus.CREATED]:
                    plan.approved_count += 1
                if item_status in [models.PlanItemStatus.CONFIRMED, models.PlanItemStatus.CREATED]:
                    plan.confirmed_count += 1
            
            # 导入确认记录
            if item.confirmations:
                for conf_data in item.confirmations:
                    conf_dict = conf_data if isinstance(conf_data, dict) else conf_data
                    if not conf_dict:
                        continue
                    op_username = conf_dict.get("operator_username")
                    resolved_operator_id = req.operator_id
                    if op_username:
                        op_user = db.query(models.User).filter(
                            models.User.username == op_username
                        ).first()
                        if op_user:
                            resolved_operator_id = op_user.id
                    
                    item_ids_val = None
                    if conf_dict.get("item_ids"):
                        item_ids_val = json.dumps(conf_dict["item_ids"], ensure_ascii=False)
                    excluded_item_ids_val = None
                    if conf_dict.get("excluded_item_ids"):
                        excluded_item_ids_val = json.dumps(conf_dict["excluded_item_ids"], ensure_ascii=False)
                    rechecked_item_ids_val = None
                    if conf_dict.get("rechecked_item_ids"):
                        rechecked_item_ids_val = json.dumps(conf_dict["rechecked_item_ids"], ensure_ascii=False)
                    diff_summary_val = None
                    if conf_dict.get("diff_summary"):
                        diff_summary_val = json.dumps(conf_dict["diff_summary"], ensure_ascii=False)
                    
                    confirmation = models.PlanConfirmation(
                        plan_id=plan.id,
                        operator_id=resolved_operator_id,
                        confirmation_type=conf_dict.get("confirmation_type", "IMPORTED"),
                        item_ids=item_ids_val,
                        excluded_item_ids=excluded_item_ids_val,
                        rechecked_item_ids=rechecked_item_ids_val,
                        diff_summary=diff_summary_val,
                        remark=conf_dict.get("remark"),
                    )
                    db.add(confirmation)
            
            # 导入审计日志
            if item.audit_logs:
                for log_data in item.audit_logs:
                    log_dict = log_data if isinstance(log_data, dict) else log_data
                    if not log_dict:
                        continue
                    action_map = {a.value: a for a in models.PlanAction}
                    log_action = action_map.get(log_dict.get("action"), models.PlanAction.PLAN_IMPORT)
                    
                    op_username = log_dict.get("operator_username")
                    resolved_operator_id = req.operator_id
                    if op_username:
                        op_user = db.query(models.User).filter(
                            models.User.username == op_username
                        ).first()
                        if op_user:
                            resolved_operator_id = op_user.id
                    
                    snapshot_val = None
                    if log_dict.get("snapshot"):
                        snapshot_val = json.dumps(log_dict["snapshot"], ensure_ascii=False)
                    
                    audit_log = models.PlanAuditLog(
                        plan_id=plan.id,
                        action=log_action,
                        operator_id=resolved_operator_id,
                        item_id=log_dict.get("item_id"),
                        detail=log_dict.get("detail"),
                        snapshot=snapshot_val,
                    )
                    db.add(audit_log)
            
            _add_plan_audit_log(
                db, plan, models.PlanAction.PLAN_IMPORT, req.operator_id,
                detail=f"导入方案: {item.name}，共 {len(item.items)} 条",
            )
            
            success += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "created",
                "id": plan.id,
                "items_count": len(item.items),
            })
            
        except BusinessError as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": e.message,
            })
        except Exception as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": str(e),
            })
    
    db.commit()
    
    return schemas.PlanImportResult(
        total=total,
        success=success,
        skipped=skipped,
        failed=failed,
        details=details,
    )


# ============== Freeze Calendar Service ==============

def _freeze_rule_snapshot(rule: models.FreezeRule) -> str:
    return json.dumps({
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "environment_id": rule.environment_id,
        "freeze_scope": rule.freeze_scope.value if rule.freeze_scope else None,
        "date_from": rule.date_from.isoformat() if rule.date_from else None,
        "date_to": rule.date_to.isoformat() if rule.date_to else None,
        "start_time": rule.start_time,
        "end_time": rule.end_time,
        "reason": rule.reason,
        "status": rule.status.value if rule.status else None,
        "remark": rule.remark,
        "creator_id": rule.creator_id,
    }, ensure_ascii=False)


def _add_freeze_audit_log(
    db: Session,
    rule: models.FreezeRule,
    action: models.FreezeAction,
    operator_id: int,
    detail: Optional[str] = None,
    target_window_id: Optional[int] = None,
    target_plan_id: Optional[int] = None,
    target_item_id: Optional[int] = None,
):
    log = models.FreezeAuditLog(
        rule_id=rule.id,
        action=action,
        operator_id=operator_id,
        detail=detail,
        snapshot=_freeze_rule_snapshot(rule),
        target_window_id=target_window_id,
        target_plan_id=target_plan_id,
        target_item_id=target_item_id,
    )
    db.add(log)


def _check_freeze_manage_permission(db: Session, operator_id: int) -> bool:
    if not user_can_approve(db, operator_id):
        raise BusinessError("只有审批角色可以管理冻结规则", 403)
    return True


def _parse_time_to_minutes(time_str: str) -> int:
    h, m = map(int, time_str.split(":"))
    return h * 60 + m


def _intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def _check_daily_time_overlap_exact(
    check_start: datetime,
    check_end: datetime,
    rule_start_minutes: Optional[int],
    rule_end_minutes: Optional[int],
) -> bool:
    if rule_start_minutes is None or rule_end_minutes is None:
        return True

    cross_day = rule_end_minutes <= rule_start_minutes

    current_date = check_start.date()
    end_date = check_end.date()
    if check_end == datetime(end_date.year, end_date.month, end_date.day, 0, 0) and end_date > current_date:
        end_date -= timedelta(days=1)

    while current_date <= end_date:
        if cross_day:
            seg_m_start = datetime(current_date.year, current_date.month, current_date.day, 0, 0)
            seg_m_end = datetime(current_date.year, current_date.month, current_date.day,
                                 rule_end_minutes // 60, rule_end_minutes % 60)
            seg_e_start = datetime(current_date.year, current_date.month, current_date.day,
                                   rule_start_minutes // 60, rule_start_minutes % 60)
            next_day = current_date + timedelta(days=1)
            seg_e_end = datetime(next_day.year, next_day.month, next_day.day, 0, 0)

            if _intervals_overlap(check_start, check_end, seg_m_start, seg_m_end) or \
               _intervals_overlap(check_start, check_end, seg_e_start, seg_e_end):
                return True
        else:
            seg_start = datetime(current_date.year, current_date.month, current_date.day,
                                 rule_start_minutes // 60, rule_start_minutes % 60)
            seg_end = datetime(current_date.year, current_date.month, current_date.day,
                               rule_end_minutes // 60, rule_end_minutes % 60)

            if _intervals_overlap(check_start, check_end, seg_start, seg_end):
                return True

        current_date += timedelta(days=1)

    return False


def _classify_overlap_type(
    check_start: datetime,
    check_end: datetime,
    rule: models.FreezeRule,
) -> str:
    if not rule.start_time or not rule.end_time:
        return "FULL_DAY"

    rule_start_min = _parse_time_to_minutes(rule.start_time)
    rule_end_min = _parse_time_to_minutes(rule.end_time)
    cross_day = rule_end_min <= rule_start_min

    if cross_day:
        return "CROSS_DAY"

    check_start_min = check_start.hour * 60 + check_start.minute
    check_end_min = check_end.hour * 60 + check_end.minute

    same_day = check_start.date() == check_end.date() or \
        check_end == datetime(check_end.year, check_end.month, check_end.day, 0, 0)

    if same_day:
        if check_start_min >= rule_start_min and check_end_min <= rule_end_min:
            if check_start_min == rule_start_min and check_end_min == rule_end_min:
                return "EXACT_MATCH"
            return "NESTED"
        if check_start_min < rule_end_min and check_end_min > rule_start_min:
            return "PARTIAL"

    return "PARTIAL"


def _detect_rule_time_overlaps(
    db: Session,
    environment_id: int,
    date_from: datetime,
    date_to: datetime,
    start_time: Optional[str],
    end_time: Optional[str],
    freeze_scope: models.FreezeRuleScope,
    exclude_rule_id: Optional[int] = None,
) -> List[dict]:
    q = db.query(models.FreezeRule).filter(
        models.FreezeRule.environment_id == environment_id,
        models.FreezeRule.status == models.FreezeRuleStatus.ACTIVE,
    )
    if exclude_rule_id is not None:
        q = q.filter(models.FreezeRule.id != exclude_rule_id)

    all_rules = q.all()
    overlaps = []

    rule_start_min = _parse_time_to_minutes(start_time) if start_time else None
    rule_end_min = _parse_time_to_minutes(end_time) if end_time else None

    for existing in all_rules:
        if existing.date_from >= date_to or existing.date_to <= date_from:
            continue

        scope_overlap = (existing.freeze_scope == models.FreezeRuleScope.ALL or
                         freeze_scope == models.FreezeRuleScope.ALL or
                         existing.freeze_scope == freeze_scope)
        if not scope_overlap:
            continue

        has_time_overlap = False

        if rule_start_min is None or rule_end_min is None:
            if existing.start_time and existing.end_time:
                has_time_overlap = _check_daily_time_overlap_exact(
                    existing.date_from, existing.date_to, rule_start_min, rule_end_min
                )
            else:
                has_time_overlap = True
        elif existing.start_time and existing.end_time:
            ex_start_min = _parse_time_to_minutes(existing.start_time)
            ex_end_min = _parse_time_to_minutes(existing.end_time)

            overlap_dates_start = max(existing.date_from, date_from)
            overlap_dates_end = min(existing.date_to, date_to)
            current = overlap_dates_start.date()
            end_d = overlap_dates_end.date()

            while current <= end_d:
                cross_day = rule_end_min <= rule_start_min
                if not cross_day:
                    r_seg_s = datetime(current.year, current.month, current.day,
                                       rule_start_min // 60, rule_start_min % 60)
                    r_seg_e = datetime(current.year, current.month, current.day,
                                       rule_end_min // 60, rule_end_min % 60)
                else:
                    r_seg_s = datetime(current.year, current.month, current.day,
                                       rule_start_min // 60, rule_start_min % 60)
                    next_d = current + timedelta(days=1)
                    r_seg_e = datetime(next_d.year, next_d.month, next_d.day,
                                       rule_end_min // 60, rule_end_min % 60)

                ex_cross = ex_end_min <= ex_start_min
                if not ex_cross:
                    e_seg_s = datetime(current.year, current.month, current.day,
                                       ex_start_min // 60, ex_start_min % 60)
                    e_seg_e = datetime(current.year, current.month, current.day,
                                       ex_end_min // 60, ex_end_min % 60)
                else:
                    e_seg_s = datetime(current.year, current.month, current.day,
                                       ex_start_min // 60, ex_start_min % 60)
                    next_d2 = current + timedelta(days=1)
                    e_seg_e = datetime(next_d2.year, next_d2.month, next_d2.day,
                                       ex_end_min // 60, ex_end_min % 60)

                if _intervals_overlap(r_seg_s, r_seg_e, e_seg_s, e_seg_e):
                    has_time_overlap = True
                    break

                current += timedelta(days=1)
        else:
            has_time_overlap = True

        if has_time_overlap:
            overlap_type = "DUPLICATE"
            if (existing.start_time == start_time and existing.end_time == end_time and
                    abs((existing.date_from - date_from).total_seconds()) < 86400 and
                    abs((existing.date_to - date_to).total_seconds()) < 86400):
                overlap_type = "DUPLICATE"
            elif start_time and end_time and existing.start_time and existing.end_time:
                r_s = _parse_time_to_minutes(start_time)
                r_e = _parse_time_to_minutes(end_time)
                e_s = _parse_time_to_minutes(existing.start_time)
                e_e = _parse_time_to_minutes(existing.end_time)
                if r_e > r_s and e_e > e_s:
                    if r_s >= e_s and r_e <= e_e:
                        overlap_type = "NESTED"
                    elif e_s >= r_s and e_e <= r_e:
                        overlap_type = "CONTAINS"
                    else:
                        overlap_type = "PARTIAL"
                elif r_e <= r_s or e_e <= e_s:
                    overlap_type = "CROSS_DAY"
            overlaps.append({
                "rule_id": existing.id,
                "rule_name": existing.name,
                "rule_scope": existing.freeze_scope.value if existing.freeze_scope else "ALL",
                "rule_date_from": existing.date_from.isoformat() if existing.date_from else None,
                "rule_date_to": existing.date_to.isoformat() if existing.date_to else None,
                "rule_start_time": existing.start_time,
                "rule_end_time": existing.end_time,
                "overlap_type": overlap_type,
            })

    return overlaps


def check_freeze_conflicts(
    db: Session,
    environment_id: int,
    start_time: datetime,
    end_time: datetime,
    scope: models.FreezeRuleScope = models.FreezeRuleScope.ALL,
    exclude_rule_id: Optional[int] = None,
) -> List[models.FreezeRule]:
    q = db.query(models.FreezeRule).filter(
        models.FreezeRule.environment_id == environment_id,
        models.FreezeRule.status == models.FreezeRuleStatus.ACTIVE,
    )
    if exclude_rule_id is not None:
        q = q.filter(models.FreezeRule.id != exclude_rule_id)

    all_rules = q.all()
    matching_rules = []

    for rule in all_rules:
        rule_scope = rule.freeze_scope
        if rule_scope != models.FreezeRuleScope.ALL and rule_scope != scope:
            continue

        if rule.date_from >= end_time or rule.date_to <= start_time:
            continue

        rule_start_min = _parse_time_to_minutes(rule.start_time) if rule.start_time else None
        rule_end_min = _parse_time_to_minutes(rule.end_time) if rule.end_time else None

        if not _check_daily_time_overlap_exact(start_time, end_time, rule_start_min, rule_end_min):
            continue

        matching_rules.append(rule)

    return matching_rules


def check_freeze_for_date(
    db: Session,
    environment_id: int,
    check_date: date,
    start_time_str: Optional[str] = None,
    end_time_str: Optional[str] = None,
    scope: models.FreezeRuleScope = models.FreezeRuleScope.ALL,
) -> List[models.FreezeRule]:
    start_dt = datetime(check_date.year, check_date.month, check_date.day, 0, 0)
    end_dt = datetime(check_date.year, check_date.month, check_date.day, 23, 59, 59)
    
    if start_time_str:
        try:
            h, m = map(int, start_time_str.split(":"))
            start_dt = datetime(check_date.year, check_date.month, check_date.day, h, m)
        except ValueError:
            pass
    
    if end_time_str:
        try:
            h, m = map(int, end_time_str.split(":"))
            end_dt = datetime(check_date.year, check_date.month, check_date.day, h, m)
        except ValueError:
            pass
    
    return check_freeze_conflicts(db, environment_id, start_dt, end_dt, scope)


def create_freeze_rule(
    db: Session, rule_in: schemas.FreezeRuleCreate
) -> models.FreezeRule:
    _check_freeze_manage_permission(db, rule_in.creator_id)

    env = get_environment(db, rule_in.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={rule_in.environment_id} 不存在", 404)

    creator = get_user(db, rule_in.creator_id)
    if not creator:
        raise BusinessError(f"创建人 ID={rule_in.creator_id} 不存在", 404)

    if rule_in.date_to <= rule_in.date_from:
        raise BusinessError("冻结结束时间必须晚于开始时间")

    if rule_in.start_time and rule_in.end_time:
        try:
            sh, sm = map(int, rule_in.start_time.split(":"))
            eh, em = map(int, rule_in.end_time.split(":"))
        except ValueError:
            raise BusinessError("时间格式不正确，应为 HH:MM")

    existing = db.query(models.FreezeRule).filter(
        models.FreezeRule.name == rule_in.name,
        models.FreezeRule.environment_id == rule_in.environment_id,
    ).first()
    if existing:
        raise BusinessError(f"同一环境下冻结规则名称 '{rule_in.name}' 已存在")

    scope_map = {s.value: s for s in models.FreezeRuleScope}
    freeze_scope = scope_map.get(rule_in.freeze_scope, models.FreezeRuleScope.ALL)

    status_map = {s.value: s for s in models.FreezeRuleStatus}
    freeze_status = status_map.get(rule_in.status, models.FreezeRuleStatus.ACTIVE) if hasattr(rule_in, 'status') and rule_in.status else models.FreezeRuleStatus.ACTIVE

    overlap_warnings = _detect_rule_time_overlaps(
        db, rule_in.environment_id, rule_in.date_from, rule_in.date_to,
        rule_in.start_time, rule_in.end_time, freeze_scope,
    )

    db_rule = models.FreezeRule(
        name=rule_in.name,
        description=rule_in.description,
        environment_id=rule_in.environment_id,
        freeze_scope=freeze_scope,
        date_from=rule_in.date_from,
        date_to=rule_in.date_to,
        start_time=rule_in.start_time,
        end_time=rule_in.end_time,
        reason=rule_in.reason,
        status=freeze_status,
        remark=rule_in.remark,
        creator_id=rule_in.creator_id,
    )
    db.add(db_rule)
    db.flush()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_CREATE, rule_in.creator_id,
        detail=f"创建冻结规则: {rule_in.name}" +
               (f"，检测到{len(overlap_warnings)}条重叠规则" if overlap_warnings else ""),
    )

    db.flush()

    revalidation = revalidate_after_freeze_change(
        db, db_rule, rule_in.creator_id, "create"
    )

    db.commit()
    db.refresh(db_rule)
    return db_rule


def get_freeze_rule(db: Session, rule_id: int) -> Optional[models.FreezeRule]:
    return db.query(models.FreezeRule).filter(models.FreezeRule.id == rule_id).first()


def list_freeze_rules(
    db: Session,
    environment_id: Optional[int] = None,
    status: Optional[models.FreezeRuleStatus] = None,
    active_only: bool = False,
) -> List[models.FreezeRule]:
    q = db.query(models.FreezeRule)
    if environment_id is not None:
        q = q.filter(models.FreezeRule.environment_id == environment_id)
    if status is not None:
        q = q.filter(models.FreezeRule.status == status)
    if active_only:
        q = q.filter(models.FreezeRule.status == models.FreezeRuleStatus.ACTIVE)
    return q.order_by(models.FreezeRule.created_at.desc()).all()


def update_freeze_rule(
    db: Session, rule_id: int, rule_in: schemas.FreezeRuleUpdate, operator_id: int
) -> models.FreezeRule:
    _check_freeze_manage_permission(db, operator_id)

    db_rule = get_freeze_rule(db, rule_id)
    if not db_rule:
        raise BusinessError(f"冻结规则 ID={rule_id} 不存在", 404)

    update_data = rule_in.model_dump(exclude_unset=True)

    if "name" in update_data:
        existing = db.query(models.FreezeRule).filter(
            models.FreezeRule.name == update_data["name"],
            models.FreezeRule.environment_id == db_rule.environment_id,
            models.FreezeRule.id != rule_id,
        ).first()
        if existing:
            raise BusinessError(f"同一环境下冻结规则名称 '{update_data['name']}' 已存在")

    if "environment_id" in update_data:
        env = get_environment(db, update_data["environment_id"])
        if not env:
            raise BusinessError(f"环境 ID={update_data['environment_id']} 不存在", 404)

    new_date_from = update_data.get("date_from", db_rule.date_from)
    new_date_to = update_data.get("date_to", db_rule.date_to)
    if new_date_to <= new_date_from:
        raise BusinessError("冻结结束时间必须晚于开始时间")

    if "freeze_scope" in update_data:
        scope_map = {s.value: s for s in models.FreezeRuleScope}
        update_data["freeze_scope"] = scope_map.get(
            update_data["freeze_scope"], models.FreezeRuleScope.ALL
        )

    new_start_time = update_data.get("start_time", db_rule.start_time)
    new_end_time = update_data.get("end_time", db_rule.end_time)
    new_env_id = update_data.get("environment_id", db_rule.environment_id)
    new_scope = update_data.get("freeze_scope", db_rule.freeze_scope)

    overlap_warnings = _detect_rule_time_overlaps(
        db, new_env_id, new_date_from, new_date_to,
        new_start_time, new_end_time, new_scope,
        exclude_rule_id=rule_id,
    )

    has_time_change = (
        "date_from" in update_data or
        "date_to" in update_data or
        "start_time" in update_data or
        "end_time" in update_data or
        "environment_id" in update_data or
        "freeze_scope" in update_data
    )

    for k, v in update_data.items():
        setattr(db_rule, k, v)

    db_rule.updated_at = datetime.utcnow()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_UPDATE, operator_id,
        detail=f"更新冻结规则: {db_rule.name}" +
               (f"，检测到{len(overlap_warnings)}条重叠规则" if overlap_warnings else ""),
    )

    if has_time_change and db_rule.status == models.FreezeRuleStatus.ACTIVE:
        db.flush()
        revalidation = revalidate_after_freeze_change(db, db_rule, operator_id, "update")

    db.commit()
    db.refresh(db_rule)
    return db_rule


def delete_freeze_rule(db: Session, rule_id: int, operator_id: int) -> None:
    _check_freeze_manage_permission(db, operator_id)
    
    db_rule = get_freeze_rule(db, rule_id)
    if not db_rule:
        raise BusinessError(f"冻结规则 ID={rule_id} 不存在", 404)

    old_status = db_rule.status
    db_rule.status = models.FreezeRuleStatus.INACTIVE
    db_rule.updated_at = datetime.utcnow()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_DELETE, operator_id,
        detail=f"删除冻结规则: {db_rule.name}",
    )

    db.flush()

    revalidation = revalidate_after_freeze_change(db, db_rule, operator_id, "delete")

    db.delete(db_rule)
    db.commit()


def activate_freeze_rule(
    db: Session, rule_id: int, operator_id: int
) -> models.FreezeRule:
    db_rule = get_freeze_rule(db, rule_id)
    if not db_rule:
        raise BusinessError(f"冻结规则 ID={rule_id} 不存在", 404)

    if not user_can_approve(db, operator_id):
        delegation = can_user_act_as_approver(db, operator_id, db_rule.environment_id, "FREEZE_TOGGLE")
        if not delegation.is_delegated:
            raise BusinessError("只有审批角色可以管理冻结规则", 403)

    old_status = db_rule.status
    db_rule.status = models.FreezeRuleStatus.ACTIVE
    db_rule.updated_at = datetime.utcnow()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_ACTIVATE, operator_id,
        detail=f"启用冻结规则: {db_rule.name}，原状态={old_status.value if old_status else '未知'}",
    )

    db.flush()

    revalidation = revalidate_after_freeze_change(db, db_rule, operator_id, "activate")

    db.commit()
    db.refresh(db_rule)
    return db_rule


def deactivate_freeze_rule(
    db: Session, rule_id: int, operator_id: int
) -> models.FreezeRule:
    db_rule = get_freeze_rule(db, rule_id)
    if not db_rule:
        raise BusinessError(f"冻结规则 ID={rule_id} 不存在", 404)

    if not user_can_approve(db, operator_id):
        delegation = can_user_act_as_approver(db, operator_id, db_rule.environment_id, "FREEZE_TOGGLE")
        if not delegation.is_delegated:
            raise BusinessError("只有审批角色可以管理冻结规则", 403)

    old_status = db_rule.status
    db_rule.status = models.FreezeRuleStatus.INACTIVE
    db_rule.updated_at = datetime.utcnow()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_DEACTIVATE, operator_id,
        detail=f"停用冻结规则: {db_rule.name}，原状态={old_status.value if old_status else '未知'}",
    )

    db.flush()

    revalidation = revalidate_after_freeze_change(db, db_rule, operator_id, "deactivate")

    db.commit()
    db.refresh(db_rule)
    return db_rule


def revalidate_after_freeze_change(
    db: Session,
    rule: models.FreezeRule,
    operator_id: int,
    action: str,
) -> dict:
    result = {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "action": action,
        "hit_count": 0,
        "recovered_count": 0,
        "still_blocked_count": 0,
        "affected_plans": [],
        "affected_windows": [],
    }

    env_id = rule.environment_id

    plans = db.query(models.SchedulePlan).filter(
        models.SchedulePlan.environment_id == env_id,
        models.SchedulePlan.status.in_([
            models.PlanStatus.DRAFT,
            models.PlanStatus.PENDING_APPROVAL,
            models.PlanStatus.APPROVED,
            models.PlanStatus.CONFIRMING,
            models.PlanStatus.CONFIRMED,
        ]),
    ).all()

    for plan in plans:
        plan_affected_items = []
        plan_has_new_hits = False
        plan_has_recoveries = False

        for item in plan.items:
            if item.status in [models.PlanItemStatus.EXCLUDED, models.PlanItemStatus.CREATED]:
                continue

            item_date = date.fromisoformat(item.date)
            start_dt = _combine_datetime(item_date, item.start_time)
            end_dt = _combine_datetime(item_date, item.end_time)

            all_conflicts = check_freeze_conflicts(
                db, env_id, start_dt, end_dt, models.FreezeRuleScope.ALL
            )

            is_hit_by_this_rule = any(c.id == rule.id for c in all_conflicts)
            has_other_conflicts = len(all_conflicts) > 0 and not is_hit_by_this_rule
            has_any_conflict = len(all_conflicts) > 0

            item_result = {
                "item_id": item.id,
                "date": item.date,
                "status_before": item.status.value if item.status else None,
            }

            if has_any_conflict:
                if item.status in [models.PlanItemStatus.APPROVED, models.PlanItemStatus.PENDING,
                                  models.PlanItemStatus.CONFIRMED]:
                    old_status = item.status
                    item.status = models.PlanItemStatus.CHANGED
                    item.current_diff_type = models.DiffType.FREEZE_CONFLICT

                    diff_hints = []
                    for c in all_conflicts:
                        overlap_type = _classify_overlap_type(start_dt, end_dt, c)
                        diff_hints.append({
                            "diff_type": "FREEZE_CONFLICT",
                            "detail": _build_freeze_conflict_reason(c, "ALL", start_dt, end_dt),
                            "rule_id": c.id,
                            "rule_name": c.name,
                            "rule_reason": c.reason,
                            "overlap_type": overlap_type,
                        })
                    item.current_diff_detail = json.dumps(diff_hints, ensure_ascii=False)
                    item.updated_at = datetime.utcnow()

                    for c in all_conflicts:
                        overlap_type = _classify_overlap_type(start_dt, end_dt, c)
                        hit_reason = _build_freeze_conflict_reason(c, "ALL", start_dt, end_dt)
                        record_freeze_hit(
                            db, c, plan.id, item, operator_id,
                            hit_reason=hit_reason,
                            overlap_type=overlap_type,
                        )

                    item_result["status_after"] = "CHANGED"
                    item_result["hit_rules"] = [c.id for c in all_conflicts]
                    item_result["action"] = "hit"
                    plan_has_new_hits = True
                    result["hit_count"] += 1

                elif item.status == models.PlanItemStatus.CHANGED:
                    existing_hits = db.query(models.FreezeHitRecord).filter(
                        models.FreezeHitRecord.item_id == item.id,
                        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
                    ).all()
                    existing_rule_ids = {h.rule_id for h in existing_hits}
                    current_rule_ids = {c.id for c in all_conflicts}
                    
                    new_hit_rules = [c for c in all_conflicts if c.id not in existing_rule_ids]
                    removed_rules = [h for h in existing_hits if h.rule_id not in current_rule_ids]
                    
                    has_changes = False
                    
                    if new_hit_rules:
                        for c in new_hit_rules:
                            overlap_type = _classify_overlap_type(start_dt, end_dt, c)
                            hit_reason = _build_freeze_conflict_reason(c, "ALL", start_dt, end_dt)
                            record_freeze_hit(
                                db, c, plan.id, item, operator_id,
                                hit_reason=hit_reason,
                                overlap_type=overlap_type,
                            )
                        has_changes = True
                        plan_has_new_hits = True
                        result["hit_count"] += 1
                    
                    if removed_rules:
                        for hit in removed_rules:
                            hit.status = models.FreezeHitRecordStatus.RECOVERED
                            hit.recovered_at = datetime.utcnow()
                            hit.recovered_by = operator_id
                            hit.recovery_reason = f"规则{action}后自动恢复"
                            hit.updated_at = datetime.utcnow()
                            
                            recovery_log = models.FreezeRecoveryLog(
                                rule_id=hit.rule_id,
                                rule_name=hit.rule_name,
                                trigger_action=action,
                                plan_id=plan.id,
                                item_id=item.id,
                                item_date=item.date,
                                status_before="CHANGED",
                                status_after="CHANGED",
                                still_blocked_by_rule_ids=json.dumps(list(current_rule_ids)) if current_rule_ids else None,
                                operator_id=operator_id,
                                detail=f"规则{action}: {hit.rule_name}，条目{item.date}，仍被其他{len(current_rule_ids)}条规则拦截",
                            )
                            db.add(recovery_log)
                        has_changes = True
                        plan_has_recoveries = True
                        result["recovered_count"] += len(removed_rules)
                    
                    if has_changes:
                        diff_hints = []
                        for c in all_conflicts:
                            overlap_type = _classify_overlap_type(start_dt, end_dt, c)
                            diff_hints.append({
                                "diff_type": "FREEZE_CONFLICT",
                                "detail": _build_freeze_conflict_reason(c, "ALL", start_dt, end_dt),
                                "rule_id": c.id,
                                "rule_name": c.name,
                                "rule_reason": c.reason,
                                "overlap_type": overlap_type,
                            })
                        item.current_diff_detail = json.dumps(diff_hints, ensure_ascii=False)
                        item.updated_at = datetime.utcnow()
                        
                        item_result["status_after"] = "CHANGED"
                        item_result["hit_rules"] = [c.id for c in all_conflicts]
                        item_result["action"] = "hit_rules_changed"
                    else:
                        item_result["status_after"] = "CHANGED"
                        item_result["action"] = "already_blocked"
                        result["still_blocked_count"] += 1

            else:
                if item.status == models.PlanItemStatus.CHANGED:
                    recovery = _recover_item_if_no_other_freeze(
                        db, item, plan, -1, operator_id, action,
                        rule.name, rule.id,
                    )
                    if recovery["recovered"]:
                        item_result["status_after"] = recovery["status_after"]
                        item_result["action"] = "recovered"
                        plan_has_recoveries = True
                        result["recovered_count"] += 1
                    else:
                        item_result["status_after"] = "CHANGED"
                        item_result["action"] = "still_blocked"
                        result["still_blocked_count"] += 1
                else:
                    active_hit = db.query(models.FreezeHitRecord).filter(
                        models.FreezeHitRecord.rule_id == rule.id,
                        models.FreezeHitRecord.item_id == item.id,
                        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
                    ).first()
                    if active_hit:
                        active_hit.status = models.FreezeHitRecordStatus.RECOVERED
                        active_hit.recovered_at = datetime.utcnow()
                        active_hit.recovered_by = operator_id
                        active_hit.recovery_reason = f"规则{action}后自动恢复"
                        active_hit.updated_at = datetime.utcnow()

                        recovery_log = models.FreezeRecoveryLog(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            trigger_action=action,
                            plan_id=plan.id,
                            item_id=item.id,
                            item_date=item.date,
                            status_before=item.status.value if item.status else None,
                            status_after=item.status.value if item.status else None,
                            still_blocked_by_rule_ids=None,
                            operator_id=operator_id,
                            detail=f"规则{action}: {rule.name}，条目{item.date}，命中记录已恢复",
                        )
                        db.add(recovery_log)

                        item_result["status_after"] = item.status.value if item.status else None
                        item_result["action"] = "hit_record_recovered"
                        plan_has_recoveries = True
                        result["recovered_count"] += 1
                    else:
                        item_result["status_after"] = item.status.value if item.status else None
                        item_result["action"] = "no_change"

            plan_affected_items.append(item_result)

        if plan_has_new_hits or plan_has_recoveries:
            if plan.status in [models.PlanStatus.APPROVED, models.PlanStatus.CONFIRMED] and plan_has_new_hits:
                plan.status = models.PlanStatus.CONFIRMING
                plan.updated_at = datetime.utcnow()

            elif plan.status == models.PlanStatus.CONFIRMING and plan_has_recoveries:
                has_any_changed = any(
                    it.status == models.PlanItemStatus.CHANGED
                    for it in plan.items
                    if it.status not in [models.PlanItemStatus.EXCLUDED, models.PlanItemStatus.CREATED]
                )
                if not has_any_changed:
                    plan.status = models.PlanStatus.APPROVED
                    plan.updated_at = datetime.utcnow()

            result["affected_plans"].append({
                "plan_id": plan.id,
                "plan_name": plan.name,
                "plan_status_before": plan.status.value,
                "plan_status_after": plan.status.value,
                "has_new_hits": plan_has_new_hits,
                "has_recoveries": plan_has_recoveries,
                "items": plan_affected_items,
            })

            _add_plan_audit_log(
                db, plan,
                models.PlanAction.PLAN_FREEZE_HIT if plan_has_new_hits else models.PlanAction.PLAN_FREEZE_RECOVER,
                operator_id,
                detail=f"冻结规则{action}后重校验: {rule.name}，"
                       f"新命中{sum(1 for i in plan_affected_items if i.get('action') == 'hit')}条，"
                       f"恢复{sum(1 for i in plan_affected_items if i.get('action') == 'recovered')}条",
            )

    windows = db.query(models.MaintenanceWindow).filter(
        models.MaintenanceWindow.environment_id == env_id,
    ).all()

    for win in windows:
        all_conflicts = check_freeze_conflicts(
            db, env_id, win.start_time, win.end_time, models.FreezeRuleScope.ALL
        )
        has_any_conflict = len(all_conflicts) > 0
        is_hit_by_this_rule = any(c.id == rule.id for c in all_conflicts)

        if is_hit_by_this_rule:
            result["affected_windows"].append({
                "window_id": win.id,
                "title": win.title,
                "status": win.status.value if win.status else None,
                "has_conflict": has_any_conflict,
                "hit_by_this_rule": True,
            })
            for c in all_conflicts:
                _add_freeze_audit_log(
                    db, c, models.FreezeAction.FREEZE_HIT_WINDOW, operator_id,
                    detail=_build_freeze_conflict_reason(c, "ALL", win.start_time, win.end_time),
                    target_window_id=win.id,
                )

    _add_freeze_audit_log(
        db, rule, models.FreezeAction.FREEZE_RECOVER, operator_id,
        detail=f"规则{action}后自动重校验: 新命中{result['hit_count']}条，"
               f"恢复{result['recovered_count']}条，仍拦截{result['still_blocked_count']}条",
    )

    return result


def get_freeze_audit_logs(
    db: Session, rule_id: int
) -> List[models.FreezeAuditLog]:
    return db.query(models.FreezeAuditLog).filter(
        models.FreezeAuditLog.rule_id == rule_id
    ).order_by(models.FreezeAuditLog.created_at.desc()).all()


def _build_freeze_conflict_reason(rule: models.FreezeRule, scope: str, check_start: Optional[datetime] = None, check_end: Optional[datetime] = None) -> str:
    reason_parts = []
    reason_parts.append(f"冻结规则「{rule.name}」")
    if rule.reason:
        reason_parts.append(f"（{rule.reason}）")
    reason_parts.append(f"禁止{scope}操作")
    if rule.start_time and rule.end_time:
        reason_parts.append(f"，每日 {rule.start_time}-{rule.end_time}")
    if check_start and check_end:
        overlap_type = _classify_overlap_type(check_start, check_end, rule)
        if overlap_type != "PARTIAL":
            reason_parts.append(f"，重叠类型={overlap_type}")
    return "".join(reason_parts)


def check_window_freeze_and_raise(
    db: Session,
    environment_id: int,
    start_time: datetime,
    end_time: datetime,
    scope: models.FreezeRuleScope,
    operator_id: int,
    window_id: Optional[int] = None,
) -> None:
    conflicts = check_freeze_conflicts(db, environment_id, start_time, end_time, scope)
    if not conflicts:
        return

    for rule in conflicts:
        _add_freeze_audit_log(
            db, rule, models.FreezeAction.FREEZE_HIT_WINDOW, operator_id,
            detail=_build_freeze_conflict_reason(rule, scope.value, start_time, end_time),
            target_window_id=window_id,
        )

    db.commit()

    conflict_descs = []
    for rule in conflicts:
        overlap_type = _classify_overlap_type(start_time, end_time, rule)
        desc = f"[{rule.name}] {rule.reason or '冻结期'} (重叠类型={overlap_type})"
        conflict_descs.append(desc)

    raise BusinessError(
        f"操作被冻结规则拦截: {'; '.join(conflict_descs)}",
        403,
    )


def check_plan_freeze_for_items(
    db: Session,
    plan_id: int,
    environment_id: int,
    items: List,
    scope: models.FreezeRuleScope,
    operator_id: int,
) -> List[dict]:
    conflicts_info = []

    for item in items:
        if item.status == models.PlanItemStatus.EXCLUDED:
            continue
        item_date = date.fromisoformat(item.date)
        start_dt = _combine_datetime(item_date, item.start_time)
        end_dt = _combine_datetime(item_date, item.end_time)

        conflicts = check_freeze_conflicts(db, environment_id, start_dt, end_dt, scope)

        for rule in conflicts:
            overlap_type = _classify_overlap_type(start_dt, end_dt, rule)
            hit_reason = _build_freeze_conflict_reason(rule, scope.value, start_dt, end_dt)
            _add_freeze_audit_log(
                db, rule, models.FreezeAction.FREEZE_HIT_PLAN, operator_id,
                detail=hit_reason,
                target_plan_id=plan_id,
                target_item_id=item.id,
            )
            record_freeze_hit(
                db, rule, plan_id, item, operator_id,
                hit_reason=hit_reason,
                overlap_type=overlap_type,
            )
            conflicts_info.append({
                "item_id": item.id,
                "date": item.date,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "freeze_scope": rule.freeze_scope.value if rule.freeze_scope else None,
                "reason": rule.reason,
                "overlap_type": overlap_type,
                "conflict_reason": hit_reason,
            })

    if conflicts_info:
        db.commit()

    return conflicts_info


# ============== Freeze Rule Import/Export ==============

def export_freeze_rules(
    db: Session,
    rule_ids: Optional[List[int]] = None,
    environment_id: Optional[int] = None,
) -> List[dict]:
    q = db.query(models.FreezeRule)
    if rule_ids:
        q = q.filter(models.FreezeRule.id.in_(rule_ids))
    if environment_id is not None:
        q = q.filter(models.FreezeRule.environment_id == environment_id)
    
    rules = q.all()
    result = []
    
    for rule in rules:
        env = rule.environment
        creator = rule.creator
        
        audit_logs_data = []
        for log in rule.audit_logs:
            operator = log.operator
            audit_logs_data.append({
                "action": log.action.value if log.action else None,
                "operator_username": operator.username if operator else None,
                "operator_name": operator.display_name if operator else None,
                "detail": log.detail,
                "snapshot": json.loads(log.snapshot) if log.snapshot else {},
                "target_window_id": log.target_window_id,
                "target_plan_id": log.target_plan_id,
                "target_item_id": log.target_item_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })
        
        result.append({
            "name": rule.name,
            "description": rule.description,
            "environment_name": env.name if env else None,
            "freeze_scope": rule.freeze_scope.value if rule.freeze_scope else "ALL",
            "date_from": rule.date_from.isoformat() if rule.date_from else None,
            "date_to": rule.date_to.isoformat() if rule.date_to else None,
            "start_time": rule.start_time,
            "end_time": rule.end_time,
            "reason": rule.reason,
            "status": rule.status.value if rule.status else "ACTIVE",
            "remark": rule.remark,
            "creator_username": creator.username if creator else None,
            "created_at": rule.created_at.isoformat() if rule.created_at else None,
            "audit_logs": audit_logs_data,
        })
    
    return result


def import_freeze_rules(
    db: Session, req: schemas.FreezeImportRequest
) -> schemas.FreezeImportResult:
    _check_freeze_manage_permission(db, req.operator_id)
    
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)
    
    total = len(req.rules)
    success = 0
    skipped = 0
    failed = 0
    details = []
    
    for idx, item in enumerate(req.rules):
        try:
            env = get_environment_by_name(db, item.environment_name)
            if not env:
                failed += 1
                details.append({
                    "index": idx,
                    "name": item.name,
                    "status": "failed",
                    "reason": f"环境 '{item.environment_name}' 不存在",
                })
                continue
            
            existing = db.query(models.FreezeRule).filter(
                models.FreezeRule.name == item.name,
                models.FreezeRule.environment_id == env.id,
            ).first()
            
            if existing:
                if req.on_conflict == "skip":
                    skipped += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "skipped",
                        "reason": "同名冻结规则已存在，跳过",
                    })
                    continue
                elif req.on_conflict == "overwrite":
                    db.delete(existing)
                    db.flush()
                else:
                    failed += 1
                    details.append({
                        "index": idx,
                        "name": item.name,
                        "status": "failed",
                        "reason": "同名冻结规则已存在",
                    })
                    continue
            
            scope_map = {s.value: s for s in models.FreezeRuleScope}
            freeze_scope = scope_map.get(item.freeze_scope, models.FreezeRuleScope.ALL)
            
            status_map = {s.value: s for s in models.FreezeRuleStatus}
            rule_status = status_map.get(item.status, models.FreezeRuleStatus.ACTIVE)
            
            date_from = None
            date_to = None
            if item.date_from:
                date_from = datetime.fromisoformat(item.date_from)
            if item.date_to:
                date_to = datetime.fromisoformat(item.date_to)
            
            resolved_creator_id = req.operator_id
            if item.creator_username:
                creator_user = db.query(models.User).filter(
                    models.User.username == item.creator_username
                ).first()
                if creator_user:
                    resolved_creator_id = creator_user.id
            
            db_rule = models.FreezeRule(
                name=item.name,
                description=item.description,
                environment_id=env.id,
                freeze_scope=freeze_scope,
                date_from=date_from,
                date_to=date_to,
                start_time=item.start_time,
                end_time=item.end_time,
                reason=item.reason,
                status=rule_status,
                remark=item.remark,
                creator_id=resolved_creator_id,
            )
            if item.created_at:
                db_rule.created_at = datetime.fromisoformat(item.created_at)
            db.add(db_rule)
            db.flush()
            
            if item.audit_logs:
                for log_data in item.audit_logs:
                    log_dict = log_data if isinstance(log_data, dict) else log_data
                    if not log_dict:
                        continue
                    
                    action_map = {a.value: a for a in models.FreezeAction}
                    log_action = action_map.get(log_dict.get("action"), models.FreezeAction.FREEZE_IMPORT)
                    
                    op_username = log_dict.get("operator_username")
                    resolved_op_id = req.operator_id
                    if op_username:
                        op_user = db.query(models.User).filter(
                            models.User.username == op_username
                        ).first()
                        if op_user:
                            resolved_op_id = op_user.id
                    
                    snapshot_val = None
                    if log_dict.get("snapshot"):
                        snapshot_val = json.dumps(log_dict["snapshot"], ensure_ascii=False)
                    
                    audit_log = models.FreezeAuditLog(
                        rule_id=db_rule.id,
                        action=log_action,
                        operator_id=resolved_op_id,
                        detail=log_dict.get("detail"),
                        snapshot=snapshot_val,
                        target_window_id=log_dict.get("target_window_id"),
                        target_plan_id=log_dict.get("target_plan_id"),
                        target_item_id=log_dict.get("target_item_id"),
                    )
                    if log_dict.get("created_at"):
                        audit_log.created_at = datetime.fromisoformat(log_dict["created_at"])
                    db.add(audit_log)
            
            _add_freeze_audit_log(
                db, db_rule, models.FreezeAction.FREEZE_IMPORT, req.operator_id,
                detail=f"导入冻结规则: {item.name}，状态={rule_status.value}",
            )
            
            success += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "created",
                "id": db_rule.id,
                "audit_logs_restored": len(item.audit_logs) if item.audit_logs else 0,
            })
            
        except BusinessError as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": e.message,
            })
        except Exception as e:
            failed += 1
            details.append({
                "index": idx,
                "name": item.name,
                "status": "failed",
                "reason": str(e),
            })
    
    db.commit()
    
    return schemas.FreezeImportResult(
        total=total,
        success=success,
        skipped=skipped,
        failed=failed,
        details=details,
    )


# ============== Freeze Recovery Center Service ==============

def record_freeze_hit(
    db: Session,
    rule: models.FreezeRule,
    plan_id: int,
    item: models.SchedulePlanItem,
    operator_id: int,
    hit_reason: Optional[str] = None,
    overlap_type: Optional[str] = None,
) -> models.FreezeHitRecord:
    existing_active = db.query(models.FreezeHitRecord).filter(
        models.FreezeHitRecord.rule_id == rule.id,
        models.FreezeHitRecord.item_id == item.id,
        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
    ).first()
    if existing_active:
        return existing_active

    record = models.FreezeHitRecord(
        rule_id=rule.id,
        rule_name=rule.name,
        plan_id=plan_id,
        item_id=item.id,
        item_date=item.date,
        item_start_time=item.start_time,
        item_end_time=item.end_time,
        item_status_before=item.status.value if item.status else None,
        freeze_scope=rule.freeze_scope,
        hit_reason=hit_reason or rule.reason,
        overlap_type=overlap_type,
        status=models.FreezeHitRecordStatus.ACTIVE,
        operator_id=operator_id,
    )
    db.add(record)

    _add_plan_audit_log(
        db, db.query(models.SchedulePlan).get(plan_id),
        models.PlanAction.PLAN_FREEZE_HIT, operator_id,
        item_id=item.id,
        detail=f"条目 {item.date} 被冻结规则「{rule.name}」命中: {hit_reason or rule.reason or '冻结拦截'}",
    )

    return record


def _recover_item_if_no_other_freeze(
    db: Session,
    item: models.SchedulePlanItem,
    plan: models.SchedulePlan,
    excluded_rule_id: int,
    operator_id: int,
    trigger_action: str,
    rule_name: str,
    rule_id: int,
) -> dict:
    item_date = date.fromisoformat(item.date)
    start_dt = _combine_datetime(item_date, item.start_time)
    end_dt = _combine_datetime(item_date, item.end_time)

    remaining_conflicts = check_freeze_conflicts(
        db, plan.environment_id, start_dt, end_dt,
        models.FreezeRuleScope.ALL, exclude_rule_id=excluded_rule_id,
    )

    still_blocked_by = []
    if remaining_conflicts:
        still_blocked_by = [c.id for c in remaining_conflicts]

    status_before = item.status.value if item.status else None
    status_after = status_before

    if not remaining_conflicts:
        if item.status == models.PlanItemStatus.CHANGED:
            item.status = models.PlanItemStatus.APPROVED
            item.current_diff_type = None
            item.current_diff_detail = None
        status_after = item.status.value if item.status else None
        item.updated_at = datetime.utcnow()

    active_hit = db.query(models.FreezeHitRecord).filter(
        models.FreezeHitRecord.rule_id == rule_id,
        models.FreezeHitRecord.item_id == item.id,
        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
    ).first()

    if active_hit:
        if not remaining_conflicts:
            active_hit.status = models.FreezeHitRecordStatus.RECOVERED
            active_hit.recovered_at = datetime.utcnow()
            active_hit.recovered_by = operator_id
            active_hit.recovery_reason = f"规则{trigger_action}后自动恢复"
            active_hit.updated_at = datetime.utcnow()
        else:
            active_hit.hit_reason = f"仍被其他规则拦截: {','.join([str(r.id) for r in remaining_conflicts])}"
            active_hit.updated_at = datetime.utcnow()

    recovery_log = models.FreezeRecoveryLog(
        rule_id=rule_id,
        rule_name=rule_name,
        trigger_action=trigger_action,
        plan_id=plan.id,
        item_id=item.id,
        item_date=item.date,
        status_before=status_before,
        status_after=status_after,
        still_blocked_by_rule_ids=json.dumps(still_blocked_by) if still_blocked_by else None,
        operator_id=operator_id,
        detail=f"规则{trigger_action}: {rule_name}，条目{item.date}，"
               f"状态 {status_before}->{status_after}"
               + (f"，仍被规则{still_blocked_by}拦截" if still_blocked_by else "，已恢复"),
    )
    db.add(recovery_log)

    return {
        "item_id": item.id,
        "date": item.date,
        "status_before": status_before,
        "status_after": status_after,
        "recovered": not bool(remaining_conflicts),
        "still_blocked_by": still_blocked_by,
    }


def _auto_recover_for_rule_change(
    db: Session,
    rule: models.FreezeRule,
    operator_id: int,
    trigger_action: str,
) -> schemas.FreezeRecoveryResult:
    result_details = []
    recovered_count = 0
    still_blocked_count = 0

    active_hits = db.query(models.FreezeHitRecord).filter(
        models.FreezeHitRecord.rule_id == rule.id,
        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
    ).all()

    plan_ids = list(set([h.plan_id for h in active_hits]))

    for plan_id in plan_ids:
        plan = db.query(models.SchedulePlan).get(plan_id)
        if not plan:
            continue

        plan_hits = [h for h in active_hits if h.plan_id == plan_id]
        sorted_hits = sorted(plan_hits, key=lambda h: h.item_date)

        for hit in sorted_hits:
            item = db.query(models.SchedulePlanItem).get(hit.item_id)
            if not item:
                continue
            if item.status in [models.PlanItemStatus.EXCLUDED, models.PlanItemStatus.CREATED]:
                continue

            recovery = _recover_item_if_no_other_freeze(
                db, item, plan, rule.id, operator_id, trigger_action,
                rule.name, rule.id,
            )
            result_details.append(recovery)
            if recovery["recovered"]:
                recovered_count += 1
            else:
                still_blocked_count += 1

    all_items_recovered = True
    for plan_id in plan_ids:
        plan = db.query(models.SchedulePlan).get(plan_id)
        if not plan:
            continue
        if plan.status == models.PlanStatus.CONFIRMING:
            has_changed = any(
                item.status == models.PlanItemStatus.CHANGED
                for item in plan.items
                if item.status not in [models.PlanItemStatus.EXCLUDED, models.PlanItemStatus.CREATED]
            )
            if not has_changed:
                plan.status = models.PlanStatus.APPROVED
                plan.updated_at = datetime.utcnow()
                all_items_recovered = all_items_recovered and True
            else:
                all_items_recovered = False
        else:
            all_items_recovered = all_items_recovered and True

    _add_freeze_audit_log(
        db, rule, models.FreezeAction.FREEZE_RECOVER, operator_id,
        detail=f"规则{trigger_action}后自动恢复: 恢复{recovered_count}条，仍被拦截{still_blocked_count}条",
    )

    return schemas.FreezeRecoveryResult(
        rule_id=rule.id,
        rule_name=rule.name,
        action=trigger_action,
        recovered_items=recovered_count,
        still_blocked_items=still_blocked_count,
        details=result_details,
    )


def revoke_freeze_rule(
    db: Session, rule_id: int, operator_id: int, reason: Optional[str] = None
) -> schemas.FreezeRecoveryResult:
    _check_freeze_manage_permission(db, operator_id)

    db_rule = get_freeze_rule(db, rule_id)
    if not db_rule:
        raise BusinessError(f"冻结规则 ID={rule_id} 不存在", 404)

    if db_rule.status != models.FreezeRuleStatus.ACTIVE:
        raise BusinessError("只有生效中的规则可以撤销", 400)

    db_rule.status = models.FreezeRuleStatus.INACTIVE
    db_rule.updated_at = datetime.utcnow()

    _add_freeze_audit_log(
        db, db_rule, models.FreezeAction.FREEZE_REVOKE, operator_id,
        detail=f"人工撤销冻结规则: {db_rule.name}" + (f"，原因: {reason}" if reason else ""),
    )

    recovery_result = _auto_recover_for_rule_change(
        db, db_rule, operator_id, "revoke"
    )

    db.commit()
    db.refresh(db_rule)
    return recovery_result


def get_freeze_hit_records(
    db: Session,
    rule_id: Optional[int] = None,
    plan_id: Optional[int] = None,
    status: Optional[models.FreezeHitRecordStatus] = None,
) -> List[models.FreezeHitRecord]:
    q = db.query(models.FreezeHitRecord)
    if rule_id is not None:
        q = q.filter(models.FreezeHitRecord.rule_id == rule_id)
    if plan_id is not None:
        q = q.filter(models.FreezeHitRecord.plan_id == plan_id)
    if status is not None:
        q = q.filter(models.FreezeHitRecord.status == status)
    return q.order_by(models.FreezeHitRecord.created_at.desc()).all()


def get_freeze_recovery_logs(
    db: Session,
    rule_id: Optional[int] = None,
    plan_id: Optional[int] = None,
) -> List[models.FreezeRecoveryLog]:
    q = db.query(models.FreezeRecoveryLog)
    if rule_id is not None:
        q = q.filter(models.FreezeRecoveryLog.rule_id == rule_id)
    if plan_id is not None:
        q = q.filter(models.FreezeRecoveryLog.plan_id == plan_id)
    return q.order_by(models.FreezeRecoveryLog.created_at.desc()).all()


def get_recovery_center_summary(db: Session) -> schemas.FreezeRecoveryCenterSummary:
    active_hits = db.query(models.FreezeHitRecord).filter(
        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.ACTIVE,
    ).all()

    recovered_hits = db.query(models.FreezeHitRecord).filter(
        models.FreezeHitRecord.status == models.FreezeHitRecordStatus.RECOVERED,
    ).all()

    by_rule = {}
    for h in active_hits:
        key = h.rule_id
        if key not in by_rule:
            by_rule[key] = {"rule_id": h.rule_id, "rule_name": h.rule_name, "active_hits": 0}
        by_rule[key]["active_hits"] += 1

    by_plan = {}
    for h in active_hits:
        key = h.plan_id
        if key not in by_plan:
            by_plan[key] = {"plan_id": h.plan_id, "active_hits": 0}
        by_plan[key]["active_hits"] += 1

    still_blocked = sum(1 for h in active_hits if h.status == models.FreezeHitRecordStatus.ACTIVE)

    return schemas.FreezeRecoveryCenterSummary(
        total_active_hits=len(active_hits),
        total_recovered=len(recovered_hits),
        total_still_blocked=still_blocked,
        by_rule=list(by_rule.values()),
        by_plan=list(by_plan.values()),
    )


# ============== Approval Proxy Center Service ==============

VALID_DELEGATE_SCOPES = {"WINDOW_APPROVE", "PLAN_CONFIRM", "FREEZE_TOGGLE"}


def _proxy_snapshot(proxy: models.ApprovalProxy) -> str:
    return json.dumps({
        "id": proxy.id,
        "approver_id": proxy.approver_id,
        "proxy_user_id": proxy.proxy_user_id,
        "environment_id": proxy.environment_id,
        "delegate_scope": json.loads(proxy.delegate_scope) if isinstance(proxy.delegate_scope, str) else proxy.delegate_scope,
        "valid_from": proxy.valid_from.isoformat() if proxy.valid_from else None,
        "valid_to": proxy.valid_to.isoformat() if proxy.valid_to else None,
        "status": proxy.status.value if proxy.status else None,
        "reason": proxy.reason,
        "remark": proxy.remark,
        "creator_id": proxy.creator_id,
    }, ensure_ascii=False)


def _add_proxy_audit_log(
    db: Session,
    proxy: models.ApprovalProxy,
    action: models.ProxyAction,
    operator_id: int,
    detail: Optional[str] = None,
    target_window_id: Optional[int] = None,
    target_plan_id: Optional[int] = None,
    target_item_id: Optional[int] = None,
):
    log = models.ProxyAuditLog(
        proxy_id=proxy.id,
        action=action,
        operator_id=operator_id,
        detail=detail,
        snapshot=_proxy_snapshot(proxy),
        target_window_id=target_window_id,
        target_plan_id=target_plan_id,
        target_item_id=target_item_id,
    )
    db.add(log)


def expire_stale_proxies(db: Session) -> int:
    now = datetime.utcnow()
    expired_count = 0
    active_proxies = db.query(models.ApprovalProxy).filter(
        models.ApprovalProxy.status == models.ProxyStatus.ACTIVE,
        models.ApprovalProxy.valid_to < now,
    ).all()
    for proxy in active_proxies:
        proxy.status = models.ProxyStatus.EXPIRED
        proxy.updated_at = now
        _add_proxy_audit_log(
            db, proxy, models.ProxyAction.PROXY_EXPIRE, proxy.creator_id,
            detail=f"代理授权过期: {proxy.valid_to.isoformat()}",
        )
        expired_count += 1
    if expired_count:
        db.commit()
    return expired_count


def create_approval_proxy(
    db: Session, proxy_in: schemas.ApprovalProxyCreate
) -> models.ApprovalProxy:
    if not user_can_approve(db, proxy_in.approver_id):
        raise BusinessError("被代理人(approver_id)必须拥有审批权限", 403)

    approver = get_user(db, proxy_in.approver_id)
    if not approver:
        raise BusinessError(f"审批人 ID={proxy_in.approver_id} 不存在", 404)

    proxy_user = get_user(db, proxy_in.proxy_user_id)
    if not proxy_user:
        raise BusinessError(f"代理人 ID={proxy_in.proxy_user_id} 不存在", 404)

    if proxy_in.approver_id == proxy_in.proxy_user_id:
        raise BusinessError("不能把代理授权给自己", 400)

    env = get_environment(db, proxy_in.environment_id)
    if not env:
        raise BusinessError(f"环境 ID={proxy_in.environment_id} 不存在", 404)

    creator = get_user(db, proxy_in.creator_id)
    if not creator:
        raise BusinessError(f"创建人 ID={proxy_in.creator_id} 不存在", 404)

    if proxy_in.creator_id != proxy_in.approver_id:
        if not user_can_approve(db, proxy_in.creator_id):
            raise BusinessError("只有审批角色可以创建代理授权", 403)

    delegate_scope_str = json.dumps(proxy_in.delegate_scope, ensure_ascii=False)
    overlapping = db.query(models.ApprovalProxy).filter(
        models.ApprovalProxy.approver_id == proxy_in.approver_id,
        models.ApprovalProxy.proxy_user_id == proxy_in.proxy_user_id,
        models.ApprovalProxy.environment_id == proxy_in.environment_id,
        models.ApprovalProxy.status == models.ProxyStatus.ACTIVE,
        models.ApprovalProxy.valid_to > proxy_in.valid_from,
        models.ApprovalProxy.valid_from < proxy_in.valid_to,
    ).all()

    for existing in overlapping:
        existing_scope = json.loads(existing.delegate_scope) if isinstance(existing.delegate_scope, str) else existing.delegate_scope
        if set(proxy_in.delegate_scope) & set(existing_scope):
            raise BusinessError(
                f"同一审批人→代理人在同一环境下存在时段重叠的活跃代理授权"
                f"(ID={existing.id}, 时段={existing.valid_from.isoformat()}~{existing.valid_to.isoformat()})"
                f"，代理范围冲突: {set(proxy_in.delegate_scope) & set(existing_scope)}",
                409,
            )

    conflict_proxies = db.query(models.ApprovalProxy).filter(
        models.ApprovalProxy.proxy_user_id == proxy_in.proxy_user_id,
        models.ApprovalProxy.environment_id == proxy_in.environment_id,
        models.ApprovalProxy.status == models.ProxyStatus.ACTIVE,
        models.ApprovalProxy.valid_to > proxy_in.valid_from,
        models.ApprovalProxy.valid_from < proxy_in.valid_to,
    ).all()

    env_conflicts = []
    for cp in conflict_proxies:
        cp_scope = json.loads(cp.delegate_scope) if isinstance(cp.delegate_scope, str) else cp.delegate_scope
        if set(proxy_in.delegate_scope) & set(cp_scope):
            env_conflicts.append({
                "proxy_id": cp.id,
                "approver_id": cp.approver_id,
                "overlapping_scopes": list(set(proxy_in.delegate_scope) & set(cp_scope)),
            })

    if env_conflicts:
        conflict_detail = "; ".join([
            f"与审批人ID={c['approver_id']}的授权ID={c['proxy_id']}范围冲突: {c['overlapping_scopes']}"
            for c in env_conflicts
        ])
        raise BusinessError(
            f"同一代理人在同一环境下存在时段重叠的代理授权，范围冲突: {conflict_detail}",
            409,
        )

    now = datetime.utcnow()
    initial_status = models.ProxyStatus.ACTIVE if proxy_in.valid_from <= now else models.ProxyStatus.INACTIVE

    proxy = models.ApprovalProxy(
        approver_id=proxy_in.approver_id,
        proxy_user_id=proxy_in.proxy_user_id,
        environment_id=proxy_in.environment_id,
        delegate_scope=delegate_scope_str,
        valid_from=proxy_in.valid_from,
        valid_to=proxy_in.valid_to,
        status=initial_status,
        reason=proxy_in.reason,
        remark=proxy_in.remark,
        creator_id=proxy_in.creator_id,
    )
    db.add(proxy)
    db.flush()

    _add_proxy_audit_log(
        db, proxy, models.ProxyAction.PROXY_CREATE, proxy_in.creator_id,
        detail=f"创建代理授权: 审批人={approver.display_name}, 代理人={proxy_user.display_name}, "
               f"环境={env.name}, 范围={proxy_in.delegate_scope}, "
               f"时段={proxy_in.valid_from.isoformat()}~{proxy_in.valid_to.isoformat()}",
    )

    db.commit()
    db.refresh(proxy)
    return proxy


def get_approval_proxy(db: Session, proxy_id: int) -> Optional[models.ApprovalProxy]:
    return db.query(models.ApprovalProxy).filter(models.ApprovalProxy.id == proxy_id).first()


def list_approval_proxies(
    db: Session,
    approver_id: Optional[int] = None,
    proxy_user_id: Optional[int] = None,
    environment_id: Optional[int] = None,
    status: Optional[models.ProxyStatus] = None,
) -> List[models.ApprovalProxy]:
    expire_stale_proxies(db)
    q = db.query(models.ApprovalProxy)
    if approver_id is not None:
        q = q.filter(models.ApprovalProxy.approver_id == approver_id)
    if proxy_user_id is not None:
        q = q.filter(models.ApprovalProxy.proxy_user_id == proxy_user_id)
    if environment_id is not None:
        q = q.filter(models.ApprovalProxy.environment_id == environment_id)
    if status is not None:
        q = q.filter(models.ApprovalProxy.status == status)
    return q.order_by(models.ApprovalProxy.created_at.desc()).all()


def deactivate_approval_proxy(
    db: Session, proxy_id: int, operator_id: int
) -> models.ApprovalProxy:
    proxy = get_approval_proxy(db, proxy_id)
    if not proxy:
        raise BusinessError(f"代理授权 ID={proxy_id} 不存在", 404)

    if proxy.status != models.ProxyStatus.ACTIVE:
        raise BusinessError(f"只有 ACTIVE 状态可以停用，当前状态={proxy.status.value}", 400)

    if operator_id != proxy.approver_id and not user_can_approve(db, operator_id):
        raise BusinessError("只有原审批人或审批角色可以停用代理授权", 403)

    proxy.status = models.ProxyStatus.INACTIVE
    proxy.updated_at = datetime.utcnow()

    _add_proxy_audit_log(
        db, proxy, models.ProxyAction.PROXY_DEACTIVATE, operator_id,
        detail=f"停用代理授权",
    )

    db.commit()
    db.refresh(proxy)
    return proxy


def reactivate_approval_proxy(
    db: Session, proxy_id: int, operator_id: int
) -> models.ApprovalProxy:
    proxy = get_approval_proxy(db, proxy_id)
    if not proxy:
        raise BusinessError(f"代理授权 ID={proxy_id} 不存在", 404)

    if proxy.status not in [models.ProxyStatus.INACTIVE]:
        raise BusinessError(f"只有 INACTIVE 状态可以重新启用，当前状态={proxy.status.value}", 400)

    if operator_id != proxy.approver_id and not user_can_approve(db, operator_id):
        raise BusinessError("只有原审批人或审批角色可以重新启用代理授权", 403)

    if proxy.valid_to <= datetime.utcnow():
        raise BusinessError("代理授权已过期，不能重新启用", 400)

    if not user_can_approve(db, proxy.approver_id):
        raise BusinessError("原审批人已无审批权限，不能重新启用代理授权", 403)

    overlapping = db.query(models.ApprovalProxy).filter(
        models.ApprovalProxy.approver_id == proxy.approver_id,
        models.ApprovalProxy.proxy_user_id == proxy.proxy_user_id,
        models.ApprovalProxy.environment_id == proxy.environment_id,
        models.ApprovalProxy.status == models.ProxyStatus.ACTIVE,
        models.ApprovalProxy.id != proxy.id,
        models.ApprovalProxy.valid_to > proxy.valid_from,
        models.ApprovalProxy.valid_from < proxy.valid_to,
    ).all()

    for existing in overlapping:
        existing_scope = json.loads(existing.delegate_scope) if isinstance(existing.delegate_scope, str) else existing.delegate_scope
        proxy_scope = json.loads(proxy.delegate_scope) if isinstance(proxy.delegate_scope, str) else proxy.delegate_scope
        if set(proxy_scope) & set(existing_scope):
            raise BusinessError(
                f"重新启用会导致与现有活跃代理授权冲突(ID={existing.id})，范围冲突: {set(proxy_scope) & set(existing_scope)}",
                409,
            )

    proxy.status = models.ProxyStatus.ACTIVE
    proxy.updated_at = datetime.utcnow()

    _add_proxy_audit_log(
        db, proxy, models.ProxyAction.PROXY_REACTIVATE, operator_id,
        detail=f"重新启用代理授权",
    )

    db.commit()
    db.refresh(proxy)
    return proxy


def revoke_approval_proxy(
    db: Session, proxy_id: int, operator_id: int, reason: Optional[str] = None
) -> models.ApprovalProxy:
    proxy = get_approval_proxy(db, proxy_id)
    if not proxy:
        raise BusinessError(f"代理授权 ID={proxy_id} 不存在", 404)

    if proxy.status in [models.ProxyStatus.REVOKED, models.ProxyStatus.EXPIRED]:
        raise BusinessError(f"当前状态={proxy.status.value}，不能撤销", 400)

    if operator_id != proxy.approver_id and not user_can_approve(db, operator_id):
        raise BusinessError("只有原审批人或审批角色可以撤销代理授权", 403)

    old_status = proxy.status
    proxy.status = models.ProxyStatus.REVOKED
    proxy.updated_at = datetime.utcnow()

    _add_proxy_audit_log(
        db, proxy, models.ProxyAction.PROXY_REVOKE, operator_id,
        detail=f"撤销代理授权: 原状态={old_status.value}" + (f"，原因: {reason}" if reason else ""),
    )

    db.commit()
    db.refresh(proxy)
    return proxy


def check_proxy_delegation(
    db: Session,
    proxy_user_id: int,
    environment_id: int,
    required_scope: str,
    now: Optional[datetime] = None,
) -> schemas.ProxyDelegationCheckResult:
    expire_stale_proxies(db)
    if now is None:
        now = datetime.utcnow()

    active_proxies = db.query(models.ApprovalProxy).filter(
        models.ApprovalProxy.proxy_user_id == proxy_user_id,
        models.ApprovalProxy.environment_id == environment_id,
        models.ApprovalProxy.status == models.ProxyStatus.ACTIVE,
        models.ApprovalProxy.valid_from <= now,
        models.ApprovalProxy.valid_to > now,
    ).all()

    for proxy in active_proxies:
        scope_list = json.loads(proxy.delegate_scope) if isinstance(proxy.delegate_scope, str) else proxy.delegate_scope
        if required_scope in scope_list:
            return schemas.ProxyDelegationCheckResult(
                is_delegated=True,
                proxy_id=proxy.id,
                original_approver_id=proxy.approver_id,
                delegate_scope=scope_list,
                valid_from=proxy.valid_from,
                valid_to=proxy.valid_to,
            )

    return schemas.ProxyDelegationCheckResult(is_delegated=False)


def can_user_act_as_approver(
    db: Session,
    user_id: int,
    environment_id: int,
    required_scope: str,
) -> schemas.ProxyDelegationCheckResult:
    if user_can_approve(db, user_id):
        return schemas.ProxyDelegationCheckResult(is_delegated=False)

    return check_proxy_delegation(db, user_id, environment_id, required_scope)


def record_proxy_delegate_action(
    db: Session,
    proxy_user_id: int,
    environment_id: int,
    action_scope: str,
    action_description: str,
    target_window_id: Optional[int] = None,
    target_plan_id: Optional[int] = None,
    target_item_id: Optional[int] = None,
) -> Optional[models.ProxyAuditLog]:
    delegation = check_proxy_delegation(db, proxy_user_id, environment_id, action_scope)
    if not delegation.is_delegated:
        return None

    proxy = get_approval_proxy(db, delegation.proxy_id)
    if not proxy:
        return None

    log = models.ProxyAuditLog(
        proxy_id=proxy.id,
        action=models.ProxyAction.PROXY_DELEGATE_ACTION,
        operator_id=proxy_user_id,
        detail=f"代理人代办操作[{action_scope}]: {action_description}",
        snapshot=_proxy_snapshot(proxy),
        target_window_id=target_window_id,
        target_plan_id=target_plan_id,
        target_item_id=target_item_id,
    )
    db.add(log)
    db.flush()
    return log


def record_proxy_delegate_reject(
    db: Session,
    proxy_user_id: int,
    environment_id: int,
    action_scope: str,
    reject_reason: str,
    target_window_id: Optional[int] = None,
    target_plan_id: Optional[int] = None,
    target_item_id: Optional[int] = None,
) -> Optional[models.ProxyAuditLog]:
    delegation = check_proxy_delegation(db, proxy_user_id, environment_id, action_scope)
    if not delegation.is_delegated:
        return None

    proxy = get_approval_proxy(db, delegation.proxy_id)
    if not proxy:
        return None

    log = models.ProxyAuditLog(
        proxy_id=proxy.id,
        action=models.ProxyAction.PROXY_DELEGATE_REJECT,
        operator_id=proxy_user_id,
        detail=f"代理人代办操作被拒[{action_scope}]: {reject_reason}",
        snapshot=_proxy_snapshot(proxy),
        target_window_id=target_window_id,
        target_plan_id=target_plan_id,
        target_item_id=target_item_id,
    )
    db.add(log)
    db.flush()
    return log


def get_proxy_audit_logs(
    db: Session, proxy_id: int
) -> List[models.ProxyAuditLog]:
    return db.query(models.ProxyAuditLog).filter(
        models.ProxyAuditLog.proxy_id == proxy_id
    ).order_by(models.ProxyAuditLog.created_at.desc()).all()


def export_approval_proxies(
    db: Session,
    proxy_ids: Optional[List[int]] = None,
    approver_id: Optional[int] = None,
    environment_id: Optional[int] = None,
) -> List[dict]:
    expire_stale_proxies(db)
    q = db.query(models.ApprovalProxy)
    if proxy_ids:
        q = q.filter(models.ApprovalProxy.id.in_(proxy_ids))
    if approver_id is not None:
        q = q.filter(models.ApprovalProxy.approver_id == approver_id)
    if environment_id is not None:
        q = q.filter(models.ApprovalProxy.environment_id == environment_id)

    proxies = q.all()
    result = []

    for proxy in proxies:
        approver = proxy.approver
        proxy_user = proxy.proxy_user
        env = proxy.environment
        creator = proxy.creator

        scope_list = json.loads(proxy.delegate_scope) if isinstance(proxy.delegate_scope, str) else proxy.delegate_scope

        audit_logs_data = []
        for log in proxy.audit_logs:
            operator = log.operator
            audit_logs_data.append({
                "action": log.action.value if log.action else None,
                "operator_username": operator.username if operator else None,
                "operator_name": operator.display_name if operator else None,
                "detail": log.detail,
                "snapshot": json.loads(log.snapshot) if log.snapshot else {},
                "target_window_id": log.target_window_id,
                "target_plan_id": log.target_plan_id,
                "target_item_id": log.target_item_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        result.append({
            "approver_username": approver.username if approver else None,
            "proxy_username": proxy_user.username if proxy_user else None,
            "environment_name": env.name if env else None,
            "delegate_scope": scope_list,
            "valid_from": proxy.valid_from.isoformat() if proxy.valid_from else None,
            "valid_to": proxy.valid_to.isoformat() if proxy.valid_to else None,
            "status": proxy.status.value if proxy.status else "ACTIVE",
            "reason": proxy.reason,
            "remark": proxy.remark,
            "creator_username": creator.username if creator else None,
            "created_at": proxy.created_at.isoformat() if proxy.created_at else None,
            "audit_logs": audit_logs_data,
        })

    return result


def import_approval_proxies(
    db: Session, req: schemas.ProxyImportRequest
) -> schemas.ProxyImportResult:
    operator = get_user(db, req.operator_id)
    if not operator:
        raise BusinessError(f"操作人 ID={req.operator_id} 不存在", 404)

    if not user_can_approve(db, req.operator_id):
        raise BusinessError("只有审批角色可以导入代理授权", 403)

    total = len(req.proxies)
    success = 0
    skipped = 0
    failed = 0
    details = []

    for idx, item in enumerate(req.proxies):
        try:
            approver_user = db.query(models.User).filter(
                models.User.username == item.approver_username
            ).first()
            if not approver_user:
                failed += 1
                details.append({
                    "index": idx,
                    "approver_username": item.approver_username,
                    "status": "failed",
                    "reason": f"审批人 '{item.approver_username}' 不存在",
                })
                continue

            if not user_can_approve(db, approver_user.id):
                failed += 1
                details.append({
                    "index": idx,
                    "approver_username": item.approver_username,
                    "status": "failed",
                    "reason": f"用户 '{item.approver_username}' 不是审批角色",
                })
                continue

            proxy_user = db.query(models.User).filter(
                models.User.username == item.proxy_username
            ).first()
            if not proxy_user:
                failed += 1
                details.append({
                    "index": idx,
                    "approver_username": item.approver_username,
                    "status": "failed",
                    "reason": f"代理人 '{item.proxy_username}' 不存在",
                })
                continue

            env = get_environment_by_name(db, item.environment_name)
            if not env:
                failed += 1
                details.append({
                    "index": idx,
                    "approver_username": item.approver_username,
                    "status": "failed",
                    "reason": f"环境 '{item.environment_name}' 不存在",
                })
                continue

            valid_from = datetime.fromisoformat(item.valid_from)
            valid_to = datetime.fromisoformat(item.valid_to)

            status_map = {s.value: s for s in models.ProxyStatus}
            proxy_status = status_map.get(item.status, models.ProxyStatus.ACTIVE)

            existing = db.query(models.ApprovalProxy).filter(
                models.ApprovalProxy.approver_id == approver_user.id,
                models.ApprovalProxy.proxy_user_id == proxy_user.id,
                models.ApprovalProxy.environment_id == env.id,
                models.ApprovalProxy.valid_from == valid_from,
                models.ApprovalProxy.valid_to == valid_to,
            ).first()

            if existing:
                if req.on_conflict == "skip":
                    skipped += 1
                    details.append({
                        "index": idx,
                        "approver_username": item.approver_username,
                        "status": "skipped",
                        "reason": "相同代理授权已存在，跳过",
                    })
                    continue
                elif req.on_conflict == "overwrite":
                    existing.delegate_scope = json.dumps(item.delegate_scope, ensure_ascii=False)
                    existing.status = proxy_status
                    existing.reason = item.reason
                    existing.remark = item.remark
                    existing.creator_id = req.operator_id
                    existing.updated_at = datetime.utcnow()

                    _add_proxy_audit_log(
                        db, existing, models.ProxyAction.PROXY_IMPORT, req.operator_id,
                        detail=f"覆盖导入代理授权",
                    )

                    success += 1
                    details.append({
                        "index": idx,
                        "approver_username": item.approver_username,
                        "status": "overwritten",
                        "id": existing.id,
                    })
                    continue
                else:
                    failed += 1
                    details.append({
                        "index": idx,
                        "approver_username": item.approver_username,
                        "status": "failed",
                        "reason": "相同代理授权已存在",
                    })
                    continue

            resolved_creator_id = req.operator_id
            if item.creator_username:
                creator_user = db.query(models.User).filter(
                    models.User.username == item.creator_username
                ).first()
                if creator_user:
                    resolved_creator_id = creator_user.id

            now = datetime.utcnow()
            if proxy_status == models.ProxyStatus.ACTIVE and valid_from > now:
                proxy_status = models.ProxyStatus.INACTIVE
            if proxy_status == models.ProxyStatus.ACTIVE and valid_to <= now:
                proxy_status = models.ProxyStatus.EXPIRED

            new_proxy = models.ApprovalProxy(
                approver_id=approver_user.id,
                proxy_user_id=proxy_user.id,
                environment_id=env.id,
                delegate_scope=json.dumps(item.delegate_scope, ensure_ascii=False),
                valid_from=valid_from,
                valid_to=valid_to,
                status=proxy_status,
                reason=item.reason,
                remark=item.remark,
                creator_id=resolved_creator_id,
            )
            if item.created_at:
                new_proxy.created_at = datetime.fromisoformat(item.created_at)

            db.add(new_proxy)
            db.flush()

            if item.audit_logs:
                for log_data in item.audit_logs:
                    log_dict = log_data if isinstance(log_data, dict) else log_data
                    if not log_dict:
                        continue

                    action_map = {a.value: a for a in models.ProxyAction}
                    log_action = action_map.get(log_dict.get("action"), models.ProxyAction.PROXY_IMPORT)

                    op_username = log_dict.get("operator_username")
                    resolved_op_id = req.operator_id
                    if op_username:
                        op_user = db.query(models.User).filter(
                            models.User.username == op_username
                        ).first()
                        if op_user:
                            resolved_op_id = op_user.id

                    snapshot_val = None
                    if log_dict.get("snapshot"):
                        snapshot_val = json.dumps(log_dict["snapshot"], ensure_ascii=False)

                    audit_log = models.ProxyAuditLog(
                        proxy_id=new_proxy.id,
                        action=log_action,
                        operator_id=resolved_op_id,
                        detail=log_dict.get("detail"),
                        snapshot=snapshot_val,
                        target_window_id=log_dict.get("target_window_id"),
                        target_plan_id=log_dict.get("target_plan_id"),
                        target_item_id=log_dict.get("target_item_id"),
                    )
                    if log_dict.get("created_at"):
                        audit_log.created_at = datetime.fromisoformat(log_dict["created_at"])
                    db.add(audit_log)

            _add_proxy_audit_log(
                db, new_proxy, models.ProxyAction.PROXY_IMPORT, req.operator_id,
                detail=f"导入代理授权: 审批人={item.approver_username}, 代理人={item.proxy_username}, 状态={proxy_status.value}",
            )

            success += 1
            details.append({
                "index": idx,
                "approver_username": item.approver_username,
                "status": "created",
                "id": new_proxy.id,
            })

        except BusinessError as e:
            failed += 1
            details.append({
                "index": idx,
                "approver_username": item.approver_username if hasattr(item, 'approver_username') else "?",
                "status": "failed",
                "reason": e.message,
            })
        except Exception as e:
            failed += 1
            details.append({
                "index": idx,
                "approver_username": item.approver_username if hasattr(item, 'approver_username') else "?",
                "status": "failed",
                "reason": str(e),
            })

    db.commit()

    return schemas.ProxyImportResult(
        total=total,
        success=success,
        skipped=skipped,
        failed=failed,
        details=details,
    )
