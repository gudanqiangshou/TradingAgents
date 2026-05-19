// ---- Single-origin: FastAPI serves this page and the /api/* endpoints,
// so all requests use relative paths (no BACKEND_URL / config.js needed) ----

// Report/decision text is LLM- and news-derived and may contain raw HTML
// (e.g. <img onerror=...>). Never put marked output in the DOM unsanitized:
// an XSS here could read the access password from sessionStorage. All
// markdown -> HTML goes through this single chokepoint.
function safeHTML(md) {
  return DOMPurify.sanitize(marked.parse(md || ""));
}

// ---- State ----
const AGENT_TEAMS = [
  { label: "分析师团队", agents: ["Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst"] },
  { label: "研究团队",   agents: ["Bull Researcher", "Bear Researcher", "Research Manager"] },
  { label: "交易/风控/组合", agents: ["Trader", "Aggressive Analyst", "Neutral Analyst", "Conservative Analyst", "Portfolio Manager"] },
];

const AGENT_NAME_ZH = {
  "Market Analyst": "市场分析师",
  "Sentiment Analyst": "情绪分析师",
  "News Analyst": "新闻分析师",
  "Fundamentals Analyst": "基本面分析师",
  "Bull Researcher": "多头研究员",
  "Bear Researcher": "空头研究员",
  "Research Manager": "研究经理",
  "Trader": "交易员",
  "Aggressive Analyst": "激进分析师",
  "Neutral Analyst": "中性分析师",
  "Conservative Analyst": "保守分析师",
  "Portfolio Manager": "投资组合经理",
};

const SECTION_LABELS = {
  market_report: "市场分析报告",
  sentiment_report: "情绪分析报告",
  news_report: "新闻分析报告",
  fundamentals_report: "基本面分析报告",
  investment_plan: "研究团队决策",
  trader_investment_plan: "交易员计划",
  final_trade_decision: "投资组合经理决策",
};

const SECTION_TEAM = {
  market_report: "Market Analyst",
  sentiment_report: "Sentiment Analyst",
  news_report: "News Analyst",
  fundamentals_report: "Fundamentals Analyst",
  investment_plan: "Research Manager",
  trader_investment_plan: "Trader",
  final_trade_decision: "Portfolio Manager",
};

let state = { agentStatus: {}, reportSections: {}, jobId: null, es: null };

// ---- Init ----
window.addEventListener("DOMContentLoaded", () => {
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("date-input").value = today;
  document.querySelectorAll(".pill").forEach(btn => {
    btn.addEventListener("click", () => btn.classList.toggle("active"));
  });
  // Wire handlers here (no inline onclick) so a strict CSP can forbid
  // inline script and block any injected <script>/event-handler payload.
  // Auth is now a <form>; handle submit (covers Enter + the button) and
  // preventDefault so it never navigates/reloads.
  document.getElementById("auth-form").addEventListener("submit", e => {
    e.preventDefault();
    submitAuth();
  });
  document.getElementById("submit-btn").addEventListener("click", startAnalysis);
  document.getElementById("history-btn").addEventListener("click", openHistory);
  document.getElementById("history-close").addEventListener("click", closeHistory);
  document.getElementById("collapse-btn").addEventListener("click", toggleProgress);
  // Click the dark backdrop (outside the box) to close the history overlay.
  document.getElementById("history-overlay").addEventListener("click", e => {
    if (e.target.id === "history-overlay") closeHistory();
  });
  renderAgentList({});
  showDecisionCard("pending", null);
  // Show the password gate until the user has entered one this session.
  // This is UX only — the real enforcement is server-side on /api/analyze.
  if (!sessionStorage.getItem("ta_pw")) {
    document.getElementById("auth-overlay").classList.remove("hidden");
  }
  // After a refresh / reopen, reconnect to the last analysis if there was
  // one. The backend keeps a durable event buffer + the final report, so a
  // running job replays its progress and a finished job shows its result.
  restoreSession();
});

