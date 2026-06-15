"""
JSON 导入导出完整性回归测试
覆盖：
1. 导出包含批量记录 + 最近参数 + 预检结果
2. 导入后批量记录、预检结果完整回填
3. 服务重启后批量记录和预检结果可查看、可复用
4. 从导入的批量记录再生成（含冲突检测）
5. 同名模板导入时的冲突提示
6. 重复窗口冲突提示
7. specific_dates 模式完整导出/导入/再生成
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
from app.models import WindowStatus, ConflictType

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
    print("  [JSON 导入导出完整性 回归测试]")
    print("=" * 70)

    # ---------- 准备数据 ----------
    print("\n--- [准备] 基础数据 ---")
    env_prod = services.create_environment(db, schemas.EnvironmentCreate(
        name="prod-export", description="prod for export test"
    ))
    env_test = services.create_environment(db, schemas.EnvironmentCreate(
        name="test-export", description="test for export test"
    ))
    role_mgr = services.create_role(db, schemas.RoleCreate(
        name="CM-Export", can_approve=1, description="approver"
    ))
    role_dev = services.create_role(db, schemas.RoleCreate(
        name="DEV-Export", can_approve=0, description="developer"
    ))
    user_mgr = services.create_user(db, schemas.UserCreate(
        username="mgr.export", display_name="ExportMgr", role_id=role_mgr.id
    ))
    user_dev1 = services.create_user(db, schemas.UserCreate(
        username="dev1.export", display_name="ExportDev1", role_id=role_dev.id
    ))
    user_dev2 = services.create_user(db, schemas.UserCreate(
        username="dev2.export", display_name="ExportDev2", role_id=role_dev.id
    ))
    saved_user_dev1_id = user_dev1.id
    saved_user_dev2_id = user_dev2.id
    saved_user_mgr_id = user_mgr.id
    saved_env_prod_id = env_prod.id
    saved_env_test_id = env_test.id
    check("基础数据创建完成", True)

    # ---------- 场景1：创建模板+批量生成，导出验证 ----------
    print("\n--- [场景1] 导出包含批量记录+参数+预检结果 ---")
    tpl = services.create_window_template(db, schemas.WindowTemplateCreate(
        name="导出测试模板",
        description="用于导出完整性测试",
        environment_id=saved_env_prod_id,
        start_time="02:00",
        end_time="04:00",
        change_reason="导出测试",
        is_shared=0,
        creator_id=saved_user_dev1_id,
    ))
    saved_tpl_id = tpl.id

    batch_result = services.batch_generate_windows(db, schemas.BatchGenerateRequest(
        template_id=tpl.id,
        operator_id=saved_user_dev1_id,
        generate_mode="date_range",
        date_from=date(2026, 12, 1),
        date_to=date(2026, 12, 3),
        auto_create=True,
    ))
    saved_batch_id = batch_result.batch_id
    check("批量生成成功", batch_result.status == "COMPLETED")
    check("批量生成3条", batch_result.total_count == 3)

    specific_dates_result = services.batch_generate_windows(db, schemas.BatchGenerateRequest(
        template_id=tpl.id,
        operator_id=saved_user_dev1_id,
        generate_mode="specific_dates",
        specific_dates=[date(2026, 12, 10), date(2026, 12, 15), date(2026, 12, 20)],
        auto_create=True,
    ))
    saved_specific_batch_id = specific_dates_result.batch_id
    check("指定日期批量生成成功", specific_dates_result.status == "COMPLETED")
    check("指定日期生成3条", specific_dates_result.total_count == 3)

    export_data = services.export_templates(db, user_id=saved_user_dev1_id)
    check("导出成功", len(export_data) >= 1)

    tpl_export = [e for e in export_data if e["name"] == "导出测试模板"][0]
    check("导出包含 batch_records 字段", "batch_records" in tpl_export)
    check("导出有2条批量记录", len(tpl_export["batch_records"]) == 2,
          f"count={len(tpl_export['batch_records'])}")

    first_br = tpl_export["batch_records"][0]
    check("批量记录包含 generate_mode", "generate_mode" in first_br)
    check("批量记录包含 precheck_items", "precheck_items" in first_br)
    check("批量记录包含 date_from", "date_from" in first_br)
    check("批量记录包含 date_to", "date_to" in first_br)
    check("批量记录包含 specific_dates", "specific_dates" in first_br)
    check("批量记录包含 status", "status" in first_br)
    check("批量记录包含 total_count", "total_count" in first_br)

    precheck_items_export = first_br.get("precheck_items", [])
    check("预检项数量=3", len(precheck_items_export) == 3,
          f"count={len(precheck_items_export)}")

    if precheck_items_export:
        first_precheck = precheck_items_export[0]
        check("预检项包含 date", "date" in first_precheck)
        check("预检项包含 conflict_type", "conflict_type" in first_precheck)
        check("预检项包含 message", "message" in first_precheck)

    specific_br = [b for b in tpl_export["batch_records"] if b.get("generate_mode") == "specific_dates"]
    check("导出包含指定日期模式的批量记录", len(specific_br) >= 1)
    if specific_br:
        sd = specific_br[0].get("specific_dates")
        check("specific_dates 完整带出",
              sd is not None and len(sd) == 3,
              f"dates={sd}")

    # ---------- 场景2：导入后批量记录+预检结果完整回填 ----------
    print("\n--- [场景2] 导入后批量记录+预检结果完整回填 ---")
    import_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                description="用于导出完整性测试",
                environment_name="test-export",
                start_time="02:00",
                end_time="04:00",
                change_reason="导入测试",
                is_shared=0,
                batch_records=[
                    schemas.BatchRecordExportItem(**br) for br in tpl_export["batch_records"]
                ],
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
        restore_batch_records=True,
    )
    import_result = services.import_templates(db, import_req)
    check("导入成功=1", import_result.success == 1, f"success={import_result.success}")

    detail0 = import_result.details[0]
    check("导入详情包含 batch_records_restored",
          "batch_records_restored" in detail0,
          f"detail={detail0}")
    check("回填了2条批量记录", detail0.get("batch_records_restored") == 2,
          f"restored={detail0.get('batch_records_restored')}")

    imported_tpl = db.query(models.WindowTemplate).filter(
        models.WindowTemplate.name == "导出测试模板",
        models.WindowTemplate.creator_id == saved_user_dev2_id,
    ).first()
    check("导入的模板存在", imported_tpl is not None)
    saved_imported_tpl_id = imported_tpl.id

    imported_batches = services.list_batch_records(db, template_id=saved_imported_tpl_id)
    check("导入模板有2条批量记录", len(imported_batches) == 2,
          f"count={len(imported_batches)}")

    if imported_batches:
        ib = imported_batches[0]
        check("导入批量记录含预检结果", ib.precheck_result is not None)
        if ib.precheck_result:
            ib_precheck = json.loads(ib.precheck_result)
            check("导入预检项数量=3", len(ib_precheck) == 3,
                  f"count={len(ib_precheck)}")
            check("导入预检项含 conflict_type",
                  "conflict_type" in ib_precheck[0],
                  f"keys={list(ib_precheck[0].keys())}")

        check("导入批量记录含 specific_dates", ib.specific_dates is not None)
        if ib.specific_dates:
            sd_loaded = json.loads(ib.specific_dates)
            check("导入 specific_dates 长度正确",
                  len(sd_loaded) == 3,
                  f"dates={sd_loaded}")

    # ---------- 场景3：服务重启后批量记录和预检可查看、可复用 ----------
    print("\n--- [场景3] 重启后批量记录和预检可查看、可复用 ---")
    db.close()
    engine.dispose()

    Base.metadata.create_all(bind=engine)
    db2 = SessionLocal()

    reloaded_tpl = services.get_window_template(db2, saved_imported_tpl_id)
    check("重启后导入模板存在", reloaded_tpl is not None)

    reloaded_batches = services.list_batch_records(db2, template_id=saved_imported_tpl_id)
    check("重启后批量记录仍存在", len(reloaded_batches) == 2)

    if reloaded_batches:
        rb = reloaded_batches[0]
        check("重启后预检结果保留", rb.precheck_result is not None)
        check("重启后 specific_dates 保留", rb.specific_dates is not None)
        if rb.precheck_result:
            rb_precheck = json.loads(rb.precheck_result)
            check("重启后预检项完整", len(rb_precheck) >= 1)

    reloaded_export = services.export_templates(db2, user_id=saved_user_dev2_id)
    reloaded_tpl_export = [e for e in reloaded_export if e["name"] == "导出测试模板"]
    check("重启后导出仍含 batch_records",
          len(reloaded_tpl_export) > 0 and len(reloaded_tpl_export[0].get("batch_records", [])) == 2)

    # ---------- 场景4：从导入的批量记录再生成 ----------
    print("\n--- [场景4] 从导入的批量记录再生成（含冲突检测）---")
    if reloaded_batches:
        regen_batch = reloaded_batches[0]
        regen_result = services.regenerate_from_batch_record(
            db2, regen_batch.id, saved_user_dev2_id
        )
        check("再生成成功", regen_result.status == "COMPLETED")
        check("再生成总数=3", regen_result.total_count == 3,
              f"total={regen_result.total_count}")
        check("再生成有预检结果", len(regen_result.precheck_items) == 3)

        has_ok = any(p.conflict_type == ConflictType.OK for p in regen_result.precheck_items)
        check("再生成预检中存在可创建项", has_ok)

    # ---------- 场景5：再生成时重复窗口冲突提示 ----------
    print("\n--- [场景5] 再生成时重复窗口冲突提示 ---")
    if reloaded_batches:
        draft_wins = services.list_maintenance_windows(
            db2, environment_id=saved_env_test_id, status=WindowStatus.DRAFT
        )
        if draft_wins:
            w = draft_wins[0]
            w = services.submit_window(db2, w.id, schemas.SubmitRequest(operator_id=saved_user_dev2_id))
            w = services.approve_window(db2, w.id, schemas.ApproveRequest(
                operator_id=saved_user_mgr_id, reason="审批以测试冲突"
            ))
            check("窗口已审批（为冲突测试准备）", w.status == WindowStatus.APPROVED)

        regen_result2 = services.regenerate_from_batch_record(
            db2, regen_batch.id, saved_user_dev2_id
        )
        has_overlap = any(
            p.conflict_type in (ConflictType.TIME_OVERLAP, ConflictType.PENDING_APPROVAL)
            for p in regen_result2.precheck_items
        )
        check("第二次再生成检测到冲突", has_overlap,
              f"types={[p.conflict_type.value for p in regen_result2.precheck_items]}")
        check("第二次再生成跳过数>0", regen_result2.skip_count > 0,
              f"skip={regen_result2.skip_count}")

    # ---------- 场景6：同名模板导入冲突提示 ----------
    print("\n--- [场景6] 同名模板导入冲突提示 ---")
    conflict_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                environment_name="test-export",
                start_time="03:00",
                end_time="05:00",
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="error",
    )
    conflict_result = services.import_templates(db2, conflict_req)
    check("error模式: 同名模板失败=1", conflict_result.failed == 1)
    check("error模式: 原因包含'同名'",
          "同名" in conflict_result.details[0].get("reason", ""),
          f"reason={conflict_result.details[0].get('reason')}")

    skip_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                environment_name="test-export",
                start_time="03:00",
                end_time="05:00",
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
    )
    skip_result = services.import_templates(db2, skip_req)
    check("skip模式: 跳过=1", skip_result.skipped == 1)

    overwrite_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                environment_name="test-export",
                start_time="03:00",
                end_time="05:00",
                description="覆盖后描述",
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="overwrite",
    )
    overwrite_result = services.import_templates(db2, overwrite_req)
    check("overwrite模式: 成功=1", overwrite_result.success == 1)

    overwritten_tpl = services.get_window_template(db2, saved_imported_tpl_id)
    check("覆盖后描述更新", overwritten_tpl.description == "覆盖后描述")
    check("覆盖后时间更新", overwritten_tpl.start_time == "03:00")

    # ---------- 场景7：带批量记录的 overwrite 导入 ----------
    print("\n--- [场景7] 带 batch_records 的 overwrite 导入回填 ---")
    full_export = services.export_templates(db2, user_id=saved_user_dev2_id)
    full_tpl_export = [e for e in full_export if e["name"] == "导出测试模板"][0]
    batch_count_in_export = len(full_tpl_export.get("batch_records", []))
    check("导出中批量记录数>=2", batch_count_in_export >= 2,
          f"count={batch_count_in_export}")

    overwrite_with_br = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                environment_name="test-export",
                start_time="06:00",
                end_time="08:00",
                description="带批量记录覆盖",
                batch_records=[
                    schemas.BatchRecordExportItem(**br)
                    for br in full_tpl_export.get("batch_records", [])
                ],
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="overwrite",
        restore_batch_records=True,
    )
    br_result = services.import_templates(db2, overwrite_with_br)
    check("带批量记录覆盖导入成功=1", br_result.success == 1)
    detail = br_result.details[0]
    check("批量记录被回填",
          detail.get("batch_records_restored", 0) > 0,
          f"restored={detail.get('batch_records_restored')}")

    overwritten_tpl2 = services.get_window_template(db2, saved_imported_tpl_id)
    check("覆盖后时间=06:00", overwritten_tpl2.start_time == "06:00")

    all_batches = services.list_batch_records(db2, template_id=saved_imported_tpl_id)
    check("覆盖后批量记录总数>=2", len(all_batches) >= 2,
          f"count={len(all_batches)}")

    # ---------- 场景8：re_generate_on_conflict 再生成模式 ----------
    print("\n--- [场景8] re_generate_on_conflict 同名模板再生成 ---")
    regen_conflict_req = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="导出测试模板",
                environment_name="test-export",
                start_time="06:00",
                end_time="08:00",
                batch_records=[
                    schemas.BatchRecordExportItem(
                        generate_mode="date_range",
                        date_from="2027-01-01T06:00:00",
                        date_to="2027-01-03T08:00:00",
                        total_count=3,
                        success_count=0,
                        skip_count=0,
                        fail_count=0,
                        status="PRECHECKED",
                        precheck_items=[],
                    ),
                ],
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
        re_generate_on_conflict=True,
    )
    regen_conflict_result = services.import_templates(db2, regen_conflict_req)
    check("re_generate_on_conflict 成功=1", regen_conflict_result.success == 1)
    detail_regen = regen_conflict_result.details[0]
    check("re_generate 状态=regenerated",
          detail_regen.get("status") == "regenerated",
          f"status={detail_regen.get('status')}")
    check("re_generate 回填1条批量记录",
          detail_regen.get("batch_records_restored") == 1)

    # ---------- 场景9：specific_dates 模式完整往返 ----------
    print("\n--- [场景9] specific_dates 模式完整导出导入再生成 ---")
    tpl_specific = services.create_window_template(db2, schemas.WindowTemplateCreate(
        name="指定日期模板",
        environment_id=saved_env_test_id,
        start_time="22:00",
        end_time="23:00",
        creator_id=saved_user_dev1_id,
    ))
    saved_specific_tpl_id = tpl_specific.id

    specific_batch = services.batch_generate_windows(db2, schemas.BatchGenerateRequest(
        template_id=tpl_specific.id,
        operator_id=saved_user_dev1_id,
        generate_mode="specific_dates",
        specific_dates=[date(2027, 3, 5), date(2027, 3, 10), date(2027, 3, 15)],
        auto_create=True,
    ))
    check("指定日期批量生成成功", specific_batch.status == "COMPLETED")
    saved_specific_batch2_id = specific_batch.batch_id

    specific_export = services.export_templates(db2, template_ids=[saved_specific_tpl_id])
    check("指定日期模板导出成功", len(specific_export) == 1)
    se = specific_export[0]
    check("导出含批量记录", len(se.get("batch_records", [])) >= 1)
    se_br = se["batch_records"][0]
    check("导出含 specific_dates 字段", "specific_dates" in se_br)
    se_sd = se_br.get("specific_dates")
    check("导出 specific_dates 完整",
          se_sd is not None and len(se_sd) == 3,
          f"dates={se_sd}")

    specific_import = schemas.TemplateImportRequest(
        templates=[
            schemas.TemplateImportItem(
                name="指定日期模板",
                environment_name="test-export",
                start_time="22:00",
                end_time="23:00",
                batch_records=[schemas.BatchRecordExportItem(**se_br)],
            ),
        ],
        operator_id=saved_user_dev2_id,
        on_conflict="skip",
        restore_batch_records=True,
    )
    sp_result = services.import_templates(db2, specific_import)
    check("指定日期模板导入成功=1", sp_result.success == 1)

    imported_sp_tpl = db2.query(models.WindowTemplate).filter(
        models.WindowTemplate.name == "指定日期模板",
        models.WindowTemplate.creator_id == saved_user_dev2_id,
    ).first()
    check("导入的指定日期模板存在", imported_sp_tpl is not None)

    if imported_sp_tpl:
        sp_batches = services.list_batch_records(db2, template_id=imported_sp_tpl.id)
        check("导入模板有1条批量记录", len(sp_batches) == 1,
              f"count={len(sp_batches)}")
        if sp_batches:
            sp_b = sp_batches[0]
            check("导入批量记录含 specific_dates", sp_b.specific_dates is not None)
            if sp_b.specific_dates:
                sp_sd = json.loads(sp_b.specific_dates)
                check("导入 specific_dates 完整",
                      len(sp_sd) == 3,
                      f"dates={sp_sd}")

            regen_sp = services.regenerate_from_batch_record(
                db2, sp_b.id, saved_user_dev2_id
            )
            check("指定日期再生成成功", regen_sp.status == "COMPLETED")
            check("指定日期再生成总数=3", regen_sp.total_count == 3)

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
        print("\n  *** JSON 导入导出完整性回归测试全部通过 ***")
        sys.exit(0)
    else:
        print(f"\n  失败 {len(failed)} 项")
        sys.exit(2)


if __name__ == "__main__":
    main()
