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
