#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CATCH 采集执行器 —— launchd 定时任务与页面「刷新数据」按钮共用同一入口。

用法：
    python3 tools/run_refresh.py [full|hourly]

full    —— 完整采集：8 平台热榜 + 跨平台共振聚类 + 每事件 1000 条真实热评 + 流行语
hourly  —— 增量采集：沿用当日事件集，只实测热度并追加走势点

行为约定：
  1. 同一时刻只允许一个采集进程（用系统进程探活判定，launchd 与手动点击互斥）；
  2. 全程写 assets/data/refresh_status.json，页面据此轮询显示进度；
  3. 采集与装配任一步失败都会如实记录，不吞错。

退出码：0 成功；2 已有采集在跑（未执行）；1 执行失败。
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "data")
STATUS = os.path.join(DATA, "refresh_status.json")
MAX_LOG = 80          # 状态里保留的日志行数上限


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(st: dict) -> None:
    # _t0 是内部计时基准，不对外暴露
    pub = {k: v for k, v in st.items() if not k.startswith("_")}
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS)


def read_status() -> dict:
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def running_pids() -> list:
    """正在运行的采集进程 pid 列表（覆盖 collect.py 与 run_refresh.py 两种入口）。"""
    out = subprocess.run(
        ["pgrep", "-f", "tools/(collect|run_refresh)\\.py"],
        capture_output=True, text=True)
    pids = []
    for line in (out.stdout or "").split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return pids


def today_label() -> str:
    return datetime.now().strftime("%Y 年 %m 月 %d 日")


def promote_to_full(mode: str) -> str:
    """小时增量若发现「今天的完整采集还没做过」，就升级为完整采集。

    否则会出现这种状况：09:00 的完整采集失败，小时增量一直沿用昨天的
    事件集——页面永远停在昨天。而用户要的是「打开就是当天」。

    判断依据是 meta.json 里的 lastFullRun（只有完整采集才会写它），
    不能用 dateLabel——小时增量也会把日期标签盖成当天，会把判断骗过去。
    """
    if mode != "hourly":
        return mode
    try:
        with open(os.path.join(DATA, "meta.json"), encoding="utf-8") as f:
            m = json.load(f)
        if m.get("lastFullRun") == datetime.now().strftime("%Y-%m-%d"):
            return mode
    except Exception:
        pass
    return "full"


def run_step(st: dict, label: str, cmd: list) -> int:
    """执行一个子步骤，边跑边把输出写进状态文件，页面可实时看到进度。

    两处细节：
      1. 子进程用 python -u 关闭输出缓冲，否则日志会在管道里攒着，
         页面上的「最近一行」会长时间不动；
      2. 另起一个心跳线程，每 2 秒刷新一次耗时——采集过程中可能
         几十秒没有任何新日志，没有心跳的话页面上的计时会停在原地。
    """
    st["step"] = label
    st["log"] = (st.get("log") or [])[-MAX_LOG:]
    st["elapsedSec"] = round(time.time() - st["_t0"], 1)
    write_status(st)

    stop_beat = threading.Event()

    def heartbeat():
        while not stop_beat.wait(2.0):
            st["elapsedSec"] = round(time.time() - st["_t0"], 1)
            write_status(st)

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            st["log"].append(line)
            if len(st["log"]) > MAX_LOG:
                st["log"] = st["log"][-MAX_LOG:]
            st["elapsedSec"] = round(time.time() - st["_t0"], 1)
            write_status(st)
        return proc.wait()
    finally:
        stop_beat.set()


def wait_for_idle(timeout: float) -> bool:
    """等待其它采集进程结束，让出空档后再开始本轮。

    09:00 的完整采集如果撞上正在跑的小时增量就直接放弃，会导致「今天没有一次
    成功的完整采集」，页面永远停在昨天的数据。所以完整采集应当排队等待。
    返回 True 表示等到空档；False 表示等待超时。
    """
    deadline = time.time() + timeout
    warned = False
    while True:
        busy = running_pids()
        if not busy:
            return True
        if time.time() >= deadline:
            print(f"[CATCH] 等待 {timeout:.0f}s 后仍有采集进程 {busy} 在运行，本次跳过。")
            return False
        if not warned:
            print(f"[CATCH] 已有采集进程 {busy} 在运行，本轮排队等待（最多 {timeout:.0f}s）…")
            warned = True
        time.sleep(30)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode not in ("full", "hourly"):
        print("用法：run_refresh.py [full|hourly] [--wait 秒]")
        return 1
    wait_s = 0.0
    if "--wait" in sys.argv:
        i = sys.argv.index("--wait")
        try:
            wait_s = float(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 0.0
        except (IndexError, ValueError):
            wait_s = 0.0
    mode = promote_to_full(mode)

    # 等待期间绝不写状态文件——它属于正在跑的那一轮，进度显示必须保持真实
    if wait_s > 0:
        if not wait_for_idle(wait_s):
            return 2
    else:
        busy = running_pids()
        if busy:
            print(f"[CATCH] 已有采集进程 {busy} 在运行，本次跳过。")
            return 2

    t0 = time.time()
    st = {"state": "running", "mode": mode, "pid": os.getpid(),
          "startedAt": now_iso(), "step": "准备中", "log": [],
          "finishedAt": None, "elapsedSec": 0, "ok": None, "error": None}
    st["_t0"] = t0
    write_status(st)

    try:
        # -u：关闭子进程输出缓冲，页面才能实时看到采集日志
        if mode == "full":
            cmd = [sys.executable, "-u", "tools/collect.py", "--events", "5",
                   "--comment-target", "1000"]
        else:
            cmd = [sys.executable, "-u", "tools/collect.py", "--hourly"]
        rc = run_step(st, "抓取公开热榜与真实热评" if mode == "full"
                      else "实测热度并追加走势点", cmd)
        if rc != 0:
            raise RuntimeError(f"collect.py 退出码 {rc}")

        rc = run_step(st, "装配前端数据",
                      [sys.executable, "-u", "tools/build_site.py"])
        if rc != 0:
            raise RuntimeError(f"build_site.py 退出码 {rc}")

        st["state"] = "done"
        st["ok"] = True
        st["step"] = "完成"
    except Exception as e:
        st["state"] = "error"
        st["ok"] = False
        st["error"] = str(e)[:300]
        st["step"] = f"失败：{str(e)[:80]}"
        print(f"[CATCH] 采集失败：{e}")

    st["finishedAt"] = now_iso()
    st["elapsedSec"] = round(time.time() - t0, 1)
    st.pop("_t0", None)
    write_status(st)
    print(f"[CATCH] {mode} 采集结束 · {st['state']} · {st['elapsedSec']}s")
    return 0 if st["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
