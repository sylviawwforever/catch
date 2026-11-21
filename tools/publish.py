#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CATCH 发布器 —— 把本地采集结果推送到公网发布分支（GitHub Pages）。

用法：
    python3 tools/publish.py            # 构建产物并推送到 origin gh-pages
    python3 tools/publish.py --dry-run  # 只构建，不推送

背景：
    云端主链路是 GitHub Actions（每天 09:00 完整采集 + 每小时增量），
    由 `.github/workflows/publish.yml` 定义。本脚本是**过渡链路**：
    在 Actions 可用之前（或本地开发调试时），让本机采集到的数据
    也能自动出现在公网链接上。

    两条链路推送的是同一个 `gh-pages` 分支，互不冲突——
    谁后推谁生效，内容始终是各自那一轮的真实采集结果。

退出码：0 成功（含「无变化可推送」）；1 失败。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "data")
BRANCH = "gh-pages"
REMOTE = "https://github.com/sylviawwforever/catch.git"


def run(cmd: list[str], cwd: str = ROOT, timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def build_site(stage: str) -> list[str]:
    """把发布所需的文件收集到暂存目录，返回文件列表。"""
    files = [
        ("index.html", "index.html"),
        (os.path.join("assets", "css", "catch.css"), os.path.join("assets", "css", "catch.css")),
        (os.path.join("assets", "js", "app.js"), os.path.join("assets", "js", "app.js")),
        (os.path.join("assets", "js", "data.js"), os.path.join("assets", "js", "data.js")),
    ]
    for name in ("preview.png",):
        src = os.path.join("assets", "img", name)
        if os.path.exists(src):
            files.append((src, os.path.join("assets", "img", name)))
    # 完整热评样本（前端按需 fetch）
    cdir = os.path.join(DATA, "comments")
    if os.path.isdir(cdir):
        for fn in sorted(os.listdir(cdir)):
            if fn.endswith(".json"):
                files.append((os.path.join(cdir, fn), os.path.join("assets", "data", "comments", fn)))

    copied = []
    for src, rel in files:
        if not os.path.exists(src):
            continue
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    # 关闭 Jekyll 处理（Pages 用静态分支发布时无需，但留着无害）
    open(os.path.join(stage, ".nojekyll"), "w").close()
    return copied


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not os.path.exists(os.path.join(ROOT, "assets", "js", "data.js")):
        print("[CATCH] 没有可发布的数据（assets/js/data.js 不存在），跳过。")
        return 1

    stage = tempfile.mkdtemp(prefix="catch-publish-")
    try:
        copied = build_site(stage)
        print(f"[CATCH] 收集发布产物 {len(copied)} 个文件")
        if dry:
            for c in copied:
                print("   -", c)
            return 0

        rc, out = run(["git", "init", "-q"], cwd=stage)
        run(["git", "checkout", "-B", BRANCH], cwd=stage)
        run(["git", "config", "user.name", "catch-bot"], cwd=stage)
        run(["git", "config", "user.email", "catch-bot@users.noreply.github.com"], cwd=stage)
        run(["git", "add", "-A"], cwd=stage)
        rc, out = run(["git", "diff", "--cached", "--quiet"], cwd=stage)
        if rc == 0:
            print("[CATCH] 发布产物无变化，跳过推送。")
            return 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        rc, out = run(["git", "commit", "-q", "-m", f"publish: {stamp}"], cwd=stage)
        if rc != 0:
            print("[CATCH] 提交失败：", out[:200])
            return 1
        rc, out = run(["git", "push", "-f", REMOTE, f"HEAD:{BRANCH}"], cwd=stage, timeout=300)
        if rc != 0:
            print("[CATCH] 推送失败：", out[:300])
            return 1
        print(f"[CATCH] 已发布到公网：https://sylviawwforever.github.io/catch/ （{stamp}）")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
