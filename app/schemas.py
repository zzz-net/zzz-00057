from pydantic import BaseModel, Field, validator
from datetime import datetime, date
from typing import Optional, List
from app.models import WindowStatus, AuditAction, TemplateAction, ConflictType


class EnvironmentBase(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = None


class EnvironmentCreate(EnvironmentBase):
    pass


class EnvironmentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None


class Environment(EnvironmentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MaintenanceSlotBase(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class MaintenanceSlotCreate(MaintenanceSlotBase):
    environment_id: int


class MaintenanceSlot(MaintenanceSlotBase):
    id: int
    environment_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class RoleBase(BaseModel):
    name: str = Field(..., max_length=50)
    can_approve: int = Field(0, ge=0, le=1)
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    can_approve: Optional[int] = Field(None, ge=0, le=1)
    description: Optional[str] = None


class Role(RoleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class UserBase(BaseModel):
    username: str = Field(..., max_length=100)
    display_name: str = Field(..., max_length=100)
    role_id: int


class UserCreate(UserBase):
    pass


class User(UserBase):
    id: int
    created_at: datetime
    role: Optional[Role] = None

    class Config:
        from_attributes = True


class MaintenanceWindowBase(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    environment_id: int
    start_time: datetime
    end_time: datetime
    change_reason: Optional[str] = None

    @validator("end_time")
    def end_time_after_start_time(cls, v, values):
        if "start_time" in values and v <= values["start_time"]:
            raise ValueError("结束时间必须晚于开始时间")
        return v


class MaintenanceWindowCreate(MaintenanceWindowBase):
    creator_id: int


class MaintenanceWindowUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    environment_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    change_reason: Optional[str] = None


class MaintenanceWindow(MaintenanceWindowBase):
    id: int
    status: WindowStatus
    creator_id: int
    approver_id: Optional[int] = None
    approval_reason: Optional[str] = None
    rollback_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    environment: Optional[Environment] = None
    creator: Optional[User] = None
    approver: Optional[User] = None

    class Config:
        from_attributes = True


class SubmitRequest(BaseModel):
    operator_id: int
    reason: Optional[str] = None


class ApproveRequest(BaseModel):
    operator_id: int
    reason: Optional[str] = None


class StartRequest(BaseModel):
    operator_id: int


class CompleteRequest(BaseModel):
    operator_id: int


class WithdrawRequest(BaseModel):
    operator_id: int
    reason: Optional[str] = None


class RollbackRequest(BaseModel):
    operator_id: int
    reason: Optional[str] = None


class AuditLogBase(BaseModel):
    window_id: int
    action: AuditAction
    operator_id: int
    from_status: Optional[WindowStatus] = None
    to_status: Optional[WindowStatus] = None
    reason: Optional[str] = None


class AuditLog(AuditLogBase):
    id: int
    snapshot: Optional[str] = None
    created_at: datetime
    operator: Optional[User] = None

    class Config:
        from_attributes = True


class MaintenanceWindowDetail(MaintenanceWindow):
    audit_logs: List[AuditLog] = []


class WindowTemplateBase(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    environment_id: int
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    change_reason: Optional[str] = None
    is_shared: int = Field(0, ge=0, le=1)


class WindowTemplateCreate(WindowTemplateBase):
    creator_id: int


class WindowTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    environment_id: Optional[int] = None
    start_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    end_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    change_reason: Optional[str] = None
    is_shared: Optional[int] = Field(None, ge=0, le=1)


class WindowTemplate(WindowTemplateBase):
    id: int
    creator_id: int
    created_at: datetime
    updated_at: datetime
    environment: Optional[Environment] = None
    creator: Optional[User] = None

    class Config:
        from_attributes = True


class TemplateAuditLogBase(BaseModel):
    template_id: int
    action: TemplateAction
    operator_id: int
    detail: Optional[str] = None


class TemplateAuditLog(TemplateAuditLogBase):
    id: int
    snapshot: Optional[str] = None
    created_at: datetime
    operator: Optional[User] = None

    class Config:
        from_attributes = True


class WindowTemplateDetail(WindowTemplate):
    audit_logs: List[TemplateAuditLog] = []


class PreCheckItem(BaseModel):
    date: str
    start_time: str
    end_time: str
    conflict_type: ConflictType
    conflict_window_id: Optional[int] = None
    conflict_window_title: Optional[str] = None
    conflict_window_status: Optional[str] = None
    message: Optional[str] = None


class BatchGenerateRequest(BaseModel):
    template_id: int
    operator_id: int
    generate_mode: str = Field(..., pattern=r"^(date_range|specific_dates)$")
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    specific_dates: Optional[List[date]] = None
    auto_create: bool = False


class BatchGenerateResult(BaseModel):
    batch_id: int
    total_count: int
    success_count: int
    skip_count: int
    fail_count: int
    status: str
    precheck_items: List[PreCheckItem]
    created_windows: List[MaintenanceWindow] = []


class BatchGenerateRecord(BaseModel):
    id: int
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    creator_id: int
    environment_id: int
    generate_mode: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    specific_dates: Optional[List[str]] = None
    total_count: int
    success_count: int
    skip_count: int
    fail_count: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class BatchRecordExportItem(BaseModel):
    generate_mode: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    specific_dates: Optional[List[str]] = None
    total_count: int
    success_count: int
    skip_count: int
    fail_count: int
    status: str
    precheck_items: List[PreCheckItem] = []
    created_at: Optional[str] = None


class TemplateImportItem(BaseModel):
    name: str
    description: Optional[str] = None
    environment_name: str
    start_time: str
    end_time: str
    change_reason: Optional[str] = None
    is_shared: int = 0
    batch_records: Optional[List[BatchRecordExportItem]] = None


class TemplateImportRequest(BaseModel):
    templates: List[TemplateImportItem]
    operator_id: int
    on_conflict: str = Field("skip", pattern=r"^(skip|overwrite|error)$")
    restore_batch_records: bool = False
    re_generate_on_conflict: bool = False


class TemplateImportResult(BaseModel):
    total: int
    success: int
    skipped: int
    failed: int
    details: List[dict]


class TemplateExportItem(BaseModel):
    name: str
    description: Optional[str] = None
    environment_name: str
    start_time: str
    end_time: str
    change_reason: Optional[str] = None
    is_shared: int
    creator_username: Optional[str] = None
    created_at: Optional[str] = None
    batch_records: List[BatchRecordExportItem] = []
