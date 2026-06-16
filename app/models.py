from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base


class WindowStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    ROLLED_BACK = "ROLLED_BACK"


class AuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    START = "START"
    COMPLETE = "COMPLETE"
    WITHDRAW = "WITHDRAW"
    ROLLBACK = "ROLLBACK"


class Environment(Base):
    __tablename__ = "environments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    maintenance_slots = relationship("MaintenanceSlot", back_populates="environment", cascade="all, delete-orphan")
    windows = relationship("MaintenanceWindow", back_populates="environment")


class MaintenanceSlot(Base):
    __tablename__ = "maintenance_slots"

    id = Column(Integer, primary_key=True, index=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    environment = relationship("Environment", back_populates="maintenance_slots")


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    can_approve = Column(Integer, default=0)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    role = relationship("Role", back_populates="users")


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(Enum(WindowStatus), default=WindowStatus.DRAFT, nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approval_reason = Column(Text, nullable=True)
    change_reason = Column(Text, nullable=True)
    rollback_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    environment = relationship("Environment", back_populates="windows")
    creator = relationship("User", foreign_keys=[creator_id])
    approver = relationship("User", foreign_keys=[approver_id])
    audit_logs = relationship("AuditLog", back_populates="window", cascade="all, delete-orphan", order_by="AuditLog.created_at")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    window_id = Column(Integer, ForeignKey("maintenance_windows.id"), nullable=False)
    action = Column(Enum(AuditAction), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_status = Column(Enum(WindowStatus), nullable=True)
    to_status = Column(Enum(WindowStatus), nullable=True)
    reason = Column(Text, nullable=True)
    snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    window = relationship("MaintenanceWindow", back_populates="audit_logs")
    operator = relationship("User", foreign_keys=[operator_id])


class TemplateAction(str, enum.Enum):
    TEMPLATE_CREATE = "TEMPLATE_CREATE"
    TEMPLATE_UPDATE = "TEMPLATE_UPDATE"
    TEMPLATE_DELETE = "TEMPLATE_DELETE"
    TEMPLATE_SHARE = "TEMPLATE_SHARE"
    TEMPLATE_UNSHARE = "TEMPLATE_UNSHARE"
    BATCH_GENERATE = "BATCH_GENERATE"
    TEMPLATE_IMPORT = "TEMPLATE_IMPORT"
    TEMPLATE_EXPORT = "TEMPLATE_EXPORT"


class WindowTemplate(Base):
    __tablename__ = "window_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    change_reason = Column(Text, nullable=True)
    is_shared = Column(Integer, default=0, nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    environment = relationship("Environment")
    creator = relationship("User", foreign_keys=[creator_id])
    audit_logs = relationship("TemplateAuditLog", back_populates="template", cascade="all, delete-orphan")


class TemplateAuditLog(Base):
    __tablename__ = "template_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("window_templates.id"), nullable=False)
    action = Column(Enum(TemplateAction), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    detail = Column(Text, nullable=True)
    snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    template = relationship("WindowTemplate", back_populates="audit_logs")
    operator = relationship("User", foreign_keys=[operator_id])


class ConflictType(str, enum.Enum):
    OK = "OK"
    TIME_OVERLAP = "TIME_OVERLAP"
    PENDING_APPROVAL = "PENDING_APPROVAL"


class BatchGenerateRecord(Base):
    __tablename__ = "batch_generate_records"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("window_templates.id"), nullable=True)
    template_name = Column(String(200), nullable=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    generate_mode = Column(String(20), nullable=False)
    date_from = Column(DateTime, nullable=True)
    date_to = Column(DateTime, nullable=True)
    specific_dates = Column(Text, nullable=True)
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    skip_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    precheck_result = Column(Text, nullable=True)
    status = Column(String(20), default="PRECHECKED")
    created_at = Column(DateTime, default=datetime.utcnow)

    creator = relationship("User", foreign_keys=[creator_id])
    environment = relationship("Environment")
    template = relationship("WindowTemplate")


class PlanStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


class PlanItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGED = "CHANGED"
    EXCLUDED = "EXCLUDED"
    CONFIRMED = "CONFIRMED"
    CREATED = "CREATED"


class DiffType(str, enum.Enum):
    TEMPLATE_CHANGED = "TEMPLATE_CHANGED"
    SLOT_CHANGED = "SLOT_CHANGED"
    WINDOW_STATUS_CHANGED = "WINDOW_STATUS_CHANGED"
    CONFLICT_CHANGED = "CONFLICT_CHANGED"
    FREEZE_CONFLICT = "FREEZE_CONFLICT"
    NO_CHANGE = "NO_CHANGE"


class SchedulePlan(Base):
    __tablename__ = "schedule_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    template_id = Column(Integer, ForeignKey("window_templates.id"), nullable=False)
    template_version_snapshot = Column(Text, nullable=False)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    environment_slots_snapshot = Column(Text, nullable=False)
    generate_mode = Column(String(20), nullable=False)
    date_from = Column(DateTime, nullable=True)
    date_to = Column(DateTime, nullable=True)
    specific_dates = Column(Text, nullable=True)
    operator_remark = Column(Text, nullable=True)
    status = Column(Enum(PlanStatus), default=PlanStatus.DRAFT, nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approval_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    total_count = Column(Integer, default=0)
    approved_count = Column(Integer, default=0)
    confirmed_count = Column(Integer, default=0)
    created_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", foreign_keys=[creator_id])
    approver = relationship("User", foreign_keys=[approver_id])
    template = relationship("WindowTemplate")
    environment = relationship("Environment")
    items = relationship("SchedulePlanItem", back_populates="plan", cascade="all, delete-orphan")
    audit_logs = relationship("PlanAuditLog", back_populates="plan", cascade="all, delete-orphan")
    confirmations = relationship("PlanConfirmation", back_populates="plan", cascade="all, delete-orphan")


class SchedulePlanItem(Base):
    __tablename__ = "schedule_plan_items"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("schedule_plans.id"), nullable=False)
    date = Column(String(10), nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    precheck_snapshot = Column(Text, nullable=False)
    conflict_type_snapshot = Column(String(30), nullable=True)
    conflict_window_id_snapshot = Column(Integer, nullable=True)
    conflict_window_title_snapshot = Column(String(200), nullable=True)
    conflict_window_status_snapshot = Column(String(30), nullable=True)
    message_snapshot = Column(Text, nullable=True)
    status = Column(Enum(PlanItemStatus), default=PlanItemStatus.PENDING, nullable=False)
    current_diff_type = Column(Enum(DiffType), nullable=True)
    current_diff_detail = Column(Text, nullable=True)
    latest_precheck = Column(Text, nullable=True)
    window_id = Column(Integer, ForeignKey("maintenance_windows.id"), nullable=True)
    excluded_at = Column(DateTime, nullable=True)
    excluded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("SchedulePlan", back_populates="items")
    window = relationship("MaintenanceWindow")


class PlanConfirmation(Base):
    __tablename__ = "plan_confirmations"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("schedule_plans.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    confirmation_type = Column(String(30), nullable=False)
    item_ids = Column(Text, nullable=True)
    excluded_item_ids = Column(Text, nullable=True)
    rechecked_item_ids = Column(Text, nullable=True)
    diff_summary = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    plan = relationship("SchedulePlan", back_populates="confirmations")
    operator = relationship("User", foreign_keys=[operator_id])


class PlanAction(str, enum.Enum):
    PLAN_CREATE = "PLAN_CREATE"
    PLAN_SUBMIT = "PLAN_SUBMIT"
    PLAN_APPROVE = "PLAN_APPROVE"
    PLAN_REJECT = "PLAN_REJECT"
    PLAN_DETECT_CHANGE = "PLAN_DETECT_CHANGE"
    PLAN_RECHECK = "PLAN_RECHECK"
    PLAN_EXCLUDE = "PLAN_EXCLUDE"
    PLAN_CONFIRM = "PLAN_CONFIRM"
    PLAN_EXECUTE = "PLAN_EXECUTE"
    PLAN_CANCEL = "PLAN_CANCEL"
    PLAN_IMPORT = "PLAN_IMPORT"
    PLAN_EXPORT = "PLAN_EXPORT"
    PLAN_FREEZE_HIT = "PLAN_FREEZE_HIT"
    PLAN_FREEZE_RECOVER = "PLAN_FREEZE_RECOVER"


class PlanAuditLog(Base):
    __tablename__ = "plan_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("schedule_plans.id"), nullable=False)
    action = Column(Enum(PlanAction), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    item_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)
    snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    plan = relationship("SchedulePlan", back_populates="audit_logs")
    operator = relationship("User", foreign_keys=[operator_id])


# ============== Freeze Calendar ==============

class FreezeRuleStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class FreezeRuleScope(str, enum.Enum):
    CREATE = "CREATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    ALL = "ALL"


class FreezeAction(str, enum.Enum):
    FREEZE_CREATE = "FREEZE_CREATE"
    FREEZE_UPDATE = "FREEZE_UPDATE"
    FREEZE_DELETE = "FREEZE_DELETE"
    FREEZE_ACTIVATE = "FREEZE_ACTIVATE"
    FREEZE_DEACTIVATE = "FREEZE_DEACTIVATE"
    FREEZE_HIT_WINDOW = "FREEZE_HIT_WINDOW"
    FREEZE_HIT_PLAN = "FREEZE_HIT_PLAN"
    FREEZE_IMPORT = "FREEZE_IMPORT"
    FREEZE_EXPORT = "FREEZE_EXPORT"
    FREEZE_REVOKE = "FREEZE_REVOKE"
    FREEZE_RECOVER = "FREEZE_RECOVER"


class FreezeRule(Base):
    __tablename__ = "freeze_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    freeze_scope = Column(Enum(FreezeRuleScope), default=FreezeRuleScope.ALL, nullable=False)
    date_from = Column(DateTime, nullable=False)
    date_to = Column(DateTime, nullable=False)
    start_time = Column(String(5), nullable=True)
    end_time = Column(String(5), nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(Enum(FreezeRuleStatus), default=FreezeRuleStatus.ACTIVE, nullable=False)
    remark = Column(Text, nullable=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    environment = relationship("Environment")
    creator = relationship("User", foreign_keys=[creator_id])
    audit_logs = relationship("FreezeAuditLog", back_populates="rule", cascade="all, delete-orphan", order_by="FreezeAuditLog.created_at")


class FreezeAuditLog(Base):
    __tablename__ = "freeze_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("freeze_rules.id"), nullable=False)
    action = Column(Enum(FreezeAction), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    detail = Column(Text, nullable=True)
    snapshot = Column(Text, nullable=True)
    target_window_id = Column(Integer, nullable=True)
    target_plan_id = Column(Integer, nullable=True)
    target_item_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    rule = relationship("FreezeRule", back_populates="audit_logs")
    operator = relationship("User", foreign_keys=[operator_id])


class FreezeHitRecordStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RECOVERED = "RECOVERED"
    REVOKED = "REVOKED"


class FreezeHitRecord(Base):
    __tablename__ = "freeze_hit_records"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("freeze_rules.id"), nullable=False)
    rule_name = Column(String(200), nullable=False)
    plan_id = Column(Integer, ForeignKey("schedule_plans.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("schedule_plan_items.id"), nullable=False)
    item_date = Column(String(10), nullable=False)
    item_start_time = Column(String(5), nullable=False)
    item_end_time = Column(String(5), nullable=False)
    item_status_before = Column(String(30), nullable=True)
    freeze_scope = Column(Enum(FreezeRuleScope), default=FreezeRuleScope.ALL, nullable=False)
    hit_reason = Column(Text, nullable=True)
    overlap_type = Column(String(20), nullable=True)
    status = Column(Enum(FreezeHitRecordStatus), default=FreezeHitRecordStatus.ACTIVE, nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recovered_at = Column(DateTime, nullable=True)
    recovered_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    recovery_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rule = relationship("FreezeRule")
    plan = relationship("SchedulePlan")
    item = relationship("SchedulePlanItem")
    operator = relationship("User", foreign_keys=[operator_id])
    recoverer = relationship("User", foreign_keys=[recovered_by])


class FreezeRecoveryLog(Base):
    __tablename__ = "freeze_recovery_logs"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, nullable=False)
    rule_name = Column(String(200), nullable=False)
    trigger_action = Column(String(30), nullable=False)
    plan_id = Column(Integer, nullable=False)
    item_id = Column(Integer, nullable=False)
    item_date = Column(String(10), nullable=False)
    status_before = Column(String(30), nullable=True)
    status_after = Column(String(30), nullable=True)
    still_blocked_by_rule_ids = Column(Text, nullable=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    operator = relationship("User", foreign_keys=[operator_id])


# ============== Approval Proxy Center ==============

class ProxyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ProxyAction(str, enum.Enum):
    PROXY_CREATE = "PROXY_CREATE"
    PROXY_DEACTIVATE = "PROXY_DEACTIVATE"
    PROXY_REACTIVATE = "PROXY_REACTIVATE"
    PROXY_REVOKE = "PROXY_REVOKE"
    PROXY_EXPIRE = "PROXY_EXPIRE"
    PROXY_DELEGATE_ACTION = "PROXY_DELEGATE_ACTION"
    PROXY_DELEGATE_REJECT = "PROXY_DELEGATE_REJECT"
    PROXY_IMPORT = "PROXY_IMPORT"
    PROXY_EXPORT = "PROXY_EXPORT"


class ApprovalProxy(Base):
    __tablename__ = "approval_proxies"

    id = Column(Integer, primary_key=True, index=True)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    proxy_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False)
    delegate_scope = Column(Text, nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=False)
    status = Column(Enum(ProxyStatus), default=ProxyStatus.ACTIVE, nullable=False)
    reason = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    approver = relationship("User", foreign_keys=[approver_id])
    proxy_user = relationship("User", foreign_keys=[proxy_user_id])
    environment = relationship("Environment")
    creator = relationship("User", foreign_keys=[creator_id])
    audit_logs = relationship("ProxyAuditLog", back_populates="proxy", cascade="all, delete-orphan", order_by="ProxyAuditLog.created_at")


class ProxyAuditLog(Base):
    __tablename__ = "proxy_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    proxy_id = Column(Integer, ForeignKey("approval_proxies.id"), nullable=False)
    action = Column(Enum(ProxyAction), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    detail = Column(Text, nullable=True)
    snapshot = Column(Text, nullable=True)
    target_window_id = Column(Integer, nullable=True)
    target_plan_id = Column(Integer, nullable=True)
    target_item_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    proxy = relationship("ApprovalProxy", back_populates="audit_logs")
    operator = relationship("User", foreign_keys=[operator_id])
