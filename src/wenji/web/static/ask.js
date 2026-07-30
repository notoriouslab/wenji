// 自由問答 /ask 頁面（唯一入口；0.6.1 起側欄 panel 退役）。
//
// 傳輸優先序：SSE (`GET /api/ask/stream`) → 失敗或 503 時退回 `POST /api/ask`。
// 對話歷史留在前端（伺服器 stateless），每次請求帶上前幾輪讓伺服器改寫追問。
(function () {
  'use strict';

  var MAX_TURNS = 10; // 與伺服器端 MAX_HISTORY_TURNS 一致，多送會被 400 拒絕
  var NO_ANSWER = '資料中未提及';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // --- citations ----------------------------------------------------------

  function renderCitations(target, citations) {
    // 引用欄只呈現最新一輪；舊輪答案裡的 [n] 錨點會指到新卡片，先退回純文字
    document.querySelectorAll('a.ask-cite-ref').forEach(function (a) {
      a.replaceWith(document.createTextNode(a.textContent));
    });
    target.innerHTML = '';
    // 引用欄在還沒問之前是空殼，用到才顯示
    var host = target.closest('.ask-col-citations');
    if (host) host.hidden = false;
    if (!citations || !citations.length) {
      target.appendChild(el('p', 'ask-empty', '這一題沒有找到可引用的段落。'));
      return;
    }
    var ol = document.createElement('ol');
    ol.className = 'ask-citations';
    citations.forEach(function (c, i) {
      var li = el('li', 'ask-citation');
      li.id = 'ask-cit-' + (i + 1);
      var idx = (c.chunk_indexes && c.chunk_indexes.length) ? c.chunk_indexes[0] : c.chunk_index;
      var a = document.createElement('a');
      a.className = 'ask-citation-title';
      a.href = '/article/' + encodeURIComponent(c.article_id) + '#c' + idx;
      // 開新分頁：對話狀態在前端記憶體，同分頁跳走再回來不保證還在
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = '[' + (i + 1) + '] ' + (c.title || c.article_id);
      li.appendChild(a);

      // 條文原文（0.6.0 新增）；沒有就退回文件級摘要
      var clauses = (c.chunk_texts && c.chunk_texts.length) ? c.chunk_texts : (c.snippet ? [c.snippet] : []);
      clauses.forEach(function (text) {
        var body = el('div', 'ask-citation-clause ask-citation-clamp', text);
        li.appendChild(body);
        // 只有真的被截斷才給展開鈕，避免出現按了沒反應的按鈕
        requestAnimationFrame(function () {
          if (body.scrollHeight - body.clientHeight < 4) return;
          var btn = el('button', 'ask-citation-toggle', '展開完整條文');
          btn.type = 'button';
          btn.addEventListener('click', function () {
            var open = body.classList.toggle('ask-citation-clamp');
            btn.textContent = open ? '展開完整條文' : '收合';
          });
          li.appendChild(btn);
        });
      });
      ol.appendChild(li);
    });
    target.appendChild(ol);
  }

  // 把答案裡的 [n] 標記換成指向引用卡片的錨點。走 text node 而非 innerHTML
  // regex：POST 後援路徑的答案是 narrative_html，字串替換會破壞既有標籤。
  function linkifyRefs(root, maxN) {
    var used = {};
    if (!maxN) return used;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      if (node.parentNode.closest('a')) return; // 已在連結內，不巢狀
      var text = node.nodeValue;
      if (text.indexOf('[') === -1) return;
      var re = /\[(\d{1,2})\]/g;
      var m;
      var last = 0;
      var frag = null;
      while ((m = re.exec(text))) {
        var n = Number(m[1]);
        if (n < 1 || n > maxN) continue;
        used[n] = true;
        if (!frag) frag = document.createDocumentFragment();
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var a = document.createElement('a');
        a.className = 'ask-cite-ref';
        a.href = '#ask-cit-' + n;
        a.textContent = m[0];
        frag.appendChild(a);
        last = m.index + m[0].length;
      }
      if (frag) {
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
      }
    });
    return used;
  }

  // --- one conversation ----------------------------------------------------

  function createSession(opts) {
    var history = [];

    function pushTurn(role, content) {
      history.push({ role: role, content: content });
      if (history.length > MAX_TURNS) history = history.slice(-MAX_TURNS);
    }

    function historyParam() {
      if (!history.length) return null;
      // btoa 只吃 latin1，中文要先 UTF-8 編碼
      var json = JSON.stringify(history);
      var bytes = new TextEncoder().encode(json);
      var bin = '';
      bytes.forEach(function (b) { bin += String.fromCharCode(b); });
      return btoa(bin);
    }

    function fallbackLink(question) {
      var a = document.createElement('a');
      a.className = 'ask-fallback-search';
      a.href = '/?q=' + encodeURIComponent(question);
      a.textContent = '改用搜尋看看相關規章';
      return a;
    }

    function showFailure(ctx, question, err) {
      ctx.answer.classList.remove('ask-streaming');
      ctx.answer.innerHTML = '<p class="ask-error">查詢失敗：' + esc(err.message || err) + '</p>';
      var actions = el('div', 'ask-answer-actions');
      actions.appendChild(fallbackLink(question));
      ctx.turn.appendChild(actions);
    }

    function finishTurn(ctx, question, answerText, citations) {
      pushTurn('user', question);
      if (answerText) pushTurn('assistant', answerText);
      ctx.answer.classList.remove('ask-streaming');

      var actions = el('div', 'ask-answer-actions');
      if (answerText) {
        var copy = el('button', 'ask-copy', '複製答案');
        copy.type = 'button';
        copy.addEventListener('click', function () {
          navigator.clipboard.writeText(answerText).then(function () {
            copy.textContent = '已複製';
            setTimeout(function () { copy.textContent = '複製答案'; }, 1500);
          });
        });
        actions.appendChild(copy);
      }
      // 情境化出口：只在答不出來時才提議改用搜尋
      var failed = !answerText || answerText.indexOf(NO_ANSWER) !== -1 || !citations || !citations.length;
      if (failed) actions.appendChild(fallbackLink(question));
      if (actions.childNodes.length) ctx.turn.appendChild(actions);

      // [n] 變錨點；沒被答案引用到的卡片弱化。模型完全沒標 [n] 時不弱化，
      // 否則整欄變灰反而誤導成「都沒用到」。
      var used = linkifyRefs(ctx.answer, (citations || []).length);
      var marked = Object.keys(used).length > 0;
      var cards = opts.citations.querySelectorAll('.ask-citation');
      Array.prototype.forEach.call(cards, function (li, i) {
        li.classList.toggle('ask-citation-unused', marked && !used[i + 1]);
      });
    }

    function newTurnNodes(question) {
      var turn = el('div', 'ask-turn');
      turn.appendChild(el('div', 'ask-turn-question', question));
      var answer = el('div', 'ask-answer markdown-body');
      turn.appendChild(answer);
      // 最新一輪放最上面：輸入框在頁面上方，追問後答案直接出現在眼前，
      // 不用捲到頁尾（追問歷史照樣往下翻）。
      opts.transcript.insertBefore(turn, opts.transcript.firstChild);
      turn.scrollIntoView({ block: 'nearest' });
      return { turn: turn, answer: answer };
    }

    function viaStream(question, ctx, axis, done) {
      var url = '/api/ask/stream?q=' + encodeURIComponent(question);
      if (axis) url += '&axis=' + encodeURIComponent(axis);
      var h = historyParam();
      if (h) url += '&history_b64=' + encodeURIComponent(h);

      var src = new EventSource(url);
      var text = '';
      var citations = [];
      var gotAnything = false;

      src.addEventListener('meta', function (e) {
        gotAnything = true;
        try { citations = JSON.parse(e.data).citations || []; } catch (_) { citations = []; }
        renderCitations(opts.citations, citations);
      });
      src.addEventListener('delta', function (e) {
        gotAnything = true;
        try { text += JSON.parse(e.data).text || ''; } catch (_) { /* skip frame */ }
        ctx.answer.textContent = text;
        ctx.answer.classList.add('ask-streaming');
      });
      src.addEventListener('error', function (e) {
        // 伺服器主動送的 error 事件（有 data）與連線層錯誤（無 data）分開處理
        if (e.data) {
          src.close();
          showFailure(ctx, question, new Error('查詢中斷，請稍後再試。'));
          done(true); // 已經顯示過失敗，不要再打一次 POST
          return;
        }
        src.close();
        if (gotAnything) { finishTurn(ctx, question, text, citations); done(true); }
        else done(false); // 連得上但什麼都沒拿到（例如 503）→ 交給 POST 後援
      });
      src.addEventListener('done', function (e) {
        src.close();
        // 串流過程是純文字，done 帶回伺服器渲染好的 markdown 整段換上
        try {
          var payload = JSON.parse(e.data || '{}');
          if (payload.narrative_html) ctx.answer.innerHTML = payload.narrative_html;
        } catch (_) { /* 保留純文字版本 */ }
        finishTurn(ctx, question, text, citations);
        done(true);
      });
    }

    function viaPost(question, ctx, axis) {
      var body = { q: question, k: 5 };
      if (axis) body.axis = axis;
      if (history.length) body.history = history;
      return fetch('/api/ask', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      }).then(function (r) {
        // 503 = 部署端沒配 LLM。同工看不懂環境變數名，訊息用使用者語言，
        // 技術細節留在 console（瀏覽器已自動記錄該回應）。
        if (r.status === 503) throw new Error('問答功能尚未啟用，請聯絡管理者；你仍可以改用搜尋。');
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
          return d;
        });
      }).then(function (data) {
        renderCitations(opts.citations, data.citations);
        if (data.narrative_html) ctx.answer.innerHTML = data.narrative_html;
        else ctx.answer.appendChild(el('p', 'ask-fallback', 'LLM 暫不可用，僅顯示引用來源。'));
        finishTurn(ctx, question, data.answer || '', data.citations);
      }).catch(function (err) {
        showFailure(ctx, question, err);
      });
    }

    function ask(question, axis) {
      if (!question) return;
      var ctx = newTurnNodes(question);
      ctx.answer.appendChild(el('p', 'ask-loading', '查詢中…'));
      opts.setBusy(true);

      var settled = false;
      function done(ok) {
        if (settled) return;
        settled = true;
        opts.setBusy(false);
        if (!ok) { ctx.answer.innerHTML = ''; viaPost(question, ctx, axis).then(function () { opts.setBusy(false); }); }
      }

      if (typeof EventSource === 'undefined') { done(false); return; }
      ctx.answer.innerHTML = '';
      viaStream(question, ctx, axis, done);
    }

    return { ask: ask };
  }

  // --- wiring -------------------------------------------------------------

  function wire(formId, textareaId, axisId, transcriptEl, citationsEl) {
    var form = document.getElementById(formId);
    if (!form || !transcriptEl || !citationsEl) return null;
    var q = document.getElementById(textareaId);
    var axisSel = document.getElementById(axisId);
    var submit = form.querySelector('.chat-submit');

    var session = createSession({
      transcript: transcriptEl,
      citations: citationsEl,
      setBusy: function (busy) {
        if (submit) submit.disabled = busy;
      },
    });

    // 維度下拉：規章站等單一語料部署沒有 axes，空的下拉是雜訊，直接隱藏
    if (axisSel) {
      fetch('/api/axes').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        var axes = (d && d.axes) || [];
        if (!axes.length) { axisSel.hidden = true; return; }
        axes.forEach(function (a) {
          var opt = document.createElement('option');
          opt.value = a.id;
          opt.textContent = a.id + (a.count ? ' (' + a.count + ')' : '');
          axisSel.appendChild(opt);
        });
      }).catch(function () { axisSel.hidden = true; });
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = (q.value || '').trim();
      if (!text) return;
      session.ask(text, axisSel ? axisSel.value : '');
      q.value = '';
      q.placeholder = '繼續追問…';
    });
    return { session: session, textarea: q, axis: axisSel };
  }

  document.addEventListener('DOMContentLoaded', function () {
    // 點 [n] 錨點時閃一下目標卡片，視線好跟
    document.addEventListener('click', function (e) {
      var ref = e.target.closest && e.target.closest('a.ask-cite-ref');
      if (!ref) return;
      var card = document.getElementById(ref.getAttribute('href').slice(1));
      if (!card) return;
      card.classList.remove('ask-citation-flash');
      requestAnimationFrame(function () { card.classList.add('ask-citation-flash'); });
    });

    var pageWired = wire(
      'ask-page-form', 'ask-page-q', 'ask-page-axis',
      document.getElementById('ask-transcript'),
      document.getElementById('ask-citations')
    );
    if (pageWired && window.WENJI_ASK_AUTOSUBMIT) {
      var initial = (pageWired.textarea.value || '').trim();
      if (initial) {
        pageWired.textarea.value = '';
        pageWired.textarea.placeholder = '繼續追問…';
        pageWired.session.ask(initial, '');
      }
    }
  });
})();
