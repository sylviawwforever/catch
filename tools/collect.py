#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CATCH · 跨平台热点与舆情采集管线
================================
真实数据源（全部为公开榜单 / 公开接口，无模拟）：

  微博       tophub.today 微博热搜榜
  抖音       douyin.com/hot 热点视频 + tophub 抖音视频总榜
  小红书     tophub.today 小红书热榜；已登录时叠加 xhs CLI（笔记 + 评论）
  X          trends24.in 24 小时趋势时间线
  知乎       tophub.today 知乎热榜（交叉验证）
  B 站       bilibili 搜索 + 公开评论接口（真实高赞热评，用于舆情事件热评样本）

输出（assets/data/）：hotspots.json · events.json · meta.json · history.json

用法：
  python3 tools/collect.py                 # 完整采集（含热评抓取）
  python3 tools/collect.py --hourly        # 小时级增量：仅刷新走势快照
  python3 tools/collect.py --no-comments   # 跳过热评抓取（更快）
  python3 tools/collect.py --events 6      # 指定事件数量
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "data")
JINA = "https://r.jina.ai/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
CST = timezone(timedelta(hours=8))

# jina 返回错误页时的特征串（tophub.today 源站 503 会透传到正文）
JINA_ERROR_RE = re.compile(
    r"Service Unavailable|Too Many Requests|Access Denied|"
    r"Error code:\s*503|HTTP/?[\d.]*\s*503", re.I)

PLATFORMS = {
    "weibo":   {"name": "微博",      "emoji": "🐦"},
    "douyin":  {"name": "抖音",      "emoji": "🎵"},
    "xhs":     {"name": "小红书",    "emoji": "📕"},
    "x":       {"name": "X",         "emoji": "✳️"},
    "zhihu":   {"name": "知乎",      "emoji": "💡"},
    "bili":    {"name": "哔哩哔哩",  "emoji": "📺"},
    "news":    {"name": "新闻",      "emoji": "📰"},
}


