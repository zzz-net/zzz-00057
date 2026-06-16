from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import os
import tempfile
from datetime import datetime

from app.database import engine, Base, get_db, DB_PATH
from app import models, schemas, services
from app.models import WindowStatus

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="维护窗口编排 API",
    description="本地维护窗口编排服务，用于管理多环境的变更申请审批流程",
    version="1.0.0",
)


@app.exception_handler(services.BusinessError)
async def business_error_handler(request, exc: services.BusinessError):
    return JSONResponse(
        status_code=exc.code,
        content={"detail": exc.message},
    )


# ========== Health ==========

@app.get("/health", tags=["健康检查"])
def health():
    return {"status": "ok", "db_path": DB_PATH}


# ========== Environment ==========

@app.post("/environments", response_model=schemas.Environment, tags=["环境管理"])
def create_environment(env_in: schemas.EnvironmentCreate, db: Session = Depends(get_db)):
    return services.create_environment(db, env_in)


@app.get("/environments", response_model=List[schemas.Environment], tags=["环境管理"])
def list_environments(db: Session = Depends(get_db)):
    return services.list_environments(db)


@app.get("/environments/{env_id}", response_model=schemas.Environment, tags=["环境管理"])
def get_environment(env_id: int, db: Session = Depends(get_db)):
    env = services.get_environment(db, env_id)
    if not env:
        raise HTTPException(status_code=404, detail="环境不存在")
    return env


@app.put("/environments/{env_id}", response_model=schemas.Environment, tags=["环境管理"])
def update_environment(env_id: int, env_in: schemas.EnvironmentUpdate, db: Session = Depends(get_db)):
    return services.update_environment(db, env_id, env_in)


@app.delete("/environments/{env_id}", tags=["环境管理"])
def delete_environment(env_id: int, db: Session = Depends(get_db)):
    services.delete_environment(db, env_id)
    return {"detail": "删除成功"}


# ========== Maintenance Slot ==========

@app.post("/maintenance-slots", response_model=schemas.MaintenanceSlot, tags=["维护时段配置"])
def create_maintenance_slot(slot_in: schemas.MaintenanceSlotCreate, db: Session = Depends(get_db)):
    return services.create_maintenance_slot(db, slot_in)


@app.get("/maintenance-slots", response_model=List[schemas.MaintenanceSlot], tags=["维护时段配置"])
def list_maintenance_slots(environment_id: Optional[int] = None, db: Session = Depends(get_db)):
    return services.list_maintenance_slots(db, environment_id)


@app.delete("/maintenance-slots/{slot_id}", tags=["维护时段配置"])
def delete_maintenance_slot(slot_id: int, db: Session = Depends(get_db)):
    services.delete_maintenance_slot(db, slot_id)
    return {"detail": "删除成功"}


# ========== Role ==========

@app.post("/roles", response_model=schemas.Role, tags=["角色管理"])
def create_role(role_in: schemas.RoleCreate, db: Session = Depends(get_db)):
    return services.create_role(db, role_in)


@app.get("/roles", response_model=List[schemas.Role], tags=["角色管理"])
def list_roles(db: Session = Depends(get_db)):
    return services.list_roles(db)


@app.get("/roles/{role_id}", response_model=schemas.Role, tags=["角色管理"])
def get_role(role_id: int, db: Session = Depends(get_db)):
    role = services.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@app.put("/roles/{role_id}", response_model=schemas.Role, tags=["角色管理"])
def update_role(role_id: int, role_in: schemas.RoleUpdate, db: Session = Depends(get_db)):
    return services.update_role(db, role_id, role_in)


@app.delete("/roles/{role_id}", tags=["角色管理"])
def delete_role(role_id: int, db: Session = Depends(get_db)):
    services.delete_role(db, role_id)
    return {"detail": "删除成功"}


# ========== User ==========

