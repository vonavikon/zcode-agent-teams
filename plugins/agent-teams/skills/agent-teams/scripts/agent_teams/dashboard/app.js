/* Dashboard logic (spec v2.1, section 16). Reads a read-only API, keeps the
   user's context while data refreshes, and never touches team state. */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var TEAM = params.get("team") || "";
  var API = "?team=" + encodeURIComponent(TEAM);
  var POLL_MS = 3000;
  var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  /* Preferences live in the browser only. Storage can be unavailable in a
     private window; the page then simply starts with its defaults. */
  function readPref(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function writePref(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* a preference is not worth an error */ }
  }
  var store = { mode: readPref("at.mode", "simple"),
                theme: readPref("at.theme", prefersDark ? "dark" : "light") };

  var ICONS = {
    circle: '<circle cx="12" cy="12" r="8"/>',
    progress: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2.5"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.2-4.2"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.6 2.6L16 9.5"/>',
    alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.2v.1"/>',
    minus: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    hourglass: '<path d="M7 3h10M7 21h10M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9s10 4 10 9"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>'
  };
  function icon(name) { return '<svg class="i" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || ICONS.circle) + '</svg>'; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
  function $(id) { return document.getElementById(id); }
  function plural(n, one, few, many) {
    var last = n % 10, tenth = n % 100;
    if (last === 1 && tenth !== 11) return one;
    if (last >= 2 && last <= 4 && (tenth < 12 || tenth > 14)) return few;
    return many;
  }
  function fmtDur(s) {
    s = Math.max(0, s | 0);
    if (s < 60) return s + " с";
    if (s < 3600) return Math.floor(s / 60) + " мин";
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h + " ч " + (m ? m + " мин" : "");
  }
  function fmtClock(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
  }
  /* The avatar letter stands for the role ("AI-аналитик" -> "А"), never for
     an internal identifier. */
  function roleLetter(role) {
    var word = String(role || "").replace(/^AI-/, "").trim();
    return word ? word[0].toUpperCase() : "?";
  }
  function fileChip(name, bad) {
    return '<span class="file' + (bad ? " bad" : "") + '">' +
      (bad ? icon("alert") + "вне разрешённой зоны: " : "") + esc(name) + '</span>';
  }
  function join(parts) { return parts.filter(Boolean).join(" · "); }
  /* Technical fields as one "key: value" line each; empty fields are left
     out, nested ones stay JSON. Readable first, complete second. */
  function fmtTech(obj) {
    return Object.keys(obj || {}).filter(function (k) {
      var v = obj[k];
      return v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length);
    }).map(function (k) {
      var v = obj[k];
      return k + ": " + (v && typeof v === "object" ? JSON.stringify(v) : String(v));
    }).join("\n");
  }

  /* ---- theme & mode: persisted, never touch team state ---- */
  function applyPrefs() {
    document.documentElement.setAttribute("data-theme", store.theme);
    $("modeSimple").setAttribute("aria-pressed", String(store.mode === "simple"));
    $("modeAdvanced").setAttribute("aria-pressed", String(store.mode === "advanced"));
    Array.prototype.forEach.call(document.querySelectorAll(".tech"), function (el) {
      el.classList.toggle("hidden", store.mode !== "advanced");
    });
  }
  $("themeBtn").addEventListener("click", function () {
    store.theme = store.theme === "light" ? "dark" : "light";
    writePref("at.theme", store.theme); applyPrefs();
  });
  $("modeSimple").addEventListener("click", function () { store.mode = "simple"; writePref("at.mode", "simple"); applyPrefs(); load(); });
  $("modeAdvanced").addEventListener("click", function () { store.mode = "advanced"; writePref("at.mode", "advanced"); applyPrefs(); load(); });

  /* ---- connection states (spec 16.4) ---- */
  var conn = $("conn");
  function setConn(state, text) {
    conn.className = "conn" + (state ? " show " + state : "");
    conn.textContent = text || "";
  }

  /* ---- rendering ---- */
  var lastSnap = null, lastGood = null;
  function renderProgress(p) {
    // Numbers from the wire are forced to numbers before they reach the
    // markup; everything else goes through esc().
    var done = Number(p.done) || 0, active = Number(p.active) || 0;
    var attention = Number(p.needs_attention) || 0, total = Number(p.total) || 0;
    var pct = Number(p.percent) || 0, c = 2 * Math.PI * 52;
    var bar = $("ringBar");
    bar.style.strokeDasharray = c;
    bar.style.strokeDashoffset = c * (1 - pct / 100);
    $("ringPct").textContent = pct + "%";
    $("ringImg").setAttribute("aria-label", "Выполнено " + pct + " процентов задач");
    var story;
    if (total === 0) story = "В плане пока нет задач.";
    else if (total && done === total) story = "Все задачи выполнены. Результат проверен и готов.";
    else if (attention && active) story = "Ещё " + active + " " + plural(active, "задача", "задачи", "задач") + " в работе, " +
      (attention === 1 ? "одна ждёт вашего решения."
       : attention + " " + plural(attention, "задача", "задачи", "задач") + " ждут вашего решения.");
    else if (attention) story = attention === 1
      ? "Команда остановилась на задаче, которая требует вашего решения."
      : "Команда остановилась: " + attention + " " + plural(attention, "задача", "задачи", "задач") + " требуют вашего решения.";
    else if (active) story = "Команда работает. " + active + " " + plural(active, "задача", "задачи", "задач") + " в работе.";
    else story = "Задачи в очереди, исполнители скоро приступят.";
    $("storyLine").textContent = story;
    $("counters").innerHTML =
      '<span class="counter"><span class="dot done"></span>Завершено <b>' + done + '</b> из ' + total + '</span>' +
      '<span class="counter"><span class="dot busy"></span>Выполняется <b>' + active + '</b></span>' +
      (attention ? '<span class="counter"><span class="dot attention"></span>Требует внимания <b>' + attention + '</b></span>' : '');
  }
  function renderNow(tasks, cost) {
    var live = tasks.filter(function (t) { return t.active; });
    var html = live.map(function (t) {
      var since = t.silent_s == null ? "" : "последняя активность " + fmtDur(Number(t.silent_s) || 0) + " назад";
      var attempt = Number(t.attempt) || 0;
      var doing = t.silent ? "ожидается ответ исполнителя"
        : t.state === "На проверке" ? "результат передан на проверку качества"
        : "работает над задачей" + (attempt > 1 ? ", попытка " + attempt : "");
      return '<article class="now-card"><div class="avatar" aria-hidden="true">' + esc(roleLetter(t.role)) + '</div>' +
        '<div><h3>' + esc(t.title) + '</h3><div class="role">' + esc(t.role) + '</div>' +
        '<div class="doing">' + esc(doing) + '</div></div>' +
        '<div class="meta"><span class="state ' + esc(t.kind) + '">' + icon(t.icon) + esc(t.state) + '</span><div class="since">' + esc(since) + '</div></div>' +
        (t.violations && t.violations.length ? '<div class="files">' + t.violations.map(function (f) { return fileChip(f, true); }).join("") + '</div>' : '') +
        '</article>';
    }).join("");
    if (!live.length) {
      var anyDone = tasks.some(function (t) { return t.kind === "done"; });
      html = '<div class="empty">' + (anyDone ? "Сейчас никто не занят: активных задач нет." : "Исполнители ещё не приступали к задачам.") + '</div>';
    }
    if (cost) {
      var spend;
      if (!cost.tokens && !(cost.usd > 0)) spend = "данных о расходах пока нет";
      else if (cost.usd > 0) spend = (cost.complete ? "" : "не менее ") + "$" + cost.usd.toFixed(2);
      else spend = "не определены";
      html += '<div class="small muted cost">Затраты: <b>' + esc(spend) + '</b>' +
        (cost.complete || cost.usd > 0 ? "" : ' <span class="muted">(данные о ценах недоступны)</span>') +
        ' · <span class="nowrap">объём использования AI: ' + (Number(cost.tokens) || 0).toLocaleString("ru-RU") + '</span></div>';
    }
    $("nowList").innerHTML = html;
  }
  function renderAttention(items) {
    $("attCount").textContent = items.length;
    $("attCount").className = "pill" + (items.length ? " state attention" : "");
    $("attCard").className = "card" + (items.length ? " attention" : "");
    if (!items.length) { $("attList").innerHTML = '<div class="empty">Ничего не требует вашего решения. Команда справляется сама.</div>'; return; }
    $("attList").innerHTML = items.map(function (a) {
      return '<article class="attention-item ' + esc(a.kind) + '"><h3>' + icon(a.icon) + '<span>' + esc(a.title) + '</span></h3>' +
        (a.remarks ? '<div class="remarks"><b>Замечания проверяющего</b>' + esc(a.remarks) + '</div>' : '') +
        '<dl>' +
        '<dt>Что произошло</dt><dd>' + esc(a.what) + '</dd>' +
        '<dt>Почему это важно</dt><dd>' + esc(a.why) + '</dd>' +
        '<dt>Что система сделала</dt><dd>' + esc(a.tried) + '</dd>' +
        '<dt>Что нужно от вас</dt><dd>' + esc(a.action) + '</dd>' +
        '<dt>Если ничего не делать</dt><dd>' + esc(a.if_nothing) + '</dd></dl></article>';
    }).join("");
  }
  function renderPlan(tasks) {
    $("planList").innerHTML = tasks.map(function (t, i) {
      var bad = t.violations || [];
      var changed = t.changed_files || [];
      var files = changed.map(function (f) { return fileChip(f, bad.indexOf(f) >= 0); }).join("") +
        bad.filter(function (f) { return changed.indexOf(f) < 0; }).map(function (f) { return fileChip(f, true); }).join("");
      var deps = (t.deps || []).filter(Boolean);
      var failed = Number(t.failed_checks) || 0;
      return '<div class="stage ' + esc(t.kind) + '"><div class="mark">' + icon(t.icon) + '</div>' +
        '<div><div class="t">' + (i + 1) + '. ' + esc(t.title) + '</div><div class="s">' + esc(t.state_hint) +
        (deps.length ? ' · после: ' + esc(deps.join(", ")) : '') + '</div></div>' +
        '<div class="who">' + esc(t.role) + (failed ? '<br>' + failed + " " + plural(failed, "раз", "раза", "раз") + " возвращена на доработку" : "") + '</div>' +
        (files ? '<div class="files">' + files + '</div>' : '') +
        (store.mode === "advanced" && t.tech ? '<div class="tech hidden">' + esc(fmtTech(t.tech)) + '</div>' : '') +
        '</div>';
    }).join("") || '<div class="empty">План ещё не сформирован.</div>';
  }
  function renderFeed(events, knownSeq) {
    /* Decided before the list is redrawn: afterwards the distance to the
       newest line always includes the rows that were just added. */
    var stick = knownSeq === null || atBottom($("feed"));
    $("feed").innerHTML = events.map(function (e) {
      var isNew = knownSeq !== null && e.seq > knownSeq;
      var cls = "feed-item " + esc(e.kind || "normal") + (isNew ? " is-new" : "");
      var sub = join([e.specialist, e.task]);
      return '<div class="' + cls + '"><time>' + fmtClock(e.ts) + '</time><div class="mark" aria-hidden="true">' + icon(e.icon) + '</div><div>' +
        '<div class="tt">' + esc(e.title) + '</div>' +
        (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') +
        (e.detail ? '<div class="dd">' + esc(e.detail) + '</div>' : '') +
        (store.mode === "advanced" && e.tech ? '<div class="tech hidden">' + esc(fmtTech(e.tech)) + '</div>' : '') +
        '</div></div>';
    }).join("") || '<div class="empty">Событий пока нет.</div>';
    return stick;
  }

  /* The bento area has two columns and four cards of very different
     heights (an empty attention card is one line, two blocked tasks are a
     page). A fixed placement leaves a hole under the shorter column, so
     the cards are split into the two most even groups, measured after
     every render, and each column keeps the reading priority. */
  var CARD_ORDER = ["attCard", "nowCard", "planCard", "feedCard"];
  function atBottom(feed) {
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
  }
  function layoutColumns(stick) {
    var a = $("colA"), b = $("colB"), feed = $("feed");
    if (stick === undefined) stick = atBottom(feed);  // a resize: keep where the reader is
    if (window.matchMedia("(max-width: 900px)").matches) {
      CARD_ORDER.forEach(function (id) { a.appendChild($(id)); });
    } else {
      var height = {};
      CARD_ORDER.forEach(function (id) { height[id] = $(id).offsetHeight; });
      var bestMask = 0, bestDiff = Infinity;
      for (var mask = 0; mask < (1 << CARD_ORDER.length); mask++) {  // a set bit = column B
        var ha = 0, hb = 0;
        CARD_ORDER.forEach(function (id, j) { if ((mask >> j) & 1) hb += height[id]; else ha += height[id]; });
        if (Math.abs(ha - hb) < bestDiff) { bestDiff = Math.abs(ha - hb); bestMask = mask; }
      }
      CARD_ORDER.forEach(function (id, j) { ((bestMask >> j) & 1 ? b : a).appendChild($(id)); });
    }
    // A card moved to another column starts scrolled to its top. The
    // feed goes back to its newest line, unless the reader has scrolled
    // up to read the history.
    if (stick) feed.scrollTop = feed.scrollHeight;
  }

  function render(snap, knownSeq) {
    $("teamName").textContent = "AI-команда · " + snap.team;
    var rs = snap.run || {};
    $("teamGoal").textContent = rs.hint || "подготовка к запуску";
    $("runStateText").textContent = rs.state || "нет данных";
    var rsKind = !rs.state ? "idle" : /внимание/.test(rs.state) ? "problem" : /Завершено/.test(rs.state) ? "done" : "busy";
    $("runState").className = "pill";
    $("runDot").className = "dot " + rsKind;
    var elapsed = $("elapsed");
    elapsed.classList.remove("hidden");
    elapsed.textContent = (snap.finished ? "работа заняла " : "в работе ") + fmtDur(snap.elapsed_s);
    $("updatedAt").textContent = "обновлено в " + fmtClock(snap.now);
    renderProgress(snap.progress);
    renderNow(snap.tasks, snap.cost);
    renderAttention(snap.attention);
    renderPlan(snap.tasks);
    var stick = renderFeed(snap.events, knownSeq);
    applyPrefs();
    layoutColumns(stick);
  }

  /* ---- polling: refresh data, keep user context (spec 16.4) ---- */
  var shownLevel = null, requestTimer = null;
  function show(snap) {
    var known = lastSnap ? lastSnap.last_seq : null;
    lastGood = Date.now(); lastSnap = snap;
    setConn("", "");
    /* A quiet team has nothing new in the journal, but the two levels
       show different things: the level the reader picked redraws too. */
    if (known !== null && snap.last_seq === known && snap.level === shownLevel) {
      $("updatedAt").textContent = "обновлено в " + fmtClock(snap.now);
      return;
    }
    try {
      render(snap, known);
      shownLevel = snap.level;
    } catch (error) {
      /* A fault of the page, not of the server: said so, and whatever
         was on screen stays there. */
      if (window.console) console.error("dashboard: could not render the snapshot", error);
      setConn("lost", "Данные получены, но доска не смогла их показать. Обновите страницу; если это повторится, запустите доску заново командой team.py ui.");
    }
  }

  function requestFailed(err) {
    clearTimeout(requestTimer);
    if (err && err.status) {
      /* The request itself is wrong (unknown team, bad value). The
         engine text goes to the console; the reader gets the next step. */
      if (window.console) console.error("dashboard:", err.message);
      lastGood = null;
      setConn("lost", "Не удалось открыть состояние этой команды. Проверьте имя команды в адресе или запустите доску заново командой team.py ui.");
      return;
    }
    /* Keep the last known state on screen. Never pretend work is live. */
    if (lastGood && Date.now() - lastGood < 15000) setConn("stale", "Обновление задерживается, показано последнее известное состояние");
    else setConn("lost", "Соединение потеряно. Показано состояние на " + fmtClock(new Date(lastGood || Date.now()).toISOString()));
  }

  function load() {
    var url = "/api/snapshot" + API + "&level=" + encodeURIComponent(store.mode);
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 8000);
    requestTimer = timer;
    fetch(url, {signal: controller.signal, credentials: "same-origin", cache: "no-store"})
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) { var err = new Error(body.error || ("HTTP " + r.status)); err.status = r.status; throw err; }
          return body;
        });
      })
      .then(function (snap) {
        clearTimeout(timer);
        return snap;
      })
      .then(show, requestFailed);
  }
  applyPrefs();
  if (!TEAM) {
    setConn("lost", "Не указана команда: откройте адрес, который напечатала команда team.py ui");
    return;
  }
  load();
  setInterval(load, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
  window.addEventListener("resize", layoutColumns);
})();