def xhs_json(cmd: list[str]) -> dict:
    """调用可选的 xhs CLI，并把缺少登录态/命令的情况转成结构化错误。

    公网 GitHub Actions 不会自动继承本机浏览器 Cookie。只有在运行环境安装
    xhs CLI 且配置了登录态时，才会叠加真实笔记与评论；否则仍保留公开热榜，
    并在 platformStatus 中明确标记，而不是伪造在线状态。
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {"_error": "xhs CLI 未安装"}
    except Exception as e:
        return {"_error": str(e)[:160]}
    raw = (p.stdout or "").strip()
    if not raw:
        return {"_error": (p.stderr or "xhs CLI 无返回").strip()[:160]}
    try:
        data = json.loads(raw)
    except Exception:
        return {"_error": (p.stderr or raw[:160]).strip()}
    if p.returncode != 0:
        data.setdefault("_error", (p.stderr or "xhs CLI 执行失败").strip()[:160])
    return data

TOPHUB_BOARDS = {
    "weibo":  [("微博热搜榜", "KqndgxeLl9")],
    "douyin": [("抖音视频总榜", "yjvQ9wDvbg"), ("抖音热点榜", "Dgey31RvZq")],
    "xhs":    [("小红书热榜", "L4MdA5ldxD")],
    "zhihu":  [("知乎热榜", "mproPpoq6O")],
    # 新闻：tophub 新闻分类页实测可用的榜单 id
    "news":   [("澎湃新闻", "wWmoO5Rd4E"), ("今日头条热榜", "x9ozB4KoXb"),
               ("百度热搜", "Jb0vmloB1G"), ("知乎日报", "KMZd7VOvrO")],
}


# ══ HTTP ═══════════════════════════════════════════════════════════════════
def curl(url: str, timeout: int = 35, headers: list[str] | None = None) -> str:
    cmd = ["curl", "-sL", "--max-time", str(timeout), "--compressed",
           "-H", f"User-Agent: {UA}"]
    for h in (headers or []):
        cmd += ["-H", h]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    return p.stdout or ""


def jina(url: str, timeout: int = 30, tries: int = 3) -> str:
    """经 r.jina.ai 抓取并转 Markdown。

    tophub.today 源站会间歇性返回 503；此时 jina 仍返回 200，但正文是错误页而非榜单。
    因此这里要求正文足够长、且开头不含错误特征，并做多轮退避重试。
    """
    last = ""
    for i in range(tries):
        out = curl(JINA + url, timeout=timeout)
        body = (out or "").strip()
        if len(body) > 200 and not JINA_ERROR_RE.search(body[:800]):
            return out
        last = body[:200]
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"jina fetch failed: {last[:120]}")


# ══ B站会话（搜索 API 需要 buvid cookie，否则返回反爬 HTML） ═════════════════
BILI_COOKIE = os.path.join(DATA, "..", "..", "tools", ".bili_cookie")


def bili_cookie_jar() -> str:
    """确保本地有一个带 buvid 的 B站 cookie，返回 cookie 文件路径。"""
    path = os.path.abspath(BILI_COOKIE)
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < 7 * 86400:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    curl("https://www.bilibili.com/", timeout=20,
         headers=[])  # 先请求一次首页以种下 buvid
    cmd = ["curl", "-sL", "--max-time", "20", "-c", path,
           "-H", f"User-Agent: {UA}", "https://www.bilibili.com/"]
    subprocess.run(cmd, capture_output=True, text=True)
    return path


def bili_api(url: str, referer: str = "https://www.bilibili.com/") -> dict:
    """带 cookie 的 B站 API 调用；返回解析后的 JSON（失败返回 {}）。"""
    jar = bili_cookie_jar()
    cmd = ["curl", "-sL", "--max-time", "25", "--compressed", "-b", jar,
           "-H", f"User-Agent: {UA}", "-H", f"Referer: {referer}", url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    raw = p.stdout or ""
    if not raw.strip().startswith("{"):
        return {}
    try:
        d = json.loads(raw)
    except Exception:
        return {}
    return d if d.get("code") == 0 else {}


def bili_raw_json(url: str, referer: str = "https://www.bilibili.com/") -> dict:
    """带 cookie 的 B站 API 原始 JSON（不校验 code，用于 code!=0 仍带 data 的接口）。"""
    jar = bili_cookie_jar()
    cmd = ["curl", "-sL", "--max-time", "25", "--compressed", "-b", jar,
           "-H", f"User-Agent: {UA}", "-H", f"Referer: {referer}", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    except Exception:
        return {}
    raw = p.stdout or ""
    if not raw.strip().startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ── WBI 签名（B站 web 接口现已要求 w_rid，否则评论接口返回 -403 / 反爬 HTML） ──
WBI_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]
_WBI_MIXIN = {"key": "", "at": 0.0}


def wbi_mixin_key() -> str:
    """取 nav 接口下发的 img_key/sub_key，按固定置换表算出 mixin key（缓存 6 小时）。"""
    now = time.time()
    if _WBI_MIXIN["key"] and now - _WBI_MIXIN["at"] < 6 * 3600:
        return _WBI_MIXIN["key"]
    # nav 接口未登录时返回 code=-101，但它仍会下发 wbi_img，因此这里直接读原始 JSON
    d = bili_raw_json("https://api.bilibili.com/x/web-interface/nav")
    wbi = (d.get("data") or {}).get("wbi_img") or {}
    img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    orig = img + sub
    key = "".join(orig[i] for i in WBI_MIXIN_TAB if i < len(orig))[:32]
    if len(key) < 32:
        return ""
    _WBI_MIXIN["key"] = key
    _WBI_MIXIN["at"] = now
    return key


def wbi_sign(params: dict) -> str:
    """给 web 接口参数加上 wts/w_rid，返回已签名的 query string。"""
    import urllib.parse
    key = wbi_mixin_key()
    if not key:
        return ""
    p = dict(params)
    p["wts"] = str(int(time.time()))
    parts = [(k, re.sub(r"[!'()*]", "", str(v))) for k, v in sorted(p.items())]
    q = urllib.parse.urlencode(parts)
    w_rid = hashlib.md5((q + key).encode("utf-8")).hexdigest()
    return q + "&w_rid=" + w_rid


def bili_api_wbi(path: str, params: dict, referer: str = "https://www.bilibili.com/") -> dict:
    """带 WBI 签名的 B站 API 调用。"""
    qs = wbi_sign(params)
    if not qs:
        return {}
    return bili_api(f"https://api.bilibili.com{path}?{qs}", referer=referer)


# ══ 解析 ════════════════════════════════════════════════════════════════════
HEAT_STRICT = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)\s*热度")
HEAT_LOOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)(?![\w一-鿿])")
HEAT_EN = re.compile(r"(\d+(?:\.\d+)?)\s*([KMB])(?![a-z])", re.IGNORECASE)
LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^)\s]+)\)")


def parse_heat(text: str) -> int:
    """'114万' / '342 万热度' / '15.5M posts' → 整数；解析失败返回 0。

    先剥离括号与方括号（含链接 URL 及其 %XX 编码），否则 URL 里的字符序列
    （如 %E9%9F%B3 中的 '9B'）会被误判成英文数量级，产生完全虚假的热度值。
    """
    if not text:
        return 0
    t = text.replace(",", "")
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    for rx in (HEAT_STRICT, HEAT_LOOSE):
        m = rx.search(t)
        if m:
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            if m.group(2) == "万":
                val *= 1e4
            elif m.group(2) == "亿":
                val *= 1e8
            return int(val)
    m = HEAT_EN.search(t)
    if m:
        try:
            val = float(m.group(1))
        except ValueError:
            return 0
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2).upper(), 1)
        return int(val * mult)
    m = re.match(r"^\s*(\d+)\s*$", t)
    return int(m.group(1)) if m else 0


def _clean_title(t: str) -> str:
    t = re.sub(r"^\s*\**|\**\s*$", "", t).strip()
    t = re.sub(r"^[\s\W_]+", "", t)
    return t.strip()


TREND24_RE = re.compile(r"^\s*(\d+)[.)]\s+\[([^\]]{1,100})\]\((https?://[^)]+)\)")
TREND24_TIME_RE = re.compile(r"^###\s+(.+?)\s*$")


def parse_trends24(md: str) -> list[dict]:
    """解析 trends24.in 的 24 小时趋势时间线，取最新时间点区块。"""
    blocks, cur_time, cur = [], None, []
    for line in md.splitlines():
        tm = TREND24_TIME_RE.match(line.strip())
        if tm:
            if cur:
                blocks.append((cur_time, cur))
            cur_time, cur = tm.group(1), []
            continue
        m = TREND24_RE.match(line)
        if m:
            cur.append((int(m.group(1)), m.group(2), m.group(3)))
    if cur:
        blocks.append((cur_time, cur))
    if not blocks:
        fallback = []
        for line in md.splitlines():
            rank_match = re.match(r"^\s*(\d+)[.)]\s+", line.strip())
            if not rank_match:
                continue
            for link_match in LINK_RE.finditer(line):
                title, url = link_match.group(1).strip(), link_match.group(2)
                if len(title) >= 2 and "trends24.in" not in url:
                    fallback.append((int(rank_match.group(1)), title, url))
                    break
        if fallback:
            blocks = [("latest", fallback)]
    if not blocks:
        return []
    # 页面按时间倒序：第一个区块是最新
    when, rows = blocks[0]
    out = []
    for rank, title, url in rows:
        out.append({
            "rank": rank, "title": title, "url": url, "heat": 0,
            "platform": "x", "board": "X Trending (24h)", "snapshot": when,
        })
    return out


def parse_board(md: str, board: str, platform: str) -> list[dict]:
    """通用 tophub 榜单解析：兼容「N.[标题](url)热度」「N.![img](..)[标题](url) + 次行热度」等格式。"""
    lines = md.splitlines()
    out, seen = [], set()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or "http" not in line:
            continue
        rm = re.match(r"^(\d{1,3})\s*\.", line)
        rank = int(rm.group(1)) if rm else len(out) + 1
        title, url, tail_start = "", "", None
        for m in LINK_RE.finditer(line):
            text, u = m.group(1), m.group(2)
            if text.strip().startswith("Image") or text.strip().startswith("!"):
                continue
            if "查看详细" in text or text.strip().startswith("_"):
                continue
            if len(text.strip()) < 2:
                continue
            title, url, tail_start = text.strip(), u, m.end()
            break
        if not title or url in seen:
            continue
        # tophub 页面里还有站点自身的导航（首页 / 报刊 / 关于我们 / App 下载 /
        # API 开放平台），jina 转 Markdown 后会混进榜单条目里——它们不是热榜内容，
        # 必须剔除。注意 API 开放平台在 tophubdata.com 这个域名下，不只是 tophub.today。
        if re.search(r"tophub(?:data)?\.(?:today|com)", url):
            continue
        seen.add(url)
        title = _clean_title(title)
        if len(title) < 2:
            continue
        heat = 0
        if tail_start is not None:
            heat = parse_heat(line[tail_start:])
        if not heat:
            for j in (i + 1, i + 2):
                if j < len(lines):
                    nxt = lines[j].strip()
                    hm = HEAT_STRICT.search(nxt) or HEAT_LOOSE.search(nxt)
                    if hm:
                        heat = parse_heat(hm.group(0))
                        break
        out.append({"rank": rank, "title": title, "url": url, "heat": heat,
                    "board": board, "platform": platform})
    return out


GH_ARCHIVE = "https://raw.githubusercontent.com/iiecho1/hot_searches_for_apps/main/archives"
GH_FALLBACK = {"weibo": "微博", "douyin": "抖音", "zhihu": "知乎", "news": "今日头条"}
GH_LINK_RE = re.compile(r"^\+\s*\[(.+?)\]\((https?://[^)]+)\)")


def _github_archive_items(platform: str, day: str) -> list[dict]:
    """兜底数据源：GitHub 每日热榜归档（公开、免鉴权）。

    仅含标题与原始链接，不含热度数值；页面需标注该口径，避免与实测热度混淆。
    """
    plat = GH_FALLBACK.get(platform)
    if not plat:
        return []
    try:
        y, m, d = day.split("-")
    except ValueError:
        return []
    raw = curl(f"{GH_ARCHIVE}/{plat}/{y}/{m}/{day}.md", timeout=25)
    if not raw or raw.lstrip().startswith("404"):
        return []
    out = []
    for line in raw.splitlines():
        m = GH_LINK_RE.match(line.strip())
        if not m:
            continue
        out.append({"rank": len(out) + 1, "title": _clean_title(m.group(1)),
                    "url": m.group(2), "heat": 0, "board": f"{plat}热榜(每日归档)",
                    "platform": platform, "fallback": True})
        if len(out) >= 50:
            break
    return out


def collect_boards(key: str, day: str | None = None,
                   budget: float = 120.0) -> tuple[list[dict], dict]:
    """抓取 tophub.today 榜单。源站间歇 503 时做 board 级重试，仍失败则用 GitHub 归档兜底。"""
    items, meta = [], {"source": "tophub.today · " + key}
    err = ""
    deadline = time.time() + budget
    for board, bid in TOPHUB_BOARDS[key]:
        got = []
        for attempt in range(3):
            if time.time() > deadline:
                err = err or "budget exhausted"
                break
            try:
                md = jina(f"https://tophub.today/n/{bid}")
                got = parse_board(md, board, key)
                if got:
                    break
            except Exception as e:
                err = str(e)[:140]
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
        items.extend(got)
    # 去重
    seen, out = set(), []
    for it in items:
        k = it["url"]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    # 兜底：tophub 全军覆没时，用 GitHub 每日归档补齐标题层（无热度）
    if not out:
        day = day or datetime.now(CST).strftime("%Y-%m-%d")
        fb = _github_archive_items(key, day)
        if fb:
            out = fb
            meta["source"] = f"GitHub 每日热榜归档 · {key}（tophub 暂不可用）"
            meta["fallback"] = True
    meta.update({"items": len(out), "ok": bool(out), "error": None if out else (err or None)})
    return out, meta


def collect_douyin() -> tuple[list[dict], dict]:
    """抖音只取文字热点条目（热搜榜话题），不再采集视频。"""
    topics, meta = collect_boards("douyin")
    meta.update({"error": meta.get("error")})
    return topics, meta


def collect_xhs() -> tuple[list[dict], dict]:
    """tophub 小红书热榜（公开） + xhs CLI（若已登录则叠加真实笔记与评论）。"""
    items, meta = collect_boards("xhs")
    meta["source"] = "tophub 小红书热榜" + (" + xhs CLI" if _xhs_authed() else "")
    # CLI 叠加（登录态可用时）
    try:
        d = xhs_json(["xhs", "hot", "-c", "fashion", "--json"])
        if d and "_error" not in d:
            notes = (d.get("data") or {}).get("items") or []
            for it in notes:
                nc, ii = it.get("note_card") or {}, it.get("note_card", {}).get("interact_info") or {}
                t = nc.get("display_title") or ""
                if t:
                    items.append({
                        "rank": len(items) + 1, "title": t, "platform": "xhs",
                        "board": "小红书热榜(CLI)", "heat": parse_heat(str(ii.get("liked_count", "0"))),
                        "url": f"https://www.xiaohongshu.com/explore/{it.get('id')}",
                        "id": it.get("id"), "xsec_token": it.get("xsec_token", ""),
                        "author": (nc.get("user") or {}).get("nick_name", ""),
                        "likes": parse_heat(str(ii.get("liked_count", "0"))),
                        "comments": parse_heat(str(ii.get("comment_count", "0"))),
                    })
            meta["items"] = len(items)
            meta["ok"] = True
    except Exception:
        pass
    if not _xhs_authed():
        meta["needs_login"] = True
        meta["note"] = "小红书 CLI 登录态已失效；执行 `xhs login` 可解锁真实笔记与评论抓取"
    return items, meta


def _xhs_authed() -> bool:
    try:
        d = xhs_json(["xhs", "feed", "--json"])
        return bool(d) and "_error" not in d
    except Exception:
        return False


def collect_x() -> tuple[list[dict], dict]:
    out, err = [], None
    for url in ["https://trends24.in/united-states/", "https://trends24.in/"]:
        try:
            out = parse_trends24(jina(url))
            if out:
                break
        except Exception as e:
            err = str(e)[:140]
    return out, {"source": "trends24.in · X 24h 趋势", "items": len(out),
                 "ok": bool(out), "error": err}


# ── 网络流行语：近 7 天社交媒体高频词条自行归纳 ──────────────────────────────
# 方法（不引用任何年度榜单）：
#   语料   近 7 天、5 个平台的公开热榜逐日归档（微博/抖音/知乎/百度/今日头条）
#   阶段一（结构判据，无需联网）  枚举 2–9 字中文连续片段，保留同时满足以下条件者：
#            ① 跨 ≥2 天复现；② 跨 ≥2 个不同标题句式，或跨 ≥2 个平台；
#            ③ 非硬新闻语境占比达标（不含事件动词 / 数字 / 长串英文）；
#            ④ 与「可复用句式」标记（教学/挑战/转场/穿搭/治愈/翻唱/破防…）共现，
#               或其本身即为该类模板词（如「官宣」「打卡」）；
#            ⑤ 非虚词碎片、非通用抽象词。
#   阶段二（百科仲裁）  对阶段一候选逐词查百度百科开放 API：
#            desc 判为「网络流行语 / 网络用语 / 流行语…」→ 直接采纳并引用百科释义；
#            desc 判为人物 / 机构 / 地名 / 普通词义 → 剔除；
#            desc 判为作品（歌曲 / 小说 / 综艺…）→ 该词多半是「作品衍生的梗」，
#               需以梗语境（而非节目语境）为主才保留；
#            无词条 → 视为新词，依据其真实热榜语境归纳释义。
#   排序   按近 7 天热榜实际出现次数倒序（口径：社交媒体上被提到最多的关键词）
SLANG_ARCHIVE_BASE = ("https://raw.githubusercontent.com/iiecho1/hot_searches_for_apps"
                      "/main/archives")
SLANG_ARCHIVE_PLATS = ["微博", "抖音", "知乎", "百度", "今日头条"]
SLANG_LINK_RE = re.compile(r'^\+\s*\[(.+?)\]\((https?://[^)]+)\)')
SLANG_BAIKE_API = ("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103"
                   "&format=json&appid=379020&bk_key=%s&bk_length=600")
SLANG_BAIKE_CACHE = os.path.join(DATA, "slang_baike_cache.json")

# ── 百科类别判定 ──────────────────────────────────────────────────────────────
SLANG_DESC_RE = re.compile(r"网络流行语|网络用语|网络流行词|网络热梗|网络梗|网络热词|流行语"
                            r"|网络语言|网络流行|俗语|俚语|弹幕|旅游热词|网络新词|年度流行语|流行舞蹈")
SLANG_HARD_WORDS = """演员 歌手 主持人 女主角 男主角 企业家 运动员 球员 教练 导演 作家 诗人 画家 音乐家 模特 网红
学者 教授 科学家 医生 律师 法官 军人 选手 主播 明星 艺人 偶像 作曲家 建筑师 院士 创始人 董事长 总裁
经纪人 官员 总统 总理 国王 皇帝 哲学家 经济学家 公司 集团 银行 大学 学院 学校 医院 协会 基金会 政党
政府 部队 俱乐部 球队 工作室 出版社 电视台 电台 品牌 企业 酒店 餐厅 饭店 赛事 联盟 研究院 实验室 国家
城市 省份 地区 县 镇 村 洲 街道 行政区 共和国 王国 名胜 景点 地标 广场 自治区 直辖市 文明古国 地级市
美利坚 日本国 中华 杂志 周刊""".split()
SLANG_GENERIC_WORDS = """汉语词语 汉语词汇 名词词汇 副词 动词 形容词 代词 量词 时间词 处所词 政治人物 国籍 水体 地貌
矿产 亲属称谓 亲属 称谓 发型 服饰 病症 疾病 医学 化学元素 植物 动物 昆虫 食品 饮料 菜肴 水果 蔬菜 货币
法律 法规 军事 体育项目 数学 物理 化学 地质 天文 会计 金融术语 信息技术 互联网技术 朝代 历史事件 战役 条约
文献 典籍 心理学 生物学 哲学 统计学 词语释义 社会学 名词 职业 生理学 解剖学 建筑学 农业 气象学""".split()
SLANG_WORK_WORDS = "歌曲 音乐 专辑 小说 书籍 综艺 动画 漫画 电影 电视剧 游戏 手游 单曲 唱片 剧集 纪录片".split()
SLANG_HARD_RE = re.compile("|".join(SLANG_HARD_WORDS))
SLANG_GENERIC_RE = re.compile("|".join(SLANG_GENERIC_WORDS))
SLANG_WORK_RE = re.compile("|".join(SLANG_WORK_WORDS))

# ── 可复用句式标记：流行语在热榜里主要以「玩法 / 教程 / 转场 / 穿搭」等模板形态流通 ──
SLANG_MEME_MARKERS = ("教学 挑战 来了 治愈 转场 穿搭 同款 玩法 教程 版本 上头 破防 模仿 打卡 模板 翻唱 弹唱 "
                       "舞蹈 拍法 姿势 配方 接梗 玩梗 刷屏 出圈 氛围 排面 仪式 种草 变装 整活 出片 改造 翻红 "
                       "围观 合拍 接力 逃不过 躲不过 逃不掉 上新 安排了 拿捏 平替 绝绝子 硬控 有活人感 "
                       "越打越火 氛围感 官宣 队形 出现了 什么 操作 这么 好像").split()
SLANG_MEME_SET = set(SLANG_MEME_MARKERS)
# 这些词是通用内容模板/描述词，不单独认定为“网络流行语”。
# 它们可以作为候选词的上下文标记，但不能作为最终收录词。
SLANG_COMMON_EXCLUDE = set("""
官宣 手势舞 同款 硬核 打卡 出片 治愈 教程 挑战 转场 穿搭
玩法 上头 破防 模仿 模板 翻唱 弹唱 变装 整活 合拍 接力
拍法 姿势 配方 接梗 玩梗 刷屏 出圈 氛围感 拿捏 平替 绝绝子
""".split())
# 二创型标记：出现「照着做」的玩法/教程/模仿结构，是流行语最可靠的信号
SLANG_CREATIVE_MARKERS = ("教学 挑战 模仿 翻唱 弹唱 转场 翻拍 改造 变装 整活 合拍 接力 拍法 姿势 "
                         "配方 接梗 玩梗 出片 跟风 二创 教程 玩法 打卡 排面 复刻 拿来 唱跳 学着 "
                         "跳个 跳了 跳起 来段 学个 做个 配上 套用").split()
# 作品 / 节目语境标记：作品名被当作节目本身报道
SLANG_WORK_MARKERS = ("第 期 定档 官宣 收官 开播 阵容 嘉宾 首播 播出 杀青 片花 上线 首期 招商 冠名 独家 版权 "
                       "更新至 直播中 完整版 正片 花絮 专访 片段 首映 票房 预售").split()
SLANG_WORK_MARK_RE = re.compile("|".join(SLANG_WORK_MARKERS))

SLANG_STOP = set("""什么 怎么 为何 哪里 哪些 为什么 吗 呢 吧 啊 了 的 地 得 是 在 有 和 与 就 都 也 还 又 被 把 给 让 对 从 到 向 于
这 那 这个 那个 一些 自己 我们 你们 他们 今天 明天 昨天 现在 已经 还是 可以 不是 没有 真的 突然 居然 竟然 但是 因为 所以
一个 一种 一场 一起 一直 记者 媒体 官方 网友 表示 认为 发现 出现 引发 关注 讨论 热议 冲上 登上 榜首 排名 热搜 榜单
视频 直播 现场 图 组图 独家 可能 需要 应该 开始 进行 属于 相关 方面 情况 问题 时候 之后 之前 终于 最近 这些 那些
最好 更多 非常 特别 有点 完全 第一次 最后 就是 只是 不过 而且 并且 然后""".split())
SLANG_FIRST_BAD = set("的地得了和与或但有很更最元块毛斤岁个名位只也都还就再又吗呢吧啊呀多所其此该每某另"
                       "两三四五六七八九十百千万进出入上下退换改发爆刷涨跌拿找看来讲述")
SLANG_LAST_BAD = set("了吗呢吧啊呀么什的得和与或到过住完好起来去掉被让对为刑罪罚不没")
SLANG_GENERIC_LIST = set("""全网 刷屏 热搜 网友 舆论 热度 榜单 平台 网友 焦虑 背后 意义 原因 方式 方法 内容 部分 时间 地方 东西 感觉 样子 效果 影响 作用 关系 态度 状态
结果 过程 水平 能力 条件 环境 阶段 时期 现实 实际 目的 手段 工具 基础 核心 关键 重点 难点 优点 缺点 未来 过去
全球 世界 中国 美国 日本 英国 法国 德国 俄罗斯 印度 韩国 国家 民族 城市 农村 区域 公司 企业 品牌 产品 商品 市场
行业 产业 领域 方向 趋势 程度 两个 几个 很多 大家 人们 网友 医生 律师 老师 护士 快递 外卖 观众 女生 男生 女孩 男孩""".split())
# 硬新闻语境标记：含这些词的标题视为事件报道，而非流行语流通场景
SLANG_EVENT_MARKS = set("""抵达 会晤 会见 夺冠 回应 宣布 发布 去世 坠机 被抓 立案 起诉 判刑 确诊 辞职 离职 入职 澄清 否认 辟谣
被曝 曝光 塌房 安葬 逮捕 通报 处罚 查处 签署 会面 接见 出席 开幕 闭幕 财报 新规 政策 通知 公告 涨价 降价 召回 禁赛 受伤
退赛 续约 转会 签约 离婚 结婚 怀孕 上涨 下跌 暴跌 暴涨 涨停 跌停 空袭 地震 台风 暴雨 暴雪 事故 坠毁 失联 遇难 疫情
证实 确认 首播 定档 上线 开售 开抢 开票 销量 数据 报告 调查 举报 不实 悼念 缅怀 追授 约谈 问责 免职 撤职 停职 晋升 履新
亮相 登场 首秀 加盟 告别 航班 列车 飞机 高铁 地铁 留学 高考 中考 考研 考试 招生 录取 成绩 放假 开学 暑假 寒假 官宣
发布会 出任 就任 访华 国事访问 会谈 获奖 提名 落选 退役 转投 解约 增持 减持 营收 净利 裁员 招聘 升级 停售 新政 试点 整治
严查 违规 罚款 撤诉 判决 上诉 赔偿""".split())


def _slang_norm(text: str) -> str:
    return re.sub(r'[\s　#《》「」“”"\'\u3000]+', "", text or "")


def _slang_is_news(title: str) -> bool:
    """硬新闻语境判定：含数字、含长串英文，或含事件动词。"""
    if re.search(r"[0-9０-９]", title):
        return True
    if re.search(r"[A-Za-z]{3,}", title):
        return True
    return any(m in title for m in SLANG_EVENT_MARKS)


def slang_fetch_archives(days: int = 7) -> tuple[list[dict], list[str]]:
    """抓取近 N 天各平台公开热榜逐日归档。

    数据源：GitHub 公开仓库 iiecho1/hot_searches_for_apps 的 archives 目录，
    每日每平台一个 Markdown（格式：+ [标题](链接)），无需鉴权。
    """
    now = datetime.now(CST)
    records: list[dict] = []
    per_day: dict[str, list[dict]] = {}
    for offset in range(days + 2):
        d = now - timedelta(days=offset)
        day = d.strftime("%Y-%m-%d")
        got = 0
        for plat in SLANG_ARCHIVE_PLATS:
            url = (f"{SLANG_ARCHIVE_BASE}/{urllib_quote(plat)}/{d.strftime('%Y')}/"
                   f"{d.strftime('%m')}/{day}.md")
            md = ""
            for _ in range(3):
                try:
                    md = curl(url, timeout=30)
                except Exception:
                    md = ""
                if "](" in md:
                    break
                time.sleep(1.2)
            if "](" not in md:
                print(f"    ! 归档缺失 {plat} {day}")
                continue
            time.sleep(0.25)
            n = 0
            for line in md.splitlines():
                m = SLANG_LINK_RE.match(line.strip())
                if not m:
                    continue
                title, link = m.group(1).strip(), m.group(2).strip()
                if not title:
                    continue
                per_day.setdefault(day, []).append(
                    {"platform": plat, "day": day, "title": title, "url": link})
                n += 1
            got += n
    # 取最近 days 个「确有数据」的日子（当日归档通常尚未提交，故多取 2 天备用）
    days_hit = sorted(per_day.keys(), reverse=True)[:days]
    days_hit = sorted(days_hit)
    for d in days_hit:
        records.extend(per_day[d])
    return records, days_hit


def slang_extract_candidates(records: list[dict]) -> list[dict]:
    """阶段一：枚举热榜标题里的 2–9 字中文片段，按结构判据筛出候选。

    不依赖分词器，靠「跨天复现 + 跨句式/跨平台复现 + 非硬新闻语境 + 可复用句式共现」区分
    可复用表达与一次性新闻事件。
    """
    cand: dict[str, dict] = defaultdict(
        lambda: {"days": set(), "plats": set(), "n": 0, "forms": set(),
                 "nforms": set(), "src": []})
    for r in records:
        t = _slang_norm(r["title"])
        if not t or len(t) > 30:
            continue
        news = _slang_is_news(t)
        L = len(t)
        for i in range(L):
            for n in range(2, min(10, L - i + 1)):
                p = t[i:i + n]
                if (p in SLANG_STOP or p in SLANG_GENERIC_LIST
                        or p in SLANG_COMMON_EXCLUDE):
                    continue
                if not re.match(r"^[一-鿿]", p) or not re.search(r"[一-鿿]$", p):
                    continue
                if re.search(r"[0-9０-９A-Za-z]", p):
                    continue
                e = cand[p]
                e["days"].add(r["day"])
                e["plats"].add(r["platform"])
                e["n"] += 1
                e["forms"].add(t)
                if not news:
                    e["nforms"].add(t)
                if len(e["src"]) < 12:
                    e["src"].append({"platform": r["platform"], "day": r["day"],
                                     "title": r["title"], "url": r["url"]})
    out = []
    for p, e in cand.items():
        if len(e["days"]) < 2:
            continue
        if len(e["nforms"]) < 2 and len(e["plats"]) < 2:
            continue
        if p[0] in SLANG_FIRST_BAD or p[-1] in SLANG_LAST_BAD:
            continue
        forms = sorted(e["forms"])
        meme_forms = _slang_meme_forms(p, forms)
        creative_forms = _slang_creative_forms(p, forms)
        if not meme_forms and p not in SLANG_MEME_SET:
            continue
        out.append({"word": p, "days": sorted(e["days"]), "platforms": sorted(e["plats"]),
                    "mentions": e["n"], "forms": forms, "variants": len(e["nforms"]),
                    "memeForms": meme_forms, "creativeForms": creative_forms,
                    "samples": e["src"]})
    out.sort(key=lambda x: (-x["mentions"], -x["variants"], -len(x["days"])))
    return out


def _slang_meme_forms(word: str, forms: list[str]) -> list[str]:
    """筛出「该词与可复用句式标记共现」的标题——流行语的典型流通形态。"""
    markers = [m for m in SLANG_MEME_MARKERS if m != word]
    out = []
    for f in forms:
        if any(m in f for m in markers):
            out.append(f)
    return out


def _slang_creative_forms(word: str, forms: list[str]) -> list[str]:
    """筛出「该词与二创型标记（教学/挑战/翻唱/转场/模仿…）共现」的标题。

    流行语被「照着做」的二创玩法包围，是它区别于普通热词与新闻事件的核心特征。
    """
    markers = [m for m in SLANG_CREATIVE_MARKERS if m != word]
    return [f for f in forms if any(m in f for m in markers)]


def slang_baike_lookup(words: list[str]) -> dict[str, dict]:
    """百度百科开放 API 逐词判定类别并取释义（带本地磁盘缓存，可增量复用）。"""
    cache: dict[str, dict] = {}
    if os.path.exists(SLANG_BAIKE_CACHE):
        try:
            with open(SLANG_BAIKE_CACHE, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}
    todo = [w for w in words if w not in cache]
    for w in todo:
        rec = {"desc": "", "abstract": "", "definition": "", "url": "", "has": False}
        raw = ""
        for _ in range(2):
            try:
                raw = curl(SLANG_BAIKE_API % urllib_quote(w), timeout=20,
                           headers=["Referer: https://baike.baidu.com/"])
                break
            except Exception:
                time.sleep(0.6)
        try:
            j = json.loads(raw) if raw and raw.strip() else {}
        except Exception:
            j = {}
        if j:
            card = j.get("card")
            definition = ""
            if isinstance(card, dict):
                v = card.get("m234_definition")
                definition = "".join(v) if isinstance(v, list) else (v if isinstance(v, str) else "")
            elif isinstance(card, list):
                for it in card:
                    if isinstance(it, dict) and it.get("key") == "m234_definition":
                        v = it.get("value")
                        definition = ("".join(str(x) for x in v) if isinstance(v, list)
                                      else str(v or ""))
                        break
            rec = {"desc": (j.get("desc") or "").strip(),
                   "abstract": (j.get("abstract") or "").strip(),
                   "definition": definition.strip(),
                   "url": j.get("url") or f"https://baike.baidu.com/item/{urllib_quote(w)}",
                   "has": bool(j.get("desc"))}
        cache[w] = rec
        time.sleep(0.12)
        try:
            with open(SLANG_BAIKE_CACHE, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
        except Exception:
            pass
    return {w: cache.get(w, {}) for w in words}


def _slang_classify(desc: str) -> int:
    """0 = 百科判为流行语；1 = 百科无词条（新词）；2 = 作品类词条；3 = 实体/普通词（剔除）。"""
    if not desc:
        return 1
    if SLANG_DESC_RE.search(desc):
        return 0
    if SLANG_HARD_RE.search(desc) or SLANG_GENERIC_RE.search(desc):
        return 3
    if SLANG_WORK_RE.search(desc):
        return 2
    return 3


def slang_filter_rank(cands: list[dict], baike: dict[str, dict]) -> list[dict]:
    """阶段二：百科仲裁 + 碎片剪枝 + 语境门槛，最终按近 7 天提及量排序。"""
    for c in cands:
        info = baike.get(c["word"], {})
        c["bk"] = info
        c["tier"] = _slang_classify(info.get("desc", ""))
        c["workForms"] = [f for f in c["forms"] if SLANG_WORK_MARK_RE.search(f)]
    byword = {c["word"]: c for c in cands}
    for c in cands:
        if c["word"] in SLANG_COMMON_EXCLUDE:
            c["drop"] = True
            continue
        c["drop"] = False
        if c["tier"] != 0:
            # 碎片剪枝：若存在提及量接近的更长完整形式，则本片段只是它的碎片
            for w, other in byword.items():
                if w == c["word"]:
                    continue
                if c["word"] in w and c["mentions"] >= other["mentions"] * 0.8:
                    c["drop"] = True
                    break
    kept = []
    for c in cands:
        if c["drop"] or c["tier"] == 3:
            continue
        if c["tier"] == 1:
            # 新词：需在多个句式中流通，且至少出现在一个「二创玩法」语境里
            if len(c["memeForms"]) < 2 or c["variants"] < 2:
                continue
            if not c["creativeForms"]:
                continue
            if any(c["word"].endswith(m) for m in SLANG_CREATIVE_MARKERS):
                continue          # 模板后缀碎片（如「舞挑战」），不是独立流行语
        elif c["tier"] == 2:
            # 作品衍生的梗：需以梗语境为主、不是节目语境，且确为二创玩法
            if len(c["memeForms"]) < 2 or len(c["memeForms"]) < len(c["forms"]) * 0.4:
                continue
            if len(c["workForms"]) >= len(c["forms"]) * 0.5:
                continue
            if not c["creativeForms"]:
                continue
        kept.append(c)
    kept.sort(key=lambda x: (-x["mentions"], -x["variants"], -len(x["days"])))
    return kept


def _slang_define(c: dict) -> tuple[str, str]:
    """给出释义与其来源：优先百科释义，其次百科摘要首句，最后依据真实热榜语境归纳。"""
    info = c.get("bk") or {}
    meaning = _clean_baike_text(info.get("definition") or "")
    if meaning:
        return meaning[:150], "百度百科"
    abstract = _clean_baike_text(info.get("abstract") or "")
    if abstract:
        return abstract.split("。")[0][:150], "百度百科"
    plats = "、".join(c["platforms"][:3])
    forms = "、".join(c["forms"][:2])
    txt = (f"近 7 天在{plats}等平台热榜被反复提及，共 {c['mentions']} 次，"
           f"覆盖 {len(c['days'])} 天、{len(c['platforms'])} 个平台；"
           f"在热榜中以「{forms}」等不同句式出现，属当下在流通的可套用表达。")
    return txt, "本轮据近 7 天热榜语境归纳"


def collect_slang_7d(limit: int = 5) -> tuple[list[dict], dict]:
    """近 7 天社交媒体高频流行语（自行归纳，不引用任何年度榜单）。"""
    records, days_hit = slang_fetch_archives(days=7)
    if not records:
        return [], {"source": "近 7 天热榜逐日归档", "items": 0, "ok": False,
                    "note": "归档源不可用"}
    cands = slang_extract_candidates(records)
    baike = slang_baike_lookup([c["word"] for c in cands])
    kept = slang_filter_rank(cands, baike)

    rows: list[dict] = []
    for c in kept:
        if len(rows) >= limit:
            break
        meaning, src = _slang_define(c)
        samples, seen = [], set()
        for s in c["samples"]:
            if s["title"] in seen:
                continue
            seen.add(s["title"])
            samples.append(s)
            if len(samples) >= 3:
                break
        rows.append({
            "word": c["word"],
            "desc": meaning,
            "defSource": src,
            "kind": (c.get("bk") or {}).get("desc", "") or "新词（百科未收录）",
            "mentions": c["mentions"],
            "days": len(c["days"]),
            "dayList": c["days"],
            "platforms": c["platforms"],
            "platformCount": len(c["platforms"]),
            "variants": c["variants"],
            "samples": samples,
            "url": (c.get("bk") or {}).get("url")
                    or f"https://baike.baidu.com/item/{urllib_quote(c['word'])}",
        })
    window = f"{min(days_hit)} ~ {max(days_hit)}" if days_hit else "近 7 天"
    return rows, {
        "source": "近 7 天公开热榜逐日归档 · " + " / ".join(SLANG_ARCHIVE_PLATS),
        "window": window,
        "days": len(days_hit),
        "corpus": len(records),
        "candidates": len(cands),
        "items": len(rows),
        "ok": bool(rows),
        "note": f"{len(days_hit)} 天 × {len(SLANG_ARCHIVE_PLATS)} 平台，"
                f"候选 {len(cands)} 词，经可复用句式与百科类别仲裁后收录 {len(rows)} 词",
    }


def _clean_baike_text(s: str) -> str:
    """清洗百度百科文本：去掉残留的引用角标数字与多余空白。"""
    s = re.sub(r"<[^>]+>", "", s or "")                     # 百科释义里的 <sup>/<a> 等标签
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"[（(]?\[\d+\]?[）)]?", "", s)          # [12] 形态角标
    s = re.sub(r"(?<=[\u4e00-\u9fff])\d{1,2}$", "", s)   # 结尾紧跟中文的孤立数字
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def collect_bili(keywords: list[str], per_query: int = 12, limit: int = 24) -> tuple[list[dict], dict]:
    """B 站相关视频（用于热评样本与视频选题）。走带 cookie 的搜索 API，可分页。"""
    out, seen = [], set()
    for kw in keywords[:4]:
        try:
            md = jina(f"https://search.bilibili.com/all?keyword={urllib_quote(kw)}")
        except Exception:
            continue
        bvs = re.findall(r"(BV[0-9A-Za-z]{10})", md)
        added = 0
        for bv in bvs:
            if bv in seen:
                continue
            seen.add(bv)
            out.append({"bv": bv, "title": "", "url": f"https://www.bilibili.com/video/{bv}",
                        "platform": "bili", "board": "B站搜索", "heat": 0})
            added += 1
            if added >= per_query or len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out, {"source": "bilibili 搜索", "items": len(out), "ok": bool(out)}


def bili_search_api(keyword: str, pages: int = 3) -> list[dict]:
    """B站搜索 API（需 buvid cookie）。返回带播放量、评论数与封面的视频列表。"""
    out, seen = [], set()
    for pn in range(1, pages + 1):
        url = ("https://api.bilibili.com/x/web-interface/search/type"
               f"?search_type=video&keyword={urllib_quote(keyword)}&page={pn}")
        d = bili_api(url, referer=f"https://search.bilibili.com/all?keyword={urllib_quote(keyword)}")
        if not d:
            break
        for r in (d.get("data") or {}).get("result") or []:
            bv = r.get("bvid")
            if not bv or bv in seen:
                continue
            seen.add(bv)
            pic = r.get("pic") or ""
            if pic.startswith("//"):
                pic = "https:" + pic
            out.append({
                "bv": bv, "title": re.sub(r"<[^>]+>", "", r.get("title") or ""),
                "review": r.get("review") or 0, "play": r.get("play") or 0,
                "pic": pic, "author": r.get("author") or "",
                "duration": r.get("duration") or "", "url": f"https://www.bilibili.com/video/{bv}",
                "platform": "bili", "board": "B站搜索",
            })
        time.sleep(0.7)
    return out


def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


def bili_aid(bv: str) -> int:
    d = bili_api(f"https://api.bilibili.com/x/web-interface/view?bvid={bv}",
                 referer=f"https://www.bilibili.com/video/{bv}")
    try:
        return int(d["data"]["aid"])
    except Exception:
        return 0


def bili_hot_comments(bv: str, pages: int = 4) -> list[dict]:
    """B 站视频的高赞热评（mode=3 热度排序，公开接口）。

    结构说明：未登录状态下根评论接口仅返回 3 条根评论，
    二级回复需走子评论接口逐条拉取。因此单视频产能 = 根评论 + 其全部子回复。

    接口现状（2026-11）：`/x/v2/reply/main` 已要求 WBI 签名（w_rid），
    否则返回反爬 HTML；带签名的 `/x/v2/reply/wbi/main` 可正常返回根评论，
    子评论仍走 `/x/v2/reply/reply`。
    """
    aid = bili_aid(bv)
    if not aid:
        return []
    ref = f"https://www.bilibili.com/video/{bv}"
    d = bili_api_wbi("/x/v2/reply/wbi/main",
                     {"oid": aid, "type": 1, "mode": 3, "ps": 20, "pn": 1},
                     referer=ref)
    if not d:
        return []
    out = []

    def push(r, is_sub=False):
        msg = ((r.get("content") or {}).get("message") or "").strip()
        if not msg:
            return
        out.append({
            "text": msg,
            "likes": int(r.get("like") or 0),
            "author": (r.get("member") or {}).get("uname", ""),
            "platform": "bili",
            "emoji": "📺",
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ctime") or 0)),
            "ip": (r.get("reply_control") or {}).get("location", "").replace("IP属地：", ""),
            "sourceUrl": ref,
            "videoId": bv,
            "isSub": is_sub,
        })

    roots = (d.get("data") or {}).get("replies") or []
    for r in roots:
        push(r)
        root = r.get("rpid")
        if not root:
            continue
        # 子评论：按 count 决定翻几页
        cnt = int(r.get("count") or 0)
        sub_pages = min(4, max(1, (cnt // 20) + 1))
        for pn in range(1, sub_pages + 1):
            sd = bili_api(
                f"https://api.bilibili.com/x/v2/reply/reply?type=1&oid={aid}&root={root}&pn={pn}&ps=20",
                referer=ref)
            if not sd:
                break
            subs = (sd.get("data") or {}).get("replies") or []
            if not subs:
                break
            for s in subs:
                push(s, is_sub=True)
            time.sleep(0.35)
        time.sleep(0.35)
    return out


# ── 热评缓存（跨轮次累积，接口被限流时不至于丢失样本） ──────────────────────
CCACHE = os.path.join(DATA, "comments_cache.json")


def load_ccache() -> dict:
    if os.path.exists(CCACHE):
        try:
            with open(CCACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_ccache(cache: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    with open(CCACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


# ══ 中文文本工具 ════════════════════════════════════════════════════════════
STOP = set("""
的 了 在 是 我 你 他 她 它 我们 你们 他们 这 那 一个 一种 这个 那个 什么 怎么 为什么 如何
哪些 谁 吗 呢 吧 啊 呀 哦 嗯 就是 不是 没有 可以 已经 还是 因为 所以 但是 而且
网友 表示 认为 回应 官方 发布 宣布 曝光 事件 出现 现场 视频 直播 话题 微博 热搜
今日 日 消息 记者 报道 新华社 央视 看到 发现 进行了 进行 方面 相关 目前 已经 之后
视频 片段 完整 版 网友 女子 男子 官方 上午 下午 晚间 首发 独家 回应 通报 情况
""".split())

POS_WORDS = """
好 赞 支持 认可 不错 优秀 厉害 伟大 满意 喜欢 期待 感动 温暖 治愈 开心 快乐 幸福
漂亮 好看 高级 惊艳 绝了 神了 太强 无敌 真诚 良心 负责 靠谱 进步 提升 突破 创新
实力 品质 用心 正能量 公平 公正 透明 安全 守护 及时 处理 解决 整改 优化 改进
感谢 谢谢 致敬 加油 努力 成功 收获 增长 上升 利好 领先 认可 干得漂亮 支持一下
""".split()

NEG_WORDS = """
差 垃圾 难看 恶心 失望 愤怒 生气 讨厌 反感 抵制 反对 质疑 批评 谴责 不满 抱怨
投诉 维权 欺骗 虚假 造假 作假 敷衍 冷漠 推诿 拖延 失职 违规 违法 曝光
黑心 无良 丧良心 恰饭 割韭菜 智商税 退钱 塌房 翻车 崩了 离谱 荒谬
事故 死亡 重伤 受伤 火灾 爆炸 地震 灾害 疫情 确诊 感染 遇难 失踪 虐待 暴力
涉嫌 被查 逮捕 起诉 判决 罚款 处罚 封禁 下架 抵制 跑路 破产 裁员 降薪
""".split()

ANGER_WORDS = """
愤怒 生气 气炸 炸了 气死 无语 荒谬 离谱 恶心 举报 维权 讨个说法 必须 严查 严惩
天理难容 不可原谅 该死 混蛋 骗子 欺诈 黑幕 底线 生气 愤怒 恶心 滚
""".split()

NEG_GRAD = ["非常", "极其", "太", "特别", "十分", "格外", "严重", "极度", "彻底", "简直"]


def sentiment_score(text: str) -> tuple[str, float]:
    """规则法中文情感判定：词命中 + 程度副词加权 + 否定翻转。"""
    t = text.lower()
    score = 0.0
    for w in POS_WORDS:
        if w in t:
            score += 1.0
    for w in NEG_WORDS:
        if w in t:
            score -= 1.2
    for g in NEG_GRAD:
        if g in t:
            score -= 0.4
    if any(n in t for n in ["不是", "并不", "没有", "别", "无法"]):
        score *= 0.6
    for w in ANGER_WORDS:
        if w in t:
            score -= 0.6
    if score >= 0.8:
        return "积极", score
    if score <= -0.8:
        return "消极", score
    return "中性", score


def tokens(text: str) -> list[str]:
    try:
        import jieba
        segs = list(jieba.cut(text))
    except Exception:
        segs = re.findall(r"[\u4e00-\u9fa5]{2,}|[A-Za-z0-9#]+", text)
    return [s for s in segs if len(s) >= 2 and s.lower() not in STOP and not s.isdigit()]


# ══ 事件聚类 ════════════════════════════════════════════════════════════════
GENERIC = set("""
回应 网友 表示 官方 发布 宣布 曝光 事件 消息 视频 直播 话题 热搜 现场 完整 版 片段
女子 男子 官方 上午 下午 晚间 今日 记者 报道 相关 目前 已经 之后 方面 进行 出现
""".split())

GENERIC_EXT = set("""
普通人 第一次 研究 全球 世界 网友 官方 回应 发布 宣布 曝光 事件 消息 视频 直播 话题
热搜 现场 完整 片段 女子 男子 上午 下午 晚间 今日 记者 报道 相关 目前 已经 之后 方面
进行 出现 一个 这个 那个 这些 那些 什么 怎么 为什么 如何 哪些 如此 这种 那种
中国 美国 英国 日本 韩国 俄罗斯 法国 德国 城市 农村 公司 企业 学校 大学 医院
工作 工资 收入 成本 价格 消费 存款 银行 理财 基金 房子 房子 股票 市场 经济
时间 时候 今天 昨天 明天 今年 去年 明年 上午 晚上 早上 中午 周末 假期
网友 朋友 大人 小孩 孩子 父母 老师 学生 医生 律师 记者 编辑 网友 大家
建议 分享 打卡 体验 感受 评价 看法 观点 想法 讨论 关注 好奇 疑问
生活 社会 文化 教育 健康 环保 科技 时尚 美食 旅游 运动 娱乐 情感
真的 有点 感觉 但是 而且 因为 所以 如果 虽然 还是 已经 可以 应该 可能
是不是 什么 怎么 如何 哪些 这个 那个 这些 那些 一种 一款 一件 一场 一次
怎么办 什么办 好不好 可不可以 有没有 有木有 会不会 能不能 干什么 为何必
""".split())

# 英文与缩写类泛词：短、通用，既会被误当事件关键词，也会误当聚类合并依据
GENERIC_LATIN = set("""
app apps api ok pdf hd tv ai cd ui mac os ios pc mp3 mp4 url news live chat dm
video png jpg txt doc top new old best hot
""".split())


def _norm_title(t: str) -> str:
    t = re.sub(r"[#＃][^#\s]{1,20}", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", t)
    return t.strip()


def cluster_events(platform_items: dict[str, list[dict]], max_events: int = 6) -> list[dict]:
    """跨平台共振聚类。

    仅当两个标题共享「低文档频率实义 token」（中文≥3 字 / 西文≥3 字符）
    时才判为同一话题；再看该 token 是否真实出现在两个标题中。
    """
    docs = []
    for pid, items in platform_items.items():
        if pid.startswith("_"):
            continue
        for it in items[:50]:
            norm = _norm_title(it.get("title", ""))
            if len(norm) < 3:
                continue
            docs.append({"platform": pid, "item": it, "norm": norm,
                         "tok": set(tokens(norm))})
    if not docs:
        return []

    df = Counter()
    for d in docs:
        for t in d["tok"]:
            df[t] += 1
    n = len(docs)
    for d in docs:
        strong = {t for t in d["tok"]
                  if len(t) >= 3 and t not in GENERIC and t not in GENERIC_EXT
                  and t.lower() not in GENERIC_LATIN
                  and df[t] / n <= 0.5}
        d["strong"] = strong or set()

    parent = list(range(len(docs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            a, b = docs[i], docs[j]
            if a["platform"] == b["platform"] or not a["strong"] or not b["strong"]:
                continue
            shared = a["strong"] & b["strong"]
            if not shared:
                continue
            if any(t in a["norm"] and t in b["norm"] for t in shared):
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[rb] = ra

    groups = defaultdict(list)
    for i in range(len(docs)):
        groups[find(i)].append(i)

    clusters = []
    for _, idxs in groups.items():
        members = [docs[i] for i in idxs]
        plats = {m["platform"] for m in members}
        if len(plats) < 2:
            continue
        heat = sum(max(m["item"].get("heat", 0), 5000) for m in members)
        cnt = Counter()
        for m in members:
            for t in m["strong"]:
                cnt[t] += 1
        if not cnt:
            continue
        # 事件名 token 优选：必须与代表标题同源，保证「标题—关键词」语义一致。
        # 同时排除泛词——「App」「怎么办」这类词既当不了事件名，
        # 也不该作为关键词（会导致检索召回大量无关内容、标题闸门失效）。
        top_title = members[0]["item"].get("title", "")

        def _ok_kw(w):
            return (len(w) >= 3 and w not in GENERIC and w not in GENERIC_EXT
                    and w.lower() not in GENERIC_LATIN)

        cand = [(w, c) for w, c in cnt.items() if _ok_kw(w) and w in top_title]
        if not cand:
            cand = [(w, 1) for w in tokens(top_title) if _ok_kw(w)]
        if not cand:
            cand = [(w, c) for w, c in cnt.items() if _ok_kw(w)]
        cand.sort(key=lambda x: (-x[1], -len(x[0])))
        keyword = cand[0][0] if cand else (top_title[:6] or "热点")
        members.sort(key=lambda m: -max(m["item"].get("heat", 0), 0))
        clusters.append({
            "keyword": keyword,
            "title": members[0]["item"]["title"][:60],
            "platforms": sorted(plats),
            "heat": heat,
            "members": [{"platform": m["platform"], **m["item"]} for m in members[:16]],
        })
    clusters.sort(key=lambda c: (-len(c["platforms"]), -c["heat"]))
    return clusters[:max_events]


# ══ 热评抓取 ════════════════════════════════════════════════════════════════
def bili_related(bv: str, limit: int = 8) -> list[str]:
    """相关视频推荐（用于补充与事件高度相关的热评来源）。"""
    aid = bili_aid(bv)
    if not aid:
        return []
    raw = curl(f"https://api.bilibili.com/x/web-interface/archive/related?aid={aid}",
               timeout=18, headers=[f"Referer: https://www.bilibili.com/video/{bv}"])
    try:
        data = json.loads(raw).get("data") or []
    except Exception:
        return []
    out = []
    for it in data:
        bvid = it.get("bvid")
        if bvid:
            out.append(bvid)
        if len(out) >= limit:
            break
    return out


def event_query_terms(ev) -> list[str]:
    """为一个事件生成检索词变体，提升热评召回。"""
    terms, seen = [], set()

    def add(t):
        t = (t or "").strip()
        if t and len(t) >= 2 and t not in seen:
            seen.add(t)
            terms.append(t)

    add(ev.get("keyword"))
    for m in (ev.get("members") or [])[:6]:
        for t in tokens(m.get("title", ""))[:5]:
            if len(t) >= 3:
                add(t)
    for t in tokens(ev.get("title", ""))[:5]:
        if len(t) >= 3:
            add(t)
    return terms[:8]


_TITLE_NOISE = re.compile(r"[\s\u3000#《》「」“”\"\'，。？！、,:：;；()（）·…—\-]+")


def norm_title(s: str) -> str:
    """归一化标题：去掉标点与空白，只留实义字符，用于字符串比对。"""
    return _TITLE_NOISE.sub("", s or "")


def lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（滚动数组实现）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def source_matches(video_title: str, terms: list[str], keyword: str,
                   triggers: list[str] | None = None, min_lcs: int = 4) -> bool:
    """判断一个 B 站视频标题是否真的在谈该事件。

    判据（自 2026-11 修订）：
      1. 标题直接含事件关键词（如「中俄美」「国台办」「董明珠」）→ 相关；
      2. 否则，标题与该事件**任一触发条目**存在 ≥4 字的公共连续片段 → 相关。

    早期版本用「命中任一检索词即通过」，会把「经济体」「十二年」「外交部」
    这类主题域泛词当成事件特征，召回大量蹭词视频（如罗大佑访谈、民进党科普、
    外交部例行记者会），实测相关率仅 24%–46%。改用触发条目长公共子串后，
    蹭词视频被系统性剔除，而真正谈到该事件的视频（含措辞差异较大的）仍被保留。
    """
    t = norm_title(video_title)
    if not t:
        return False
    if keyword and len(keyword) >= 2 and norm_title(keyword) in t:
        return True
    for tr in (triggers or []):
        trn = norm_title(tr)
        if trn and lcs_len(t, trn) >= min_lcs:
            return True
    # 兜底：命中 ≥2 个长度≥3 的检索词变体
    strong = 0
    for term in terms:
        if len(term) >= 3:
            tn = norm_title(term)
            if tn and (tn in t or tn[:4] in t):
                strong += 1
        if strong >= 2:
            return True
    return False


def measure_event_heat(ev, platform_items) -> dict[str, int]:
    """按当前榜单实测某事件的实时热度（按平台累加命中条目的榜单热度）。

    命中判据与评论相关性闸门一致：标题含事件关键词，或与该事件任一
    触发条目共享 ≥4 字连续片段。事件已跌出所有榜单时不返回任何平台，
    即当小时实测热度为 0——这是真实测量，不是估算。
    """
    kw = (ev.get("keyword") or "").strip()
    triggers = [m.get("title") or "" for m in (ev.get("members") or [])]
    heat: dict[str, int] = defaultdict(int)
    for pid, items in platform_items.items():
        if pid.startswith("_"):
            continue
        for it in items:
            t = (it.get("title") or "").strip()
            if not t or not source_matches(t, [], kw, triggers):
                continue
            try:
                h = int(it.get("heat") or 0)
            except Exception:
                h = 0
            heat[pid] += max(h, 5000)
    return dict(heat)


def fetch_comments(ev, want: int = 1000) -> tuple[list[dict], dict]:
    """真实热评：B 站公开评论接口（热度排序 + 二级回复）为主，小红书评论为辅。

    产能策略（每事件目标 1000 条）：
      1. 多个检索词变体 → B站搜索 API 多页召回视频；
      2. 按 review（评论数）降序扫视频，评论多的先采；
      3. 每视频取 3 条根评论 + 其全部二级回复（单视频约 12–20 条）；
      4. 与历史缓存合并去重——接口被限流时仍能保留既有样本，
         每日增量累积可逐步逼近 1000 条。

    stats 如实说明来源、扫描视频数与是否达标。
    """
    terms = event_query_terms(ev)
    triggers = [m.get("title") or "" for m in (ev.get("members") or [])]
    stats = {"source": "B站公开评论接口（热度排序 + 二级回复）+ 小红书评论",
             "videos_scanned": 0, "videos_skipped": 0, "collected": 0, "want": want,
             "queries": terms, "cached": 0, "fresh": 0,
             "relevanceGate": "仅采集标题与该事件相关的源视频评论",
             "relevanceBasis": "标题含事件关键词，或与事件触发条目共享 ≥4 字连续片段"}
    cache = load_ccache()
    seen, out = set(), []
    time.sleep(2.0)   # 与上一个事件之间留出冷却，避免连续触发 B 站风控
    try:
        bili_cookie_jar()      # 确保带 buvid 的 cookie 已就位
    except Exception:
        pass

    def add(c):
        key = (c.get("text") or "")[:38]
        if not key or key in seen:
            return False
        seen.add(key)
        if "sentiment" not in c:
            c["sentiment"], c["score"] = sentiment_score(c["text"])
        out.append(c)
        return True

    # 1) 历史缓存（只复用带源视频标题的条目——没有标题就无法核验与事件的相关性；
    #    且标题必须重新过闸门，旧判据放行的蹭词条目不再复用）
    for t in terms:
        for c in (cache.get(t, {}) or {}).get("comments", []):
            vt = (c.get("videoTitle") or "").strip()
            if not vt:
                continue
            if not source_matches(vt, terms, ev.get("keyword") or "", triggers):
                stats["cached_rejected"] = stats.get("cached_rejected", 0) + 1
                continue
            if add(c):
                stats["cached"] += 1

    # 2) 本轮新抓（B 站）：多关键词 × 多页搜索，按评论数降序扫
    try:
        vids, seen_bv = [], set()
        for term in terms[:6]:
            if len(out) >= want:
                break
            got = []
            for attempt in range(5):
                got = bili_search_api(term, pages=4)
                # 检索结果异常稀薄或标题普遍为空，通常意味着被限流，退避后重试
                titled = [v for v in got if (v.get("title") or "").strip()]
                if len(titled) >= 8:
                    break
                stats["search_retries"] = stats.get("search_retries", 0) + 1
                stats["throttled"] = True
                time.sleep(5 + attempt * 7)
            for v in got:
                if v["bv"] in seen_bv:
                    continue
                seen_bv.add(v["bv"])
                vids.append(v)
            time.sleep(1.2)
        vids.sort(key=lambda v: -int(v.get("review") or 0))
        for v in vids:
            if len(out) >= want:
                break
            vtitle = v.get("title") or ""
            # 相关性闸门：只从标题真正谈到该事件的视频取评论，
            # 否则关键词检索会召回大量「蹭词」视频，样本混入无关评论。
            if not source_matches(vtitle, terms, ev.get("keyword") or "", triggers):
                stats["videos_skipped"] = stats.get("videos_skipped", 0) + 1
                continue
            got = bili_hot_comments(v["bv"])
            if not got:
                continue
            stats["videos_scanned"] += 1
            for c in got:
                c["videoTitle"] = vtitle
                if add(c):
                    stats["fresh"] += 1
                    if len(out) >= want:
                        break
            time.sleep(0.35)   # 视频级节流
    except Exception as e:
        stats["error"] = str(e)[:140]

    # 3) 小红书（登录态可用时）
    if len(out) < want:
        try:
            for term in terms[:2]:
                d = xhs_json(["xhs", "search", term, "--json"])
                if not d or "_error" in d:
                    continue
                notes = (d.get("data") or {}).get("items") or []
                for n in notes[:6]:
                    nid, tok = n.get("id"), n.get("xsec_token", "")
                    cmd = ["xhs", "comments", nid, "--all", "--json"]
                    if tok:
                        cmd += ["--xsec-token", tok]
                    cd = xhs_json(cmd)
                    if not cd or "_error" in cd:
                        continue
                    for c in ((cd.get("data") or {}).get("items") or []):
                        txt = (c.get("content") or "").strip()
                        if not txt:
                            continue
                        lab, sc = sentiment_score(txt)
                        if add({
                            "text": txt, "likes": parse_heat(str(c.get("like_count", "0"))),
                            "author": (c.get("user_info") or {}).get("nickname", ""),
                            "platform": "xhs", "emoji": "📕", "sentiment": lab,
                            "score": round(sc, 2), "time": c.get("create_time", ""),
                            "ip": c.get("ip_location", ""),
                            "sourceUrl": f"https://www.xiaohongshu.com/explore/{nid}",
                        }):
                            stats["fresh"] += 1
                        if len(out) >= want:
                            break
                    if len(out) >= want:
                        break
        except Exception:
            pass

    # 4) 回写缓存（同样只保留过闸门的条目，避免蹭词样本长期沉淀）
    for t in terms:
        prev = [c for c in ((cache.get(t, {}) or {}).get("comments", []))
                if (c.get("videoTitle") or "").strip()
                and source_matches(c.get("videoTitle") or "", terms,
                                   ev.get("keyword") or "", triggers)]
        merged = {c["text"][:38]: c for c in prev}
        for c in out:
            merged.setdefault(c["text"][:38], c)
        cache[t] = {"comments": sorted(merged.values(), key=lambda c: -c.get("likes", 0))[:1500],
                    "updatedAt": datetime.now(CST).isoformat()}
    save_ccache(cache)

    out.sort(key=lambda c: -c.get("likes", 0))
    stats["collected"] = len(out)
    stats["complete"] = len(out) >= want
    stats["target"] = want
    return out[:want], stats


# ══ 趋势 ════════════════════════════════════════════════════════════════════
HISTORY = os.path.join(DATA, "history.json")


def load_history() -> dict:
    if os.path.exists(HISTORY):
        try:
            with open(HISTORY, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"series": {}}


def diffusion_estimate(peak: float, hours: int = 24, age: int = 4) -> list[float]:
    out = []
    for t in range(hours):
        if t < age:
            v = peak * (0.18 + 0.82 * (t / max(age, 1)) ** 0.6)
        else:
            v = peak * (1 - 0.55 * (1 - math.exp(-(t - age) / 9.0)))
        out.append(round(max(v, 0), 0))
    return out


def build_series(event_id: str, heat_now: int, platforms: dict[str, int]) -> list[dict]:
    hist = load_history()
    series = hist["series"].setdefault(event_id, [])
    stamp = datetime.now(CST).replace(minute=0, second=0, microsecond=0)
    point = {"t": stamp.isoformat(), "label": stamp.strftime("%H:%M"),
             "total": int(heat_now), "platforms": platforms, "type": "measured"}
    if series and series[-1]["t"][:13] == stamp.isoformat()[:13]:
        series[-1] = point
    else:
        series.append(point)
    series = series[-72:]
    hist["series"][event_id] = series
    os.makedirs(DATA, exist_ok=True)
    with open(HISTORY, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    return series


def merge_estimated(series: list[dict], peak: int) -> list[dict]:
    measured = [s for s in series if s.get("type") == "measured"]
    if len(measured) >= 6:
        return series[-24:]
    now = datetime.now(CST).replace(minute=0, second=0, microsecond=0)
    est = diffusion_estimate(peak)
    base = []
    for i, v in enumerate(est):
        t = now - timedelta(hours=len(est) - 1 - i)
        base.append({"t": t.isoformat(), "label": t.strftime("%H:%M"),
                     "total": int(v), "platforms": {}, "type": "estimated"})
    by_hour = {b["t"][:13]: b for b in base}
    for m in measured:
        by_hour[m["t"][:13]] = m
    return sorted(by_hour.values(), key=lambda s: s["t"])[-24:]


# ══ 主流程 ══════════════════════════════════════════════════════════════════
def _prev_last_full_run() -> str:
    """读取上一轮 meta.json 里记录的「最近一次完整采集日期」。"""
    try:
        with open(os.path.join(DATA, "meta.json"), encoding="utf-8") as f:
            return (json.load(f) or {}).get("lastFullRun", "")
    except Exception:
        return ""


def main() -> int:
    requested_hourly = "--hourly" in sys.argv
    want_comments = "--no-comments" not in sys.argv
    n_events = 5          # 每日固定选取 5 个舆情事件
    cmt_target = 1000     # 每事件真实热评目标条数
    if "--events" in sys.argv:
        try:
            n_events = int(sys.argv[sys.argv.index("--events") + 1])
        except Exception:
            pass
    if "--comment-target" in sys.argv:
        try:
            cmt_target = int(sys.argv[sys.argv.index("--comment-target") + 1])
        except Exception:
            pass
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()
    now = datetime.now(CST)
    # 云端工作流每小时统一调用 --hourly。跨午夜后的第一轮必须自动升级为
    # 完整采集，否则会沿用昨天的事件集，页面会出现“日期是今天、内容是昨天”。
    hourly = requested_hourly and _prev_last_full_run() == now.strftime("%Y-%m-%d")
    if requested_hourly and not hourly:
        print("[CATCH] 今天尚未完成完整采集，自动升级为 full")
    print(f"[CATCH] 采集开始 {now:%Y-%m-%d %H:%M:%S} CST{'（hourly 增量）' if hourly else ''}")

    # 预先建立所有平台状态，保证某个平台失败时页面不会把“缺失”误显示成“没有数据”。
    tracked_platforms = [k for k in PLATFORMS if k != "bili"]
    platform_items: dict[str, list[dict]] = {k: [] for k in tracked_platforms}
    status: dict[str, dict] = {
        k: {"ok": False, "items": 0, "error": "本轮未完成采集",
            "name": PLATFORMS[k]["name"], "emoji": PLATFORMS[k]["emoji"]}
        for k in tracked_platforms
    }

    def safe(key, fn):
        try:
            res = fn()
        except Exception as e:
            status[key] = {"ok": False, "error": str(e)[:160], "items": 0}
            platform_items[key] = []
            print(f"  ✗ {key:7s} {str(e)[:70]}")
            return None
        print(f"  ✓ {key:7s} ok")
        return res

    r = safe("weibo", lambda: collect_boards("weibo"))
    if r:
        platform_items["weibo"], status["weibo"] = r
    r = safe("douyin", collect_douyin)
    if r:
        platform_items["douyin"], status["douyin"] = r[0], r[1]
    r = safe("xhs", collect_xhs)
    if r:
        platform_items["xhs"], status["xhs"] = r
    r = safe("x", collect_x)
    if r:
        platform_items["x"], status["x"] = r
    r = safe("zhihu", lambda: collect_boards("zhihu"))
    if r:
        platform_items["zhihu"], status["zhihu"] = r
    r = safe("news", lambda: collect_boards("news"))
    if r:
        platform_items["news"], status["news"] = r

    for k in platform_items:
        if k.startswith("_"):
            continue
        if not isinstance(status.get(k), dict):
            status[k] = {"ok": False, "items": 0}
        status[k].setdefault("name", PLATFORMS.get(k, {}).get("name", k))
        status[k].setdefault("emoji", PLATFORMS.get(k, {}).get("emoji", "•"))
        status[k]["items"] = len(platform_items.get(k, []))

    # ── 上一轮事件档案 ──
    # 小时级增量必须沿用当日 09:00 选定的 5 个事件，不能重新聚类：
    # 榜单会随时间轮换（9am 的第 2–5 号事件到下午可能已跌出榜单，
    # 同时冒出新话题），一旦重新聚类，事件身份就会漂移，当日的热评样本、
    # 分析层与词云会被整体冲掉——这正是 15:02 那次小时任务把
    # 「首同框 / 演唱会 / 女演员」换成「董明珠 / GTA6 / 外地人 / 民政局」的原因。
    prev = {}
    ev_path = os.path.join(DATA, "events.json")
    if os.path.exists(ev_path):
        try:
            with open(ev_path, encoding="utf-8") as f:
                prev = {e["id"]: e for e in json.load(f).get("events", [])}
        except Exception:
            prev = {}

    # ── 事件聚类 ──
    if hourly:
        events_src = [
            {"keyword": e.get("keyword", ""), "title": e.get("title", ""),
             "platforms": e.get("platforms", []),
             "members": e.get("members", []),
             "heat": e.get("heat", 0)}
            for _, e in sorted(prev.items())
        ]
        print(f"[CATCH] 沿用当日 {len(events_src)} 个事件（小时级增量不重新聚类）")
    else:
        events_src = cluster_events({k: v for k, v in platform_items.items()
                                     if not k.startswith("_")}, max_events=n_events)
        print(f"[CATCH] 跨平台共振聚类 → {len(events_src)} 个事件")
    if not events_src:
        events_src = [{
            "keyword": "全网热榜", "title": "全网热榜焦点", "platforms": ["weibo"],
            "heat": sum(len(v) for k, v in platform_items.items() if not k.startswith("_")),
            "members": [{"platform": p, **it} for p, items in platform_items.items()
                         if not p.startswith("_") for it in items[:6]],
        }]

    # 预取评论：B 站接口有风控，预算有限——让「已缓存样本最少」的事件先抓，
    # 否则排在后面的事件往往被限流成 0 条。展示顺序仍按热度，不受此影响。
    prefetch: dict[int, tuple] = {}
    if want_comments and not hourly:
        ccache = load_ccache()

        def _cached_n(ev) -> int:
            n = 0
            for t in event_query_terms(ev):
                n += len((ccache.get(t, {}) or {}).get("comments", []))
            return n

        for i in sorted(range(len(events_src)), key=lambda k: _cached_n(events_src[k])):
            try:
                prefetch[i] = fetch_comments(events_src[i], cmt_target)
            except Exception as e:
                prefetch[i] = ([], {"collected": 0, "error": str(e)[:120]})

    events = []
    for idx, ev in enumerate(events_src):
        eid = f"E{idx + 1:02d}"
        old = prev.get(eid, {})
        # 事件关键词变化时，旧的分析文本与标题不再适用（避免张冠李戴）
        if old and old.get("keyword") and old.get("keyword") != ev.get("keyword"):
            old = {}
        # 旧标题若已不属于当前簇，也必须丢弃
        if old.get("title"):
            titles = [m.get("title", "") for m in ev["members"]]
            kw = ev.get("keyword", "")
            hit = any((old["title"][:10] and old["title"][:10] in t) for t in titles)
            if not hit:
                old = dict(old)
                old.pop("title", None)
                old.pop("subtitle", None)
        platforms_heat = defaultdict(int)
        if hourly:
            # 小时级增量：按当前榜单实测该事件的实时热度（跌出榜单即 0），
            # 事件身份与触发条目仍沿用当日 09:00 的版本。
            platforms_heat = measure_event_heat(ev, platform_items)
            total = sum(platforms_heat.values())
        else:
            for m in ev["members"]:
                platforms_heat[m["platform"]] += max(m.get("heat", 0), 5000)
            total = sum(platforms_heat.values()) or int(ev.get("heat", 0))

        comments, cstats = [], {"collected": 0}
        if want_comments and not hourly:
            if idx in prefetch:
                comments, cstats = prefetch[idx]
            else:
                try:
                    comments, cstats = fetch_comments(ev, cmt_target)
                except Exception as e:
                    cstats = {"collected": 0, "error": str(e)[:120]}
        elif old.get("comments"):
            # 增量轮次或跳过抓取时，复用上一轮样本，避免丢失
            comments = old["comments"]
            cstats = {"collected": len(comments), "reused": True,
                      "target": cmt_target,
                      "complete": len(comments) >= cmt_target}

        counts = Counter(c["sentiment"] for c in comments)
        n = max(len(comments), 1)
        anger_hits = sum(1 for c in comments if any(w in c["text"] for w in ANGER_WORDS))
        sentiment = {
            "positive": round(counts["积极"] / n * 100, 1),
            "neutral": round(counts["中性"] / n * 100, 1),
            "negative": round(counts["消极"] / n * 100, 1),
            "anger": round(anger_hits / n * 100, 1),
            "samples": len(comments),
            "keywords": [],
        }
        kwc = Counter()
        for c in comments:
            for w in ANGER_WORDS + NEG_WORDS + POS_WORDS:
                if w in c["text"]:
                    kwc[w] += 1
        sentiment["keywords"] = [{"word": w, "count": cnt}
                                 for w, cnt in kwc.most_common(14)]

        wc_text = " ".join([m["title"] for m in ev["members"]] + [c["text"] for c in comments])
        wc_items = []
        try:
            import jieba.analyse
            for w, weight in jieba.analyse.extract_tags(wc_text, topK=70):
                if len(w) < 2 or w.lower() in STOP:
                    continue
                lab, _ = sentiment_score(w)
                wc_items.append({"word": w, "weight": round(float(weight), 3),
                                 "sentiment": lab})
        except Exception:
            for w, cnt in Counter(tokens(wc_text)).most_common(60):
                lab, _ = sentiment_score(w)
                wc_items.append({"word": w, "weight": float(cnt), "sentiment": lab})
        wc_items = sorted(wc_items, key=lambda x: -x["weight"])[:48]

        series = merge_estimated(build_series(eid, total, dict(platforms_heat)), total)

        risk_score = min(100, int(sentiment["negative"] * 1.6 + sentiment["anger"] * 2.2 + 18))
        risk_level = "高" if risk_score >= 55 else ("中" if risk_score >= 32 else "低")

        events.append({
            "id": eid,
            "keyword": ev["keyword"],
            "title": old.get("title") or ev["title"],
            "subtitle": old.get("subtitle", ""),
            "platforms": ev["platforms"],
            "platformHeat": {k: int(v) for k, v in platforms_heat.items()},
            "heat": total,
            "members": ev["members"][:10],
            "comments": comments,
            "commentStats": cstats,
            "sentiment": sentiment,
            "wordcloud": wc_items,
            "wordcloudBasis": ("真实热评 + 榜单标题" if comments
                               else "仅榜单标题（本轮无相关热评）"),
            "trend": {"series": series,
                      "updatedAt": datetime.now(CST).isoformat(),
                      "measuredPoints": len([s for s in series if s.get("type") == "measured"])},
            "risk": {"score": risk_score, "level": risk_level},
            "summary": old.get("summary", ""),
            "facts": old.get("facts", []),
            "phases": old.get("phases", []),
            "analysis": old.get("analysis", {}),
            "timeline": old.get("timeline", []),
            "sourceCount": len(ev["members"]),
            "resonance": len(ev["platforms"]),
        })

    # B站只作为事件热评来源，不进入热榜排序；仍单独展示真实采集状态。
    bili_comments = sum(
        1 for e in events for c in (e.get("comments") or [])
        if c.get("platform") == "bili"
    )
    status["bili"] = {
        "source": "B站公开搜索与评论接口",
        "items": bili_comments,
        "ok": bool(bili_comments),
        "error": None if bili_comments else "本轮没有取得可验证的B站评论样本",
        "name": PLATFORMS["bili"]["name"],
        "emoji": PLATFORMS["bili"]["emoji"],
        "note": "B站评论按事件相关性闸门抽样，不等同于全站舆情全量",
    }

    # ── 热点：文字 ──
    pool = []
    for pid, items in platform_items.items():
        if pid.startswith("_"):
            continue
        for it in items:
            pool.append({"platform": pid, **it})
    pool.sort(key=lambda x: -max(x.get("heat", 0), 0))

    def to_entry(it):
        t = it.get("title") or ""
        return {
            "title": t, "platform": it["platform"],
            "platformName": PLATFORMS.get(it["platform"], {}).get("name", it["platform"]),
            "emoji": PLATFORMS.get(it["platform"], {}).get("emoji", "•"),
            "heat": it.get("heat", 0), "rank": it.get("rank"),
            "url": it.get("url", ""), "board": it.get("board", ""),
            "tokens": tokens(t)[:8],
        }

    # 总榜：跨平台统一排序。
    # 注意：各平台热度口径不可比（微博为搜索热度、知乎为讨论热度、抖音为点赞），
    # 因此综合排序以「各平台原榜序位」为主，热度值仅作展示，不用于跨平台比较。
    ranking_all = [to_entry(it) for it in pool if len(it.get("title") or "") >= 2]
    ranking_all.sort(key=lambda x: (x.get("rank") if x.get("rank") else 999,
                                    -max(x.get("heat", 0), 0)))
    ranking = ranking_all[:60]

    # 热点榜：平台轮转，避免被单一平台霸屏
    by_platform = defaultdict(list)
    for it in pool:
        if len(it.get("title") or "") >= 2:
            by_platform[it["platform"]].append(to_entry(it))
    order = sorted(by_platform.keys(),
                   key=lambda k: -(by_platform[k][0]["heat"] if by_platform[k] else 0))
    text_hot, idx = [], {k: 0 for k in order}
    while len(text_hot) < 60:
        progressed = False
        for k in order:
            lst = by_platform[k]
            if idx[k] < len(lst):
                text_hot.append(lst[idx[k]])
                idx[k] += 1
                progressed = True
                if len(text_hot) >= 60:
                    break
        if not progressed:
            break

    token_platforms = defaultdict(set)
    token_count = Counter()
    for it in text_hot:
        for t in set(it["tokens"]):
            token_platforms[t].add(it["platform"])
            token_count[t] += 1
    resonance = [{"token": t, "platforms": sorted(ps), "count": token_count[t]}
                 for t, ps in token_platforms.items()
                 if len(ps) >= 2 and token_count[t] >= 2 and len(t) >= 3]
    resonance.sort(key=lambda x: (-len(x["platforms"]), -x["count"]))

    # ── 网络流行语（独立板块，不参与事件聚类） ──
    # 近 7 天公开热榜逐日归档 → 自行归纳被提到最多的流行语，不引用任何年度榜单。
    # 小时级增量只刷新走势快照；流行语的统计窗口按天滚动，沿用上一次完整采集的结果。
    slang_rows, slang_status = [], {"source": "近 7 天热榜逐日归档",
                                    "items": 0, "ok": False}
    if hourly:
        try:
            with open(os.path.join(DATA, "hotspots.json"), encoding="utf-8") as fh:
                slang_rows = (json.load(fh) or {}).get("slang") or []
            with open(os.path.join(DATA, "meta.json"), encoding="utf-8") as fh:
                slang_status = (json.load(fh) or {}).get("slangStatus") or slang_status
            print(f"  ✓ slang   沿用 {len(slang_rows)} 个流行语（小时级增量不重算）")
        except Exception as e:
            slang_status["error"] = str(e)[:140]
    else:
        try:
            slang_rows, slang_status = collect_slang_7d(limit=5)
            print(f"  ✓ slang   {len(slang_rows)} 个流行语 · "
                  f"语料 {slang_status.get('corpus', 0)} 条 / {slang_status.get('window', '')}")
        except Exception as e:
            slang_status["error"] = str(e)[:140]
            print(f"  ✗ slang   {str(e)[:60]}")


    hotspots = {"text": text_hot, "ranking": ranking,
                "resonance": resonance[:24],
                "slang": slang_rows}

    prev_meta = {}
    try:
        with open(os.path.join(DATA, "meta.json"), encoding="utf-8") as f:
            prev_meta = json.load(f) or {}
    except Exception:
        pass
    today = now.strftime("%Y-%m-%d")
    try:
        previous_no = int(str(prev_meta.get("issueNo", "0")).lstrip("0") or "0")
    except ValueError:
        previous_no = 0
    if prev_meta.get("issueDate") == today:
        issue_no = previous_no or 1
    elif prev_meta.get("issueDate") or prev_meta.get("dateLabel"):
        issue_no = previous_no + 1
    else:
        issue_no = 1

    failed_platforms = [
        k for k, v in status.items()
        if (not v.get("ok") or v.get("needs_login")) and k != "bili"
    ]
    degraded_platforms = [
        k for k, v in status.items()
        if v.get("needs_login") or v.get("note")
    ]
    meta = {
        "generatedAt": datetime.now(CST).isoformat(),
        "issueDate": today,
        "issueNo": f"{issue_no:03d}",
        "dateLabel": now.strftime("%Y 年 %m 月 %d 日"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()],
        "mode": "hourly" if hourly else "full",
        # 最近一次**完整采集**的日期（小时增量不覆盖它）。
        # run_refresh.py 用它判断「今天是否已经做过完整采集」——
        # 若只看 dateLabel，小时增量盖上的当天标签会把这个判断骗过去。
        "lastFullRun": (now.strftime("%Y-%m-%d") if not hourly
                        else _prev_last_full_run()),
        "durationSec": round(time.time() - t0, 1),
        "platformStatus": status,
        "partial": bool(failed_platforms),
        "failedPlatforms": failed_platforms,
        "degradedPlatforms": degraded_platforms,
        "freshness": {
            "generatedAt": datetime.now(CST).isoformat(),
            "maxExpectedAgeMinutes": 90,
            "note": "页面展示最近一次成功发布的云端快照；单个平台失败会明确标记",
        },
        "slangStatus": slang_status,
        "commentTarget": cmt_target,
        "eventsPerDay": n_events,
        "counts": {
            "hotItems": len(text_hot),
            "events": len(events),
            "comments": sum(len(e["comments"]) for e in events),
            "rawScanned": sum(len(v) for k, v in platform_items.items() if not k.startswith("_")),
            "slang": len(slang_rows),
        },
        "source": "微博 / 抖音 / 小红书 / X / 知乎 / 新闻 / B站 公开榜单与真实接口",
    }

    with open(os.path.join(DATA, "hotspots.json"), "w", encoding="utf-8") as f:
        json.dump(hotspots, f, ensure_ascii=False, indent=1)
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump({"events": events}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"[CATCH] 完成：热点 {len(text_hot)} · 事件 {len(events)} · "
          f"热评 {meta['counts']['comments']} 条（{meta['durationSec']}s）")
    for e in events:
        print(f"   {e['id']} {e['keyword']:<14s} 平台{len(e['platforms'])} "
              f"热度{e['heat']:>9d} 热评{len(e['comments']):>3d} 风险{e['risk']['level']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