@app.post("/users", response_model=schemas.User, tags=["用户管理"])
def create_user(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    return services.create_user(db, user_in)


@app.get("/users", response_model=List[schemas.User], tags=["用户管理"])
def list_users(db: Session = Depends(get_db)):
    return services.list_users(db)


@app.get("/users/{user_id}", response_model=schemas.User, tags=["用户管理"])
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = services.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


# ========== Maintenance Window ==========

@app.post("/maintenance-windows", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def create_maintenance_window(win_in: schemas.MaintenanceWindowCreate, db: Session = Depends(get_db)):
    return services.create_maintenance_window(db, win_in)


@app.get("/maintenance-windows", response_model=List[schemas.MaintenanceWindow], tags=["维护窗口"])
def list_maintenance_windows(
    environment_id: Optional[int] = Query(None),
    status: Optional[WindowStatus] = Query(None),
    db: Session = Depends(get_db),
):
    return services.list_maintenance_windows(db, environment_id, status)


@app.get("/maintenance-windows/{win_id}", response_model=schemas.MaintenanceWindowDetail, tags=["维护窗口"])
def get_maintenance_window(win_id: int, db: Session = Depends(get_db)):
    win = services.get_maintenance_window(db, win_id)
    if not win:
        raise HTTPException(status_code=404, detail="维护窗口不存在")
    return win


@app.put("/maintenance-windows/{win_id}", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def update_maintenance_window(
    win_id: int,
    win_in: schemas.MaintenanceWindowUpdate,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.update_maintenance_window(db, win_id, win_in, operator_id)


@app.post("/maintenance-windows/{win_id}/submit", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def submit_window(win_id: int, req: schemas.SubmitRequest, db: Session = Depends(get_db)):
    return services.submit_window(db, win_id, req)


@app.post("/maintenance-windows/{win_id}/approve", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def approve_window(win_id: int, req: schemas.ApproveRequest, db: Session = Depends(get_db)):
    return services.approve_window(db, win_id, req)


@app.post("/maintenance-windows/{win_id}/start", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def start_window(win_id: int, req: schemas.StartRequest, db: Session = Depends(get_db)):
    return services.start_window(db, win_id, req)


@app.post("/maintenance-windows/{win_id}/complete", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def complete_window(win_id: int, req: schemas.CompleteRequest, db: Session = Depends(get_db)):
    return services.complete_window(db, win_id, req)


@app.post("/maintenance-windows/{win_id}/withdraw", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def withdraw_window(win_id: int, req: schemas.WithdrawRequest, db: Session = Depends(get_db)):
    return services.withdraw_window(db, win_id, req)


@app.post("/maintenance-windows/{win_id}/rollback", response_model=schemas.MaintenanceWindow, tags=["维护窗口"])
def rollback_window(win_id: int, req: schemas.RollbackRequest, db: Session = Depends(get_db)):
    return services.rollback_window(db, win_id, req)


@app.get("/maintenance-windows/{win_id}/export", tags=["导出"])
def export_window(win_id: int, db: Session = Depends(get_db)):
    data = services.export_window_records(db, win_id)
    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_path = os.path.join(export_dir, f"window_{win_id}_{data['status']}_{ts}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "detail": "导出成功",
        "file_path": file_path,
        "storage_location": "system_tempdir_outside_repo",
        "data": data,
    }


# ========== Window Templates ==========

@app.post("/window-templates", response_model=schemas.WindowTemplate, tags=["窗口模板"])
def create_window_template(tpl_in: schemas.WindowTemplateCreate, db: Session = Depends(get_db)):
    return services.create_window_template(db, tpl_in)


@app.get("/window-templates", response_model=List[schemas.WindowTemplate], tags=["窗口模板"])
def list_window_templates(
    user_id: Optional[int] = Query(None, description="用户ID（只看自己的和共享的）"),
    environment_id: Optional[int] = Query(None),
    is_shared: Optional[int] = Query(None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    return services.list_window_templates(db, user_id, environment_id, is_shared)


@app.get("/window-templates/{tpl_id}", response_model=schemas.WindowTemplateDetail, tags=["窗口模板"])
def get_window_template(tpl_id: int, db: Session = Depends(get_db)):
    tpl = services.get_window_template(db, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


@app.put("/window-templates/{tpl_id}", response_model=schemas.WindowTemplate, tags=["窗口模板"])
def update_window_template(
    tpl_id: int,
    tpl_in: schemas.WindowTemplateUpdate,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.update_window_template(db, tpl_id, tpl_in, operator_id)


@app.delete("/window-templates/{tpl_id}", tags=["窗口模板"])
def delete_window_template(
    tpl_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    services.delete_window_template(db, tpl_id, operator_id)
    return {"detail": "删除成功"}


@app.post("/window-templates/batch-generate", response_model=schemas.BatchGenerateResult, tags=["窗口模板"])
def batch_generate_windows(
    req: schemas.BatchGenerateRequest,
    db: Session = Depends(get_db),
):
    return services.batch_generate_windows(db, req)


@app.post("/batch-records/{batch_id}/confirm", response_model=schemas.BatchGenerateResult, tags=["批量生成"])
def confirm_batch_generate(
    batch_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.confirm_batch_generate(db, batch_id, operator_id)


@app.get("/batch-records", response_model=List[schemas.BatchGenerateRecord], tags=["批量生成"])
def list_batch_records(
    template_id: Optional[int] = Query(None),
    creator_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return services.list_batch_records(db, template_id, creator_id)


@app.get("/batch-records/{batch_id}", tags=["批量生成"])
def get_batch_record(batch_id: int, db: Session = Depends(get_db)):
    record = services.get_batch_record(db, batch_id)
    if not record:
        raise HTTPException(status_code=404, detail="批量生成记录不存在")
    precheck = []
    if record.precheck_result:
        precheck = json.loads(record.precheck_result)
    specific_dates = None
    if record.specific_dates:
        try:
            specific_dates = json.loads(record.specific_dates)
        except (json.JSONDecodeError, TypeError):
            specific_dates = None
    return {
        "id": record.id,
        "template_id": record.template_id,
        "template_name": record.template_name,
        "creator_id": record.creator_id,
        "environment_id": record.environment_id,
        "generate_mode": record.generate_mode,
        "date_from": record.date_from.isoformat() if record.date_from else None,
        "date_to": record.date_to.isoformat() if record.date_to else None,
        "specific_dates": specific_dates,
        "total_count": record.total_count,
        "success_count": record.success_count,
        "skip_count": record.skip_count,
        "fail_count": record.fail_count,
        "status": record.status,
        "precheck_items": precheck,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


# ========== Template Import/Export ==========

@app.post("/window-templates/import", response_model=schemas.TemplateImportResult, tags=["模板导入导出"])
def import_templates(req: schemas.TemplateImportRequest, db: Session = Depends(get_db)):
    return services.import_templates(db, req)


@app.post("/window-templates/export", tags=["模板导入导出"])
def export_templates(
    template_ids: Optional[List[int]] = Query(None),
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    data = services.export_templates(db, template_ids, user_id)
    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_path = os.path.join(export_dir, f"templates_{ts}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "detail": "导出成功",
        "file_path": file_path,
        "storage_location": "system_tempdir_outside_repo",
        "count": len(data),
        "data": data,
    }


@app.post("/batch-records/{batch_id}/regenerate", response_model=schemas.BatchGenerateResult, tags=["批量生成"])
def regenerate_from_batch_record(
    batch_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.regenerate_from_batch_record(db, batch_id, operator_id)


# ========== Schedule Plan ==========

@app.post("/schedule-plans", response_model=schemas.SchedulePlan, tags=["排期方案"])
def create_schedule_plan(plan_in: schemas.SchedulePlanCreate, db: Session = Depends(get_db)):
    return services.create_schedule_plan(db, plan_in)


@app.get("/schedule-plans", response_model=List[schemas.SchedulePlanListItem], tags=["排期方案"])
def list_schedule_plans(
    template_id: Optional[int] = Query(None),
    creator_id: Optional[int] = Query(None),
    status: Optional[models.PlanStatus] = Query(None),
    db: Session = Depends(get_db),
):
    plans = services.list_schedule_plans(db, template_id, creator_id, status)
    result = []
    for plan in plans:
        tpl = plan.template
        env = plan.environment
        creator = plan.creator
        approver = plan.approver
        result.append({
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "template_id": plan.template_id,
            "template_name": tpl.name if tpl else None,
            "environment_id": plan.environment_id,
            "environment_name": env.name if env else None,
            "status": plan.status,
            "generate_mode": plan.generate_mode,
            "total_count": plan.total_count,
            "approved_count": plan.approved_count,
            "confirmed_count": plan.confirmed_count,
            "created_count": plan.created_count,
            "creator_id": plan.creator_id,
            "creator_name": creator.display_name if creator else None,
            "approver_id": plan.approver_id,
            "approver_name": approver.display_name if approver else None,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        })
    return result


@app.get("/schedule-plans/{plan_id}", response_model=schemas.SchedulePlanDetail, tags=["排期方案"])
def get_schedule_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = services.get_schedule_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    
    items_with_hints = []
    for item in plan.items:
        diff_hints = []
        if item.current_diff_detail:
            try:
                diff_hints = json.loads(item.current_diff_detail)
            except (json.JSONDecodeError, TypeError):
                diff_hints = []
        
        items_with_hints.append({
            "id": item.id,
            "plan_id": item.plan_id,
            "date": item.date,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "status": item.status,
            "conflict_type_snapshot": item.conflict_type_snapshot,
            "conflict_window_id_snapshot": item.conflict_window_id_snapshot,
            "conflict_window_title_snapshot": item.conflict_window_title_snapshot,
            "conflict_window_status_snapshot": item.conflict_window_status_snapshot,
            "message_snapshot": item.message_snapshot,
            "current_diff_type": item.current_diff_type,
            "current_diff_detail": item.current_diff_detail,
            "latest_precheck": item.latest_precheck,
            "window_id": item.window_id,
            "excluded_at": item.excluded_at,
            "excluded_by": item.excluded_by,
            "confirmed_at": item.confirmed_at,
            "confirmed_by": item.confirmed_by,
            "diff_hints": diff_hints,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        })
    
    return {
        "id": plan.id,
        "name": plan.name,
        "description": plan.description,
        "template_id": plan.template_id,
        "generate_mode": plan.generate_mode,
        "date_from": plan.date_from,
        "date_to": plan.date_to,
        "specific_dates": plan.specific_dates,
        "operator_remark": plan.operator_remark,
        "status": plan.status,
        "environment_id": plan.environment_id,
        "template_version_snapshot": plan.template_version_snapshot,
        "environment_slots_snapshot": plan.environment_slots_snapshot,
        "creator_id": plan.creator_id,
        "approver_id": plan.approver_id,
        "approval_reason": plan.approval_reason,
        "approved_at": plan.approved_at,
        "total_count": plan.total_count,
        "approved_count": plan.approved_count,
        "confirmed_count": plan.confirmed_count,
        "created_count": plan.created_count,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "creator": plan.creator,
        "approver": plan.approver,
        "items": items_with_hints,
    }


@app.post("/schedule-plans/{plan_id}/submit", response_model=schemas.SchedulePlan, tags=["排期方案"])
def submit_schedule_plan(
    plan_id: int,
    req: schemas.SchedulePlanSubmit,
    db: Session = Depends(get_db),
):
    return services.submit_schedule_plan(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/approve", response_model=schemas.SchedulePlan, tags=["排期方案"])
def approve_schedule_plan(
    plan_id: int,
    req: schemas.SchedulePlanApprove,
    db: Session = Depends(get_db),
):
    return services.approve_schedule_plan(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/reject", response_model=schemas.SchedulePlan, tags=["排期方案"])
def reject_schedule_plan(
    plan_id: int,
    req: schemas.SchedulePlanReject,
    db: Session = Depends(get_db),
):
    return services.reject_schedule_plan(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/detect-changes", response_model=schemas.SchedulePlanDetectChangeResult, tags=["排期方案"])
def detect_plan_changes(
    plan_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.detect_plan_changes(db, plan_id, operator_id)


@app.post("/schedule-plans/{plan_id}/items/{item_id}/recheck", response_model=schemas.SchedulePlanItem, tags=["排期方案"])
def recheck_plan_item(
    plan_id: int,
    item_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    req = schemas.SchedulePlanRecheckItem(item_id=item_id, operator_id=operator_id)
    return services.recheck_plan_item(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/items/{item_id}/exclude", response_model=schemas.SchedulePlanItem, tags=["排期方案"])
def exclude_plan_item(
    plan_id: int,
    item_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    reason: Optional[str] = Query(None, description="剔除原因"),
    db: Session = Depends(get_db),
):
    req = schemas.SchedulePlanExcludeItem(item_id=item_id, operator_id=operator_id, reason=reason)
    return services.exclude_plan_item(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/confirm", response_model=schemas.SchedulePlan, tags=["排期方案"])
def confirm_schedule_plan(
    plan_id: int,
    req: schemas.SchedulePlanConfirm,
    db: Session = Depends(get_db),
):
    return services.confirm_schedule_plan(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/execute", response_model=schemas.BatchGenerateResult, tags=["排期方案"])
def execute_schedule_plan(
    plan_id: int,
    req: schemas.SchedulePlanExecute,
    db: Session = Depends(get_db),
):
    return services.execute_schedule_plan(db, plan_id, req)


@app.post("/schedule-plans/{plan_id}/cancel", response_model=schemas.SchedulePlan, tags=["排期方案"])
def cancel_schedule_plan(
    plan_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.cancel_schedule_plan(db, plan_id, operator_id)


@app.get("/schedule-plans/{plan_id}/confirmations", response_model=List[schemas.PlanConfirmation], tags=["排期方案"])
def get_plan_confirmations(plan_id: int, db: Session = Depends(get_db)):
    plan = services.get_schedule_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return services.get_plan_confirmations(db, plan_id)


@app.get("/schedule-plans/{plan_id}/audit-logs", response_model=List[schemas.PlanAuditLog], tags=["排期方案"])
def get_plan_audit_logs(plan_id: int, db: Session = Depends(get_db)):
    plan = services.get_schedule_plan(db, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return services.get_plan_audit_logs(db, plan_id)


# ========== Schedule Plan Import/Export ==========

@app.post("/schedule-plans/export", tags=["方案导入导出"])
def export_schedule_plans(
    plan_ids: Optional[List[int]] = Query(None),
    user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    data = services.export_schedule_plans(db, plan_ids, user_id)
    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_path = os.path.join(export_dir, f"schedule_plans_{ts}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "detail": "导出成功",
        "file_path": file_path,
        "storage_location": "system_tempdir_outside_repo",
        "count": len(data),
        "data": data,
    }


@app.post("/schedule-plans/import", response_model=schemas.PlanImportResult, tags=["方案导入导出"])
def import_schedule_plans(req: schemas.PlanImportRequest, db: Session = Depends(get_db)):
    return services.import_schedule_plans(db, req)


# ========== Freeze Calendar ==========

@app.post("/freeze-rules", response_model=schemas.FreezeRule, tags=["冻结日历"])
def create_freeze_rule(rule_in: schemas.FreezeRuleCreate, db: Session = Depends(get_db)):
    return services.create_freeze_rule(db, rule_in)


@app.get("/freeze-rules", response_model=List[schemas.FreezeRule], tags=["冻结日历"])
def list_freeze_rules(
    environment_id: Optional[int] = Query(None, description="环境ID筛选"),
    status: Optional[str] = Query(None, description="状态筛选 (ACTIVE/INACTIVE)"),
    active_only: bool = Query(False, description="只看生效中"),
    db: Session = Depends(get_db),
):
    status_enum = None
    if status:
        try:
            status_enum = models.FreezeRuleStatus(status)
        except ValueError:
            pass
    return services.list_freeze_rules(db, environment_id, status_enum, active_only)


@app.get("/freeze-rules/{rule_id}", response_model=schemas.FreezeRuleDetail, tags=["冻结日历"])
def get_freeze_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = services.get_freeze_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="冻结规则不存在")
    
    audit_logs = []
    for log in rule.audit_logs:
        operator = log.operator
        audit_logs.append({
            "id": log.id,
            "action": log.action.value if log.action else None,
            "operator_id": log.operator_id,
            "operator_username": operator.username if operator else None,
            "operator_name": operator.display_name if operator else None,
            "detail": log.detail,
            "snapshot": json.loads(log.snapshot) if log.snapshot else {},
            "target_window_id": log.target_window_id,
            "target_plan_id": log.target_plan_id,
            "target_item_id": log.target_item_id,
            "created_at": log.created_at,
        })
    
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "environment_id": rule.environment_id,
        "freeze_scope": rule.freeze_scope.value if rule.freeze_scope else "ALL",
        "date_from": rule.date_from,
        "date_to": rule.date_to,
        "start_time": rule.start_time,
        "end_time": rule.end_time,
        "reason": rule.reason,
        "status": rule.status.value if rule.status else "ACTIVE",
        "remark": rule.remark,
        "creator_id": rule.creator_id,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "environment": rule.environment,
        "creator": rule.creator,
        "audit_logs": audit_logs,
    }


@app.put("/freeze-rules/{rule_id}", response_model=schemas.FreezeRule, tags=["冻结日历"])
def update_freeze_rule(
    rule_id: int,
    rule_in: schemas.FreezeRuleUpdate,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    return services.update_freeze_rule(db, rule_id, rule_in, operator_id)


@app.delete("/freeze-rules/{rule_id}", tags=["冻结日历"])
def delete_freeze_rule(
    rule_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    services.delete_freeze_rule(db, rule_id, operator_id)
    return {"detail": "删除成功"}


@app.post("/freeze-rules/{rule_id}/activate", response_model=schemas.FreezeRule, tags=["冻结日历"])
def activate_freeze_rule(
    rule_id: int,
    req: schemas.FreezeRuleToggleRequest,
    db: Session = Depends(get_db),
):
    return services.activate_freeze_rule(db, rule_id, req.operator_id)


@app.post("/freeze-rules/{rule_id}/deactivate", response_model=schemas.FreezeRule, tags=["冻结日历"])
def deactivate_freeze_rule(
    rule_id: int,
    req: schemas.FreezeRuleToggleRequest,
    db: Session = Depends(get_db),
):
    return services.deactivate_freeze_rule(db, rule_id, req.operator_id)


@app.get("/freeze-rules/{rule_id}/audit-logs", tags=["冻结日历"])
def get_freeze_rule_audit_logs(rule_id: int, db: Session = Depends(get_db)):
    rule = services.get_freeze_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="冻结规则不存在")
    
    logs = services.get_freeze_audit_logs(db, rule_id)
    result = []
    for log in logs:
        operator = log.operator
        result.append({
            "id": log.id,
            "action": log.action.value if log.action else None,
            "operator_id": log.operator_id,
            "operator_username": operator.username if operator else None,
            "operator_name": operator.display_name if operator else None,
            "detail": log.detail,
            "snapshot": json.loads(log.snapshot) if log.snapshot else {},
            "target_window_id": log.target_window_id,
            "target_plan_id": log.target_plan_id,
            "target_item_id": log.target_item_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    return result


@app.post("/freeze-rules/check", response_model=schemas.FreezeCheckResult, tags=["冻结日历"])
def check_freeze(
    environment_id: int = Query(..., description="环境ID"),
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    scope: str = Query("ALL", description="检查范围 (CREATE/SUBMIT/APPROVE/ALL)"),
    db: Session = Depends(get_db),
):
    scope_enum = models.FreezeRuleScope.ALL
    try:
        scope_enum = models.FreezeRuleScope(scope)
    except ValueError:
        pass

    conflicts = services.check_freeze_conflicts(
        db, environment_id, start_time, end_time, scope_enum
    )

    conflict_items = []
    for rule in conflicts:
        overlap_type = services._classify_overlap_type(start_time, end_time, rule)
        conflict_items.append(schemas.FreezeConflictItem(
            rule_id=rule.id,
            rule_name=rule.name,
            freeze_scope=rule.freeze_scope.value if rule.freeze_scope else "ALL",
            reason=rule.reason,
            date_from=rule.date_from,
            date_to=rule.date_to,
            conflict_reason=services._build_freeze_conflict_reason(rule, scope, start_time, end_time),
            overlap_type=overlap_type,
        ))

    return schemas.FreezeCheckResult(
        has_conflict=len(conflicts) > 0,
        conflicts=conflict_items,
    )


# ========== Freeze Rule Import/Export ==========

@app.post("/freeze-rules/export", tags=["冻结日历导入导出"])
def export_freeze_rules(
    rule_ids: Optional[List[int]] = Query(None),
    environment_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    data = services.export_freeze_rules(db, rule_ids, environment_id)
    export_dir = os.path.join(tempfile.gettempdir(), "maintenance_window_exports")
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_path = os.path.join(export_dir, f"freeze_rules_{ts}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "detail": "导出成功",
        "file_path": file_path,
        "storage_location": "system_tempdir_outside_repo",
        "count": len(data),
        "data": data,
    }


@app.post("/freeze-rules/{rule_id}/revalidate", tags=["冻结日历"])
def revalidate_freeze_rule(
    rule_id: int,
    operator_id: int = Query(..., description="操作人ID"),
    db: Session = Depends(get_db),
):
    _check_freeze_manage_permission(db, operator_id)
    rule = services.get_freeze_rule(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="冻结规则不存在")
    return services.revalidate_after_freeze_change(db, rule, operator_id, "manual")


def _check_freeze_manage_permission(db: Session, operator_id: int):
    if not services.user_can_approve(db, operator_id):
        raise HTTPException(status_code=403, detail="只有审批角色可以管理冻结规则")


@app.post("/freeze-rules/import", response_model=schemas.FreezeImportResult, tags=["冻结日历导入导出"])
def import_freeze_rules(req: schemas.FreezeImportRequest, db: Session = Depends(get_db)):
    return services.import_freeze_rules(db, req)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
