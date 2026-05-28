/* Sentiment-scan history viewer — Phase 10.
 *
 * Parses /sentiment-scan/<DATE>/<code> from the URL, fetches:
 *   GET /api/sentiment-scan/<DATE>/<code>                      → metadata
 *   GET /api/sentiment-scan/<DATE>/<code>/reports/<name>       → markdown body
 * and renders five report tabs with marked + DOMPurify. No SPA — plain vanilla JS.
 */
(function () {
  "use strict";

  // The 5 report sections served by the API. Order = tab order. The display
  // label is what the user sees; the key matches the on-disk filename
  // (without .md) AND the whitelist enforced by the backend route.
  var REPORTS = [
    { key: "final_trade_decision",   label: "终极交易决策" },
    { key: "investment_plan",        label: "研究团队决策" },
    { key: "trader_investment_plan", label: "交易员计划" },
    { key: "fundamentals_report",    label: "基本面分析" },
    { key: "news_report",            label: "新闻分析" }
  ];

  // ─── URL parsing ────────────────────────────────────────────────────────
  var pathMatch = window.location.pathname.match(
    /^\/sentiment-scan\/(\d{4}-\d{2}-\d{2})\/([A-Za-z0-9.]{1,12})\/?$/
  );
  if (!pathMatch) {
    document.getElementById("viewer-title").textContent = "URL 无效";
    document.getElementById("viewer-body").textContent =
      "路径应为 /sentiment-scan/YYYY-MM-DD/<股票代码>";
    document.getElementById("viewer-body").classList.remove("loading");
    return;
  }
  var date = pathMatch[1];
  var code = pathMatch[2];

  document.title = code + " · " + date + " · 情绪扫描历史";

  // ─── DOM refs ───────────────────────────────────────────────────────────
  var titleEl = document.getElementById("viewer-title");
  var metaEl  = document.getElementById("viewer-meta");
  var fundEl  = document.getElementById("viewer-fund");
  var tabsEl  = document.getElementById("viewer-tabs");
  var bodyEl  = document.getElementById("viewer-body");

  // ─── helpers ────────────────────────────────────────────────────────────
  function dash(v) {
    if (v === null || v === undefined || v === "") return "—";
    return v;
  }

  function fmtPe(v) {
    if (v === null || v === undefined) return "—";
    var n = parseFloat(v);
    if (isNaN(n)) return "—";
    return n.toFixed(1);
  }

  function fmtRoe(v) {
    if (v === null || v === undefined) return "—";
    var n = parseFloat(v);
    if (isNaN(n)) return "—";
    return (n * 100).toFixed(1) + "%";
  }

  function fmtFcf(v, currency) {
    if (v === null || v === undefined) return "—";
    var n = parseFloat(v);
    if (isNaN(n)) return "—";
    var sign = n < 0 ? "-" : "";
    var a = Math.abs(n);
    var sym, big, mid, bigLabel, midLabel;
    if (currency === "CNY") {
      sym = "¥"; big = 1e8; mid = 1e4; bigLabel = "亿"; midLabel = "万";
    } else if (currency === "HKD") {
      sym = "HK$"; big = 1e9; mid = 1e6; bigLabel = "B"; midLabel = "M";
    } else {
      sym = "$"; big = 1e9; mid = 1e6; bigLabel = "B"; midLabel = "M";
    }
    if (a >= big) return sign + sym + (a / big).toFixed(1) + bigLabel;
    if (a >= mid) return sign + sym + (a / mid).toFixed(1) + midLabel;
    return sign + sym + Math.round(a);
  }

  function tierLabel(t) {
    return ({
      "triple":  "⭐⭐⭐ 三源命中",
      "ab_only": "⭐⭐ 飙升榜 ∩ 龙虎榜",
      "ac_only": "⭐⭐ 飙升榜 ∩ 雪球",
      "bc_only": "⭐⭐ 龙虎榜 ∩ 雪球"
    })[t] || t || "—";
  }

  function renderMarkdown(md) {
    // marked + DOMPurify are vendored as globals in the host page.
    if (window.marked && window.DOMPurify) {
      var html = window.marked.parse(md, { breaks: true, gfm: true });
      return window.DOMPurify.sanitize(html);
    }
    // Fallback: render as preformatted text. Still readable, no XSS risk.
    var pre = document.createElement("pre");
    pre.className = "viewer-raw";
    pre.textContent = md;
    return pre.outerHTML;
  }

  // ─── data fetch + render ────────────────────────────────────────────────
  function showError(title, body) {
    titleEl.textContent = title;
    metaEl.textContent = "";
    fundEl.textContent = "";
    tabsEl.innerHTML = "";
    bodyEl.classList.remove("loading");
    bodyEl.innerHTML = '<div class="viewer-empty">' +
      String(body).replace(/[&<>]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
      }) +
      '<span class="hint">数据来源：~/.tradingagents/sentiment-scan/reports/' + date + '/' + code + '/</span>' +
      '</div>';
  }

  // Render the tab strip + initial body content once metadata + report_paths
  // are known. Clicking a tab fetches that one report's markdown.
  //
  // Codex C2: the metadata block is rebuilt with textContent / createTextNode
  // ONLY — never innerHTML — because some fields (decision.summary_1line,
  // meta.name) are LLM-derived and could carry HTML-shaped content (or
  // future prompt injection). DOMPurify is only wired up for the markdown
  // body; metadata bypassed it before this fix.
  // Whitelist of legal action strings → badge CSS class. Anything outside
  // the whitelist falls through to "neutral" so an attacker-controlled
  // value can never set an arbitrary className.
  var ACTION_BADGE_CLASS = { "BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD" };
  function renderShell(meta) {
    titleEl.textContent = code + " " + (meta.name || "");
    var statusZh = ({
      "ok":               "完整",
      "partial":          "部分",
      "incomplete":       "未产出决策",
      "timeout":          "超时",
      "error":            "失败",
      "budget_exhausted": "未分析"
    })[meta.status] || meta.status;
    var decision = meta.decision || {};

    // Build the metadata line element-by-element using only safe DOM APIs.
    while (metaEl.firstChild) metaEl.removeChild(metaEl.firstChild);
    metaEl.appendChild(document.createTextNode(
      date + " · " + tierLabel(meta.tier) + " · 状态：" + statusZh + " · "
    ));
    if (decision.rating) {
      metaEl.appendChild(document.createTextNode("评级 " + decision.rating + " · "));
    }
    if (decision.action) {
      var badge = document.createElement("span");
      // className from a whitelist — never from user/LLM data.
      var safeClass = ACTION_BADGE_CLASS[decision.action] || "neutral";
      badge.className = "badge " + safeClass;
      // textContent escapes HTML; even if decision.action is "<img onerror>"
      // it appears as literal text, not as DOM.
      badge.textContent = decision.action;
      metaEl.appendChild(badge);
    }
    if (decision.summary_1line) {
      metaEl.appendChild(document.createTextNode(decision.summary_1line));
    }

    var f = meta.fundamentals || {};
    fundEl.textContent =
      "PE " + fmtPe(f.pe_ttm) +
      " · 远期PE " + fmtPe(f.pe_forward) +
      " · ROE " + fmtRoe(f.roe) +
      " · FCF " + fmtFcf(f.fcf, f.currency) +
      (f.as_of ? "  (as of " + f.as_of + ")" : "");

    var paths = meta.report_paths || {};
    tabsEl.innerHTML = "";
    var activeKey = null;
    REPORTS.forEach(function (rpt) {
      var btn = document.createElement("button");
      btn.className = "viewer-tab";
      btn.textContent = rpt.label;
      btn.dataset.key = rpt.key;
      if (!paths[rpt.key]) {
        btn.classList.add("missing");
        btn.title = "该报告未生成";
      } else if (activeKey === null) {
        activeKey = rpt.key;
        btn.classList.add("active");
      }
      btn.addEventListener("click", function () {
        if (btn.classList.contains("missing")) return;
        Array.prototype.forEach.call(tabsEl.children, function (n) {
          n.classList.remove("active");
        });
        btn.classList.add("active");
        loadReport(rpt.key);
      });
      tabsEl.appendChild(btn);
    });

    if (activeKey) {
      loadReport(activeKey);
    } else {
      bodyEl.classList.remove("loading");
      bodyEl.innerHTML =
        '<div class="viewer-empty">未生成任何完整报告' +
        '<span class="hint">' +
        (meta.status === "ok" || meta.status === "partial"
          ? "可能是磁盘写入失败，请检查 LaunchAgent 日志"
          : "该股票分析未成功完成") +
        '</span></div>';
    }
  }

  function loadReport(key) {
    bodyEl.classList.add("loading");
    bodyEl.textContent = "加载 " + key + "…";
    fetch("/api/sentiment-scan/" + encodeURIComponent(date) +
          "/" + encodeURIComponent(code) +
          "/reports/" + encodeURIComponent(key))
      .then(function (r) {
        if (!r.ok) {
          if (r.status === 404) {
            bodyEl.classList.remove("loading");
            bodyEl.innerHTML = '<div class="viewer-empty">报告未找到</div>';
            return null;
          }
          throw new Error("HTTP " + r.status);
        }
        return r.text();
      })
      .then(function (md) {
        if (md === null) return;
        bodyEl.classList.remove("loading");
        bodyEl.innerHTML = renderMarkdown(md);
      })
      .catch(function (err) {
        bodyEl.classList.remove("loading");
        bodyEl.innerHTML = '<div class="viewer-empty">加载失败：' +
          String(err.message || err) + '</div>';
      });
  }

  // Kick off: fetch ticker metadata
  fetch("/api/sentiment-scan/" + encodeURIComponent(date) +
        "/" + encodeURIComponent(code))
    .then(function (r) {
      if (r.status === 404) {
        showError("未找到", "该日期 " + date + " 没有 " + code + " 的分析记录");
        return null;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (meta) {
      if (meta) renderShell(meta);
    })
    .catch(function (err) {
      showError("加载失败", String(err.message || err));
    });
})();
