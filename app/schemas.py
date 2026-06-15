from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List
from app.models import WindowStatus, AuditAction


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
