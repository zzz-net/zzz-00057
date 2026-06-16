from pydantic import BaseModel, Field, validator, field_validator
from datetime import datetime, date
import json
from typing import Optional, List, Any
from app.models import WindowStatus, AuditAction, TemplateAction, ConflictType
from app.models import PlanStatus, PlanItemStatus, DiffType, PlanAction


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

    @field_validator("specific_dates", mode="before")
    @classmethod
    def _parse_specific_dates(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            try:
                data = json.loads(v)
                if isinstance(data, list):
                    return [str(x) for x in data]
            except (json.JSONDecodeError, TypeError):
                return None
        return None

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


# ============== Schedule Plan Schemas ==============

class DiffHintItem(BaseModel):
    diff_type: DiffType
    detail: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None


class PlanItemSnapshot(BaseModel):
    conflict_type: ConflictType
    conflict_window_id: Optional[int] = None
    conflict_window_title: Optional[str] = None
    conflict_window_status: Optional[str] = None
    message: Optional[str] = None


class SchedulePlanItemBase(BaseModel):
    date: str
    start_time: str
    end_time: str


class SchedulePlanItemCreate(SchedulePlanItemBase):
    precheck_snapshot: PlanItemSnapshot


class SchedulePlanItem(SchedulePlanItemBase):
    id: int
    plan_id: int
    status: PlanItemStatus
    conflict_type_snapshot: Optional[ConflictType] = None
    conflict_window_id_snapshot: Optional[int] = None
    conflict_window_title_snapshot: Optional[str] = None
    conflict_window_status_snapshot: Optional[str] = None
    message_snapshot: Optional[str] = None
    current_diff_type: Optional[DiffType] = None
    current_diff_detail: Optional[str] = None
    latest_precheck: Optional[PlanItemSnapshot] = None
    window_id: Optional[int] = None
    excluded_at: Optional[datetime] = None
    excluded_by: Optional[int] = None
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[int] = None
    diff_hints: List[DiffHintItem] = []
    created_at: datetime
    updated_at: datetime

    @field_validator("latest_precheck", mode="before")
    @classmethod
    def _parse_latest_precheck(cls, v: Any) -> Optional[PlanItemSnapshot]:
        if v is None:
            return None
        if isinstance(v, PlanItemSnapshot):
            return v
        if isinstance(v, str):
            try:
                data = json.loads(v)
                return PlanItemSnapshot(**data)
            except (json.JSONDecodeError, TypeError):
                return None
        if isinstance(v, dict):
            return PlanItemSnapshot(**v)
        return None

    class Config:
        from_attributes = True


class SchedulePlanBase(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    template_id: int
    generate_mode: str = Field(..., pattern=r"^(date_range|specific_dates)$")
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    specific_dates: Optional[List[date]] = None
    operator_remark: Optional[str] = None


class SchedulePlanCreate(SchedulePlanBase):
    creator_id: int


class SchedulePlanSubmit(BaseModel):
    operator_id: int
    remark: Optional[str] = None


class SchedulePlanApprove(BaseModel):
    operator_id: int
    reason: Optional[str] = None


class SchedulePlanReject(BaseModel):
    operator_id: int
    reason: str


class SchedulePlanDetectChangeResult(BaseModel):
    plan_id: int
    total_items: int
    changed_items: int
    unchanged_items: int
    excluded_items: int
    details: List[dict]


class SchedulePlanRecheckItem(BaseModel):
    item_id: int
    operator_id: int


class SchedulePlanExcludeItem(BaseModel):
    item_id: int
    operator_id: int
    reason: Optional[str] = None


class SchedulePlanConfirm(BaseModel):
    operator_id: int
    item_ids: Optional[List[int]] = None
    remark: Optional[str] = None


class SchedulePlanExecute(BaseModel):
    operator_id: int


class SchedulePlan(SchedulePlanBase):
    id: int
    status: PlanStatus
    environment_id: int
    template_version_snapshot: dict
    environment_slots_snapshot: List[dict]
    creator_id: int
    approver_id: Optional[int] = None
    approval_reason: Optional[str] = None
    approved_at: Optional[datetime] = None
    total_count: int
    approved_count: int
    confirmed_count: int
    created_count: int
    created_at: datetime
    updated_at: datetime
    creator: Optional[User] = None
    approver: Optional[User] = None

    @field_validator("template_version_snapshot", mode="before")
    @classmethod
    def _parse_template_snapshot(cls, v: Any) -> dict:
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @field_validator("environment_slots_snapshot", mode="before")
    @classmethod
    def _parse_slots_snapshot(cls, v: Any) -> List[dict]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                data = json.loads(v)
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @field_validator("specific_dates", mode="before")
    @classmethod
    def _parse_specific_dates(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            try:
                data = json.loads(v)
                if isinstance(data, list):
                    return [str(x) for x in data]
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    class Config:
        from_attributes = True


class SchedulePlanDetail(SchedulePlan):
    items: List[SchedulePlanItem] = []


class SchedulePlanListItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    template_id: int
    template_name: Optional[str] = None
    environment_id: int
    environment_name: Optional[str] = None
    status: PlanStatus
    generate_mode: str
    total_count: int
    approved_count: int
    confirmed_count: int
    created_count: int
    creator_id: int
    creator_name: Optional[str] = None
    approver_id: Optional[int] = None
    approver_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PlanConfirmationBase(BaseModel):
    plan_id: int
    operator_id: int
    confirmation_type: str
    remark: Optional[str] = None


class PlanConfirmation(PlanConfirmationBase):
    id: int
    item_ids: Optional[List[int]] = None
    excluded_item_ids: Optional[List[int]] = None
    rechecked_item_ids: Optional[List[int]] = None
    diff_summary: Optional[dict] = None
    operator: Optional[User] = None
    created_at: datetime

    @field_validator("item_ids", "excluded_item_ids", "rechecked_item_ids", mode="before")
    @classmethod
    def _parse_int_list(cls, v: Any) -> Optional[List[int]]:
        if v is None:
            return None
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            try:
                data = json.loads(v)
                if isinstance(data, list):
                    return [int(x) for x in data]
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @field_validator("diff_summary", mode="before")
    @classmethod
    def _parse_diff_summary(cls, v: Any) -> Optional[dict]:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    class Config:
        from_attributes = True


class PlanAuditLogBase(BaseModel):
    plan_id: int
    action: PlanAction
    operator_id: int
    item_id: Optional[int] = None
    detail: Optional[str] = None


class PlanAuditLog(PlanAuditLogBase):
    id: int
    snapshot: Optional[dict] = None
    operator: Optional[User] = None
    created_at: datetime

    @field_validator("snapshot", mode="before")
    @classmethod
    def _parse_snapshot(cls, v: Any) -> Optional[dict]:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    class Config:
        from_attributes = True


# ============== Plan Import/Export ==============

class PlanItemExport(BaseModel):
    date: str
    start_time: str
    end_time: str
    precheck_snapshot: dict
    conflict_type_snapshot: Optional[str] = None
    conflict_window_id_snapshot: Optional[int] = None
    conflict_window_title_snapshot: Optional[str] = None
    conflict_window_status_snapshot: Optional[str] = None
    message_snapshot: Optional[str] = None
    status: str


class PlanExportItem(BaseModel):
    name: str
    description: Optional[str] = None
    template_name: str
    template_version_snapshot: dict
    environment_name: str
    environment_slots_snapshot: List[dict]
    generate_mode: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    specific_dates: Optional[List[str]] = None
    operator_remark: Optional[str] = None
    status: str
    approval_reason: Optional[str] = None
    items: List[PlanItemExport] = []
    confirmations: List[dict] = []
    audit_logs: List[dict] = []
    creator_username: Optional[str] = None
    approver_username: Optional[str] = None
    created_at: Optional[str] = None


class PlanImportItem(BaseModel):
    name: str
    description: Optional[str] = None
    template_name: str
    template_version_snapshot: dict
    environment_name: str
    environment_slots_snapshot: List[dict]
    generate_mode: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    specific_dates: Optional[List[str]] = None
    operator_remark: Optional[str] = None
    status: str
    approval_reason: Optional[str] = None
    items: List[PlanItemExport] = []
    confirmations: Optional[List[dict]] = None
    audit_logs: Optional[List[dict]] = None
    creator_username: Optional[str] = None
    approver_username: Optional[str] = None
    created_at: Optional[str] = None


class PlanImportRequest(BaseModel):
    plans: List[PlanImportItem]
    operator_id: int
    on_conflict: str = Field("skip", pattern=r"^(skip|overwrite|error)$")


class PlanImportResult(BaseModel):
    total: int
    success: int
    skipped: int
    failed: int
    details: List[dict]
