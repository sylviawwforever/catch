#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CATCH · 站点数据装配器
=====================
把 assets/data/ 下的原始采集结果装配为前端可直接加载的 assets/js/data.js。

  assets/data/hotspots.json  → 热点榜 / 视频模板
  assets/data/events.json    → 舆情事件（原始）
  assets/data/analysis.json  → 分析师撰写层（事件概括、研判、选题角度）
  assets/data/meta.json      → 采集元信息

分析层按「事件 id + 关键词」匹配，采集轮次变化导致事件顺序变化时仍能保留撰写内容。

用法：python3 tools/build_site.py
"""

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import collect as C          # 复用分词 / 情绪词典，保证与采集层口径一致
except Exception:                # pragma: no cover
    C = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "data")
OUT = os.path.join(ROOT, "assets", "js", "data.js")
CST = timezone(timedelta(hours=8))


def read(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ! 读取 {name} 失败：{e}")
        return default


# ── 热评相关性测算 ────────────────────────────────────────────────────────────
# 问题：B 站评论按「事件关键词检索到的视频」取样，视频本身未必与事件相关，
# 因此样本中会混入跑题评论（实测部分事件跑题比例过半）。
# 办法：用该事件各条热榜条目「共有的特征词」作为相关性判据——
# 一条评论只要命中该事件独有的特征词（如「空军一号」「中俄美」），即视为相关；
# 词云与情绪分布改用相关评论重算，页面同时如实标注相关比例。
CJK = re.compile(r"^[\u4e00-\u9fff]{2,6}$")


def _flat(text: str) -> str:
    """去掉空白与书名号，便于两段文本做包含判断。"""
    return re.sub(r"[\s\u3000《》「」\"']+", "", text or "")


def _overlay_match_terms(a: dict) -> list[str]:
    """分析层的实义匹配片段：优先取显式 matchTerms，否则由「触发条目」要素推导。

    推导方式：从要素表「触发条目」里取出被引号包裹的榜单原标题片段——
    这些片段正是该分析层所依据的原始条目，只有本轮事件也包含它们时才算同一事件。
    """
    if a.get("matchTerms"):
        return [t for t in a["matchTerms"] if t]
    out = []
    for f in (a.get("facts") or []):
        if "触发条目" in (f.get("label") or ""):
            out += re.findall(r"[「『]([^」』]{6,})[」『]", f.get("value") or "")
    return out


def _content_tokens(text: str) -> set:
    """抽取实义词（分词后长度 ≥2 且非停用词），用于分析层与事件的实义重合校验。"""
    out = set()
    if not text:
        return out
    if C:
        try:
            for w in C.tokens(text):
                if len(w) >= 2:
                    out.add(w)
        except Exception:
            pass
    if not out:
        out = {w for w in re.findall(r"[\u4e00-\u9fff]{2,6}", text)}
    return out


_TITLE_NOISE = re.compile(r"[\s\u3000#《》「」“”\"\'，。？！、,:：;；()（）·…—\-]+")


def norm_title(s: str) -> str:
    """归一化标题：去掉标点与空白，只留实义字符。"""
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


def title_is_relevant(title: str, triggers: list[str], keyword: str,
                      min_lcs: int = 4) -> bool:
    """源视频标题是否真的在谈该事件（与采集端 source_matches 同一判据）。

    1) 标题直接含事件关键词 → 相关；
    2) 否则与该事件任一触发条目共享 ≥4 字连续片段 → 相关。

    早期判据是「命中 ≥1 个跨条目特征词」，会把「经济体」「十二年」这类
    主题域泛词当成事件特征，使罗大佑访谈、民进党科普、外交部例行记者会
    等蹭词视频被判为相关（实测相关率仅 24%–46%）。改用长公共子串后，
    蹭词视频被系统性剔除，真正谈到该事件的视频仍被保留。
    """
    t = norm_title(title)
    if not t:
        return False
    if keyword and len(keyword) >= 2 and norm_title(keyword) in t:
        return True
    for tr in triggers:
        trn = norm_title(tr)
        if trn and lcs_len(t, trn) >= min_lcs:
            return True
    return False


def event_tokens(ev: dict) -> list[str]:
    """从事件各热榜条目中提取「共有特征词」——出现在 ≥2 条目中的 2–6 字片段。

    只用单条目的词会把泛词混进来（如「现场」「最新」）；要求跨条目复现，
    得到的才是这个事件独有的表述。
    """
    cnt = Counter()
    for m in ev.get("members", []) or []:
        title = re.sub(r"[\s\u3000#《》「」“”\"\']+", "", m.get("title") or "")
        for n in (6, 5, 4, 3, 2):
            for i in range(len(title) - n + 1):
                seg = title[i:i + n]
                if not CJK.match(seg):
                    continue
                if C and seg.lower() in C.STOP:
                    continue
                cnt[seg] += 1
    # 跨条目复现，且不是更长片段的子串（保留最完整形式）
    base = {s for s, c in cnt.items() if c >= 2}
    keep = []
    for s in sorted(base, key=lambda x: (-len(x), -cnt[x])):
        if any(s != k and s in k for k in keep):
            continue
        keep.append(s)
    return keep


def event_aliases(ev: dict) -> list[str]:
    """事件实体的别称（关键词本身 + 跨条目复现的特征词），用于源视频标题判定。"""
    out = []
    kw = (ev.get("keyword") or "").strip()
    if kw:
        out.append(kw)
    for t in event_tokens(ev):
        if len(t) >= 3:
            out.append(t)
    return out


def measure_relevance(ev: dict) -> dict:
    """标注每条评论与事件的相关性，并给出可复核的统计。

    首选判据是「评论所属源视频的标题是否谈到该事件」（采集层已带 videoTitle），
    与采集端 source_matches 用同一把尺子（关键词命中 或 与触发条目共享 ≥4 字连续片段）；
    老样本没有 videoTitle 时回落到「评论正文是否命中事件特征词」。
    两种判据各自可核，页面上如实标注用的是哪一种。
    """
    toks = event_tokens(ev)
    aliases = event_aliases(ev)
    kw = (ev.get("keyword") or "").strip()
    triggers = [m.get("title") or "" for m in (ev.get("members") or [])]
    comments = ev.get("comments") or []
    if not comments:
        return {"tokens": toks[:12], "total": 0, "relevant": 0, "ratio": 0.0,
                "basis": "无样本", "withVideoTitle": 0}
    hits = 0
    with_title = 0
    for c in comments:
        vt = (c.get("videoTitle") or "").strip()
        if vt:
            with_title += 1
            rel = title_is_relevant(vt, triggers, kw)
            if not rel:
                # 兜底：命中 ≥2 个跨条目特征词
                rel = (sum(1 for t in toks if t and t in vt) >= 2
                       or sum(1 for a in aliases if a and a in vt) >= 2)
        else:
            text = c.get("text") or ""
            rel = any(t in text for t in toks)
        c["relevant"] = rel
        if rel:
            hits += 1
    return {"tokens": toks[:12], "total": len(comments), "relevant": hits,
            "ratio": round(hits / len(comments), 4) if comments else 0.0,
            "basis": "源视频标题" if with_title >= len(comments) * 0.5 else
                     ("评论正文特征词" if with_title == 0 else "混合"),
            "withVideoTitle": with_title}


def mark_recency(comments: list[dict], cutoff: float) -> int:
    """标注每条评论是否发布于时效窗口内（采集日 - 30 天）。

    本系统是每日舆情监测：B 站检索会召回跨年份的旧视频，其评论不代表当期舆情。
    不删除任何评论，只做标记，供页面筛选、CSV 导出与词云/情绪重算使用。
    """
    n = 0
    for c in comments:
        ts = c.get("time") or ""
        try:
            t = datetime.fromisoformat(str(ts).replace(" ", "T")).timestamp()
        except Exception:
            t = 0.0
        c["recent"] = bool(t >= cutoff) if t else False
        if c["recent"]:
            n += 1
    return n


def rebuild_from_relevant(ev: dict) -> None:
    """用「相关且近期」的评论重算词云与情绪分布；样本不足时逐级回落并如实标注。

    回退顺序：相关且近期 → 相关 → 原值。
    """
    comments = ev.get("comments") or []
    rel = [c for c in comments if c.get("relevant")]
    if len(rel) < 30:
        return
    use = [c for c in rel if c.get("recent")]
    basis = "相关且近 30 天"
    if len(use) < 30:
        use = rel
        basis = "相关评论"
    sent = ev.get("sentiment") or {}
    pos = neu = neg = ang = 0
    kwc = Counter()
    for c in use:
        lab, _ = (C.sentiment_score(c.get("text") or "") if C else ("中性", 0.0))
        if lab == "积极":
            pos += 1
        elif lab == "消极":
            neg += 1
        elif lab == "愤怒":
            ang += 1
        else:
            neu += 1
        for w in ((C.ANGER_WORDS + C.NEG_WORDS + C.POS_WORDS) if C else []):
            if w in (c.get("text") or ""):
                kwc[w] += 1
    n = len(use)
    sent = dict(sent)
    sent.update({
        "positive": round(pos / n * 100, 1),
        "neutral": round(neu / n * 100, 1),
        "negative": round(neg / n * 100, 1),
        "anger": round(ang / n * 100, 1),
        "samples": n,
        "basis": basis,
    })
    if kwc:
        sent["keywords"] = [{"word": w, "count": c} for w, c in kwc.most_common(14)]
    ev["sentiment"] = sent

    wc_text = " ".join([m.get("title", "") for m in (ev.get("members") or [])]
                       + [c.get("text", "") for c in use])
    wc_items = []
    try:
        import jieba.analyse
        for w, weight in jieba.analyse.extract_tags(wc_text, topK=70):
            if len(w) < 2 or (C and w.lower() in C.STOP):
                continue
            lab, _ = (C.sentiment_score(w) if C else ("中性", 0.0))
            wc_items.append({"word": w, "weight": round(float(weight), 3), "sentiment": lab})
    except Exception:
        for w, cnt in Counter(C.tokens(wc_text) if C else []).most_common(60):
            lab, _ = (C.sentiment_score(w) if C else ("中性", 0.0))
            wc_items.append({"word": w, "weight": float(cnt), "sentiment": lab})
    wc_items = sorted(wc_items, key=lambda x: -x["weight"])[:48]
    if wc_items:
        ev["wordcloud"] = wc_items


def auto_overlay(ev: dict, rel: dict, rec: dict) -> dict:
    """为「还没有人工研判」的事件生成一层**只含事实、不含推断**的兜底档案。

    触发场景：新一轮采集出现了新事件，而分析层（analysis.json）还没来得及
    为它撰写研判。若此时把事件留空，页面会开天窗；若硬套旧事件的文字，
    就是张冠李戴。这里的做法是——要素表全部来自本轮真实数据（平台、热度、
    触发条目、情绪分布、样本量与相关率），并明确标注「研判待撰写」，
    传播阶段、时间线与选题角度一律留空，不做任何推断。
    """
    members = ev.get("members") or []
    plats = ev.get("platforms") or []
    heat = ev.get("heat") or 0
    sent = ev.get("sentiment") or {}
    trig = "；".join(f"「{m.get('title', '')}」" for m in members[:4])
    heat_parts = " + ".join(f"{k} {v}" for k, v in (ev.get("platformHeat") or {}).items())
    top = members[0].get("title", "") if members else (ev.get("keyword") or "")
    return {
        "title": f"{top}：{len(plats)} 平台共振的当轮热点",
        "subtitle": ("本事件的事实要素由本轮采集数据自动生成；传播阶段、"
                     "时间线与研判要点尚待撰写，未做任何推断。"),
        "summary": (
            f"本事件由 {len(plats)} 个平台共振（{' / '.join(plats)}），综合热度约 {heat:,}，"
            f"各平台分项为 {heat_parts or '未拆分'}。本轮触发条目共 {len(members)} 条，"
            f"热度居前的是{('「' + members[0].get('title', '') + '」') if members else '（无）'}"
            f"等。系统抓取的真实热评样本 {rel.get('total', 0)} 条，其中 {rel.get('relevant', 0)} 条"
            f"与本事件相关，近 30 天内发布的有 {rec.get('recent', 0)} 条；情绪分布为"
            f"积极 {sent.get('positive', 0)}%、中性 {sent.get('neutral', 0)}%、"
            f"消极 {sent.get('negative', 0)}%、愤怒 {sent.get('anger', 0)}%，"
            f"风险等级 {((ev.get('risk') or {}).get('level'))}。"
            "本段仅陈述本轮可核验的数据事实，不含对传播机制的解释；"
            "完整的传播阶段梳理与研判要点将在分析层撰写后补入。"),
        "facts": [
            {"label": "事件类型", "value": f"跨平台共振话题（{len(plats)} 平台）"},
            {"label": "共振平台", "value": " / ".join(plats)},
            {"label": "综合热度", "value": f"约 {heat:,}（{heat_parts or '未拆分'}）"},
            {"label": "触发条目", "value": trig or "（本轮无触发条目）"},
            {"label": "情绪基调",
             "value": f"积极 {sent.get('positive', 0)}% / 中性 {sent.get('neutral', 0)}% / "
                      f"消极 {sent.get('negative', 0)}% / 愤怒 {sent.get('anger', 0)}%"},
            {"label": "热评样本与相关性",
             "value": f"{rel.get('total', 0)} 条真实热评中 {rel.get('relevant', 0)} 条相关"
                      f"（相关率 {round((rel.get('ratio') or 0) * 100, 1)}%）；"
                      f"近 30 天样本 {rec.get('recent', 0)} 条"},
            {"label": "风险等级", "value": f"{(ev.get('risk') or {}).get('level', '—')}"
                                      f"（风险指数 {(ev.get('risk') or {}).get('score', '—')}）"},
            {"label": "研判状态", "value": "要素由本轮采集数据自动生成；"
                                          "传播阶段、时间线与研判要点待撰写（未作推断）"},
        ],
        "phases": [],
        "timeline": [],
        "analysis": {"points": ["本事件尚未撰写研判要点。页面现有内容全部来自本轮采集的"
                                "可核验数据（榜单条目、热度、情绪分布、热评样本与相关率），"
                                "不含任何对传播机制的推断。"],
                     "angles": [], "methods": []},
    }


def main() -> int:
    hotspots = read("hotspots.json", {"text": [], "ranking": [], "resonance": [], "slang": []})
    events_doc = read("events.json", {"events": []})
    meta = read("meta.json", {})
    analysis = read("analysis.json", {})

    overlay = analysis.get("events", {}) if isinstance(analysis, dict) else {}
    events = []
    unmatched = []
    for ev in events_doc.get("events", []):
        # 候选分析层：先按关键词匹配（语义稳定），其次回退到 id（顺序可能漂移），
        # 其余条目作为备选——当首选条目未通过实义校验时继续尝试，
        # 避免「同一热词、本轮指向另一议题」时错过真正对得上的分析层。
        cands = []
        kw0 = (ev.get("keyword") or "").strip()
        if kw0:
            for k2, v2 in overlay.items():
                if isinstance(v2, dict) and v2.get("keyword") == kw0:
                    cands.append((k2, v2))
                    break
        if ev.get("id") in overlay and isinstance(overlay.get(ev.get("id")), dict):
            cands.append((ev.get("id"), overlay[ev["id"]]))
        for k2, v2 in overlay.items():
            if isinstance(v2, dict) and all(v2 is not c[1] for c in cands):
                cands.append((k2, v2))

        a = {}
        # 兜底校验（关键词匹配同样要过）：同一个热词在不同轮次可能指向完全不同的议题
        # （如「GTA6」先是指 PC 版发售传闻、后指 Metacritic 开分）。
        # 因此要求分析层标题与本轮事件除关键词外还共享至少一个实义特征串，
        # 否则宁可留空，也不能把别的事件的文字安到当前事件上（张冠李戴）。
        for cand_key, cand in cands:
            if not cand:
                continue
            if not (ev.get("title") and cand.get("title")):
                a = cand
                break
            kw = kw0
            at = cand.get("title") or ""
            ev_text = _flat(" ".join([ev.get("title") or ""] +
                                     [m.get("title", "") for m in (ev.get("members") or [])]))
            # 「实义重合」校验：同一个热词可能在一周内指向完全不同的议题
            # （如「GTA6」先指 PC 版发售传闻、后指 Metacritic 开分）。
            # 主判据：分析层自带的 matchTerms（或由其「触发条目」要素自动推导的片段）
            # 必须出现在本轮事件的标题或榜单条目里。
            terms = _overlay_match_terms(cand)
            if terms:
                hit = any(_flat(t) and _flat(t) in ev_text for t in terms)
            else:
                hit = bool(kw) and (kw in at or at[:6] in (ev.get("title") or ""))
                if not hit and kw:
                    hit = any(ch in at for ch in kw[:3])
                if hit:
                    shared = False
                    for tk in _content_tokens(at):
                        if kw and (tk == kw or kw in tk or tk in kw):
                            continue
                        if tk in _content_tokens(ev_text):
                            shared = True
                            break
                    hit = shared
            if hit:
                a = cand
                break
        if a:
            ev = dict(ev)
            for f in ("title", "subtitle", "summary", "facts", "phases",
                      "analysis", "timeline"):
                if a.get(f):
                    ev[f] = a[f]
        else:
            # 所有候选都未通过实义校验 → 本事件不套用任何分析层（留空，避免张冠李戴）
            unmatched.append((ev.get("id"), ev.get("title", "")[:30]))
        events.append(ev)
    if unmatched:
        print("  ! 分析层未匹配（已留空，避免张冠李戴）：")
        for u in unmatched:
            print(f"      {u[0]} 事件「{u[1]}」无可用分析层")

    # 完整热评样本体积很大（每事件 1000 条），从主包中剥离为按需加载的独立文件，
    # 页面首屏只带轻量样本，切换事件时再拉取完整数据。
    comments_dir = os.path.join(DATA, "comments")
    os.makedirs(comments_dir, exist_ok=True)
    lite_events = []
    auto_layered = []
    for ev in events:
        # 热评相关性：标注并据此重算词云 / 情绪
        rel = measure_relevance(ev)
        ev["commentRelevance"] = rel
        # 时效标注：标出发布于「采集日 - 30 天」内的评论（不删除，仅标记）
        cs_all = ev.get("comments") or []
        cutoff = (datetime.now(CST) - timedelta(days=30)).timestamp()
        n_recent = mark_recency(cs_all, cutoff)
        ev["commentRecency"] = {
            "windowDays": 30,
            "total": len(cs_all),
            "recent": n_recent,
            "ratio": round(n_recent / len(cs_all), 4) if cs_all else 0.0,
        }
        rebuild_from_relevant(ev)
        cs = ev.get("comments") or []
        ev = dict(ev)
        ev["commentCount"] = len(cs)
        # 没有人工研判的事件：补一层只含事实的自动档案，避免页面开天窗
        if not ev.get("summary"):
            ev.update(auto_overlay(ev, rel, ev.get("commentRecency") or {}))
            auto_layered.append(ev.get("id"))
        ev["comments"] = cs[:40]          # 首屏内置 40 条样本
        ev["commentsFile"] = f"assets/data/comments/{ev['id']}.json"
        fname = os.path.join(comments_dir, f"{ev['id']}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump({"id": ev["id"], "keyword": ev.get("keyword", ""),
                       "total": len(cs), "comments": cs},
                      f, ensure_ascii=False, indent=1)
        lite_events.append(ev)

    payload = {
        "meta": meta,
        "hotspots": hotspots,
        "events": lite_events,
        "builtAt": datetime.now(CST).isoformat(),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("/* CATCH · 自动生成，请勿手工修改 —— 由 tools/build_site.py 装配 */\n")
        f.write("window.CATCH_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(";\n")

    size = os.path.getsize(OUT)
    print(f"[CATCH] 已装配 {OUT}（{size / 1024:.1f} KB）")
    print(f"        热点 {len(hotspots.get('text', []))} · "
          f"事件 {len(events)} · 热评 {sum(len(e.get('comments', [])) for e in events)}")
    print(f"        完整热评已拆分至 assets/data/comments/（每事件一个 JSON）")
    if auto_layered:
        print(f"        ! {len(auto_layered)} 个事件尚无人工研判（{', '.join(auto_layered)}）："
              f"已自动生成只含事实的要素表，传播阶段与研判要点待撰写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
