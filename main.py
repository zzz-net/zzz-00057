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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
