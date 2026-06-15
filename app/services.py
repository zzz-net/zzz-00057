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
