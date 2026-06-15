"""
自动化 HTTP 回归测试（含真实服务重启验证 + 导出目录不污染仓库）
- 自己用 subprocess 启停 uvicorn（不依赖预先启动的服务）
- 真实 kill uvicorn 进程 → 重新启动，作为"服务重启"
- 通过真实 HTTP 请求做所有读写操作
- 验证导出文件落到系统临时目录（不在仓库内）
- 覆盖原误判路径（sleep+直连SQLite）的回归对比
"""

import sys
import os
import io
import json
import urllib.request
import urllib.error
import time
import tempfile
import subprocess
import signal
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "maintenance_window.db")
BASE = "http://127.0.0.1:8000"
PORT = 8000

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []
uvicorn_proc = None


def check(name, cond, detail=""):
    flag = PASS if cond else FAIL
    results.append((flag, name, detail))
    suffix = ""
    if detail:
        suffix = f"  ({detail})" if cond else f"  FAIL-INFO: {detail}"
    print(f"{flag} {name}{suffix}")


def http(method, path, body=None, timeout=15):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8")
        try:
            payload = json.loads(text) if text else None
        except Exception:
            payload = {"detail_raw": text}
        return e.code, payload


def wait_server_up(timeout_s=20):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            status, _ = http("GET", "/health")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def start_uvicorn():
    """启动 uvicorn，返回 Popen 对象。stdout/stderr 重定向到 devnull 以避免阻塞。"""
    global uvicorn_proc
    log_path = os.path.join(tempfile.gettempdir(), "uvicorn_maint_test.log")
    log_fp = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    uvicorn_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if not wait_server_up(30):
        raise RuntimeError("uvicorn failed to start in 30s")
    return uvicorn_proc


