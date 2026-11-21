#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CATCH 本地服务器（禁用缓存 + 采集刷新接口）

用法：python3 tools/serve.py [端口]
默认端口 8900。

接口：
    GET  /api/refresh/status   → 当前采集状态（JSON）
    POST /api/refresh?mode=full|hourly → 启动一次采集（后台执行，立即返回）

页面上的「刷新数据」按钮走的就是这两个接口：点击后由本机真正跑一遍采集，
装配完成后浏览器自动重载，页面即为最新一期。
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import threading
import webbrowser

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "assets", "data", "refresh_status.json")


def read_status() -> dict:
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"state": "idle", "step": "尚未运行过采集"}


def pid_alive(pid) -> bool:
    """状态文件里的采集进程是否还活着（防止状态陈旧导致误判为「正在运行」）。"""
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/refresh/status":
            st = read_status()
            if st.get("state") == "running":
                # 状态文件说在跑，但采集进程已经不在了（被强杀 / 机器重启）。
                # 必须如实标记为「中断」，否则页面会永远停在「刷新中」。
                if pid_alive(st.get("pid")):
                    st["running"] = True
                else:
                    st["state"] = "interrupted"
                    st["step"] = "上一轮采集被中断"
                    st["ok"] = False
                    st["error"] = st.get("error") or "采集进程已结束，未写回结果"
            self._json(st)
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/api/refresh":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        mode = "full"
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("mode="):
                    mode = kv[5:] or "full"
        if mode not in ("full", "hourly"):
            mode = "full"
        st = read_status()
        if st.get("state") == "running" and pid_alive(st.get("pid")):
            self._json({"ok": False, "started": False,
                        "reason": "采集已在运行中，请等待当前这轮结束。", "status": st})
            return
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        # 独立进程组：即使本地服务随后关闭，这一轮采集也会跑完
        subprocess.Popen(
            [sys.executable, "tools/run_refresh.py", mode], cwd=ROOT,
            stdout=open(os.path.join(ROOT, "assets", "data", "refresh.log"), "a",
                        encoding="utf-8"),
            stderr=subprocess.STDOUT,
            start_new_session=True)
        self._json({"ok": True, "started": True, "mode": mode})


def main():
    handler = functools.partial(NoCacheHandler, directory=ROOT)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    url = f"http://localhost:{PORT}/index.html"
    print(f"CATCH 已启动：{url}")
    print("（页面「刷新数据」按钮可直接触发一轮采集；按 Control+C 关闭）")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已关闭。")


if __name__ == "__main__":
    main()
