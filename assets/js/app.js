/* ==========================================================================
   CATCH · 前端渲染引擎
   数据源：window.CATCH_DATA（由 tools/build_site.py 生成）
   零外部依赖：图表全部手写 SVG / Canvas
   ========================================================================== */
(function () {
  "use strict";

  const D = window.CATCH_DATA || { meta: {}, hotspots: {}, events: [] };
  const META = D.meta || {};
  const HOT = D.hotspots || {};
  const EVENTS = D.events || [];

  const PLATFORM = {
    weibo:  { name: "微博",       emoji: "🐦" },
    douyin: { name: "抖音",       emoji: "🎵" },
    xhs:    { name: "小红书",     emoji: "📕" },
    x:      { name: "X",          emoji: "✳️" },
    zhihu:  { name: "知乎",       emoji: "💡" },
    bili:   { name: "哔哩哔哩",   emoji: "📺" },
    news:   { name: "新闻",       emoji: "📰" },
  };
  const pname = (id) => (PLATFORM[id] || { name: id, emoji: "•" });
  const SENT = {
    积极: { key: "pos", color: "#0f1ea1", label: "积极" },
    中性: { key: "neu", color: "#8b8d92", label: "中性" },
    消极: { key: "neg", color: "#b3261e", label: "消极" },
    愤怒: { key: "ang", color: "#7a1b16", label: "愤怒" },
  };

  /* ── utils ───────────────────────────────────────────────────────────── */
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const nfmt = (n) => {
    n = Number(n) || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
    if (n >= 1e4) return (n / 1e4).toFixed(n >= 1e6 ? 0 : 1) + " 万";
    return String(n);
  };
  const pad = (n) => String(n).padStart(2, "0");
  const el = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  };

  /* 累计监测统计（本地留存，用于长期累计计数） */
  const STATS_KEY = "catch.stats.v1";
  function bumpStats(add) {
    let s = {};
    try { s = JSON.parse(localStorage.getItem(STATS_KEY) || "{}"); } catch (e) { s = {}; }
    s.items = (s.items || 0) + (add.items || 0);
    s.comments = (s.comments || 0) + (add.comments || 0);
    s.events = (s.events || 0) + (add.events || 0);
    s.runs = (s.runs || 0) + 1;
    s.lastRun = META.generatedAt || new Date().toISOString();
    try { localStorage.setItem(STATS_KEY, JSON.stringify(s)); } catch (e) { /* ignore */ }
    return s;
  }
  function getStats() {
    try { return JSON.parse(localStorage.getItem(STATS_KEY) || "{}"); } catch (e) { return {}; }
  }

  /* ── 1. 顶部状态条 ───────────────────────────────────────────────────── */
  function renderUtility() {
    const bar = $("#utilityStatus");
    if (!bar) return;
    const st = META.platformStatus || {};
    bar.innerHTML = Object.keys(st).map((k) => {
      const s = st[k] || {};
      const cls = s.ok ? "ok" : (s.needs_login ? "warn" : "off");
      const extra = s.needs_login ? "需重新登录" : (s.ok ? "在线" : "降级");
      return `<span class="pulse" title="${esc(s.source || "")}">
        <i class="dot ${cls}"></i>${esc(pname(k).name)} · ${extra} · ${s.items || 0}条</span>`;
    }).join("");
  }

  function renderClock() {
    const c = $("#clock");
    if (!c) return;
    const tick = () => {
      const d = new Date();
      c.textContent = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ── 2. 报头 + 指标 ──────────────────────────────────────────────────── */
  function renderMasthead() {
    const elDate = $("#issueDate");
    if (elDate) {
      elDate.innerHTML =
        `<strong>${esc(META.dateLabel || "")} ${esc(META.weekday || "")}</strong>` +
        `第 ${esc(META.issueNo || "001")} 期 · 每日 09:00 自动出报`;
    }
    const elGen = $("#issueGen");
    if (elGen) {
      const t = META.generatedAt ? new Date(META.generatedAt) : new Date();
      elGen.innerHTML = `采集于 ${pad(t.getHours())}:${pad(t.getMinutes())} · ` +
        `耗时 ${META.durationSec || 0}s`;
    }

    const c = META.counts || {};
    const st = getStats();
    const metrics = [
      { n: c.rawScanned || 0, k: "本轮采集原始条目", s: "条" },
      { n: c.events || 0, k: "跨平台舆情事件", s: "个" },
      { n: c.comments || 0, k: "真实热评样本", s: "条" },
      { n: Object.keys(META.platformStatus || {}).length, k: "接入平台", s: "个" },
    ];
    const box = $("#metrics");
    if (box) {
      box.innerHTML = metrics.map((m) =>
        `<div class="metric"><div class="n">${m.n}<small>${m.s}</small></div>
         <div class="k">${esc(m.k)}</div></div>`).join("");
    }
    const cum = $("#cumStats");
    if (cum) {
      // 「本期」用系统实测值；「累计」来自浏览器本地存储，只计访问者自己的浏览记录，
      // 不能与系统层面的累计量混淆（公网版每个访客的计数器都是独立的）。
      cum.innerHTML = `本期采集：<b>${(c.rawScanned || 0)}</b> 条原始条目 · ` +
        `<b>${(c.comments || 0)}</b> 条热评样本 · ` +
        `本浏览器已累计 <b>${(st.items || c.rawScanned || 0)}</b> 条 / ` +
        `<b>${(st.runs || 1)}</b> 轮打开`;
    }
  }

  /* ── 3. 跨平台热榜总览 ───────────────────────────────────────────────── */
  let boardFilter = "all";
  function renderBoard() {
    const list = $("#boardList");
    if (!list) return;
    const all = HOT.ranking || HOT.text || [];
    const items = boardFilter === "all" ? all : all.filter((x) => x.platform === boardFilter);
    // 热度条按「同平台内归一」计算：跨平台热度口径不同，只在同平台内可比
    const byPlat = {};
    items.forEach((x) => {
      byPlat[x.platform] = Math.max(byPlat[x.platform] || 0, x.heat || 0);
    });
    if (!items.length) {
      list.innerHTML = `<div class="empty">该平台本轮未取到条目（可能登录态或榜单结构变化）。</div>`;
      return;
    }
    list.innerHTML = items.slice(0, 40).map((it, i) => {
      const p = pname(it.platform);
      const denom = Math.max(1, byPlat[it.platform] || 1);
      const w = Math.max(2, Math.min(100, Math.round(((it.heat || 0) / denom) * 100)));
      return `<div class="rank-row">
        <div class="rank-no">${pad(i + 1)}</div>
        <div class="rank-main">
          <a class="rank-title" href="${esc(it.url || "#")}" target="_blank" rel="noopener"
             title="${esc(it.title)}">${esc(it.title)}</a>
          <div class="rank-meta">
            <span class="chip emoji">${p.emoji} ${esc(p.name)}</span>
            ${it.board ? `<span class="cap">${esc(it.board)}</span>` : ""}
            ${it.rank ? `<span class="cap">原榜第 ${it.rank} 位</span>` : ""}
          </div>
        </div>
        <div><div class="rank-bar"><i style="width:${w}%"></i></div></div>
        <div class="rank-heat">${it.heat ? nfmt(it.heat) : "—"}</div>
      </div>`;
    }).join("");
  }

  function renderBoardTabs() {
    const box = $("#boardTabs");
    if (!box) return;
    const st = META.platformStatus || {};
    const all = HOT.text || [];
    const counts = {};
    all.forEach((x) => { counts[x.platform] = (counts[x.platform] || 0) + 1; });
    const tabs = [["all", "🌐", "全平台", all.length]];
    Object.keys(counts).forEach((k) => {
      tabs.push([k, pname(k).emoji, pname(k).name, counts[k]]);
    });
    box.innerHTML = tabs.map(([id, emoji, name, n]) =>
      `<button class="tab" role="tab" data-filter="${id}"
        aria-selected="${id === boardFilter}">
        <span class="av">${emoji}</span>
        <span class="tx"><b>${esc(name)}</b><span>${n} 条${
        st[id] && !st[id].ok ? " · 降级" : ""}</span></span>
        <span class="idx">${pad(n)}</span></button>`).join("");
    $$("#boardTabs .tab").forEach((b) => b.addEventListener("click", () => {
      boardFilter = b.dataset.filter;
      renderBoardTabs(); renderBoard();
    }));
  }

  /* ── 4. 热点（只保留文字：话题与梗） ──────────────────────────────── */
  function renderHotspots() {
    const box = $("#hotBody");
    if (!box) return;
    box.innerHTML = textHotHTML();
    wireTopicCards();
  }

  function angleTemplates(it, i) {
    const p = it.platform;
    const q = /[？?]|怎么看|如何|该不该|值不值|有没有/.test(it.title);
    const banks = {
      weibo: [
        `微博热搜的「在场感」机制：一条话题如何在 3 小时内从榜单走进日常对话`,
        `以「${it.title.slice(0, 10)}」为切口，写热搜话题的议程设置与情绪动员`,
        `回声室效应检验：同一话题在微博的转发链上被谁放大、被谁稀释`,
      ],
      douyin: [
        `抖音的算法推荐如何把「${it.title.slice(0, 10)}」推给非关注者`,
        `拆解该话题的短视频二创结构：模仿、搬运还是再叙事`,
        `视频化叙事的情绪阈值：什么强度的画面才能让用户完成转发`,
      ],
      xhs: [
        `小红书的种草逻辑如何介入公共议题：体验分享如何变成立场表达`,
        `以「${it.title.slice(0, 10)}」为例，观察笔记生态里的身份表演`,
        `平台规训研究：什么话在该平台能说、什么话会被折叠`,
      ],
      x: [
        `X 的国际舆论场如何改写中文平台的报道框架`,
        `跨语种传播中的翻译损耗：同一事实的不同措辞如何制造分歧`,
        `用「${it.title.slice(0, 10)}」追踪英文舆论对中文事件的重述`,
      ],
      zhihu: [
        `知乎式提问如何把社会情绪转译为「值得讨论」的知识议题`,
        `回答区的意见结构：谁在提供证据、谁在提供情绪`,
        `以「${it.title.slice(0, 10)}」为切口研究「如何评价」类提问的修辞`,
      ],
      bili: [
        `弹幕与评论区的双重文本：同一条视频的两种集体表达`,
        `以「${it.title.slice(0, 10)}」为例，观察亚文化语汇的对外扩散`,
        `从 UP 主选题看内容平台如何引导议题的注意力分配`,
      ],
      news: [
        `硬新闻进入热搜榜后的「再叙事」路径：从通稿到表情包`,
        `以「${it.title.slice(0, 10)}」为样本，比较通稿、门户标题与微博热搜的框架差异`,
        `议程设置研究：谁在决定「什么值得被报道」，以及报道之后被讨论的是什么`,
      ],
    };
    const base = banks[p] || banks.weibo;
    const isQ = q
      ? [
          `「如何评价」类提问的框架建构：${it.title.slice(0, 12)} 预设了哪些前提`,
          `答案区的意见极化：赞同、反对与「先问是不是」三派如何分布`,
          `把「${it.title.slice(0, 10)}」当作一个研究问题，而不是一个观点`,
        ]
      : base;
    return isQ.slice(0, 3);
  }

  function textHotHTML() {
    const items = (HOT.text || []).slice(0, 18);
    if (!items.length) return `<div class="empty">暂无热点数据。</div>`;
    const res = HOT.resonance || [];
    const resBar = res.length ? `
      <div class="panel" style="margin-bottom:24px">
        <div class="panel-head">
          <h4>跨平台共振词</h4>
          <span class="sub">同一实体在多个平台同时出现 → 优先采写信号</span>
        </div>
        <div class="kwlist">${res.slice(0, 16).map((r) => `
          <span class="chip chip-soft">#${esc(r.token)}
            <span style="opacity:.65">${r.platforms.map((p) => pname(p).emoji).join(" ")}</span>
            <span style="opacity:.6">${r.count}</span></span>`).join("")}</div>
      </div>` : "";

    return resBar + `<div class="grid-3">${items.map((it, i) => {
      const p = pname(it.platform);
      const tags = (it.tokens || []).slice(0, 4);
      const angles = angleTemplates(it, i);
      const heatScore = Math.min(99, Math.round(
        Math.log10((it.heat || 1000) + 10) * 12 + (it.rank ? 20 - it.rank * 0.4 : 0)));
      return `<article class="topic">
        <div class="top">
          <span class="av">${p.emoji}</span>
          <div style="min-width:0">
            <h4><a href="${esc(it.url || "#")}" target="_blank" rel="noopener">${esc(it.title)}</a></h4>
            <div class="cap" style="margin-top:4px">${esc(p.name)} · ${esc(it.board || "热榜")}${
              it.rank ? ` · 第 ${it.rank} 位` : ""}</div>
          </div>
        </div>
        <div class="rank-meta">${tags.map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}</div>
        <ul class="angles">${angles.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <div class="foot">
          <span class="score">传播力 <b>${heatScore}</b></span>
          <span class="cap">热度 ${it.heat ? nfmt(it.heat) : "—"}</span>
        </div>
      </article>`;
    }).join("")}</div>`;
  }

  function wireTopicCards() { /* 预留：卡片级交互 */ }

  /* ── 4b. 网络流行语 ──────────────────────────────────────────────────── */
  function renderSlang() {
    const box = $("#slangGrid");
    if (!box) return;
    const rows = HOT.slang || [];
    const src = $("#slangSource");
    if (src) {
      const st = META.slangStatus || {};
      const platCount = st.plats || ((st.source || "").split(" / ").length || 0);
      const bits = [];
      if (st.source) bits.push(st.source);
      if (st.window) bits.push(`统计窗口 ${st.window}`);
      if (st.corpus) bits.push(`热榜语料 ${st.corpus} 条`);
      if (st.days) bits.push(`${st.days} 天 × ${platCount} 平台`);
      if (st.candidates) bits.push(`候选 ${st.candidates} 词`);
      bits.push(`本期 ${rows.length} 条`);
      src.textContent = bits.join(" · ");
    }
    if (!rows.length) {
      box.innerHTML = `<div class="empty">本轮未取到符合条件的流行语（归档源更新或网络异常）。</div>`;
      return;
    }
    box.innerHTML = rows.map((r, i) => `
      <article class="slang">
        <div class="idx">${pad(i + 1)}</div>
        <div class="body">
          <h4><a href="${esc(r.url || "#")}" target="_blank" rel="noopener">${esc(r.word)}</a></h4>
          <p class="desc">${esc(r.desc || "")}</p>
          <div class="meta">
            ${r.kind ? `<span class="tag">${esc(r.kind)}</span>` : ""}
            ${r.mentions ? `<span class="tag live">近 7 天热榜出现 ${r.mentions} 次</span>` : ""}
            ${r.days ? `<span class="tag">覆盖 ${r.days} 天</span>` : ""}
            ${r.platformCount ? `<span class="tag">${r.platformCount} 个平台</span>` : ""}
            ${r.variants ? `<span class="tag">${r.variants} 种句式</span>` : ""}
            ${r.defSource ? `<span class="tag src">释义：${esc(r.defSource)}</span>` : ""}
          </div>
          ${(r.samples && r.samples.length) ? `<ul class="evid">${r.samples.map((s) => `
            <li><span class="pl">${esc(s.platform || "")}</span><span class="dt">${esc((s.day || "").slice(5))}</span>
              <a href="${esc(s.url || "#")}" target="_blank" rel="noopener">${esc(s.title || "")}</a></li>`).join("")}</ul>` : ""}
        </div>
      </article>`).join("");
  }

  /* ── 5. 舆情事件 ─────────────────────────────────────────────────────── */
  let curEvent = 0;
  let cmtPage = 0;
  let cmtQuery = "";
  let cmtSent = "all";
  let cmtOnlyRelevant = false;
  let cmtOnlyRecent = false;
  const CMT_PER_PAGE = 20;
  // 时效窗口：评论发布时间晚于「采集日 - 30 天」视为近 30 天。
  // 本系统是每日舆情监测，跨年份的旧评论会稀释当期舆情，故单列一档供研究者切换。
  const RECENT_DAYS = 30;

  function renderEventNav() {
    const nav = $("#eventNav");
    if (!nav) return;
    if (!EVENTS.length) {
      nav.innerHTML = `<div class="empty">本轮未聚类出跨平台事件。</div>`;
      return;
    }
    nav.innerHTML = EVENTS.map((e, i) => {
      const plats = (e.platforms || []).map((p) => pname(p).emoji).join(" ");
      return `<button class="tab" role="tab" data-ev="${i}"
        aria-selected="${i === curEvent}">
        <span class="av">${plats || "•"}</span>
        <span class="tx">
          <b>${esc(e.title.length > 22 ? e.title.slice(0, 22) + "…" : e.title)}</b>
          <span>${esc(e.keyword)} · 热度 ${nfmt(e.heat)} · 风险${esc(e.risk.level)}</span>
        </span>
        <span class="idx">${esc(e.id)}</span></button>`;
    }).join("");
    $$("#eventNav .tab").forEach((b) => b.addEventListener("click", () => {
      curEvent = +b.dataset.ev; cmtPage = 0; cmtQuery = ""; cmtSent = "all"; cmtOnlyRelevant = false; cmtOnlyRecent = false;
      renderEventNav(); renderEventDetail();
      $("#eventDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }

  function renderEventDetail(initial = false) {
    const box = $("#eventDetail");
    if (!box) return;
    const e = EVENTS[curEvent];
    if (!e) { box.innerHTML = `<div class="empty">暂无事件数据。</div>`; return; }
    const sent = e.sentiment || {};
    const rel = e.commentRelevance || {};
    const rs = recencyStats(markRecency((FULL[e.id] || e.comments || []).slice()));
    const relNote = (rel && rel.total)
      ? `本事件 <b>${rel.relevant}</b> / <b>${rel.total}</b> 条评论被判定为与事件主题相关（<b>${(rel.ratio * 100).toFixed(1)}%</b>），
         判据为 <b>${esc(rel.basis || "")}</b>——即源视频标题含事件关键词，
         或与该事件的任一触发条目共享 ≥4 字连续片段。
         关键词检索会召回标题与事件无关的源视频，
         本系统在采集端已加标题闸门、缓存同步过闸，并在装配端逐条复核。`
      : "本事件未取得可用于相关性判定的样本。";
    const recNote = (rs && rs.total)
      ? `本事件 <b>${rs.recent}</b> / <b>${rs.total}</b> 条评论发布于最近 30 天内（<b>${(rs.ratio * 100).toFixed(1)}%</b>），其余 ${rs.old} 条来自更早的源视频。
         本系统为每日舆情监测，跨年份旧评论会稀释当期舆情，
         页面提供「只看近 30 天」筛选，导出 CSV 时亦含「是否近 30 天」列，
         引用当期结论时建议使用该子样本。`
      : "本事件未取得可用于时效判定的样本。";
    const updated = e.trend && e.trend.updatedAt ? new Date(e.trend.updatedAt) : new Date();
    const facts = (e.facts && e.facts.length) ? e.facts : defaultFacts(e);
    const phases = (e.phases && e.phases.length) ? e.phases : defaultPhases(e);
    const analysis = e.analysis || {};

    box.innerHTML = `
      <div class="event-head">
        <div class="eyebrow">${esc(e.id)} · 舆情事件档案</div>
        <h3>${esc(e.title)}</h3>
        ${e.subtitle ? `<div class="lede" style="margin-top:8px">${esc(e.subtitle)}</div>` : ""}
        <div class="meta-row">
          ${(e.platforms || []).map((p) => `<span class="chip emoji">${pname(p).emoji} ${esc(pname(p).name)}</span>`).join("")}
          <span class="chip">热度 ${nfmt(e.heat)}</span>
          <span class="chip ${e.risk.level === "高" ? "chip-signal" : ""}">风险 ${esc(e.risk.level)} · ${e.risk.score}</span>
          <span class="chip">热评样本 ${e.commentCount || (e.comments || []).length} 条${rel && rel.total ? `（相关 ${rel.relevant}）` : ""}</span>
          <span class="chip">跨 ${e.platforms.length} 平台</span>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h4>事件概括梳理</h4>
          <span class="sub">要素 · 阶段 · 信源</span>
        </div>
        <p class="lede">${esc(e.summary || analysis.summary || "（本轮自动聚类事件，研判文字将在每日 09:00 任务中由分析师补全）")}</p>
        <p class="cap" style="margin:-6px 0 14px">研判正文与要素表引用的数字为撰写该段时的采集实测值；标题栏的综合热度为实时实测，随榜单轮换变化，二者可能存在小幅差异。</p>
        <div class="facts">${facts.map((f) =>
          `<div class="fact"><div class="k">${esc(f.label)}</div><div class="v">${esc(f.value)}</div></div>`).join("")}</div>
        <div class="phases" style="margin-top:20px">${phases.map((p) =>
          `<div class="phase"><div class="k">${esc(p.name)}</div><div class="v">${esc(p.desc)}</div>
           <div class="t">${esc(p.time || "")}</div></div>`).join("")}</div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h4>舆情走势 · 每 6 小时更新</h4>
          <span class="sub">最后更新 ${pad(updated.getHours())}:${pad(updated.getMinutes())} ·
            实测点 ${(e.trend && e.trend.measuredPoints) || 0} 个（每 6 小时采集一次）</span>
        </div>
        <div class="chart-wrap" id="trendWrap"></div>
        <div class="legend" style="margin-top:12px" id="trendLegend"></div>
        <p class="cap" style="margin-top:10px">
          基础数据量：走势图由 <b>${((e.trend && e.trend.series) || []).length}</b> 个时间点构成，
          其中 <b>${(e.trend && e.trend.measuredPoints) || 0}</b> 个为每 6 小时实测点、
          <b>${(((e.trend && e.trend.series) || []).length) - ((e.trend && e.trend.measuredPoints) || 0)}</b> 个为扩散模型估算基线；
          每个实测点记录该事件的<b>综合热度</b>与<b>各平台分项热度</b>，即
          ${Object.keys(e.platformHeat || {}).length} 个平台 × ${(e.trend && e.trend.measuredPoints) || 0} 次实测 =
          <b>${Object.keys(e.platformHeat || {}).length * ((e.trend && e.trend.measuredPoints) || 0)}</b> 个平台级观测值。
          事件热度本身由 <b>${e.sourceCount || 0}</b> 条原始榜单条目聚合而成。
        </p>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h4>每日词云</h4>
          <span class="sub">${esc(e.wordcloudBasis || "真实热评 + 榜单标题")} · 权重 = 词频 × 语义强度（jieba TF-IDF）</span>
        </div>
        <div class="wc-box"><canvas id="wcCanvas"></canvas></div>
        <div class="wc-legend">
          <span><i style="background:#0f1ea1"></i>积极语义</span>
          <span><i style="background:#1a1a1a"></i>中性词</span>
          <span><i style="background:#b3261e"></i>消极 / 愤怒语义</span>
          <span class="muted" id="wcTip">悬停查看词频</span>
        </div>
        <p class="cap" style="margin-top:10px">
          基础数据量：词云由 <b>${sent.samples || 0}</b> 条真实热评全文
          + <b>${e.sourceCount || 0}</b> 条原始榜单标题共同分词，
          经 TF-IDF 提取后保留 <b>${(e.wordcloud || []).length}</b> 个词条进入可视化；
          每个词的权重来自其在上述语料中的出现频次与区分度，颜色按词的情感极性着色。
          语料量越大，词的权重分布越稳定；若本轮没有相关热评，页面只展示榜单标题词，
          不将其误称为评论区舆情。
        </p>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h4>情感倾向分析</h4>
          <span class="sub">基于 ${sent.samples || 0} 条真实热评的规则法判定</span>
        </div>
        <div class="sent-bar">
          <i style="width:${sent.positive || 0}%;background:${SENT.积极.color}"></i>
          <i style="width:${sent.neutral || 0}%;background:${SENT.中性.color}"></i>
          <i style="width:${sent.negative || 0}%;background:${SENT.消极.color}"></i>
          <i style="width:${sent.anger || 0}%;background:${SENT.愤怒.color}"></i>
        </div>
        <div class="sent-legend">
          <div class="sent-item"><div class="k"><i style="background:${SENT.积极.color}"></i>积极</div>
            <div class="v">${sent.positive || 0}%</div><div class="s">支持 / 认可 / 情绪正向</div></div>
          <div class="sent-item"><div class="k"><i style="background:${SENT.中性.color}"></i>中性</div>
            <div class="v">${sent.neutral || 0}%</div><div class="s">陈述 / 追问 / 信息补充</div></div>
          <div class="sent-item"><div class="k"><i style="background:${SENT.消极.color}"></i>消极</div>
            <div class="v">${sent.negative || 0}%</div><div class="s">质疑 / 批评 / 情绪负向</div></div>
          <div class="sent-item"><div class="k"><i style="background:${SENT.愤怒.color}"></i>愤怒</div>
            <div class="v">${sent.anger || 0}%</div><div class="s">强对抗 / 维权 / 举报</div></div>
        </div>
        ${(sent.keywords || []).length ? `<div class="kwlist">${sent.keywords.map((k) =>
          `<span class="tag">${esc(k.word)} · ${k.count}</span>`).join("")}</div>` : ""}
      </div>

      <div class="panel">
        <div class="panel-head">
          <h4>真实热评样本 <span style="color:var(--muted);font-weight:400">${e.commentCount || (e.comments || []).length} 条</span></h4>
          <span class="sub">按点赞排序 · 来源 ${esc((e.commentStats && e.commentStats.source) || "多平台公开评论接口")}${
            (e.commentStats && e.commentStats.videos_scanned)
              ? ` · 扫描 ${e.commentStats.videos_scanned} 个源视频`
              : ""}${
            (e.commentStats && e.commentStats.videos_skipped)
              ? ` · 跳过 ${e.commentStats.videos_skipped} 个标题无关的视频`
              : ""}${
            (rel && rel.total)
              ? ` · 相关 ${rel.relevant} 条（${(rel.ratio * 100).toFixed(1)}%，判据：${esc(rel.basis || "")}）`
              : ""}${
            (rs && rs.total)
              ? ` · 近 30 天 ${rs.recent} 条（${(rs.ratio * 100).toFixed(1)}%）`
              : ""}</span>
        </div>
        <div class="cmt-toolbar">
          <input type="search" id="cmtSearch" placeholder="搜索评论关键词…" value="${esc(cmtQuery)}">
          <div class="seg" id="cmtSent">
            <button data-s="all" aria-pressed="${cmtSent === "all"}">全部</button>
            <button data-s="积极" aria-pressed="${cmtSent === "积极"}">积极</button>
            <button data-s="中性" aria-pressed="${cmtSent === "中性"}">中性</button>
            <button data-s="消极" aria-pressed="${cmtSent === "消极"}">消极</button>
          </div>
          <button class="btn" id="cmtOnlyRel" aria-pressed="${cmtOnlyRelevant}"
            title="仅显示与该事件主题相关的评论">只看相关</button>
          <button class="btn" id="cmtOnlyRecent" aria-pressed="${cmtOnlyRecent}"
            title="仅显示最近 30 天内发布的评论">只看近 30 天</button>
          <button class="btn" id="cmtExport">导出 CSV</button>
          <span class="cap" id="cmtCount"></span>
        </div>
        <div id="cmtList"></div>
        <div class="pager">
          <button class="btn" id="cmtPrev">← 上一页</button>
          <span id="cmtPage"></span>
          <button class="btn" id="cmtNext">下一页 →</button>
          <span class="spacer"></span>
          <span class="cap">每页 ${CMT_PER_PAGE} 条</span>
        </div>
        ${(e.commentStats && e.commentStats.target) ? `<p class="cap" style="margin-top:12px">
          抽样边界：本期目标每事件 <b>${e.commentStats.target}</b> 条，实得 <b>${e.commentCount || (e.comments || []).length}</b> 条。
          ${e.commentStats.complete
            ? "已达标。样本为公开评论接口返回的真实评论，按热度排序取样，非全量抓取。"
            : "尚未达标：受接口限流与源视频评论量限制，样本随每日采集累积（缓存跨轮次保留）。"}
          样本仅覆盖公开可见评论，平台未公开的评论不可得，结论不应外推到全网。<br>
          相关性口径：${relNote}
          时效口径：${recNote}
        </p>` : ""}
      </div>

      ${renderAnalysisPanel(analysis, e)}
      ${renderSourcesPanel(e)}
    `;

    drawTrend(e);
    // 即使平台没有返回评论，也从真实榜单标题生成“条目词云”，
    // 避免页面空白；页面同时标注该词云不是评论区舆情。
    drawWordCloud((e.wordcloud && e.wordcloud.length)
      ? e.wordcloud : titleWordCloud(e));
    renderComments(e);
    wireCommentTools(e);   // 事件切换后立即重新绑定筛选/分页/导出
    // 首屏先完成渲染，再在浏览器空闲时补齐完整热评；切换事件时立即加载。
    if (!FULL[e.id] && e.commentsFile) {
      const hydrate = () => loadFullComments(e).then(() => {
        if (EVENTS[curEvent] && EVENTS[curEvent].id === e.id) renderComments(EVENTS[curEvent]);
      });
      if (initial && "requestIdleCallback" in window) {
        window.requestIdleCallback(hydrate, { timeout: 3000 });
      } else if (initial) {
        window.setTimeout(hydrate, 1500);
      } else {
        hydrate();
      }
    }
  }

  function defaultFacts(e) {
    const top = (e.members || []).slice(0, 4);
    return [
      { label: "事件类型", value: e.keyword + "（跨平台共振话题）" },
      { label: "共振平台", value: (e.platforms || []).map((p) => pname(p).name).join(" · ") },
      { label: "综合热度", value: nfmt(e.heat) + "（各平台热度加总）" },
      { label: "风险等级", value: `${e.risk.level}（指数 ${e.risk.score}）` },
      { label: "信源条目", value: `${e.sourceCount || 0} 条原始榜单条目` },
      { label: "热评样本", value: `${e.commentCount || (e.comments || []).length} 条真实评论` },
      { label: "代表条目", value: top.length ? top[0].title.slice(0, 40) : "—" },
      { label: "监测平台", value: "微博 / 抖音 / 小红书 / X / 知乎 / 新闻 / B站" },
    ];
  }

  function defaultPhases(e) {
    const n = (e.trend && e.trend.measuredPoints) || 0;
    return [
      { name: "发生", desc: "事件在单平台首发并被原始信源证实。", time: "T0" },
      { name: "扩散", desc: "跨平台转载，话题进入各平台热榜。", time: "T0+2h" },
      { name: "转折", desc: "回应、澄清或二次舆情改变情绪走向。", time: "T0+8h" },
      { name: "沉淀", desc: n > 2 ? "热度回落，讨论进入长尾。" : "热度持续高位，需继续监测。",
        time: n > 2 ? "T0+20h" : "进行中" },
    ];
  }

  function renderAnalysisPanel(a, e) {
    if (!a || (!a.points && !a.angles && !a.methods)) return "";
    const pts = (a.points || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const ang = (a.angles || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const met = (a.methods || []).map((p) => `<li>${esc(p)}</li>`).join("");
    return `<div class="panel">
      <div class="panel-head"><h4>研判与选题建议</h4>
        <span class="sub">议程设置 · 情绪机制 · 采写路径</span></div>
      <div class="grid-2">
        <div><div class="cap" style="margin-bottom:8px">关键研判</div>
          <ul class="angles">${pts || "<li>待分析师补充</li>"}</ul></div>
        <div><div class="cap" style="margin-bottom:8px">可选选题角度</div>
          <ul class="angles">${ang || "<li>待分析师补充</li>"}</ul></div>
      </div>
      ${met ? `<div class="hr"></div><div class="cap" style="margin-bottom:8px">可用研究方法</div>
        <ul class="angles">${met}</ul>` : ""}
    </div>`;
  }

  function renderSourcesPanel(e) {
    const members = (e.members || []).slice(0, 8);
    if (!members.length) return "";
    return `<div class="panel">
      <div class="panel-head"><h4>信源与可复核性</h4>
        <span class="sub">每条均指向原始榜单 / 原帖</span></div>
      <table class="tbl">
        <thead><tr><th>平台</th><th>原始条目</th><th>热度</th><th>来源</th></tr></thead>
        <tbody>${members.map((m) => `<tr>
          <td class="nowrap">${pname(m.platform).emoji} ${esc(pname(m.platform).name)}</td>
          <td><a href="${esc(m.url || "#")}" target="_blank" rel="noopener">${esc((m.title || "").slice(0, 46))}</a></td>
          <td class="nowrap">${m.heat ? nfmt(m.heat) : "—"}</td>
          <td class="nowrap"><span class="cap">${esc(m.board || "")}</span></td>
        </tr>`).join("")}</tbody>
      </table>
    </div>`;
  }

  /* ── 走势图（SVG） ───────────────────────────────────────────────────── */
  function drawTrend(e) {
    const wrap = $("#trendWrap");
    if (!wrap) return;
    const series = (e.trend && e.trend.series) || [];
    const legend = $("#trendLegend");
    if (!series.length) {
      wrap.innerHTML = `<div class="empty">暂无走势数据。</div>`;
      return;
    }
    const W = 860, H = 250, PL = 44, PR = 12, PT = 16, PB = 26;
    const vals = series.map((s) => s.total || 0);
    const max = Math.max(1, ...vals) * 1.12;
    const min = Math.min(0, ...vals);
    const x = (i) => PL + (i * (W - PL - PR)) / Math.max(1, series.length - 1);
    const y = (v) => PT + (H - PT - PB) * (1 - (v - min) / (max - min || 1));

    const line = series.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(s.total || 0).toFixed(1)}`).join(" ");
    const area = `${line} L${x(series.length - 1).toFixed(1)},${(H - PB).toFixed(1)} L${x(0).toFixed(1)},${(H - PB).toFixed(1)} Z`;

    // 平台分线（仅实测点）
    const platKeys = Object.keys(e.platformHeat || {});
    const platColors = { weibo: "#0f1ea1", douyin: "#8fbaff", xhs: "#b3261e", x: "#1a1a1a", zhihu: "#5a5e62", bili: "#8b8d92" };
    let platPaths = "";
    platKeys.forEach((pk) => {
      const pts = [];
      series.forEach((s, i) => {
        const v = (s.platforms || {})[pk];
        if (v != null) pts.push(`${pts.length ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      });
      if (pts.length > 1) platPaths += `<path d="${pts.join(" ")}" fill="none" stroke="${platColors[pk] || "#8b8d92"}" stroke-width="1.5" stroke-dasharray="4 3"/>`;
    });

    const gridlines = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const v = min + (max - min) * f;
      return `<line x1="${PL}" x2="${W - PR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"
        stroke="#e2e0da" stroke-width="1"/>
        <text x="${PL - 8}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end"
        font-size="10" fill="#a3a19a" font-family="monospace">${nfmt(Math.round(v))}</text>`;
    }).join("");

    const xlabels = series.map((s, i) => {
      if (series.length > 12 && i % Math.ceil(series.length / 8) !== 0 && i !== series.length - 1) return "";
      return `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="10"
        fill="#a3a19a" font-family="monospace">${esc(s.label || "")}</text>`;
    }).join("");

    const dots = series.map((s, i) => {
      const measured = s.type !== "estimated";
      return `<circle cx="${x(i).toFixed(1)}" cy="${y(s.total || 0).toFixed(1)}" r="${measured ? 3 : 2}"
        fill="${measured ? "#0f1ea1" : "#ffffff"}" stroke="#0f1ea1" stroke-width="1"/>`;
    }).join("");

    const peakIdx = vals.indexOf(Math.max(...vals));

    wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="舆情每小时走势图">
      ${gridlines}
      <path d="${area}" fill="#0f1ea1" opacity="0.06"/>
      <path d="${line}" fill="none" stroke="#0f1ea1" stroke-width="2"/>
      ${platPaths}
      ${dots}
      <circle cx="${x(peakIdx).toFixed(1)}" cy="${y(vals[peakIdx]).toFixed(1)}" r="5"
        fill="none" stroke="#0f1ea1" stroke-width="1"/>
      <text x="${x(peakIdx).toFixed(1)}" y="${(y(vals[peakIdx]) - 10).toFixed(1)}" text-anchor="middle"
        font-size="10" fill="#0f1ea1" font-family="monospace">峰值 ${nfmt(vals[peakIdx])}</text>
      ${xlabels}
      <g id="trendHover"></g>
    </svg>
    <div class="chart-tip" id="trendTip"></div>`;

    const svg = wrap.querySelector("svg");
    const tip = $("#trendTip");
    const hoverG = wrap.querySelector("#trendHover");
    svg.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      let best = 0, bd = 1e9;
      series.forEach((s, i) => { const d = Math.abs(x(i) - px); if (d < bd) { bd = d; best = i; } });
      const s = series[best];
      const plats = Object.entries(s.platforms || {})
        .map(([k, v]) => `${pname(k).emoji}${pname(k).name} ${nfmt(v)}`).join("<br>");
      hoverG.innerHTML = `<line x1="${x(best).toFixed(1)}" y1="${PT}" x2="${x(best).toFixed(1)}" y2="${H - PB}"
        stroke="#cfccc4" stroke-width="1"/>
        <circle cx="${x(best).toFixed(1)}" cy="${y(s.total).toFixed(1)}" r="4" fill="#0f1ea1"/>`;
      tip.innerHTML = `<b>${esc(s.label)}</b> · ${s.type === "estimated" ? "扩散模型估算点" : "实测点"}<br>
        综合热度 ${nfmt(s.total)}${plats ? "<br>" + plats : ""}`;
      tip.classList.add("on");
      const box = wrap.getBoundingClientRect();
      const tx = (x(best) / W) * box.width;
      tip.style.left = Math.min(box.width - 170, Math.max(0, tx - 60)) + "px";
      tip.style.top = "6px";
    });
    svg.addEventListener("mouseleave", () => {
      tip.classList.remove("on");
      hoverG.innerHTML = "";
    });

    if (legend) {
      legend.innerHTML =
        `<span><i style="background:#0f1ea1"></i>综合热度（实线）</span>` +
        `<span class="dash" style="color:#8fbaff"><i></i>平台分线（实测点）</span>` +
        `<span>○ 空心点 = 扩散模型估算点，● 实心点 = 每小时实测点</span>`;
    }
  }

  /* ── 词云（Canvas） ──────────────────────────────────────────────────── */
  function drawWordCloud(items) {
    const cv = $("#wcCanvas");
    if (!cv) return;
    const box = cv.parentElement;
    const w = box.clientWidth || 800, h = 340;
    const dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = h * dpr;
    cv.style.height = h + "px";
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!items.length) {
      ctx.fillStyle = "#a3a19a"; ctx.font = "13px Inter, sans-serif";
      ctx.textAlign = "center"; ctx.fillText("暂无词云数据", w / 2, h / 2);
      return;
    }
    const weights = items.map((i) => i.weight);
    const max = Math.max(...weights), min = Math.min(...weights);
    const placed = [];
    const colorOf = (s) => (s === "积极" ? "#0f1ea1" : (s === "消极" ? "#b3261e" : "#1a1a1a"));
    const sorted = items.slice().sort((a, b) => b.weight - a.weight);

    const fits = (x0, y0, w0, h0) => {
      if (x0 < 2 || y0 < 2 || x0 + w0 > w - 2 || y0 + h0 > h - 2) return false;
      for (const p of placed) {
        if (!(x0 + w0 < p.x || p.x + p.w < x0 || y0 + h0 < p.y || p.y + p.h < y0)) return false;
      }
      return true;
    };

    sorted.forEach((it, idx) => {
      const ratio = max === min ? 0.6 : (it.weight - min) / (max - min);
      const size = Math.round(12 + ratio * 34);
      ctx.font = `700 ${size}px Inter, "PingFang SC", sans-serif`;
      const tw = ctx.measureText(it.word).width;
      const th = size * 1.02;
      let done = false;
      const cx = w / 2, cy = h / 2;
      for (let a = 0; a < 900 && !done; a++) {
        const t = a * 0.32;
        const r = 3.2 * t;
        const px = cx + r * Math.cos(t) * 1.15 - tw / 2;
        const py = cy + r * Math.sin(t) * 0.72 - th / 2;
        if (fits(px, py, tw, th)) {
          placed.push({ x: px, y: py, w: tw, h: th, word: it.word, weight: it.weight,
            sentiment: it.sentiment });
          done = true;
        }
      }
      if (!done && idx < 10) {
        // 兜底：沿左边缘纵向排布，保证不越界
        let yy = 6, xx = 6;
        for (const p of placed) {
          if (p.x < 120 && Math.abs(p.y - yy) < p.h + 4) { yy = p.y + p.h + 6; }
        }
        xx = 6 + (idx % 3) * 40;
        yy = Math.min(h - th - 6, yy);
        placed.push({ x: xx, y: yy, w: tw, h: th, word: it.word, weight: it.weight,
          sentiment: it.sentiment });
      }
    });

    placed.forEach((p) => {
      const ratio = max === min ? 0.6 : (p.weight - min) / (max - min);
      const size = Math.round(12 + ratio * 34);
      ctx.font = `700 ${size}px Inter, "PingFang SC", sans-serif`;
      ctx.fillStyle = colorOf(p.sentiment);
      ctx.textBaseline = "top";
      ctx.fillText(p.word, p.x, p.y);
    });

    const tip = $("#wcTip");
    cv.onmousemove = (ev) => {
      const r = cv.getBoundingClientRect();
      const mx = ev.clientX - r.left, my = ev.clientY - r.top;
      const hit = placed.find((p) => mx >= p.x && mx <= p.x + p.w && my >= p.y && my <= p.y + p.h);
      if (tip) tip.textContent = hit
        ? `${hit.word} · 权重 ${hit.weight.toFixed(2)} · ${hit.sentiment || "中性"}`
        : "悬停查看词频";
    };
    cv.onmouseleave = () => { if (tip) tip.textContent = "悬停查看词频"; };
  }

  function titleWordCloud(e) {
    const stop = new Set(["黄景瑜", "泰国", "一个", "为什么", "如何", "什么", "网友"]);
    const counts = {};
    for (const member of (e.members || [])) {
      const text = String(member.title || "");
      for (const word of (text.match(/[\u4e00-\u9fff]{2,6}/g) || [])) {
        if (!stop.has(word)) counts[word] = (counts[word] || 0) + 1;
      }
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 24)
      .map(([word, weight]) => ({word, weight, sentiment: "中性"}));
  }

  /* ── 评论 ────────────────────────────────────────────────────────────── */
  // 完整热评（每事件 1000 条）按需加载：切换事件时异步拉取对应 JSON
  const FULL = {};      // id → 完整评论数组
  const LOADING = {};   // id → Promise

  function loadFullComments(ev) {
    const id = ev.id;
    if (FULL[id]) return Promise.resolve(FULL[id]);
    if (LOADING[id]) return LOADING[id];
    const url = ev.commentsFile;
    if (!url) return Promise.resolve(ev.comments || []);
    LOADING[id] = fetch(url, { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && Array.isArray(d.comments)) {
          FULL[id] = d.comments;
          return d.comments;
        }
        return ev.comments || [];
      })
      .catch(() => (ev.comments || []))
      .finally(() => { delete LOADING[id]; });
    return LOADING[id];
  }

  function currentComments() {
    const e = EVENTS[curEvent];
    if (!e) return [];
    const cs = FULL[e.id] || e.comments || [];
    markRecency(cs);
    return cs;
  }

  // 采集日 = meta.generatedAt；早于「采集日 - 30 天」的评论标记为非近期
  function recencyCutoff() {
    const t = Date.parse((META.generatedAt || "").replace(" ", "T"));
    const base = Number.isFinite(t) ? t : Date.now();
    return base - RECENT_DAYS * 86400000;
  }

  function markRecency(cs) {
    const cut = recencyCutoff();
    for (const c of cs) {
      const ts = Date.parse(String(c.time || "").replace(" ", "T"));
      c.recent = Number.isFinite(ts) ? ts >= cut : false;
    }
    return cs;
  }

  function recencyStats(cs) {
    const recent = cs.filter((c) => c.recent).length;
    return { recent, old: cs.length - recent,
             ratio: cs.length ? recent / cs.length : 0 };
  }

  function filteredComments(e) {
    let cs = currentComments();
    if (cmtOnlyRelevant) cs = cs.filter((c) => c.relevant);
    if (cmtOnlyRecent) cs = cs.filter((c) => c.recent);
    if (cmtSent !== "all") cs = cs.filter((c) => c.sentiment === cmtSent);
    if (cmtQuery) {
      const q = cmtQuery.toLowerCase();
      cs = cs.filter((c) => (c.text || "").toLowerCase().includes(q));
    }
    return cs;
  }

  function renderComments(e) {
    const list = $("#cmtList");
    if (!list) return;
    const total = e.commentCount || (e.comments || []).length;
    const cs = filteredComments(e);
    const pages = Math.max(1, Math.ceil(cs.length / CMT_PER_PAGE));
    cmtPage = Math.min(cmtPage, pages - 1);
    const slice = cs.slice(cmtPage * CMT_PER_PAGE, (cmtPage + 1) * CMT_PER_PAGE);
    const countEl = $("#cmtCount"), pageEl = $("#cmtPage");
    if (countEl) {
      const loaded = FULL[e.id] ? cs.length : Math.min(cs.length, 40);
      countEl.textContent = FULL[e.id]
        ? `匹配 ${cs.length} / ${total} 条`
        : `已载入 ${loaded} / ${total} 条 · 正在加载完整样本…`;
    }
    if (pageEl) pageEl.textContent = `第 ${cmtPage + 1} / ${pages} 页`;

    if (!slice.length) {
      list.innerHTML = `<div class="empty">没有匹配的评论样本。</div>`;
    } else {
      const maxLike = Math.max(1, ...slice.map((c) => c.likes || 0));
      list.innerHTML = slice.map((c, i) => `
        <div class="cmt">
          <div class="no">${pad(cmtPage * CMT_PER_PAGE + i + 1)}</div>
          <div>
            <div class="txt">${esc(c.text)}</div>
            <div class="who">
              <span class="chip emoji">${c.emoji || pname(c.platform).emoji} ${esc(pname(c.platform).name)}</span>
              <span class="sent-pill ${c.sentiment === "积极" ? "pos" : (c.sentiment === "消极" ? "neg" : "")}">${esc(c.sentiment || "中性")}</span>
              ${c.author ? `<span>${esc(c.author)}</span>` : ""}
              ${c.ip ? `<span class="muted">IP ${esc(c.ip)}</span>` : ""}
              ${c.time ? `<span class="muted">${esc(String(c.time).slice(0, 16))}</span>` : ""}
            </div>
          </div>
          <div class="likes">
            <b>${c.likes ? nfmt(c.likes) : "—"}</b>
            <span>赞 ${maxLike ? Math.round(((c.likes || 0) / maxLike) * 100) : 0}%</span>
          </div>
        </div>`).join("");
    }
    const prev = $("#cmtPrev"), next = $("#cmtNext");
    if (prev) prev.disabled = cmtPage === 0;
    if (next) next.disabled = cmtPage >= pages - 1;
  }

  function wireCommentTools(e) {
    const e2 = EVENTS[curEvent];
    const search = $("#cmtSearch");
    if (search) {
      search.addEventListener("input", (ev) => {
        cmtQuery = ev.target.value.trim();
        cmtPage = 0;
        renderComments(e2);
      });
    }
    const seg = $("#cmtSent");
    if (seg) {
      $$("#cmtSent button").forEach((b) => b.addEventListener("click", () => {
        cmtSent = b.dataset.s; cmtPage = 0;
        $$("#cmtSent button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x.dataset.s === cmtSent)));
        renderComments(e2);
      }));
    }
    const relBtn = $("#cmtOnlyRel");
    if (relBtn) relBtn.addEventListener("click", () => {
      cmtOnlyRelevant = !cmtOnlyRelevant; cmtPage = 0;
      relBtn.setAttribute("aria-pressed", String(cmtOnlyRelevant));
      renderComments(e2);
    });
    const recBtn = $("#cmtOnlyRecent");
    if (recBtn) recBtn.addEventListener("click", () => {
      cmtOnlyRecent = !cmtOnlyRecent; cmtPage = 0;
      recBtn.setAttribute("aria-pressed", String(cmtOnlyRecent));
      renderComments(e2);
    });
    const prev = $("#cmtPrev"), next = $("#cmtNext");
    if (prev) prev.addEventListener("click", () => { if (cmtPage > 0) { cmtPage--; renderComments(e2); } });
    if (next) next.addEventListener("click", () => { cmtPage++; renderComments(e2); });
    const exp = $("#cmtExport");
    if (exp) exp.addEventListener("click", () => exportCSV(e2));
  }

  function exportCSV(e) {
    const rows = [["序号", "平台", "点赞", "情感", "是否与事件相关", "是否近30天", "发布时间",
                   "作者", "IP", "评论", "来源"]];
    filteredComments(e).forEach((c, i) => {
      rows.push([i + 1, pname(c.platform).name, c.likes || 0, c.sentiment || "",
        c.relevant ? "是" : "否",
        c.recent ? "是" : "否",
        String(c.time || "").slice(0, 16),
        c.author || "", c.ip || "", (c.text || "").replace(/\n/g, " "), c.sourceUrl || ""]);
    });
    const csv = "\ufeff" + rows.map((r) =>
      r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `CATCH_热评_${e.id}_${(e.keyword || "").replace(/[^\w\u4e00-\u9fa5]/g, "")}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
  }

  /* ── 6. 选题雷达 ─────────────────────────────────────────────────────── */
  function renderRadar() {
    const box = $("#radarGrid");
    if (!box) return;
    const items = EVENTS.map((e) => {
      const s = e.sentiment || {};
      const heat = Math.min(100, Math.round(Math.log10((e.heat || 1000) + 10) * 13));
      const controversy = Math.min(100, Math.round((s.negative || 0) * 0.9 + (s.anger || 0) * 1.6 + 12));
      const resonance = Math.min(100, (e.platforms || []).length * 26);
      const sample = Math.min(100, Math.round(((e.commentCount || (e.comments || []).length) / 1000) * 100));
      const score = Math.round(heat * 0.3 + controversy * 0.28 + resonance * 0.24 + sample * 0.18);
      return { e, heat, controversy, resonance, sample, score };
    }).sort((a, b) => b.score - a.score);

    box.innerHTML = items.map((it) => `
      <article class="radar">
        <div class="spread">
          <span class="sc">${it.score}<small>/100</small></span>
          <span class="chip ${it.score >= 70 ? "chip-navy" : ""}">${it.score >= 70 ? "优先采写" : "可跟进"}</span>
        </div>
        <h4>${esc(it.e.title.length > 30 ? it.e.title.slice(0, 30) + "…" : it.e.title)}</h4>
        <div class="bars">
          <div class="b"><span>热度</span><span class="track"><i style="width:${it.heat}%"></i></span><span>${it.heat}</span></div>
          <div class="b"><span>争议度</span><span class="track"><i style="width:${it.controversy}%"></i></span><span>${it.controversy}</span></div>
          <div class="b"><span>跨平台</span><span class="track"><i style="width:${it.resonance}%"></i></span><span>${it.resonance}</span></div>
          <div class="b"><span>样本</span><span class="track"><i style="width:${it.sample}%"></i></span><span>${it.sample}</span></div>
        </div>
        <ul class="angles">
          <li>情绪结构：消极 ${it.e.sentiment.negative || 0}% / 愤怒 ${it.e.sentiment.anger || 0}% 的成因拆解</li>
          <li>平台差异：${(it.e.platforms || []).map((p) => pname(p).name).join(" / ")} 的叙事分化</li>
          <li>方法建议：内容分析 + 议程设置分析，辅以框架理论</li>
        </ul>
      </article>`).join("");
  }

  /* ── 7. 方法论 ───────────────────────────────────────────────────────── */
  function renderMethod() {
    const box = $("#methodBody");
    if (!box) return;
    const st = META.platformStatus || {};
    const rows = Object.keys(st).map((k) => {
      const s = st[k] || {};
      return `<tr>
        <td class="nowrap">${pname(k).emoji} ${esc(pname(k).name)}</td>
        <td>${esc(s.source || "—")}</td>
        <td class="nowrap">${s.items || 0} 条</td>
        <td class="nowrap">${s.ok ? "正常" : (s.needs_login ? "需重新登录" : "降级")}</td>
      </tr>`;
    }).join("");
    box.innerHTML = `
      <div class="grid-2">
        <div>
          <div class="cap" style="margin-bottom:10px">采集链路</div>
          <p class="lede">CATCH 每日 09:00 触发一次全平台采集，采集结果落盘为结构化 JSON；
            前端不依赖任何外部 CDN，所有图表（走势图、词云、情感堆叠条）均由本地代码绘制，
            断网亦可完整呈现最新一期数据。</p>
          <p class="lede">舆情事件由「跨平台共振聚类」自动生成：只有同一实体在两个及以上平台同时出现，
            才会被判定为事件；单平台高热条目则进入热点榜并单独标注。</p>
        </div>
        <div>
          <div class="cap" style="margin-bottom:10px">指标定义</div>
          <table class="tbl">
            <tbody>
              <tr><td>综合热度</td><td>各平台原始热度（万/亿单位归一）加总</td></tr>
              <tr><td>风险指数</td><td>消极占比 ×1.6 + 愤怒占比 ×2.2 + 18 基线</td></tr>
              <tr><td>情感判定</td><td>规则法：情绪词典 + 程度副词 + 否定翻转</td></tr>
              <tr><td>词云权重</td><td>jieba TF-IDF，叠加语义极性着色</td></tr>
              <tr><td>共振指数</td><td>共享实义 token 的平台数量</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="hr"></div>
      <div class="cap" style="margin-bottom:10px">数据源清单</div>
      <table class="tbl">
        <thead><tr><th>平台</th><th>采集入口</th><th>本轮条目</th><th>状态</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="hr"></div>
      <div class="grid-2">
        <div>
          <div class="cap" style="margin-bottom:10px">伦理与局限</div>
          <ul class="angles">
            <li>仅采集公开可见内容，不涉及任何账号操作与个人信息再识别。</li>
            <li>热评样本保留原平台链接，便于复核与引用；导出 CSV 时附带来源 URL。</li>
            <li>走势图中空心点为扩散模型估算基线，随每小时采集逐步被实测点替换。</li>
            <li>平台榜单口径不一（热度值非统一标准），故仅做同平台内比较与序位解读。</li>
            <li>热评样本按热度排序取样，并非全量抓取；未公开评论不可得，结论不宜外推至全网。
              样本相关性与事件的相关性由「源视频标题是否谈到该事件」判定
              （标题含事件关键词，或与该事件任一触发条目共享 ≥4 字连续片段），
              页面标注每事件的相关条数与比例，并可一键只看相关样本。</li>
            <li>网络流行语由系统自行归纳：语料为近 7 天 5 个平台公开热榜的逐日归档，
              先用四条结构判据（跨 ≥2 天复现、跨 ≥2 句式或平台复现、非硬新闻语境、
              与教学 / 挑战 / 转场 / 穿搭 / 治愈 / 翻唱等可复用句式共现）
              区分「可复用表达」与「一次性新闻事件」，
              再由百度百科开放 API 逐词仲裁类别并提供释义；
              无百科词条的新词依其真实热榜语境归纳，页面逐条标注释义来源。
              不引用任何年度榜单，因此「提及量」指该词在所采平台热榜条目中的出现次数，
              不等于全网发帖量；作品衍生的梗需以梗语境为主才收录，可能存在漏收。</li>
          </ul>
        </div>
        <div>
          <div class="cap" style="margin-bottom:10px">引用格式</div>
          <p class="lede">CATCH 舆情监测系统. ${esc(META.dateLabel || "")}，
            采集时间 ${esc((META.generatedAt || "").replace("T", " ").slice(0, 19))}（UTC+8）。
            数据来自微博、抖音、小红书、X、知乎、新闻与哔哩哔哩公开榜单。</p>
          <p class="lede">建议在论文附录中同时提供原始榜单快照与本系统导出的热评 CSV，
            以满足可重复性（reproducibility）要求。</p>
        </div>
      </div>`;
  }

  /* ── 交互：快捷键 / 打印 ─────────────────────────────────────────────── */
  function wireControls() {
    const printBtn = $("#printBtn");
    if (printBtn) printBtn.addEventListener("click", () => window.print());
    const topBtn = $("#topBtn");
    if (topBtn) topBtn.addEventListener("click", () =>
      window.scrollTo({ top: 0, behavior: "smooth" }));
    wireRefresh();

    document.addEventListener("keydown", (ev) => {
      if (ev.target && /input|textarea/i.test(ev.target.tagName)) return;
      if (ev.key === "ArrowDown" || ev.key === "j") stepEvent(1);
      if (ev.key === "ArrowUp" || ev.key === "k") stepEvent(-1);
    });
  }

  /* ── 刷新数据：点击后由本机真的跑一遍采集，装配完成自动重载页面 ────── */
  let refreshTimer = null;
  let refreshWasRunning = false;
  function refreshElapsed(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m ? `${m} 分 ${s} 秒` : `${s} 秒`;
  }
  function setRefreshLabel(text, busy) {
    const b = $("#refreshBtn");
    if (!b) return;
    b.textContent = text;
    b.setAttribute("aria-busy", String(!!busy));
    b.disabled = !!busy;
  }
  function wireRefresh() {
    const btn = $("#refreshBtn");
    if (!btn) return;
    const st0 = $("#refreshState");
    const host = location.hostname;
    const isLocal = location.protocol === "file:" ||
      host === "localhost" || host === "127.0.0.1" || host === "[::1]";
    if (!isLocal) {
      // 公网静态站点不能安全地把 GitHub 写权限放进浏览器。
      // 但“刷新”仍然真实执行：重新请求带时间戳的页面和最新 data.js，
      // 读取云端最近一次已发布快照，而不是只做视觉上的 reload。
      btn.textContent = "刷新云端数据";
      if (st0) st0.textContent = "云端每 6 小时采集；点击后读取最近一次成功发布的快照。";
      btn.addEventListener("click", () => {
        if (st0) st0.textContent = "正在读取云端最新快照…";
        btn.disabled = true;
        const u = new URL(window.location.href);
        u.searchParams.set("refresh", Date.now());
        window.location.replace(u.toString());
      });
      // 所有访客打开后每 5 分钟检查一次新快照；发现 generatedAt 变化就自动重载。
      setTimeout(async () => {
        try {
          const r = await fetch("assets/js/data.js?fresh=" + Date.now(),
                                { cache: "no-store" });
          const text = await r.text();
          const m = text.match(/"generatedAt"\s*:\s*"([^"]+)"/);
          if (m && META.generatedAt && m[1] !== META.generatedAt) {
            window.location.reload();
          }
        } catch (_) { /* 静态站点暂时不可达时保留当前快照 */ }
      }, 5 * 60 * 1000);
      return;
    }
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      setRefreshLabel("正在启动采集…", true);
      const st = $("#refreshState");
      if (st) st.textContent = "已请求本机采集：8 平台热榜 + 每事件 1000 条真实热评，完成后页面自动更新。";
      try {
        const r = await fetch("/api/refresh?mode=full", { method: "POST" });
        const j = await r.json();
        if (!j.started) {
          if (st) st.textContent = j.reason || "采集已在运行中，请等待当前这轮结束。";
          setRefreshLabel("刷新数据", false);
          pollRefresh();
          return;
        }
        pollRefresh();
      } catch (e) {
        if (st) {
          st.textContent = location.protocol === "file:"
            ? "本页是以 file:// 直接打开的，浏览器不允许它访问本机采集服务。请改用 http://localhost:8900/index.html 打开。"
            : "无法连接本地采集服务（tools/serve.py 是否在运行？）。";
        }
        setRefreshLabel("刷新数据", false);
      }
    });
    pollRefresh(true);
  }
  function pollRefresh(initial) {
    const btn = $("#refreshBtn");
    const st = $("#refreshState");
    const tick = async () => {
      let j = null;
      try {
        const r = await fetch("/api/refresh/status", { cache: "no-store" });
        j = await r.json();
      } catch (e) {
        if (btn) btn.disabled = false;
        return;
      }
      if (j.state === "running") {
        refreshWasRunning = true;
        setRefreshLabel(`刷新中 · ${refreshElapsed(j.elapsedSec || 0)}`, true);
        if (st) {
          st.textContent = `正在${j.step || "采集"}　已运行 ${refreshElapsed(j.elapsedSec || 0)}` +
            (j.log && j.log.length ? `　最近：${(j.log[j.log.length - 1] || "").slice(0, 80)}` : "");
        }
        return;
      }
      if (btn) btn.disabled = false;
      if (refreshWasRunning) {
        refreshWasRunning = false;
        if (j.state === "done") {
          if (st) st.textContent = "本轮采集完成，正在重载页面…";
          setRefreshLabel("已刷新", false);
          setTimeout(() => window.location.reload(), 700);
          return;
        }
        if (j.state === "error") {
          if (st) st.textContent = `采集失败：${j.error || "未知错误"}`;
          setRefreshLabel("刷新数据", false);
          return;
        }
      }
      if (initial && st && !st.textContent && j.finishedAt) {
        st.textContent = `上一轮采集：${String(j.finishedAt).replace("T", " ").slice(0, 16)}` +
          (j.ok ? `（成功，耗时 ${refreshElapsed(j.elapsedSec)}）`
                 : j.state === "interrupted" ? "（被中断）" : "（失败）");
      }
    };
    tick();
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(tick, 3000);
  }
  function stepEvent(d) {
    if (!EVENTS.length) return;
    curEvent = (curEvent + d + EVENTS.length) % EVENTS.length;
    cmtPage = 0;
    renderEventNav(); renderEventDetail();
  }

  function initNavHighlight() {
    const links = $$(".utility-nav a");
    const secs = links.map((a) => $(a.getAttribute("href"))).filter(Boolean);
    if (!secs.length) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        links.forEach((l) =>
          l.classList.toggle("on", l.getAttribute("href") === "#" + en.target.id));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    secs.forEach((s) => io.observe(s));
  }

  /* ── boot ────────────────────────────────────────────────────────────── */
  function boot() {
    renderUtility(); renderClock(); renderMasthead();
    renderBoardTabs(); renderBoard();
    renderHotspots();
    renderSlang();
    renderEventNav(); renderEventDetail(true);
    renderRadar(); renderMethod();
    wireControls();
    initNavHighlight();
    bumpStats({
      items: (META.counts || {}).rawScanned || 0,
      comments: (META.counts || {}).comments || 0,
      events: (META.counts || {}).events || 0,
    });

    window.addEventListener("resize", () => {
      const e = EVENTS[curEvent];
      if (e) { drawWordCloud(e.wordcloud || []); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