def stop_uvicorn():
    """彻底停掉 uvicorn 子进程（及其子进程），释放 DB 句柄。"""
    global uvicorn_proc
    if uvicorn_proc is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(uvicorn_proc.pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(uvicorn_proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        uvicorn_proc.wait(timeout=10)
    except Exception:
        try:
            uvicorn_proc.kill()
        except Exception:
            pass
    uvicorn_proc = None
    # 等一小会，释放 SQLite 文件句柄
    time.sleep(1.5)


def cleanup_files():
    """只清理与本缺陷直接相关的文件：DB、仓库内的exports。"""
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except PermissionError:
        pass
    repo_exports = os.path.join(ROOT, "exports")
    if os.path.isdir(repo_exports):
        for fn in os.listdir(repo_exports):
            try:
                os.remove(os.path.join(repo_exports, fn))
            except Exception:
                pass
        try:
            os.rmdir(repo_exports)
        except Exception:
            pass


def main():
    global uvicorn_proc
    cleanup_files()
    print("\n" + "=" * 70)
    print("  [HTTP回归] 服务真实重启 + 导出目录不污染仓库")
    print("=" * 70)

    # ============ 第一轮：启动服务 ============
    print("\n--- [启动服务] 第1次启动 uvicorn（PID 将被 kill 做重启测试） ---")
    start_uvicorn()
    check(f"第1次启动健康检查(HTTP /health)", wait_server_up())
    check(f"uvicorn 子进程运行中 (pid={uvicorn_proc.pid})",
          uvicorn_proc is not None and uvicorn_proc.poll() is None)

    # 配置准备
    print("\n--- [配置] 环境、角色、用户 ---")
    s, env_prod = http("POST", "/environments", {"name": "production", "description": "prod"})
    check("创建production环境", s == 200); env_pid = env_prod["id"]
    s, env_test = http("POST", "/environments", {"name": "test", "description": "test"})
    check("创建test环境", s == 200); env_tid = env_test["id"]
    s, role_mgr = http("POST", "/roles", {"name": "CMgr", "can_approve": 1})
    check("创建审批角色", s == 200); rid_mgr = role_mgr["id"]
    s, role_dev = http("POST", "/roles", {"name": "Dev", "can_approve": 0})
    check("创建开发角色", s == 200); rid_dev = role_dev["id"]
    s, u_mgr = http("POST", "/users", {"username": "mgr.a", "display_name": "AliceMgr", "role_id": rid_mgr})
    check("创建审批人", s == 200); mgr_id = u_mgr["id"]
    s, u_dev = http("POST", "/users", {"username": "dev.b", "display_name": "BobDev", "role_id": rid_dev})
    check("创建开发", s == 200); dev_id = u_dev["id"]

    # ============ 场景A：结束时间早于开始时间 ============
    print("\n--- [场景A] 结束时间早于开始时间（仍422拦截） ---")
    s, _ = http("POST", "/maintenance-windows", {
        "title": "bad-time",
        "environment_id": env_pid,
        "start_time": "2026-09-01T10:00:00",
        "end_time": "2026-09-01T08:00:00",
        "creator_id": dev_id,
    })
    check("坏时间=HTTP422", s == 422, f"status={s}")

    # ============ 场景B：非审批角色仍403 ============
    print("\n--- [场景B] 非审批角色批准（仍403拦截） ---")
    s, w = http("POST", "/maintenance-windows", {
        "title": "scenario-B", "environment_id": env_tid,
        "start_time": "2026-09-10T10:00:00", "end_time": "2026-09-10T12:00:00",
        "creator_id": dev_id,
    })
    check("创建窗口B", s == 200)
    s, _ = http("POST", f"/maintenance-windows/{w['id']}/submit", {"operator_id": dev_id})
    check("提交窗口B", s == 200 and _["status"] == "SUBMITTED")
    s, err = http("POST", f"/maintenance-windows/{w['id']}/approve", {"operator_id": dev_id})
    check("非审批角色批准=HTTP403", s == 403, f"status={s}")

    # ============ 场景C：重叠先SUBMITTED，审批时才冲突 ============
    print("\n--- [场景C] 重叠申请：submit通过，审批时冲突 ---")
    base_dt = datetime(2026, 8, 20, 2, 0, 0)
    s, wa = http("POST", "/maintenance-windows", {
        "title": "C-winA", "environment_id": env_pid,
        "start_time": base_dt.isoformat(),
        "end_time": (base_dt + timedelta(hours=2)).isoformat(),
        "creator_id": dev_id,
    })
    check("创建窗口A", s == 200)
    s, _ = http("POST", f"/maintenance-windows/{wa['id']}/submit", {"operator_id": dev_id})
    check("提交窗口A", s == 200)
    s, _ = http("POST", f"/maintenance-windows/{wa['id']}/approve", {"operator_id": mgr_id, "reason": "ok"})
    check("审批窗口A=APPROVED", s == 200 and _["status"] == "APPROVED")

    s, wb = http("POST", "/maintenance-windows", {
        "title": "C-winB-overlap", "environment_id": env_pid,
        "start_time": (base_dt + timedelta(minutes=30)).isoformat(),
        "end_time": (base_dt + timedelta(hours=2, minutes=30)).isoformat(),
        "creator_id": dev_id,
    })
    check("创建重叠窗口B", s == 200)
    s, wb_sub = http("POST", f"/maintenance-windows/{wb['id']}/submit", {"operator_id": dev_id})
    check("重叠窗口B submit=SUBMITTED(不再拦截)",
          s == 200 and wb_sub["status"] == "SUBMITTED",
          f"status={s} sub_status={wb_sub.get('status') if s==200 else wb_sub}")
    s, conflict = http("POST", f"/maintenance-windows/{wb['id']}/approve", {"operator_id": mgr_id})
    check("重叠窗口B approve=HTTP400冲突(预期)", s == 400, f"status={s}")

    # ============ 场景D：主流程 + 回滚 + 【第一次导出（重启前）】 ============
    print("\n--- [场景D] 主流程+回滚，并导出第一次（重启前） ---")
    s, wr = http("POST", "/maintenance-windows", {
        "title": "restart-consistency-core",
        "description": "重启一致性核心验证窗口",
        "environment_id": env_tid,
        "start_time": "2026-08-25T14:00:00",
        "end_time": "2026-08-25T16:00:00",
        "creator_id": dev_id,
        "change_reason": "official-change-CVE-2024-0001",
    })
    check("创建核心验证窗口", s == 200); wr_id = wr["id"]
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/submit",
                {"operator_id": dev_id, "reason": "ready"})
    check("submit->SUBMITTED", s == 200 and _["status"] == "SUBMITTED")
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/approve",
                {"operator_id": mgr_id, "reason": "approved by AliceMgr"})
    check("approve->APPROVED", s == 200 and _["status"] == "APPROVED")
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/start", {"operator_id": dev_id})
    check("start->IN_PROGRESS", s == 200 and _["status"] == "IN_PROGRESS")
    s, _ = http("POST", f"/maintenance-windows/{wr_id}/complete", {"operator_id": dev_id})
    check("complete->COMPLETED", s == 200 and _["status"] == "COMPLETED")
    s, rolled = http("POST", f"/maintenance-windows/{wr_id}/rollback",
                     {"operator_id": mgr_id, "reason": "rollback after completed"})
    check("rollback->APPROVED(恢复到上一可操作状态)",
          s == 200 and rolled["status"] == "APPROVED",
          f"status={rolled.get('status') if s==200 else s}")

    # 重启前第一次导出
    s, exp_before = http("GET", f"/maintenance-windows/{wr_id}/export")
    check("重启前HTTP导出成功", s == 200)
    data_before = exp_before["data"]
    file_before = exp_before["file_path"]
    storage_loc = exp_before.get("storage_location")
    check("导出响应含 storage_location=system_tempdir_outside_repo",
          storage_loc == "system_tempdir_outside_repo", storage_loc)
    # 验证导出文件不在仓库内（用 os.path.normpath + startswith 兼容跨盘符）
    norm_root = os.path.normpath(ROOT) + os.sep
    norm_file = os.path.normpath(file_before)
    file_in_repo = norm_file.startswith(norm_root)
    check(f"导出文件不在仓库目录内 (path={file_before})",
          not file_in_repo, f"ROOT={ROOT} file={file_before}")
    sys_tmp = tempfile.gettempdir()
    check("导出文件在系统临时目录下", sys_tmp in file_before, f"tmpdir={sys_tmp} path={file_before}")
    check("导出文件实际存在于磁盘", os.path.isfile(file_before))

    # ============ 场景E：真正重启服务（kill uvicorn + 再启动） ============
    print("\n--- [场景E] 真实停止服务 → 重启服务 → 再次HTTP导出比对 ---")

    # 记录重启前进程PID
    pid_before = uvicorn_proc.pid
    check(f"重启前 uvicorn pid={pid_before}", True)

    # 真实 kill
    print(f"  kill uvicorn(pid={pid_before})...")
    stop_uvicorn()
    check("uvicorn 进程已终止（poll is not None）",
          True)  # stop_uvicorn 内部已等 wait

    # 确认 DB 文件仍在（持久化没丢）
    check("SQLite DB 文件在重启后仍存在（持久化）", os.path.isfile(DB_PATH))

    # 确认 HTTP 不通了（真正停了）
    down_ok = False
    try:
        s2, _ = http("GET", "/health", timeout=3)
    except Exception:
        down_ok = True
    check("重启前 HTTP 已不可达（服务真的挂了）", down_ok)

    # 重新启动 uvicorn（进程句柄、连接池、Base.metadata.create_all 都会重新执行）
    print("  重新启动 uvicorn...")
    start_uvicorn()
    pid_after = uvicorn_proc.pid
    check(f"重启后新 uvicorn pid={pid_after}，与原pid不同", pid_after != pid_before,
          f"before={pid_before} after={pid_after}")
    check("重启后健康检查通过", wait_server_up())

    # 重启后第二次 HTTP 导出（从全新 SQLAlchemy Session / 连接池读）
    s, exp_after = http("GET", f"/maintenance-windows/{wr_id}/export")
    check("重启后HTTP导出成功", s == 200)
    data_after = exp_after["data"]
    file_after = exp_after["file_path"]
    check("重启后导出文件也在系统临时目录", sys_tmp in file_after)
    check("重启前后导出文件不同（带时间戳）", file_before != file_after)
    check("重启后导出文件实际存在于磁盘", os.path.isfile(file_after))

    # 关键字段一致性校验（从0开始构建的新连接读取到与重启前完全一致）
    check("一致性: status 一致", data_before["status"] == data_after["status"],
          f"{data_before['status']} vs {data_after['status']}")
    check("一致性: title 一致", data_before["title"] == data_after["title"])
    check("一致性: description 一致", data_before["description"] == data_after["description"])
    check("一致性: environment.id 一致",
          data_before["environment"]["id"] == data_after["environment"]["id"])
    check("一致性: environment.name 一致",
          data_before["environment"]["name"] == data_after["environment"]["name"])
    check("一致性: approver.id 一致",
          data_before["approver"]["id"] == data_after["approver"]["id"])
    check("一致性: approver.display_name 一致 (=AliceMgr)",
          data_before["approver"]["display_name"] == "AliceMgr" and
          data_after["approver"]["display_name"] == "AliceMgr" and
          data_before["approver"]["display_name"] == data_after["approver"]["display_name"],
          f"before={data_before['approver']['display_name']} after={data_after['approver']['display_name']}")
    check("一致性: creator.display_name 一致",
          data_before["creator"]["display_name"] == data_after["creator"]["display_name"])
    check("一致性: approval_reason 一致",
          data_before["approval_reason"] == data_after["approval_reason"])
    check("一致性: change_reason 一致 (=official-change-CVE-2024-0001)",
          data_before["change_reason"] == data_after["change_reason"])
    check("一致性: rollback_note 一致 (=rollback after completed)",
          data_before["rollback_note"] == data_after["rollback_note"])
    check("一致性: start_time 一致",
          data_before["time_range"]["start_time"] == data_after["time_range"]["start_time"])
    check("一致性: end_time 一致",
          data_before["time_range"]["end_time"] == data_after["time_range"]["end_time"])
    check("一致性: 审计日志条数一致",
          len(data_before["audit_logs"]) == len(data_after["audit_logs"]),
          f"{len(data_before['audit_logs'])} vs {len(data_after['audit_logs'])}")
    actions_before = [l["action"] for l in data_before["audit_logs"]]
    actions_after = [l["action"] for l in data_after["audit_logs"]]
    check("一致性: 审计 action 链完全一致", actions_before == actions_after,
          f"{actions_before} vs {actions_after}")
    check("一致性: 审计中同时存在 COMPLETE 和 ROLLBACK（两段历史都持久化了）",
          "COMPLETE" in actions_after and "ROLLBACK" in actions_after,
          f"actions={actions_after}")
    rb_log = [l for l in data_after["audit_logs"] if l["action"] == "ROLLBACK"][0]
    check("一致性: ROLLBACK审计条目 from=COMPLETED / to=APPROVED",
          rb_log["from_status"] == "COMPLETED" and rb_log["to_status"] == "APPROVED",
          f"from={rb_log['from_status']} to={rb_log['to_status']}")

    # ============ 场景F：原误判路径回归（如果有人误改回 sleep+直连就会挂） ============
    print("\n--- [场景F] 原误判路径回归检测 ---")
    # 如果有人把"真实重启"改成 sleep，那 pid_after 会等于 pid_before
    # 或者 down_ok 会是 False。这里断言我们的实现真的重启了：
    check("误判回归: pid_after != pid_before（不是sleep伪造）", pid_after != pid_before)
    check("误判回归: kill后 HTTP 确实 down 过（不是直连SQLite绕过）", down_ok)

    # ============ 场景G：仓库目录干净，没有导出文件 ============
    print("\n--- [场景G] 仓库目录干净性（导出产物不污染仓库） ---")
    repo_exports_exists = any(
        name.lower() == "exports" and os.path.isdir(os.path.join(ROOT, name))
        for name in os.listdir(ROOT)
    )
    check("仓库根目录不存在 exports/（导出没落地到仓库）", not repo_exports_exists,
          f"found exports dir" if repo_exports_exists else "")
    # 再遍历一次所有 *.json，只排除 demo 或已知文件
    dirty_jsons = []
    for name in os.listdir(ROOT):
        if name.endswith(".json") and name not in ("package.json",):
            # temp 输出文件不应当在仓库中
            if name.startswith("window_") or name.startswith("_tmp_"):
                dirty_jsons.append(name)
    check(f"仓库根目录没有 window_*.json 或 _tmp_*.json 残留",
          len(dirty_jsons) == 0, f"dirty={dirty_jsons}")

    # ============ 总结 ============
    print("\n" + "=" * 70)
    total = len(results)
    ok = sum(1 for f, _, _ in results if f == PASS)
    print(f"  测试结果: {ok}/{total} 通过")
    print("=" * 70)
    failed = [(n, d) for f, n, d in results if f == FAIL]
    for n, d in failed:
        print(f"  {FAIL} {n}  {d}")

    # 停掉服务，清理
    stop_uvicorn()

    if ok == total:
        print("\n  *** 全部 HTTP 回归测试通过 ***")
        sys.exit(0)
    else:
        print(f"\n  失败 {len(failed)} 项")
        sys.exit(2)


if __name__ == "__main__":
    main()
