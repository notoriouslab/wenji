// 自由問答 panel — single-turn POST /api/ask + render answer + citations.
(function () {
  var form = document.getElementById('ask-form');
  if (!form) return;
  var qEl = document.getElementById('ask-q');
  var axisEl = document.getElementById('ask-axis');
  var resultEl = document.getElementById('ask-result');

  // Populate axis dropdown from /api/axes (best-effort; failure leaves only "全部").
  fetch('/api/axes')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.axes) return;
      data.axes.forEach(function (a) {
        var opt = document.createElement('option');
        opt.value = a.id;
        opt.textContent = a.id + (a.count ? ' (' + a.count + ')' : '');
        axisEl.appendChild(opt);
      });
    })
    .catch(function () { /* swallow */ });

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderCitations(citations) {
    if (!citations || !citations.length) return '';
    var items = citations.map(function (c, i) {
      var href = '/article/' + encodeURIComponent(c.article_id) + '#c' + c.chunk_index;
      return '<li><a href="' + href + '">[' + (i + 1) + '] ' +
        escapeHtml(c.title || c.article_id) + '</a>' +
        (c.snippet ? ' — <span class="ask-citation-snippet">' + c.snippet + '</span>' : '') +
        '</li>';
    });
    return '<h4>引用</h4><ol class="ask-citations">' + items.join('') + '</ol>';
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = (qEl.value || '').trim();
    if (!q) return;
    resultEl.innerHTML = '<p class="ask-loading">查詢中…</p>';
    var body = { q: q, k: 5 };
    if (axisEl.value) body.axis = axisEl.value;
    fetch('/api/ask', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (r.status === 503) {
          throw new Error('LLM 尚未設定（請設定 WENJI_LLM_* 環境變數）');
        }
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
          return data;
        });
      })
      .then(function (data) {
        var parts = [];
        if (data.narrative_html) {
          parts.push('<div class="ask-answer">' + data.narrative_html + '</div>');
        } else if (data.answer === null) {
          parts.push('<p class="ask-fallback">LLM 暫不可用，僅顯示檢索結果。</p>');
        }
        parts.push(renderCitations(data.citations));
        resultEl.innerHTML = parts.join('') ||
          '<p class="ask-empty">沒有找到相關段落。</p>';
      })
      .catch(function (err) {
        resultEl.innerHTML = '<p class="ask-error">查詢失敗：' +
          escapeHtml(err.message || err) + '</p>';
      });
  });
})();
