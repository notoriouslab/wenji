// Chat panel: single-turn aggregator (topic / concept).
// Each form submission is independent (no conversation history).

(function () {
  "use strict";

  const tabs = document.querySelectorAll(".chat-tab");
  const form = document.getElementById("aggregate-form");
  const queryInput = document.getElementById("chat-query");
  const labelEl = document.getElementById("chat-input-label");
  const hintEl = document.getElementById("chat-input-hint");
  const subtypeFilterEl = document.getElementById("chat-subtype-filter");
  const resultEl = document.getElementById("aggregate-result");

  if (!form || !queryInput || !resultEl) return;

  let mode = "topic";

  const COPY = {
    topic: {
      label: "主題",
      placeholder: "例：勞動、禱告",
      hint: "挑一個 tag 或關鍵字，從語料中找最相關的文章彙總。",
    },
    concept: {
      label: "跨文比較的概念",
      placeholder: "例：因信稱義",
      hint: "輸入一個概念，看不同來源如何詮釋（共識 / 分歧）。",
    },
  };

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      mode = tab.dataset.mode;
      const copy = COPY[mode];
      labelEl.textContent = copy.label;
      queryInput.placeholder = copy.placeholder;
      if (hintEl) hintEl.textContent = copy.hint;
    });
  });

  // Populate the subtype checkbox list from the server. Hidden gracefully
  // when the corpus has no subtype values.
  async function loadSubtypes() {
    if (!subtypeFilterEl) return;
    try {
      const resp = await fetch("/api/aggregate/subtypes");
      if (!resp.ok) return;
      const data = await resp.json();
      const subtypes = data.subtypes || [];
      if (subtypes.length === 0) return; // keep the "no subtype" message
      subtypeFilterEl.innerHTML = subtypes
        .map(
          (s) => `
        <label class="chat-checkbox">
          <input type="checkbox" name="exclude_subtype" value="${escapeHtml(s.name)}">
          <span>${escapeHtml(s.name)} <span class="chat-count">(${s.count})</span></span>
        </label>
      `
        )
        .join("");
    } catch (_) {
      // network failure — leave the placeholder text in place
    }
  }

  loadSubtypes();

  function collectExcludedSubtypes() {
    if (!subtypeFilterEl) return null;
    const checked = subtypeFilterEl.querySelectorAll(
      'input[name="exclude_subtype"]:checked'
    );
    if (checked.length === 0) return null;
    return Array.from(checked).map((el) => el.value);
  }

  function renderError(msg) {
    resultEl.innerHTML = '<p class="chat-error"></p>';
    resultEl.querySelector(".chat-error").textContent = msg;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function renderTopicResult(data) {
    const stats = data.statistics || {};
    const sources = (data.top_sources || []).map((s) => `
      <li>
        <a href="/article/${encodeURIComponent(s.article_id)}">${escapeHtml(s.title || "")}</a>
        <span class="chat-score">${s.bm25_score.toFixed(2)}</span>
        <div class="chat-snippet">${s.snippet || ""}</div>
      </li>
    `).join("");
    const narrative = data.narrative_html
      ? `<section class="chat-narrative">${data.narrative_html}</section>`
      : `<p class="chat-narrative-empty">（未配置 LLM 或失敗，僅顯示結構化結果）</p>`;
    resultEl.innerHTML = `
      ${narrative}
      <h4>Top sources</h4>
      <ol class="chat-sources">${sources}</ol>
      <p class="chat-stats">命中 ${stats.total_hits || 0} 篇</p>
    `;
  }

  function renderConceptResult(data) {
    const views = (data.per_source_views || []).map((v) => {
      const excerpts = (v.excerpts || []).map((e) => `<li>${escapeHtml(e)}</li>`).join("");
      const sr = v.source_ref || {};
      return `
        <li>
          <a href="/article/${encodeURIComponent(sr.article_id || "")}">${escapeHtml(sr.title || "")}</a>
          <ul class="chat-excerpts">${excerpts}</ul>
        </li>
      `;
    }).join("");
    const consensusItems = (data.consensus_html || []).map((c) => `<li>${c}</li>`).join("");
    const disagreementItems = (data.disagreements_html || []).map((c) => `<li>${c}</li>`).join("");
    const narrative = data.narrative_html
      ? `<section class="chat-narrative">${data.narrative_html}</section>`
      : `<p class="chat-narrative-empty">（未配置 LLM 或失敗，僅顯示結構化結果）</p>`;
    resultEl.innerHTML = `
      ${narrative}
      ${consensusItems ? `<h4>共識</h4><ul class="chat-consensus">${consensusItems}</ul>` : ""}
      ${disagreementItems ? `<h4>分歧</h4><ul class="chat-disagreements">${disagreementItems}</ul>` : ""}
      <h4>來源觀點</h4>
      <ol class="chat-sources">${views}</ol>
    `;
  }

  form.addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const query = (queryInput.value || "").trim();
    if (!query) return;

    resultEl.innerHTML = '<p class="chat-loading">讀取中...</p>';
    const exclude = collectExcludedSubtypes();
    let body;
    let url;
    if (mode === "topic") {
      url = "/api/aggregate/topic";
      body = { tag: query, k: 5 };
    } else {
      url = "/api/aggregate/concept";
      body = { concept: query, top_sources: 4, per_source: 3 };
    }
    if (exclude) body.filter = { subtype__not_in: exclude };

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const text = await resp.text();
        renderError(`HTTP ${resp.status}: ${text}`);
        return;
      }
      const data = await resp.json();
      if (mode === "topic") renderTopicResult(data);
      else renderConceptResult(data);
    } catch (err) {
      renderError("請求失敗：" + (err && err.message ? err.message : String(err)));
    }
  });
})();