async function restoreSession() {
  const jobId = localStorage.getItem("ta_job");
  if (!jobId) return;
  let resp;
  try {
    resp = await fetch(`/api/report/${jobId}`, { headers: pwHeaders() });
  } catch {
    return;  // backend unreachable; leave the idle screen, user can retry
  }
  if (resp.status === 401) {
    return;  // need the password first; user enters it, can use 历史 instead
  }
  if (resp.status === 200) {
    // Job finished — render the stored report and its decision.
    const { content } = await resp.json();
    state.jobId = jobId;
    const cards = document.getElementById("report-cards");
    cards.innerHTML =
      '<div class="report-card"><div class="report-card-header">' +
      '<span class="report-card-title completed">✓ 上次分析报告（已完成）</span>' +
      '</div><div class="report-card-body">' + safeHTML(content) +
      '</div></div>';
    const m = content.match(/最终交易决策[\s\S]*?\*\*(BUY|SELL|HOLD)\*\*/);
    if (m) showDecisionCard("final", { action: m[1], raw: content });
    setStatus("已恢复上次分析结果", "");
    showDownloadButton(jobId);
    return;
  }
  let detail = "";
  try { detail = (await resp.json()).detail || ""; } catch {}
  if (detail.includes("尚未生成")) {
    // Job still running — replay buffered progress + continue live.
    state.jobId = jobId;
    setStatus("正在恢复进行中的分析...", "");
    connectSSE(jobId);
  } else {
    // Job unknown / expired (e.g. backend restarted) — forget it.
    localStorage.removeItem("ta_job");
  }
}

function submitAuth() {
  const val = document.getElementById("auth-input").value;
  if (!val) return;
  sessionStorage.setItem("ta_pw", val);
  document.getElementById("auth-error").textContent = "";
  document.getElementById("auth-overlay").classList.add("hidden");
}

function showAuthGate(message) {
  sessionStorage.removeItem("ta_pw");
  const err = document.getElementById("auth-error");
  if (err) err.textContent = message || "";
  document.getElementById("auth-input").value = "";
  document.getElementById("auth-overlay").classList.remove("hidden");
}

// ---- UI helpers ----
function setStatus(msg, hint = "") {
  document.getElementById("status-text").textContent = msg;
  document.getElementById("status-hint").textContent = hint;
}

function toggleProgress() {
  const body = document.getElementById("progress-body");
  const btn = document.getElementById("collapse-btn");
  body.classList.toggle("collapsed");
  btn.textContent = body.classList.contains("collapsed") ? "▸" : "▾";
}

function renderAgentList(agentStatus) {
  const list = document.getElementById("agent-list");
  list.innerHTML = "";
  let total = 0, completed = 0;
  AGENT_TEAMS.forEach(team => {
    const visible = team.agents.filter(a => a in agentStatus || Object.keys(agentStatus).length === 0);
    if (visible.length === 0) return;
    const label = document.createElement("div");
    label.className = "agent-team-label";
    label.textContent = team.label;
    list.appendChild(label);
    team.agents.forEach(agent => {
      if (!(agent in agentStatus) && Object.keys(agentStatus).length > 0) return;
      const status = agentStatus[agent] || "pending";
      total++;
      if (status === "completed") completed++;
      const item = document.createElement("div");
      item.className = `agent-item ${status}`;
      item.id = `agent-${agent.replace(/ /g, "-")}`;
      item.innerHTML = `
        <span class="agent-dot ${status}"></span>
        <span class="agent-name ${status}">${AGENT_NAME_ZH[agent] || agent}</span>
        ${status === "completed" ? '<span class="agent-check">✓</span>' : ""}
        ${status === "in_progress" ? '<span class="agent-check" style="color:var(--amber)">···</span>' : ""}
      `;
      list.appendChild(item);
    });
  });
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  document.getElementById("progress-fill").style.width = pct + "%";
  document.getElementById("progress-label").textContent = `${completed} / ${total}`;
}

function updateAgentStatus(agent, status) {
  state.agentStatus[agent] = status;
  renderAgentList(state.agentStatus);
}

function upsertReportCard(section, content, status) {
  const cards = document.getElementById("report-cards");
  let card = document.getElementById(`card-${section}`);
  if (!card) {
    card = document.createElement("div");
    card.className = "report-card";
    card.id = `card-${section}`;
    card.innerHTML = `
      <div class="report-card-header">
        <span class="report-card-title" id="title-${section}"></span>
        <span class="report-card-team">${SECTION_TEAM[section] || ""}</span>
      </div>
      <div class="report-card-body" id="body-${section}"></div>
    `;
    cards.appendChild(card);
  }
  const titleEl = document.getElementById(`title-${section}`);
  const bodyEl = document.getElementById(`body-${section}`);
  const isInProgress = status === "in_progress";
  card.className = `report-card ${isInProgress ? "in_progress" : ""}`;
  const label = SECTION_LABELS[section] || section;
  titleEl.className = `report-card-title ${isInProgress ? "in_progress" : "completed"}`;
  titleEl.textContent = isInProgress ? `⟳ ${label} 生成中` : `✓ ${label}`;
  bodyEl.innerHTML = safeHTML(content) + (isInProgress ? '<span class="cursor">▌</span>' : "");
}

