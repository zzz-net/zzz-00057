"""
维护窗口编排 API - HTTP 回归测试脚本
使用真实 HTTP 写操作（urllib，无第三方依赖），覆盖：
1. 重叠申请：先允许 SUBMITTED，审批时才报冲突
2. 回滚：状态恢复到上一状态，保留两段审计历史
3. 结束时间早于开始时间（仍拦截）
4. 非审批角色批准（仍 403）
5. 服务重启后导出：审批人/环境/状态/备注一致
"""

import sys
import os
import io
import json
import urllib.request
import urllib.error
import time
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE = "http://127.0.0.1:8000"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance_window.db")

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def http(method, path, body=None):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8")
        try:
            payload = json.loads(text) if text else None
        except Exception:
            payload = {"detail_raw": text}
        return e.code, payload


def check(name, cond, detail=""):
    flag = PASS if cond else FAIL
    results.append((flag, name, detail))
    print(f"{flag} {name}" + (f"  - {detail}" if detail and not cond else ""))


def wait_server_up():
    for _ in range(30):
        try:
            status, _ = http("GET", "/health")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def reset_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print(f"[INIT] 旧DB已删除: {DB_PATH}")
        except PermissionError:
            print("[WARN] DB被占用，无法删除，可能影响测试数据纯净度")


# ============================================================
# 测试入口
# ============================================================

