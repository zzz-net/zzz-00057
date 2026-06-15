"""
模板 + 批量排期 综合测试
覆盖：
1. 模板 CRUD + 审计日志
2. 批量排期：日期范围 + 指定日期
3. 冲突预检：OK / TIME_OVERLAP / PENDING_APPROVAL
4. 重启前后一致性（模板、批量记录、预检结果）
5. JSON 导入导出往返
6. 权限控制：非审批角色不能改别人已共享模板
7. 导入冲突规则：skip / overwrite / error
"""

import sys
import os
import io
import json
from datetime import datetime, date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "maintenance_window.db")

from app.database import SessionLocal, Base, engine
from app import models, schemas, services
from app.models import WindowStatus, ConflictType, TemplateAction

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def check(name, cond, detail=""):
    flag = PASS if cond else FAIL
    results.append((flag, name, detail))
    suffix = ""
    if detail:
        suffix = f"  ({detail})" if cond else f"  FAIL-INFO: {detail}"
    print(f"{flag} {name}{suffix}")


def cleanup():
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except PermissionError:
        pass


def main():
    cleanup()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    print("\n" + "=" * 70)
    print("  [模板 + 批量排期 综合测试]")
    print("=" * 70)

    # ---------- 准备数据 ----------
    print("\n--- [准备] 基础数据 ---")
    env_prod = services.create_environment(db, schemas.EnvironmentCreate(
        name="prod-template", description="prod for template test"
    ))
    env_test = services.create_environment(db, schemas.EnvironmentCreate(
        name="test-template", description="test for template test"
    ))
    role_mgr = services.create_role(db, schemas.RoleCreate(
        name="CM-Template", can_approve=1, description="approver"
    ))
    role_dev = services.create_role(db, schemas.RoleCreate(
        name="DEV-Template", can_approve=0, description="developer"
    ))
    user_mgr = services.create_user(db, schemas.UserCreate(
        username="mgr.tpl", display_name="TemplateMgr", role_id=role_mgr.id
    ))
    user_dev1 = services.create_user(db, schemas.UserCreate(
        username="dev1.tpl", display_name="TemplateDev1", role_id=role_dev.id
    ))
    user_dev2 = services.create_user(db, schemas.UserCreate(
        username="dev2.tpl", display_name="TemplateDev2", role_id=role_dev.id
    ))
    check("基础数据创建完成", True)

    # ---------- 场景1：模板 CRUD ----------
    print("\n--- [场景1] 模板 CRUD + 审计日志 ---")
    tpl = services.create_window_template(db, schemas.WindowTemplateCreate(
        name="日常维护模板",
        description="每周例行维护",
        environment_id=env_prod.id,
        start_time="02:00",
        end_time="04:00",
        change_reason="例行系统维护",
        is_shared=0,
        creator_id=user_dev1.id,
    ))
    check("模板创建成功", tpl.id > 0, f"id={tpl.id}")
    check("模板名称正确", tpl.name == "日常维护模板")
    check("模板时间正确", tpl.start_time == "02:00" and tpl.end_time == "04:00")
    check("模板默认私有", tpl.is_shared == 0)

    tpl_detail = services.get_window_template(db, tpl.id)
    check("模板详情可查询", tpl_detail is not None)
    check("模板有审计日志", len(tpl_detail.audit_logs) >= 1)
    create_log = tpl_detail.audit_logs[0]
    check("创建审计动作正确", create_log.action == TemplateAction.TEMPLATE_CREATE)

    tpl2 = services.update_window_template(db, tpl.id, schemas.WindowTemplateUpdate(
        description="更新后的描述",
        is_shared=1,
    ), operator_id=user_dev1.id)
    check("模板更新成功", tpl2.description == "更新后的描述")
    check("模板已分享", tpl2.is_shared == 1)

    tpl_detail2 = services.get_window_template(db, tpl.id)
    actions = [log.action.value for log in tpl_detail2.audit_logs]
    check("分享审计日志已记录", "TEMPLATE_SHARE" in actions, f"actions={actions}")

    my_templates = services.list_window_templates(db, user_id=user_dev1.id)
    check("创建者能看到自己的模板", len(my_templates) >= 1)

    other_templates = services.list_window_templates(db, user_id=user_dev2.id)
    shared_count = sum(1 for t in other_templates if t.is_shared == 1)
    check("其他用户能看到共享模板", shared_count >= 1, f"shared={shared_count}")

    # ---------- 场景2：权限控制 ----------
    print("\n--- [场景2] 权限控制：非审批角色不能改别人共享模板 ---")
    try:
        services.update_window_template(db, tpl.id, schemas.WindowTemplateUpdate(
            description="恶意修改",
        ), operator_id=user_dev2.id)
        check("非审批角色修改他人共享模板应被拦截", False, "未拦截")
    except services.BusinessError as e:
        check("非审批角色修改他人共享模板正确拦截", True, f"code={e.code} msg={e.message}")

    try:
        services.update_window_template(db, tpl.id, schemas.WindowTemplateUpdate(
            description="审批人修改",
        ), operator_id=user_mgr.id)
        check("审批角色可以修改他人共享模板", True)
    except services.BusinessError as e:
        check("审批角色可以修改他人共享模板", False, f"msg={e.message}")

    try:
        services.delete_window_template(db, tpl.id, operator_id=user_dev2.id)
        check("非审批角色删除他人共享模板应被拦截", False, "未拦截")
    except services.BusinessError as e:
        check("非审批角色删除他人共享模板正确拦截", True, f"code={e.code}")

    check("模板仍然存在", services.get_window_template(db, tpl.id) is not None)

    # ---------- 场景3：冲突预检 ----------
    print("\n--- [场景3] 冲突预检：OK / 时间重叠 / 审批中 ---")
    tpl_dev1 = services.create_window_template(db, schemas.WindowTemplateCreate(
        name="预检测试模板",
        environment_id=env_prod.id,
        start_time="03:00",
        end_time="05:00",
        is_shared=0,
        creator_id=user_dev1.id,
    ))

    base_date = date(2026, 10, 15)
    win_approved = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="已批准窗口",
        environment_id=env_prod.id,
        start_time=datetime(2026, 10, 15, 2, 30),
        end_time=datetime(2026, 10, 15, 3, 30),
        creator_id=user_dev1.id,
    ))
    win_approved = services.submit_window(db, win_approved.id, schemas.SubmitRequest(operator_id=user_dev1.id))
    win_approved = services.approve_window(db, win_approved.id, schemas.ApproveRequest(operator_id=user_mgr.id))
    check("已批准窗口创建成功", win_approved.status == WindowStatus.APPROVED)

    win_submitted = services.create_maintenance_window(db, schemas.MaintenanceWindowCreate(
        title="审批中窗口",
        environment_id=env_prod.id,
        start_time=datetime(2026, 10, 16, 3, 0),
        end_time=datetime(2026, 10, 16, 5, 0),
        creator_id=user_dev1.id,
    ))
    win_submitted = services.submit_window(db, win_submitted.id, schemas.SubmitRequest(operator_id=user_dev1.id))
    check("审批中窗口创建成功", win_submitted.status == WindowStatus.SUBMITTED)

    dates = [date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17)]
    precheck = services.precheck_batch_windows(db, tpl_dev1, dates)
    check("预检返回3条结果", len(precheck) == 3, f"count={len(precheck)}")

    item_15 = [i for i in precheck if i.date == "2026-10-15"][0]
    check("10-15: 时间重叠检测正确", item_15.conflict_type == ConflictType.TIME_OVERLAP,
          f"type={item_15.conflict_type.value} msg={item_15.message}")

    item_16 = [i for i in precheck if i.date == "2026-10-16"][0]
    check("10-16: 审批中检测正确", item_16.conflict_type == ConflictType.PENDING_APPROVAL,
          f"type={item_16.conflict_type.value} msg={item_16.message}")

    item_17 = [i for i in precheck if i.date == "2026-10-17"][0]
    check("10-17: 可创建检测正确", item_17.conflict_type == ConflictType.OK,
          f"type={item_17.conflict_type.value}")

    # ---------- 场景4：批量生成 ----------
    print("\n--- [场景4] 批量生成：先预检再确认 ---")
    batch_req = schemas.BatchGenerateRequest(
        template_id=tpl_dev1.id,
        operator_id=user_dev1.id,
        generate_mode="specific_dates",
        specific_dates=dates,
        auto_create=False,
    )
    batch_result = services.batch_generate_windows(db, batch_req)
    check("批量预检创建成功", batch_result.status == "PRECHECKED")
    check("批量记录总数正确", batch_result.total_count == 3)
    check("预检阶段成功数为0", batch_result.success_count == 0)

    batch_id = batch_result.batch_id
    batch_record = services.get_batch_record(db, batch_id)
    check("批量记录可查询", batch_record is not None)
    check("批量记录含预检结果", batch_record.precheck_result is not None)

    confirm_result = services.confirm_batch_generate(db, batch_id, user_dev1.id)
    check("确认生成成功", confirm_result.status == "COMPLETED")
    check("成功1条（仅10-17可创建）", confirm_result.success_count == 1)
    check("跳过2条（冲突）", confirm_result.skip_count == 2)
    check("失败0条", confirm_result.fail_count == 0)

    all_windows = services.list_maintenance_windows(db, environment_id=env_prod.id)
    batch_wins = [w for w in all_windows if "预检测试模板" in w.title]
    check("实际创建了1条窗口", len(batch_wins) == 1, f"count={len(batch_wins)}")

    # ---------- 场景5：日期范围批量 ----------
    print("\n--- [场景5] 日期范围模式批量生成 ---")
    tpl_test = services.create_window_template(db, schemas.WindowTemplateCreate(
        name="测试环境批量模板",
        environment_id=env_test.id,
        start_time="22:00",
        end_time="23:00",
        is_shared=0,
        creator_id=user_dev1.id,
    ))

    range_req = schemas.BatchGenerateRequest(
        template_id=tpl_test.id,
        operator_id=user_dev1.id,
        generate_mode="date_range",
        date_from=date(2026, 11, 1),
        date_to=date(2026, 11, 5),
        auto_create=True,
    )
    range_result = services.batch_generate_windows(db, range_req)
    check("日期范围批量总数=5", range_result.total_count == 5)
    check("全部成功=5", range_result.success_count == 5, f"success={range_result.success_count}")
    check("状态=COMPLETED", range_result.status == "COMPLETED")

    test_wins = services.list_maintenance_windows(db, environment_id=env_test.id)
    check("测试环境窗口数量=5", len(test_wins) == 5)

    # ---------- 场景6：重启一致性 ----------
    print("\n--- [场景6] 重启后数据一致性 ---")
    saved_tpl_id = tpl_dev1.id
    saved_batch_id = batch_id
    saved_range_batch_id = range_result.batch_id
    saved_tpl_name = tpl_dev1.name
    saved_user_dev1_id = user_dev1.id
    saved_user_dev2_id = user_dev2.id
    saved_user_mgr_id = user_mgr.id
    saved_env_prod_id = env_prod.id
    saved_env_test_id = env_test.id
    saved_first_tpl_id = tpl.id
    saved_tpl_test_id = tpl_test.id

    db.close()
    engine.dispose()

    Base.metadata.create_all(bind=engine)
    db2 = SessionLocal()

    reloaded_tpl = services.get_window_template(db2, saved_tpl_id)
    check("重启后模板存在", reloaded_tpl is not None)
    check("重启后模板名称一致", reloaded_tpl.name == saved_tpl_name)
    check("重启后模板审计日志保留", len(reloaded_tpl.audit_logs) >= 1)

    reloaded_batch = services.get_batch_record(db2, saved_batch_id)
    check("重启后批量记录存在", reloaded_batch is not None)
    check("重启后预检结果保留", reloaded_batch.precheck_result is not None)
    precheck_data = json.loads(reloaded_batch.precheck_result)
    check("重启后预检结果有3条", len(precheck_data) == 3)

    reloaded_range_batch = services.get_batch_record(db2, saved_range_batch_id)
    check("重启后日期范围批量记录存在", reloaded_range_batch is not None)
    check("重启后成功数=5", reloaded_range_batch.success_count == 5)

    batch_list = services.list_batch_records(db2, creator_id=saved_user_dev1_id)
    check("重启后可列出批量记录", len(batch_list) >= 2)

    # ---------- 场景7：模板导入导出 ----------
    print("\n--- [场景7] 模板导入导出往返 ---")
    export_data = services.export_templates(db2, user_id=saved_user_dev1_id)
    check("导出成功，数量>=3", len(export_data) >= 3, f"count={len(export_data)}")

    export_item = export_data[0]
    check("导出包含环境名称", "environment_name" in export_item)
    check("导出包含创建者用户名", "creator_username" in export_item)

    import_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导入测试模板1",
                description="从JSON导入",
                environment_name="prod-template",
                start_time="01:00",
                end_time="02:00",
                change_reason="导入测试",
                is_shared=0,
            ),
            schemas.TemplateImportItem(
                name="导入测试模板2",
                description="第二个导入模板",
                environment_name="prod-template",
                start_time="02:00",
                end_time="03:00",
                is_shared=0,
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
    )
    import_result = services.import_templates(db2, import_req)
    check("导入总数=2", import_result.total == 2)
    check("导入成功=2", import_result.success == 2)
    check("导入跳过=0（无重名）", import_result.skipped == 0, f"skipped={import_result.skipped}")
    check("导入失败=0", import_result.failed == 0)

    new_tpl = db2.query(models.WindowTemplate).filter(
        models.WindowTemplate.name == "导入测试模板1",
        models.WindowTemplate.creator_id == saved_user_dev2_id,
    ).first()
    check("导入的模板可查询", new_tpl is not None)
    check("导入模板时间正确", new_tpl.start_time == "01:00" and new_tpl.end_time == "02:00")
    saved_new_tpl_id = new_tpl.id

    second_tpl = db2.query(models.WindowTemplate).filter(
        models.WindowTemplate.name == "导入测试模板2",
        models.WindowTemplate.creator_id == saved_user_dev2_id,
    ).first()
    check("第二个导入模板也可查询", second_tpl is not None)
    saved_second_tpl_id = second_tpl.id

    reimport_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导入测试模板1",
                description="重新导入（应该跳过）",
                environment_name="prod-template",
                start_time="05:00",
                end_time="06:00",
                is_shared=0,
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
    )
    reimport_result = services.import_templates(db2, reimport_req)
    check("同名重导入：跳过=1", reimport_result.skipped == 1, f"skipped={reimport_result.skipped}")
    check("同名重导入：成功=0", reimport_result.success == 0)

    reloaded_tpl = services.get_window_template(db2, saved_new_tpl_id)
    check("跳过模式下原模板未被修改", reloaded_tpl.start_time == "01:00")

    tpl_detail3 = services.get_window_template(db2, saved_new_tpl_id)
    import_actions = [log.action.value for log in tpl_detail3.audit_logs]
    check("导入审计日志记录", "TEMPLATE_IMPORT" in import_actions, f"actions={import_actions}")

    # ---------- 场景8：导入冲突规则 ----------
    print("\n--- [场景8] 导入冲突规则：overwrite / error ---")
    overwrite_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导入测试模板1",
                description="覆盖后的描述",
                environment_name="prod-template",
                start_time="08:00",
                end_time="09:00",
                is_shared=1,
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="overwrite",
    )
    overwrite_result = services.import_templates(db2, overwrite_req)
    check("overwrite模式成功=1", overwrite_result.success == 1)
    check("overwrite模式跳过=0", overwrite_result.skipped == 0)

    reloaded_overwrite = services.get_window_template(db2, saved_new_tpl_id)
    check("overwrite后描述更新", reloaded_overwrite.description == "覆盖后的描述")
    check("overwrite后时间更新", reloaded_overwrite.start_time == "08:00")

    error_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导入测试模板1",
                description="错误模式测试",
                environment_name="prod-template",
                start_time="10:00",
                end_time="11:00",
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="error",
    )
    error_result = services.import_templates(db2, error_req)
    check("error模式失败=1", error_result.failed == 1)
    check("error模式成功=0", error_result.success == 0)

    # ---------- 场景9：不存在环境导入失败 ----------
    print("\n--- [场景9] 不存在环境导入失败 ---")
    bad_env_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="坏环境模板",
                environment_name="not-exist-env",
                start_time="00:00",
                end_time="01:00",
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
    )
    bad_env_result = services.import_templates(db2, bad_env_req)
    check("不存在环境导入失败", bad_env_result.failed == 1)

    # ---------- 场景10：模板删除 ----------
    print("\n--- [场景10] 模板删除 ---")
    tpl_to_delete = services.create_window_template(db2, schemas.WindowTemplateCreate(
        name="待删除模板",
        environment_id=saved_env_test_id,
        start_time="12:00",
        end_time="13:00",
        creator_id=saved_user_dev1_id,
    ))
    delete_id = tpl_to_delete.id
    check("待删模板创建成功", tpl_to_delete is not None)

    services.delete_window_template(db2, delete_id, saved_user_dev1_id)
    deleted = services.get_window_template(db2, delete_id)
    check("模板删除成功", deleted is None)

    # ---------- 总结 ----------
    print("\n" + "=" * 70)
    total = len(results)
    ok = sum(1 for f, _, _ in results if f == PASS)
    print(f"  测试结果: {ok}/{total} 通过")
    print("=" * 70)
    failed = [(n, d) for f, n, d in results if f == FAIL]
    for n, d in failed:
        print(f"  {FAIL} {n}  {d}")

    db2.close()

    if ok == total:
        print("\n  *** 模板 + 批量排期综合测试全部通过 ***")
        sys.exit(0)
    else:
        print(f"\n  失败 {len(failed)} 项")
        sys.exit(2)


if __name__ == "__main__":
    main()