function showDecisionCard(type, data) {
  const card = document.getElementById("decision-card");
  const actionEl = document.getElementById("decision-action");
  const detailEl = document.getElementById("decision-detail");
  card.classList.remove("hidden", "BUY", "SELL", "HOLD", "pending");
  if (type === "pending") {
    card.classList.add("pending");
    actionEl.textContent = "等待分析完成...";
    detailEl.textContent = "";
  } else {
    card.classList.add(data.action);
    actionEl.textContent = data.action;
    detailEl.innerHTML = safeHTML((data.raw || "").slice(0, 800));
  }
}

// ---- Analysis flow ----
async function startAnalysis() {
  const ticker = document.getElementById("ticker-input").value.trim().toUpperCase();
  const date = document.getElementById("date-input").value;
  const analysts = [...document.querySelectorAll(".pill.active")].map(p => p.dataset.analyst);
  const language = document.getElementById("language-select").value;

  if (!ticker) { alert("请输入股票代码"); return; }
  if (!date) { alert("请选择分析日期"); return; }
  if (analysts.length === 0) { alert("请至少选择一个分析师"); return; }

  document.getElementById("submit-btn").disabled = true;
  document.getElementById("report-cards").innerHTML = "";
  state = { agentStatus: {}, reportSections: {}, jobId: null, es: null };
  renderAgentList({});
  showDecisionCard("pending", null);

  let resp;
  try {
    resp = await fetch(`/api/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Access-Password": sessionStorage.getItem("ta_pw") || "",
      },
      body: JSON.stringify({ ticker, date, analysts, language }),
    });
  } catch {
    setStatus("无法连接到分析服务器");
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  if (resp.status === 401) {
    document.getElementById("submit-btn").disabled = false;
    showAuthGate("口令错误，请重新输入");
    return;
  }

  if (resp.status === 429) {
    document.getElementById("busy-overlay").classList.remove("hidden");
    setTimeout(() => document.getElementById("busy-overlay").classList.add("hidden"), 3000);
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  if (!resp.ok) {
    setStatus(`启动失败: ${resp.status}`);
    document.getElementById("submit-btn").disabled = false;
    return;
  }

  const { job_id } = await resp.json();
  state.jobId = job_id;
  // Remember the job so a refresh / reopen can reconnect to it. Overwritten
  // by the next analysis; cleared only if the backend later forgets the job.
  localStorage.setItem("ta_job", job_id);
  setStatus(`分析中 · ${ticker}`,
    "分析师越多越久（1个约5分钟，4个约15-25分钟）。可关闭页面，完成后到「历史」查看。");
  connectSSE(job_id);
}

function connectSSE(jobId) {
  const es = new EventSource(`/api/stream/${jobId}`);
  state.es = es;

  es.addEventListener("agent_status", e => {
    const { agent, status } = JSON.parse(e.data);
    updateAgentStatus(agent, status);
  });

  es.addEventListener("report_section", e => {
    const { section, content } = JSON.parse(e.data);
    state.reportSections[section] = content;
    const agentForSection = SECTION_TEAM[section];
    const agentStatus = state.agentStatus[agentForSection] || "in_progress";
    upsertReportCard(section, content, agentStatus === "completed" ? "completed" : "in_progress");
  });

  es.addEventListener("final_decision", e => {
    const data = JSON.parse(e.data);
    showDecisionCard("final", data);
    // Mark final_trade_decision card as completed
    if (state.reportSections["final_trade_decision"]) {
      upsertReportCard("final_trade_decision", state.reportSections["final_trade_decision"], "completed");
    }
  });

  es.addEventListener("done", () => {
    es.close();
    setStatus("分析完成", "");
    document.getElementById("submit-btn").disabled = false;
    // Mark all in-progress report cards as completed
    document.querySelectorAll(".report-card.in_progress").forEach(card => {
      card.classList.remove("in_progress");
      const section = card.id.replace("card-", "");
      const titleEl = document.getElementById(`title-${section}`);
      if (titleEl) {
        titleEl.className = "report-card-title completed";
        titleEl.textContent = `✓ ${SECTION_LABELS[section] || section}`;
      }
      const bodyEl = document.getElementById(`body-${section}`);
      const cursor = bodyEl && bodyEl.querySelector(".cursor");
      if (cursor) cursor.remove();
    });
    // Show download button
    showDownloadButton(state.jobId);
  });

  es.addEventListener("error", e => {
    es.close();
    let msg = "分析出错";
    try { msg = JSON.parse(e.data).message; } catch {}
    setStatus(`错误: ${msg}`);
    document.getElementById("submit-btn").disabled = false;
  });

  es.onerror = () => {
    // EventSource will auto-reconnect using Last-Event-ID
    setStatus("连接中断，正在重连...", "");
  };
}

async function showDownloadButton(jobId) {
  try {
    const resp = await fetch(`/api/report/${jobId}`, { headers: pwHeaders() });
    if (!resp.ok) return;
    const { content } = await resp.json();
    const btn = document.createElement("button");
    btn.className = "submit-btn";
    btn.style.cssText = "margin:8px 0;font-size:12px;padding:6px 16px;background:#374151";
    btn.textContent = "⬇ 下载完整报告";
    btn.onclick = () => {
      const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `report-${jobId.slice(0, 8)}.md`;
      a.click();
    };
    document.getElementById("report-cards").prepend(btn);
  } catch {}
}

// ---- History ----
function pwHeaders() {
  return { "X-Access-Password": sessionStorage.getItem("ta_pw") || "" };
}

async function openHistory() {
  const overlay = document.getElementById("history-overlay");
  const list = document.getElementById("history-list");
  list.innerHTML = '<div class="history-empty">加载中...</div>';
  overlay.classList.remove("hidden");
  let resp;
  try {
    resp = await fetch("/api/history", { headers: pwHeaders() });
  } catch {
    list.innerHTML = '<div class="history-empty">无法连接服务器</div>';
    return;
  }
  if (resp.status === 401) {
    overlay.classList.add("hidden");
    showAuthGate("口令错误，请重新输入");
    return;
  }
  const items = (await resp.json()).items || [];
  if (items.length === 0) {
    list.innerHTML = '<div class="history-empty">暂无历史记录</div>';
    return;
  }
  list.innerHTML = "";
  items.forEach(it => {
    const badge = ["BUY", "SELL", "HOLD"].includes(it.action) ? it.action : "NA";
    const when = (it.created_at || "").replace("T", " ").slice(0, 16);
    // Build via DOM API + textContent so server-supplied fields can never
    // inject markup (defence-in-depth even though they are constrained).
    const row = document.createElement("div");
    row.className = "history-item";
    const tk = document.createElement("span");
    tk.className = "history-tk";
    tk.textContent = it.ticker || "";
    const bd = document.createElement("span");
    bd.className = "history-badge " + badge;
    bd.textContent = it.action || "—";
    const meta = document.createElement("span");
    meta.className = "history-meta";
    meta.textContent = `分析日 ${it.date || ""} · ${when}`;
    row.append(tk, bd, meta);
    row.addEventListener("click", () => loadHistoryReport(it.id, it.action));
    list.appendChild(row);
  });
}

function closeHistory() {
  document.getElementById("history-overlay").classList.add("hidden");
}

async function loadHistoryReport(id, action) {
  let resp;
  try {
    resp = await fetch(`/api/history/${id}`, { headers: pwHeaders() });
  } catch {
    return;
  }
  if (resp.status === 401) {
    closeHistory();
    showAuthGate("口令错误，请重新输入");
    return;
  }
  if (!resp.ok) return;
  const { content } = await resp.json();
  closeHistory();
  const card = document.createElement("div");
  card.className = "report-card";
  const head = document.createElement("div");
  head.className = "report-card-header";
  const title = document.createElement("span");
  title.className = "report-card-title completed";
  title.textContent = `✓ 历史报告 · ${id}`;  // id is server-supplied; textContent
  head.appendChild(title);
  const body = document.createElement("div");
  body.className = "report-card-body";
  body.innerHTML = safeHTML(content);
  card.append(head, body);
  const host = document.getElementById("report-cards");
  host.innerHTML = "";
  host.appendChild(card);
  const m = content.match(/最终交易决策[\s\S]*?\*\*(BUY|SELL|HOLD)\*\*/);
  if (m) showDecisionCard("final", { action: m[1], raw: content });
  else if (["BUY", "SELL", "HOLD"].includes(action))
    showDecisionCard("final", { action, raw: content });
  setStatus(`已打开历史报告 · ${id}`, "");
}