def main():
    if not wait_server_up():
        print("[ERROR] HTTP 服务未启动，请先运行: python -m uvicorn main:app --port 8000")
        sys.exit(1)

    reset_db()

    print("\n" + "=" * 70)
    print("  [回归测试] 维护窗口编排 API - 两个缺陷修复 + 原有保护校验")
    print("=" * 70)

    # ---------- 准备：配置数据 ----------
    print("\n--- [准备] 创建环境、角色、用户 ---")

    s, env_prod = http("POST", "/environments", {"name": "production", "description": "生产环境"})
    check("创建生产环境", s == 200)
    env_prod_id = env_prod["id"]

    s, env_test = http("POST", "/environments", {"name": "test", "description": "测试环境"})
    check("创建测试环境", s == 200)
    env_test_id = env_test["id"]

    s, role_mgr = http("POST", "/roles", {"name": "ChangeManager", "can_approve": 1, "description": "审批角色"})
    check("创建审批角色", s == 200)
    role_mgr_id = role_mgr["id"]

    s, role_dev = http("POST", "/roles", {"name": "Developer", "can_approve": 0, "description": "开发"})
    check("创建开发角色", s == 200)
    role_dev_id = role_dev["id"]

    s, user_mgr = http("POST", "/users", {"username": "mgr.zhao", "display_name": "赵经理", "role_id": role_mgr_id})
    check("创建审批人赵经理", s == 200)
    user_mgr_id = user_mgr["id"]

    s, user_dev = http("POST", "/users", {"username": "dev.sun", "display_name": "孙开发", "role_id": role_dev_id})
    check("创建开发孙开发", s == 200)
    user_dev_id = user_dev["id"]

    # ============================================================
    # 测试场景 A：原有失败链路 - 结束时间早于开始时间（仍拦截）
    # ============================================================
    print("\n--- [场景 A] 结束时间早于开始时间：直接拦截 ---")

    s, payload = http("POST", "/maintenance-windows", {
        "title": "坏时间窗口",
        "environment_id": env_prod_id,
        "start_time": "2026-08-01T10:00:00",
        "end_time": "2026-08-01T08:00:00",
        "creator_id": user_dev_id,
        "change_reason": "故意错"
    })
    check("创建-结束时间早于开始时间返回422", s == 422, f"实际status={s}")

    # ============================================================
    # 测试场景 B：原有失败链路 - 非审批角色批准（仍403）
    # ============================================================
    print("\n--- [场景 B] 非审批角色批准：403 拦截 ---")

    s, w1 = http("POST", "/maintenance-windows", {
        "title": "非审批人测试窗口",
        "environment_id": env_test_id,
        "start_time": "2026-07-10T10:00:00",
        "end_time": "2026-07-10T12:00:00",
        "creator_id": user_dev_id,
    })
    check("创建测试窗口", s == 200)
    w1_id = w1["id"]

    s, _ = http("POST", f"/maintenance-windows/{w1_id}/submit", {"operator_id": user_dev_id})
    check("提交成功-SUBMITTED", s == 200 and _["status"] == "SUBMITTED")

    s, payload = http("POST", f"/maintenance-windows/{w1_id}/approve", {"operator_id": user_dev_id})
    check("非审批角色批准返回403", s == 403, f"实际status={s} detail={payload}")

    # ============================================================
    # 测试场景 C：修复缺陷1 - 重叠窗口先允许提交，审批时才冲突
    # ============================================================
    print("\n--- [场景 C] 缺陷修复1：重叠先允许 SUBMITTED，审批才报冲突 ---")

    base_dt = datetime(2026, 7, 20, 2, 0, 0)
    s1 = base_dt.isoformat()
    e1 = (base_dt + timedelta(hours=2)).isoformat()
    s2 = (base_dt + timedelta(minutes=30)).isoformat()
    e2 = (base_dt + timedelta(hours=2, minutes=30)).isoformat()

    s, wa = http("POST", "/maintenance-windows", {
        "title": "窗口A（先通过）",
        "environment_id": env_prod_id,
        "start_time": s1,
        "end_time": e1,
        "creator_id": user_dev_id,
    })
    wa_id = wa["id"]
    check("创建窗口A", s == 200)

    s, _ = http("POST", f"/maintenance-windows/{wa_id}/submit", {"operator_id": user_dev_id})
    check("提交窗口A->SUBMITTED", s == 200 and _["status"] == "SUBMITTED")
    s, _ = http("POST", f"/maintenance-windows/{wa_id}/approve", {"operator_id": user_mgr_id, "reason": "先批了窗口A"})
    check("审批窗口A->APPROVED", s == 200 and _["status"] == "APPROVED")

    s, wb = http("POST", "/maintenance-windows", {
        "title": "窗口B（与A重叠）",
        "environment_id": env_prod_id,
        "start_time": s2,
        "end_time": e2,
        "creator_id": user_dev_id,
    })
    wb_id = wb["id"]
    check("创建与窗口A重叠的窗口B", s == 200)

    s, wb_submitted = http("POST", f"/maintenance-windows/{wb_id}/submit", {"operator_id": user_dev_id})
    check("重叠窗口B提交成功（SUBMITTED，不再在submit拦截）",
          s == 200 and wb_submitted["status"] == "SUBMITTED",
          f"实际status={s}, body={wb_submitted}")

    s, conflict_payload = http("POST", f"/maintenance-windows/{wb_id}/approve", {"operator_id": user_mgr_id})
    check("重叠窗口B审批时返回冲突（400）", s == 400,
          f"实际status={s} detail={conflict_payload}")

    # ============================================================
    # 测试场景 D：修复缺陷2 - 完成后回滚到APPROVED，保留两段审计
    # ============================================================
    print("\n--- [场景 D] 缺陷修复2：完成->回滚，状态回到APPROVED，两段审计保留 ---")

    s, wr = http("POST", "/maintenance-windows", {
        "title": "回滚验证主窗口",
        "description": "走完整主流程并回滚，用于重启后校验",
        "environment_id": env_test_id,
        "start_time": "2026-07-25T14:00:00",
        "end_time": "2026-07-25T16:00:00",
        "creator_id": user_dev_id,
        "change_reason": "CVE修复-正式变更",
    })
    wr_id = wr["id"]
    check("创建主验证窗口", s == 200)

    s, _ = http("POST", f"/maintenance-windows/{wr_id}/submit", {"operator_id": user_dev_id, "reason": "准备完毕"})
    check("主窗口: submit", s == 200 and _["status"] == "SUBMITTED")
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/approve", {"operator_id": user_mgr_id, "reason": "正式审批通过"})
    check("主窗口: approve", s == 200 and _["status"] == "APPROVED")
    approver_before = _["approver_id"]
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/start", {"operator_id": user_dev_id})
    check("主窗口: start", s == 200 and _["status"] == "IN_PROGRESS")
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/complete", {"operator_id": user_dev_id})
    check("主窗口: complete (COMPLETED)", s == 200 and _["status"] == "COMPLETED")

    s, rolled = http("POST", f"/maintenance-windows/{wr_id}/rollback", {
        "operator_id": user_mgr_id,
        "reason": "上线后业务异常，执行回滚"
    })
    check("回滚后状态=APPROVED（不是ROLLED_BACK）",
          s == 200 and rolled["status"] == "APPROVED",
          f"实际status={rolled.get('status') if s == 200 else s}")
    check("回滚后审批人信息仍保留", rolled.get("approver_id") == approver_before,
          f"approver_id 丢失: {rolled.get('approver_id')}")
    check("rollback_note 字段已写入", rolled.get("rollback_note") is not None)

    s, detail = http("GET", f"/maintenance-windows/{wr_id}")
    check("主窗口详情接口返回200", s == 200)
    actions = [log["action"] for log in detail["audit_logs"]]
    check("审计日志同时存在 COMPLETE + ROLLBACK 两段历史",
          "COMPLETE" in actions and "ROLLBACK" in actions,
          f"实际actions={actions}")

    rollback_log = next(log for log in detail["audit_logs"] if log["action"] == "ROLLBACK")
    check("ROLLBACK 审计条目 from_status=COMPLETED", rollback_log["from_status"] == "COMPLETED")
    check("ROLLBACK 审计条目 to_status=APPROVED", rollback_log["to_status"] == "APPROVED")

    # ============================================================
    # 测试场景 E：模拟服务重启后导出一致性
    # ============================================================
    print("\n--- [场景 E] 重启后导出：审批人/环境/状态/备注一致 ---")

    s, export_before = http("GET", f"/maintenance-windows/{wr_id}/export")
    check("重启前导出成功", s == 200)
    data_before = export_before["data"]

    print("  请执行以下操作模拟重启：")
    print("  1) Ctrl+C 停止当前 uvicorn 服务")
    print("  2) 再次执行: python -m uvicorn main:app --port 8000")
    print("  3) 按回车继续...")
    try:
        input()
    except EOFError:
        print("  [SKIP] 无 TTY，跳过手动重启步骤，直接在当前连接验证")

    if not wait_server_up():
        print("[ERROR] 重启后无法连接")
        sys.exit(1)

    s, export_after = http("GET", f"/maintenance-windows/{wr_id}/export")
    check("重启后导出成功", s == 200)
    data_after = export_after["data"]

    check("重启后 status 一致", data_before["status"] == data_after["status"],
          f"{data_before['status']} vs {data_after['status']}")
    check("重启后审批人姓名一致",
          data_before["approver"]["display_name"] == data_after["approver"]["display_name"])
    check("重启后环境名称一致",
          data_before["environment"]["name"] == data_after["environment"]["name"])
    check("重启后 approval_reason 一致",
          data_before["approval_reason"] == data_after["approval_reason"])
    check("重启后 change_reason 一致",
          data_before["change_reason"] == data_after["change_reason"])
    check("重启后 rollback_note 一致",
          data_before["rollback_note"] == data_after["rollback_note"])
    check("重启后审计日志条数一致",
          len(data_before["audit_logs"]) == len(data_after["audit_logs"]))
    after_actions = [log["action"] for log in data_after["audit_logs"]]
    check("重启后仍可看到 COMPLETE + ROLLBACK 两段",
          "COMPLETE" in after_actions and "ROLLBACK" in after_actions)

    # ============================================================
    # 输出总结
    # ============================================================
    print("\n" + "=" * 70)
    total = len(results)
    ok = sum(1 for f, _, _ in results if f == PASS)
    print(f"  测试结果: {ok}/{total} 通过")
    print("=" * 70)
    for f, n, d in results:
        if f == FAIL:
            print(f"  {FAIL} {n}  {d}")
    if ok == total:
        print("\n  *** 所有回归测试通过 ***")
        sys.exit(0)
    else:
        print(f"\n  有 {total - ok} 个失败，需要修复")
        sys.exit(2)


if __name__ == "__main__":
    main()
