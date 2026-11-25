/* Dashboard logic (v2.1 spec, sections 4, 5, 8 and 16). Reads a read-only
   API, keeps what the reader is doing while data refreshes, and never
   touches team state. */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var TEAM = params.get("team") || "";
  var API = "?team=" + encodeURIComponent(TEAM);
  var POLL_MS = 3000;       // how often the page asks whether anything changed
  var RESYNC_MS = 30000;    // and how often it takes a fresh snapshot anyway
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
                theme: readPref("at.theme", prefersDark ? "dark" : "light"),
                control: false };      // whether the owner can act (never persisted)

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
  /* A block is written only when its markup changed (v2.1 spec 21): a
     refresh that moved nothing leaves the screen exactly as it was, and
     one that moved a single card redraws that card alone. What the page
     changes inside a block afterwards (a bar's width, the graph's
     position, an open file) is kept, because the block is not rebuilt. */
  function paint(el, html) {
    if (!el) return false;
    if (el.painted === html) return false;
    el.painted = html;
    el.innerHTML = html;
    return true;
  }
  function list(value) { return Array.isArray(value) ? value : []; }
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
  function fmtStart(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString("ru-RU", {day: "numeric", month: "short"}) + ", " + fmtClock(iso);
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
  function shorten(text, limit) {
    text = String(text || "");
    return text.length > limit ? text.slice(0, limit - 1).replace(/\s+$/, "") + "…" : text;
  }
  function lines(parts) { return parts.filter(Boolean).map(function (p) { return "<p>" + esc(p) + "</p>"; }).join(""); }
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
  function techBlock(tech) {
    return store.mode === "advanced" && tech ? '<div class="tech hidden">' + esc(fmtTech(tech)) + '</div>' : "";
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
  function switchMode(mode) {
    store.mode = mode; writePref("at.mode", mode); applyPrefs();
    takeSnapshot();   // the other level shows other things even on a quiet team
  }
  $("modeSimple").addEventListener("click", function () { switchMode("simple"); });
  $("modeAdvanced").addEventListener("click", function () { switchMode("advanced"); });

  /* ---- connection states (spec 16.4) ---- */
  var conn = $("conn");
  function setConn(state, text) {
    conn.className = "conn" + (state ? " show " + state : "");
    conn.textContent = text || "";
  }
  function setRefreshing(on) { $("updatedAt").classList.toggle("refreshing", on); }

  /* ---- rendering ---- */
  var lastSnap = null, lastGood = null, shownLevel = null, requestTimer = null, snapshotAt = 0;
  var sheet = $("sheet"), sheetTarget = null;

  function timers() {
    /* Minutes pass between snapshots: the clock values follow them. */
    if (!lastSnap) return;
    var delta = Math.round((Date.now() - snapshotAt) / 1000);
    var elapsed = $("elapsed");
    elapsed.textContent = (lastSnap.finished ? "работа заняла " : "в работе ") +
      fmtDur(Number(lastSnap.elapsed_s) + (lastSnap.finished ? 0 : delta));
    var timed = document.querySelectorAll("[data-silent],[data-worked]");
    Array.prototype.forEach.call(timed, function (el) {
      var silent = el.getAttribute("data-silent"), worked = el.getAttribute("data-worked");
      if (silent !== null) el.textContent = "последняя активность " + fmtDur(Number(silent) + delta) + " назад";
      else if (worked !== null && el.getAttribute("data-live")) el.textContent = fmtDur(Number(worked) + delta);
    });
  }

  function renderHeader(snap) {
    var project = snap.project || {}, rs = snap.run || {};
    $("projectName").textContent = project.name || "AI-команда";
    $("projectGoal").textContent = project.goal || rs.hint || "";
    $("runStateText").textContent = rs.state || "нет данных";
    var rsKind = !rs.state ? "idle" : /внимание/.test(rs.state) ? "problem"
      : /Пауза/.test(rs.state) ? "attention" : /Завершено|Готово/.test(rs.state) ? "done" : "busy";
    $("runState").className = "pill";
    $("runDot").className = "dot " + rsKind;
    $("elapsed").classList.remove("hidden");
    var started = $("startedAt");
    started.classList.remove("hidden");
    started.textContent = "старт " + fmtStart(project.started_at);
    timers();
  }

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

  /* The three checkpoints the engine really records. No percentage is
     invented between them (spec 4.2). */
  function track(points, labels) {
    var names = ["Начата", "Передана на проверку", "Принята"];
    var passed = points.filter(Boolean).length;
    return '<span class="track" role="img" aria-label="Пройдено шагов: ' + passed + ' из 3">' +
      points.map(function (on, i) {
        return (i ? '<i class="link' + (points[i] && points[i - 1] ? " on" : "") + '"></i>' : "") +
          '<i class="point' + (on ? " on" : "") + '"' + (labels ? ' title="' + names[i] + '"' : "") + '></i>';
      }).join("") + '</span>';
  }
  function checkpoints(t) { var c = t.checkpoints || {}; return [!!c.started, !!c.submitted, !!c.accepted]; }

  function renderNow(tasks, cost) {
    var live = tasks.filter(function (t) { return t.active; });
    var html = live.map(function (t) {
      var since = t.silent_s == null ? "" :
        '<div class="since" data-silent="' + (Number(t.silent_s) || 0) + '">последняя активность ' + fmtDur(Number(t.silent_s) || 0) + " назад</div>";
      var attempt = Number(t.attempt) || 0;
      var doing = t.silent ? "ожидается ответ исполнителя"
        : t.state === "На проверке" ? "результат передан на проверку качества"
        : "работает над задачей" + (attempt > 1 ? ", попытка " + attempt : "");
      var worked = t.worked_s == null ? "" :
        ' · в работе <span data-worked="' + (Number(t.worked_s) || 0) + '" data-live="1">' + fmtDur(Number(t.worked_s) || 0) + "</span>";
      var last = t.last_event && t.last_event.title ?
        '<div class="last">Последнее обновление: ' + esc(t.last_event.title) +
          (t.last_event.detail ? ' <span class="muted">' + esc(t.last_event.detail) + "</span>" : "") + "</div>" : "";
      return '<article class="now-card"><div class="avatar" aria-hidden="true">' + esc(roleLetter(t.role)) + '</div>' +
        '<div><h3>' + esc(t.title) + '</h3><div class="role">' + esc(t.role) + '</div>' +
        '<div class="doing">' + esc(doing) + worked + '</div>' + last + '</div>' +
        '<div class="meta"><span class="state ' + esc(t.kind) + '">' + icon(t.icon) + esc(t.state) + '</span>' + since + '</div>' +
        (t.violations && t.violations.length ? '<div class="files">' + t.violations.map(function (f) { return fileChip(f, true); }).join("") + '</div>' : '') +
        '</article>';
    }).join("");
    if (!live.length) {
      var anyDone = tasks.some(function (t) { return t.kind === "done"; });
      html = '<div class="empty">' + (anyDone ? "Сейчас никто не занят: активных задач нет." : "Исполнители ещё не приступали к задачам.") + '</div>';
    }
    if (cost) {
      var spend;
      if (!(Number(cost.tokens) || 0) && !(Number(cost.usd) || 0)) spend = "данных о расходах пока нет";
      else if (Number(cost.usd) > 0) spend = (cost.complete ? "" : "не менее ") + "$" + Number(cost.usd).toFixed(2);
      else spend = "не определены";
      html += '<div class="small muted cost">Затраты: <b>' + esc(spend) + '</b>' +
        (cost.complete || Number(cost.usd) > 0 ? "" : ' <span class="muted">(данные о ценах недоступны)</span>') +
        ' · <span class="nowrap">объём использования AI: ' + (Number(cost.tokens) || 0).toLocaleString("ru-RU") + '</span></div>';
    }
    paint($("nowList"), html);
  }

  function renderTeam(agents, tasks) {
    paint($("teamList"), agents.map(function (a, index) {
      var facts = [];
      if (a.checks != null) facts.push("проверок: " + (Number(a.checks) || 0) +
        (Number(a.completed) > 0 ? " · принято по проверке: " + (Number(a.completed) || 0) : ""));
      else facts.push("завершено " + (Number(a.completed) || 0) + " из " + (Number(a.total) || 0));
      if (a.worked_s != null) facts.push('в работе <span data-worked="' + (Number(a.worked_s) || 0) + '" data-live="1">' + fmtDur(Number(a.worked_s) || 0) + "</span>");
      if (a.silent_s != null) facts.push('<span data-silent="' + (Number(a.silent_s) || 0) + '">последняя активность ' + fmtDur(Number(a.silent_s) || 0) + " назад</span>");
      return '<article class="person ' + esc(a.kind || "idle") + '" role="button" tabindex="0" data-specialist="' + index + '">' +
        '<div class="avatar" aria-hidden="true">' + esc(roleLetter(a.name)) + '</div>' +
        '<div class="who"><h3>' + esc(a.name) + '</h3><div class="does">' + esc(a.does) + '</div>' +
        '<div class="doing">' + esc(a.doing) + '</div><div class="facts">' + facts.join(" · ") + "</div></div>" +
        '<span class="state ' + esc(a.kind || "idle") + '">' + icon(a.icon) + esc(a.state) + "</span>" +
        "</article>";
    }).join("") || '<div class="empty">Специалистов пока нет: план ещё не сформирован.</div>');
  }

  function renderAttention(items) {
    $("attCount").textContent = items.length;
    $("attCount").className = "pill" + (items.length ? " state attention" : "");
    $("attCard").className = "card" + (items.length ? " attention" : "");
    if (!items.length) { paint($("attList"), '<div class="empty">Ничего не требует вашего решения. Команда справляется сама.</div>'); return; }
    paint($("attList"), items.map(function (a) {
      return '<article class="attention-item ' + esc(a.kind) + '"><h3>' + icon(a.icon) + '<span>' + esc(a.title) + '</span></h3>' +
        (a.remarks ? '<div class="remarks"><b>Замечания проверяющего</b>' + esc(a.remarks) + '</div>' : '') +
        '<dl>' +
        '<dt>Что произошло</dt><dd>' + esc(a.what) + '</dd>' +
        '<dt>Почему это важно</dt><dd>' + esc(a.why) + '</dd>' +
        '<dt>Что система сделала</dt><dd>' + esc(a.tried) + '</dd>' +
        '<dt>Что нужно от вас</dt><dd>' + esc(a.action) + '</dd>' +
        '<dt>Если ничего не делать</dt><dd>' + esc(a.if_nothing) + '</dd></dl></article>';
    }).join(""));
  }

  function renderPlan(tasks) {
    paint($("planList"), tasks.map(function (t, i) {
      var bad = list(t.violations);
      var changed = list(t.changed_files);
      var files = changed.map(function (f) { return fileChip(f, bad.indexOf(f) >= 0); }).join("") +
        bad.filter(function (f) { return changed.indexOf(f) < 0; }).map(function (f) { return fileChip(f, true); }).join("");
      var deps = list(t.deps).filter(Boolean);
      var failed = Number(t.failed_checks) || 0;
      var expected = list(t.expected);
      return '<div class="stage ' + esc(t.kind) + '" role="button" tabindex="0" data-task="' + esc(t.id) + '">' +
        '<div class="mark">' + icon(t.icon) + '</div>' +
        '<div><div class="t">' + (i + 1) + ". " + esc(t.title) + '</div>' +
        '<div class="s"><div>' + esc(t.state_hint) + "</div>" +
        (deps.length ? "<div>после: " + esc(deps.join(", ")) + "</div>" : "") +
        (t.next_step ? "<div>дальше: " + esc(t.next_step) + "</div>" : "") + "</div>" +
        track(checkpoints(t), false) +
        (files ? '<div class="files">' + files + "</div>" :
          expected.length ? '<div class="files"><span class="label">результат:</span>' +
            expected.map(function (f) { return fileChip(f, false); }).join("") + "</div>" : "") +
        techBlock(t.tech) +
        "</div>" +
        '<div class="who">' + esc(t.role) + (failed ? "<br>" + failed + " " + plural(failed, "раз", "раза", "раз") + " возвращена на доработку" : "") + "</div>" +
        "</div>";
    }).join("") || '<div class="empty">План ещё не сформирован.</div>');
  }

  function renderFeed(events, knownSeq) {
    var all = feedItems(events);
    var shown = all.filter(feedMatches);
    var listEvents = shown;
    if (feedView.paused) {          // the list is held where the reader paused it
      listEvents = shown.filter(function (e) { return e.seq <= feedView.frozen; });
      feedView.unseen = shown.length - listEvents.length;
    }
    var names = {};
    // The lead and the system are no card: their lines are found by name.
    all.forEach(function (e) { if (e.specialist && e.by == null) names[e.specialist] = true; });
    var who = $("feedWho"), current = feedView.who;
    paint(who, '<option value="">Все специалисты</option>' +
      list(lastSnap && lastSnap.agents).map(function (a, index) {
        return '<option value="' + index + '">' + esc(a.name) + "</option>";
      }).join("") + Object.keys(names).map(function (name) { return '<option value="' + esc(name) + '">' + esc(name) + "</option>"; }).join(""));
    who.value = current;
    var stage = $("feedTask");
    paint(stage, '<option value="">Все этапы</option>' +
      list(lastSnap && lastSnap.tasks).map(function (t) {
        return '<option value="' + esc(t.title) + '">' + esc(t.title) + "</option>";
      }).join(""));
    stage.value = feedView.task;
    var idsByTitle = {};
    list(lastSnap && lastSnap.tasks).forEach(function (t) { idsByTitle[t.title] = t.id; });
    var pause = $("feedPause");
    pause.textContent = feedView.paused ? "Продолжить" + (feedView.unseen ? " (" + feedView.unseen + " новых)" : "") : "Пауза";
    pause.setAttribute("aria-pressed", String(feedView.paused));
    pause.classList.toggle("on", feedView.paused);
    var oldest = all.reduce(function (low, e) { return Math.min(low, e.seq); }, Infinity);
    $("feedOlder").classList.toggle("hidden", !(lastSnap && isFinite(oldest) && oldest > 1));
    events = listEvents;
    paint($("feed"), events.map(function (e) {
      var isNew = knownSeq !== null && e.seq > knownSeq;
      var cls = "feed-item " + esc(e.kind || "normal") + (isNew ? " is-new" : "");
      var sub = join([e.specialist, e.task]);
      var stage = idsByTitle[e.task];       // a line about a stage opens that stage
      return '<div class="' + cls + '"' + (stage ? ' data-task="' + esc(stage) + '" role="button" tabindex="0"' : "") +
        '><time>' + fmtClock(e.ts) + '</time><div class="mark" aria-hidden="true">' + icon(e.icon) + '</div><div>' +
        '<div class="tt">' + esc(e.title) + '</div>' +
        (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') +
        (e.detail ? '<div class="dd">' + esc(e.detail) + '</div>' : '') +
        techBlock(e.tech) +
        "</div></div>";
    }).join("") || '<div class="empty">' + (all.length ? "По этим условиям событий нет." : "Событий пока нет.") + "</div>");
  }

  /* ---- the plan as a graph (spec 6): left to right, the position is the
     reader's and survives refreshes ---- */
  var NODE_W = 196, NODE_H = 64, COL_GAP = 36, ROW_GAP = 22;
  var graph = { k: 1, x: 0, y: 0, filter: "all", q: "", width: 0, height: 0 };

  function graphShown(t) {
    var q = graph.q.trim().toLowerCase();
    if (graph.filter === "problem" && t.kind !== "problem") return false;
    if (graph.filter === "open" && t.kind === "done") return false;
    if (q && String(t.title || "").toLowerCase().indexOf(q) < 0) return false;
    return true;
  }
  function graphMove() {
    var viewport = $("graphViewport");
    if (viewport) viewport.setAttribute("transform", "translate(" + graph.x + " " + graph.y + ") scale(" + graph.k + ")");
  }
  function graphFit() {
    var plan = lastSnap && lastSnap.plan;
    if (!plan) return;
    graph.width = plan.columns * NODE_W + (plan.columns + 1) * COL_GAP;
    graph.height = plan.rows * NODE_H + (plan.rows + 1) * ROW_GAP;
    var room = ($("graphWrap").clientWidth || 600) - 4;
    // The whole plan across the width, never larger than life, and never
    // so small that its words cannot be read: on a phone the reader pans
    // instead of squinting.
    graph.k = Math.max(0.75, Math.min(1, room / graph.width));
    graph.x = 0; graph.y = 0;
  }
  function renderGraph(tasks) {
    var plan = (lastSnap && lastSnap.plan) || { nodes: [], edges: [], columns: 0, rows: 0 };
    var byId = {};
    tasks.forEach(function (t) { byId[t.id] = t; });
    var shown = {};
    tasks.forEach(function (t) { shown[t.id] = graphShown(t); });
    var width = plan.columns * NODE_W + (plan.columns + 1) * COL_GAP;
    var height = plan.rows * NODE_H + (plan.rows + 1) * ROW_GAP;
    if (width !== graph.width) graphFit();       // the plan itself changed
    var pos = {};
    plan.nodes.forEach(function (n) {
      pos[n.id] = { x: COL_GAP + n.col * (NODE_W + COL_GAP), y: ROW_GAP + n.row * (NODE_H + ROW_GAP) };
    });
    var links = plan.edges.map(function (e) {
      var a = pos[e.from], b = pos[e.to];
      if (!a || !b) return "";
      var x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, mx = (x1 + x2) / 2;
      return '<path class="link ' + esc((byId[e.from] || {}).kind || "idle") + (shown[e.from] && shown[e.to] ? "" : " dim") +
        '" d="M' + x1 + " " + y1 + "C" + mx + " " + y1 + " " + mx + " " + y2 + " " + x2 + " " + y2 + '"><title>Результат этапа «' +
        esc((byId[e.from] || {}).title || e.from) + "» нужен для этапа «" + esc((byId[e.to] || {}).title || e.to) + "»</title></path>";
    }).join("");
    var nodes = plan.nodes.map(function (n) {
      var t = byId[n.id] || {}, p = pos[n.id];
      var tech = store.mode === "advanced" && t.tech ? '<text class="node-tech" x="12" y="' + (NODE_H - 6) + '">' +
        esc(n.id + (t.tech.branch ? " · " + String(t.tech.branch).split("/").pop() : "")) + "</text>" : "";
      return '<g class="node ' + esc(t.kind || "idle") + (shown[n.id] ? "" : " dim") + '" data-task="' + esc(n.id) +
        '" role="button" tabindex="0" aria-label="' + esc((t.title || n.id) + ", " + (t.state || "")) + '" transform="translate(' + p.x + " " + p.y + ')">' +
        '<rect class="box" width="' + NODE_W + '" height="' + NODE_H + '" rx="16"/><circle class="mark" cx="18" cy="18" r="6"/>' +
        '<text class="node-state" x="32" y="22">' + esc(t.state || "") + "</text>" +
        '<foreignObject x="12" y="27" width="' + (NODE_W - 24) + '" height="' + (tech ? 22 : 32) + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" class="node-title">' + esc(t.title || n.id) + "</div></foreignObject>" + tech + "</g>";
    }).join("");
    $("graph").setAttribute("viewBox", "0 0 " + (width || 600) + " " + (height || 120));
    $("graph").setAttribute("height", height || 120);
    paint($("graph"), '<g id="graphViewport">' + links + nodes + "</g>");
    graphMove();
  }
  function redrawGraph() { if (lastSnap) renderGraph(lastSnap.tasks); }
  (function graphControls() {
    var svg = $("graph"), wrap = $("graphWrap"), drag = null, moved = false;
    function zoom(factor, cx, cy) {
      var k = Math.min(2.5, Math.max(0.25, graph.k * factor));
      graph.x = cx - (cx - graph.x) * (k / graph.k);
      graph.y = cy - (cy - graph.y) * (k / graph.k);
      graph.k = k; graphMove();
    }
    wrap.addEventListener("wheel", function (event) {   // the wheel zooms around the pointer
      if (!lastSnap || !lastSnap.plan || !lastSnap.plan.nodes.length) return;
      event.preventDefault();
      var rect = wrap.getBoundingClientRect();
      zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    wrap.addEventListener("pointerdown", function (event) {
      if (event.target.closest && event.target.closest("[data-task]")) return;   // a node is pressed, not the canvas
      drag = { x: event.clientX, y: event.clientY, ox: graph.x, oy: graph.y };
      wrap.setPointerCapture && wrap.setPointerCapture(event.pointerId);
    });
    wrap.addEventListener("pointermove", function (event) {
      if (!drag) return;
      graph.x = drag.ox + event.clientX - drag.x;
      graph.y = drag.oy + event.clientY - drag.y;
      moved = true; graphMove();
    });
    function stop() { drag = null; }
    wrap.addEventListener("pointerup", stop);
    wrap.addEventListener("pointercancel", stop);
    wrap.addEventListener("click", function (event) {   // a drag must not open a sheet
      if (moved) { moved = false; event.stopPropagation(); event.preventDefault(); }
    }, true);
    $("graphZoomIn").addEventListener("click", function () { zoom(1.25, (wrap.clientWidth || 600) / 2, 150); });
    $("graphZoomOut").addEventListener("click", function () { zoom(0.8, (wrap.clientWidth || 600) / 2, 150); });
    $("graphReset").addEventListener("click", function () { graphFit(); graphMove(); });
    $("graphSearch").addEventListener("input", function () { graph.q = $("graphSearch").value; redrawGraph(); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-plan-filter]"), function (chip) {
      chip.addEventListener("click", function () {
        graph.filter = chip.getAttribute("data-plan-filter");
        Array.prototype.forEach.call(document.querySelectorAll("[data-plan-filter]"), function (other) {
          var on = other === chip;
          other.classList.toggle("on", on);
          other.setAttribute("aria-pressed", String(on));
        });
        redrawGraph();
      });
    });
    window.addEventListener("resize", function () { graphFit(); graphMove(); });
  })();
  opener($("graph"), "data-task", function (id) { openSheet("task", id); loadHistory(id); });

  /* ---- the timeline (spec 7): what the journal says happened, and when ---- */
  var SPAN_ORDER = ["work", "check", "fix", "wait", "recovery", "done", "stopped"];
  function renderTimeline(tl) {
    var rows = list(tl && tl.rows);
    if (!tl || !rows.length) { paint($("timeline"), ""); paint($("timeLegend"), ""); return; }
    var start = Date.parse(tl.start), end = Date.parse(tl.end) || Date.now();
    if (!(end > start)) end = start + 3600000;
    var left = 172, top = 34, rowH = 46;
    // Drawn at real size: shrinking a diagram makes its words unreadable.
    // A narrow window scrolls it sideways instead.
    var plotW = Math.max(360, ($("timeline").clientWidth || 680) - left - 16);
    var width = left + plotW + 16, height = top + rows.length * rowH + 12;
    var at = function (iso) {
      var t = Date.parse(iso);
      return left + plotW * Math.min(1, Math.max(0, (t - start) / (end - start)));
    };
    var axis = "";
    for (var i = 0; i <= 4; i++) {
      var ts = start + (end - start) * i / 4, x = left + plotW * i / 4;
      axis += '<line class="grid" x1="' + x + '" y1="' + top + '" x2="' + x + '" y2="' + (height - 12) + '"/>' +
        '<text class="tick" x="' + x + '" y="' + (top - 10) + '" text-anchor="' + (i === 0 ? "start" : i === 4 ? "end" : "middle") + '">' +
        fmtClock(new Date(ts).toISOString()) + "</text>";
    }
    var body = rows.map(function (row, index) {
      var y = top + index * rowH;
      var spans = list(row.spans).map(function (sp) {
        var x1 = at(sp.from), x2 = Math.max(at(sp.to), x1 + 3);
        return '<rect class="span ' + esc(sp.kind) + '" x="' + x1 + '" y="' + (y + 12) + '" width="' + (x2 - x1) + '" height="18" rx="9">' +
          "<title>" + esc(sp.text + ": с " + fmtClock(sp.from) + " по " + fmtClock(sp.to)) + "</title></rect>";
      }).join("");
      var marks = list(row.marks).map(function (m) {
        return '<circle class="tmark ' + esc(m.kind || "normal") + '" cx="' + at(m.ts) + '" cy="' + (y + 21) + '" r="4">' +
          "<title>" + esc(fmtClock(m.ts) + " · " + m.title + (m.detail ? ": " + m.detail : "")) + "</title></circle>";
      }).join("");
      return '<g class="trow" data-task="' + esc(row.id) + '" role="button" tabindex="0" aria-label="' + esc(row.title + ", " + row.state) + '">' +
        '<text class="row-title" x="12" y="' + (y + 18) + '"><title>' + esc(row.title) + "</title>" +
        esc(shorten(row.title, 26)) + "</text>" +
        '<text class="row-state" x="12" y="' + (y + 34) + '">' + esc(row.state) + "</text>" + spans + marks + "</g>";
    }).join("");
    $("timeline").setAttribute("viewBox", "0 0 " + width + " " + height);
    $("timeline").setAttribute("width", width);
    $("timeline").setAttribute("height", height);
    paint($("timeline"), axis + body);
    var present = {};
    rows.forEach(function (row) { row.spans.forEach(function (sp) { present[sp.kind] = true; }); });
    paint($("timeLegend"), SPAN_ORDER.filter(function (kind) { return present[kind]; }).map(function (kind) {
      return '<span class="legend-item"><i class="swatch ' + esc(kind) + '"></i>' + esc(tl.span_text[kind] || kind) + "</span>";
    }).join("") + '<span class="legend-item"><i class="swatch mark"></i>событие</span>');
  }
  opener($("timeline"), "data-task", function (id) { openSheet("task", id); loadHistory(id); });

  /* ---- the activity feed: filters, search, pause, further back (spec 9) ---- */
  var feedView = { q: "", kind: "all", who: "", task: "", hour: false, paused: false, frozen: null,
                   unseen: 0, older: [] };
  function feedMatches(e) {
    if (feedView.kind !== "all" && (e.kind || "normal") !== feedView.kind) return false;
    if (feedView.who !== "" && String(e.by) !== feedView.who && e.specialist !== feedView.who) return false;
    if (feedView.task !== "" && e.task !== feedView.task) return false;
    if (feedView.hour && !(Date.parse(e.ts) >= Date.now() - 3600000)) return false;
    var q = feedView.q.trim().toLowerCase();
    if (q && [e.title, e.detail, e.task, e.specialist].join(" ").toLowerCase().indexOf(q) < 0) return false;
    return true;
  }
  function feedItems(events) {
    var merged = feedView.older.slice();
    events.forEach(function (e) { merged.push(e); });
    var seen = {}, out = [];
    merged.forEach(function (e) { if (!seen[e.seq]) { seen[e.seq] = true; out.push(e); } });
    return out;
  }
  (function feedControls() {
    $("feedSearch").addEventListener("input", function () { feedView.q = $("feedSearch").value; redrawFeed(); });
    $("feedWho").addEventListener("change", function () { feedView.who = $("feedWho").value; redrawFeed(); });
    $("feedTask").addEventListener("change", function () { feedView.task = $("feedTask").value; redrawFeed(); });
    $("feedHour").addEventListener("click", function () {
      feedView.hour = !feedView.hour;
      $("feedHour").setAttribute("aria-pressed", String(feedView.hour));
      $("feedHour").classList.toggle("on", feedView.hour);
      redrawFeed();
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-feed-kind]"), function (chip) {
      chip.addEventListener("click", function () {
        feedView.kind = chip.getAttribute("data-feed-kind");
        Array.prototype.forEach.call(document.querySelectorAll("[data-feed-kind]"), function (other) {
          var on = other === chip;
          other.classList.toggle("on", on);
          other.setAttribute("aria-pressed", String(on));
        });
        redrawFeed();
      });
    });
    $("feedPause").addEventListener("click", function () {
      feedView.paused = !feedView.paused;
      if (feedView.paused) {
        var shownNow = feedItems(lastSnap ? lastSnap.events : []).filter(feedMatches);
        var newest = shownNow.length ? shownNow[shownNow.length - 1].seq : 0;
        feedView.frozen = newest;          // the reader holds the list at this event
        feedView.unseen = 0;
      }
      redrawFeed();
    });
    $("feedOlder").addEventListener("click", function () {
      if (!lastSnap) return;
      var oldest = feedItems(lastSnap.events).reduce(function (low, e) { return Math.min(low, e.seq); }, Infinity);
      if (!isFinite(oldest) || oldest <= 1) return;
      $("feedOlder").disabled = true;
      request("/api/events" + API + "&level=" + encodeURIComponent(store.mode) + "&before=" + oldest + "&limit=100")
        .then(function (page) {
          $("feedOlder").disabled = false;
          feedView.older = list(page.events).concat(feedView.older);
          redrawFeed();
        }, function () { $("feedOlder").disabled = false; });
    });
  })();
  function redrawFeed() { if (lastSnap) renderFeed(lastSnap.events, null); }
  opener($("feed"), "data-task", function (id) { openSheet("task", id); loadHistory(id); });

  /* ---- what the numbers mean (spec 10 to 13). A figure always comes
     with the words that say what it counts; a bar gets its width through
     the style object, never through a style attribute (the page's policy
     allows no inline style). ---- */
  function noun(n, one, few, many) { return plural(n, one, few, many); }
  function stats(items, cls) {
    return '<div class="stats' + (cls ? " " + cls : "") + '">' + items.map(function (it) {
      return '<div class="stat"><b>' + esc(it.n) + "</b><span>" + esc(it.label) + "</span>" +
        (it.note ? '<small>' + esc(it.note) + "</small>" : "") + "</div>";
    }).join("") + "</div>";
  }
  function moneyText(usd) {
    usd = Number(usd) || 0;
    return "$" + (usd >= 0.01 || usd === 0 ? usd.toFixed(2) : usd.toFixed(6));
  }
  /* The lines of one bar list of the money card. A stage or a specialist
     some of whose records have no price keeps its known sum and says so;
     one with no priced record at all is "not defined", never a zero. */
  function moneyRows(sums, unknown) {
    sums = sums || {};
    unknown = unknown || {};
    var names = {};
    Object.keys(sums).forEach(function (k) { names[k] = true; });
    Object.keys(unknown).forEach(function (k) { names[k] = true; });
    return Object.keys(names).map(function (name) {
      var value = Number(sums[name]) || 0, missing = Number(unknown[name]) || 0;
      return {name: name, value: value, text: value ? moneyText(value) : "не определена",
              note: missing ? "и ещё цена " + missing + " " + (missing === 1 ? "записи" : "записей") +
                " неизвестна" : ""};
    });
  }
  function bars(rows) {
    if (!rows.length) return '<p class="muted small">Данных пока нет.</p>';
    var top = Math.max.apply(null, rows.map(function (r) { return Number(r.value) || 0; })) || 1;
    return '<ul class="bars">' + rows.map(function (r) {
      var share = Math.round(100 * (Number(r.value) || 0) / top);
      return '<li><span class="name">' + esc(r.name) + '</span><span class="value">' + esc(r.text) +
        '</span><span class="bar"><i data-w="' + (share < 2 && Number(r.value) > 0 ? 2 : share) +
        '"></i></span>' + (r.note ? '<span class="note">' + esc(r.note) + "</span>" : "") + "</li>";
    }).join("") + "</ul>";
  }
  function fitBars(scope) {
    // Widths go through the style object: a style attribute in markup
    // is refused by the page's policy, this is not.
    Array.prototype.forEach.call((scope || document).querySelectorAll("[data-w]"), function (el) {
      el.style.width = el.getAttribute("data-w") + "%";
    });
  }
  function spark(series, limit) {
    var points = list(series);
    if (points.length < 2) return "";
    var top = limit || Math.max.apply(null, points.map(function (p) { return Number(p.usd) || 0; })) || 1;
    var coords = points.map(function (p, i) {
      return (200 * i / (points.length - 1)).toFixed(1) + "," + (38 - 36 * (Number(p.usd) || 0) / top).toFixed(1);
    }).join(" ");
    return '<svg class="spark" viewBox="0 0 200 40" preserveAspectRatio="none" role="img" aria-label="Как росли расходы">' +
      '<polyline points="' + coords + '"/></svg>';
  }
  function duo(leftTitle, leftBody, rightTitle, rightBody) {
    return '<div class="duo"><div><h3>' + esc(leftTitle) + "</h3>" + leftBody + "</div>" +
      "<div><h3>" + esc(rightTitle) + "</h3>" + rightBody + "</div></div>";
  }
  var linesOf = function (items) {
    return items.length ? '<ul class="plain">' + items.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>" : "";
  };

  /* How the handed-over results got as far as they did: one bar, every
     result takes a share (spec 10.2). The legend says each share in
     words: colour never carries the meaning alone. */
  function distribution(split) {
    split = split || {};
    var parts = [
      {key: "first", label: "С первого раза", note: "проверка пройдена без доработки"},
      {key: "reworked", label: "После доработки", note: "сначала проверку не прошли, результат переделывался"},
      {key: "decision", label: "Решением руководителя", note: "принято без проверки"},
      {key: "awaiting", label: "Ещё на проверке", note: "ответ проверяющего не пришёл"}
    ];
    var count = function (p) { return Number(split[p.key]) || 0; };
    var total = parts.reduce(function (sum, p) { return sum + count(p); }, 0);
    if (!total) return "";
    return '<div class="dist" role="img" aria-label="Как результаты дошли до принятия: ' +
      esc(parts.map(function (p) { return p.label + " " + count(p); }).join(", ")) + '">' +
      parts.map(function (p) {
        return count(p) ? '<i class="' + p.key + '" data-w="' + (100 * count(p) / total).toFixed(2) +
          '" title="' + esc(p.label + ": " + count(p)) + '"></i>' : "";
      }).join("") + "</div>" +
      '<ul class="legend">' + parts.map(function (p) {
        return '<li class="' + (count(p) ? "" : "zero") + '"><span class="swatch ' + p.key +
          '"></span><span><b>' + esc(p.label) + "</b> <span class='num'>" + count(p) + "</span><small>" +
          esc(p.note) + "</small></span></li>";
      }).join("") + "</ul>";
  }
  /* Every check in the order the answers came back, one mark each: a
     result that came back more than once is seen at a glance. */
  function strip(timeline) {
    var marks = list(timeline);
    if (!marks.length) return "";
    var fails = {};
    marks.forEach(function (m) { if (m.verdict !== "pass") fails[m.task] = (fails[m.task] || 0) + 1; });
    var again = Object.keys(fails).filter(function (t) { return fails[t] > 1; }).length;
    return "<h3>Проверки по порядку</h3>" +
      '<div class="strip" role="img" aria-label="Проверки по порядку: ' + marks.length + " " +
      plural(marks.length, "проверка", "проверки", "проверок") + '">' +
      marks.map(function (m) {
        var ok = m.verdict === "pass";
        return '<i class="' + (ok ? "ok" : "bad") + '" title="' +
          esc(fmtClock(m.ts) + " · " + m.task + ": " + (ok ? "пройдена" : "не пройдена")) + '"></i>';
      }).join("") + "</div>" +
      '<p class="small muted">Слева раньше: <span class="swatch ok cap"></span> пройдена, ' +
      '<span class="swatch bad cap"></span> вернула результат на доработку.' +
      (again ? " " + again + " " + plural(again, "результат возвращался", "результата возвращались",
                                          "результатов возвращались") + " на доработку больше одного раза." : "") +
      "</p>";
  }
  function renderQuality(q) {
    if (!q) return;
    var machine = q.machine || {};
    var machineBody = machine.run
      ? stats([{n: machine.run, label: noun(machine.run, "проверка", "проверки", "проверок"),
                note: "команды и файлы, которые проверяет машина"},
               {n: machine.passed, label: "пройдено"}, {n: machine.failed, label: "провалено"}])
      : '<p class="muted small">Машинных проверок не было: критерии этих задач проверяет человек или проверяющий.</p>';
    var judgedBody = q.checks
      ? stats([{n: q.checks, label: noun(q.checks, "проверка", "проверки", "проверок"),
                note: "оценка содержания проверяющим"},
               {n: q.passed, label: "пройдено"}, {n: q.failed, label: "не пройдено"}])
      : '<p class="muted small">Проверок ещё не было.</p>';
    var problems = list(q.problems);
    var qualityHtml =
      '<p class="story-text">' + esc(q.story) + "</p>" +
      stats([{n: q.handed_over, label: "передано на проверку", note: "результатов, которые исполнители сдали"},
             {n: q.first_time, label: q.first_time === 1 ? "прошёл с первого раза" : "прошли с первого раза",
              note: "без доработки"},
             {n: q.reworked, label: noun(q.reworked, "доработка", "доработки", "доработок"),
              note: "результатов пришлось переделывать"},
             {n: q.accepted_by_decision, label: "принято решением", note: "руководитель команды принял без проверки"}]) +
      distribution(q.split) +
      duo("Что проверила машина", machineBody, "Что оценил проверяющий", judgedBody) +
      strip(q.timeline) +
      (list(q.empty).length ? "<p class='bad-text'>Принято без единого файла: " + esc(list(q.empty).join(", ")) +
       ". Исполнитель ничего не подготовил.</p>" : "") +
      (problems.length ? "<h3>Найденные недостатки <span class='count'>" + problems.length +
       "</span></h3><ul class='plain'>" + problems.map(function (p) {
        return "<li><b>" + esc(p.task) + ".</b> " + esc(p.remark || "замечаний не записано") + "</li>";
      }).join("") + "</ul>" : "") +
      (list(q.awaited).length ? "<h3>Ещё ждут проверки</h3>" + linesOf(list(q.awaited)) : "") +
      techBlock(q);
    paint($("qualityBody"), qualityHtml);
    fitBars($("qualityBody"));
  }

  function renderMoney(b, cost) {
    if (!b) return;
    var unknown = Number(b.unknown_records) || 0;
    var reliability = unknown === 0 ? "Все расходы известны." : "Цена " + unknown + " " + (unknown === 1 ? "записи" : "записей") +
      " неизвестна: " + (unknown === 1 ? "она не учтена" : "они не учтены") + ".";
    var html = '<p class="story-text">' + esc(b.story) + "</p>" +
      stats([{n: b.spent_text, label: "потрачено", note: reliability},
             {n: b.budget_usd == null ? "не согласован" : "$" + Number(b.budget_usd).toFixed(2), label: "бюджет"},
             {n: b.remaining_usd == null ? "нет предела" : "$" + Number(b.remaining_usd).toFixed(2), label: "осталось"},
             {n: b.per_task_text, label: "в среднем на задачу", note: "по задачам, которые уже начались"},
             {n: b.per_result_text, label: "цена принятого результата", note: "расходы на все попытки, включая доработки"},
             {n: b.per_task_usd == null ? "не задан" : "$" + Number(b.per_task_usd).toFixed(2), label: "лимит одной задачи",
              note: "после этой суммы новая попытка задачи не начнётся"}], "wide");
    if (b.budget_usd != null) {
      html += '<div class="gauge' + (b.state === "limit" ? " limit" : b.state === "warning" ? " warning" : "") +
        '" role="img" aria-label="Израсходовано ' + Math.round(100 * (b.share || 0)) + '% бюджета">' +
        '<i data-w="' + Math.min(100, Math.round(100 * (b.share || 0))) + '"></i>' +
        '<em data-x="' + Math.round(100 * (b.warn_ratio || 0.8)) + '" title="Отсюда доска предупреждает"></em></div>' +
        '<div class="small muted">' + (b.state === "limit" ? "Предел достигнут: новые задачи не запускаются." :
          b.state === "warning" ? "Приближается предел бюджета." : "До предупреждения ещё " +
          Math.max(0, Math.round(100 * ((b.warn_ratio || 0.8) - (b.share || 0)))) + "% бюджета.") + "</div>";
    }
    if (b.over_limit && b.over_limit.length) {
      html += "<p class='bad-text'>Лимит одной задачи (" + esc("$" + Number(b.per_task_usd).toFixed(2)) + ") исчерпан: " +
        esc(b.over_limit.join(", ")) + "</p>";
    }
    html += duo("По этапам", bars(moneyRows(b.by_task, b.unknown_by_task)),
                "По специалистам", bars(moneyRows(b.by_role, b.unknown_by_role)));
    if (list(b.series).length >= 2) {
      html += "<h3>Как росли расходы</h3>" + spark(b.series);
    }
    html += modelsList(cost && cost.models);
    html += techBlock(b);
    paint($("moneyBody"), html);
    fitBars($("moneyBody"));
    Array.prototype.forEach.call($("moneyBody").querySelectorAll("[data-x]"), function (el) {
      el.style.left = el.getAttribute("data-x") + "%";
    });
  }

  /* Tokens and the price per model (spec 11.3), for the technical
     reader only: the simple level is not loaded with tariffs. A block
     per model, so it fits any width without a scroll of its own. */
  var QUALITY_TEXT = {estimated: "оценка по таблице цен", stored: "цена на момент работы",
                      unknown: "неизвестно"};
  function modelsList(rows) {
    rows = list(rows);
    if (store.mode !== "advanced" || !rows.length) return "";
    // Grouped with an ordinary space, the same on every platform.
    function num(v) { return String(Math.round(Number(v) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
    return "<h3>По моделям</h3>" + '<div class="records">' + rows.map(function (r) {
      var price = r.price_per_1m;
      var quality = list(r.quality).map(function (q) { return QUALITY_TEXT[q] || q; }).join(", ");
      return '<div class="record"><div class="tt">' + esc(r.model) + ' <span class="muted">· ' + esc(r.provider) +
        " · " + r.records + " " + plural(r.records, "запись", "записи", "записей") + "</span></div>" +
        "<dl><dt>Токены: вход / выход</dt><dd>" + num(r.input_tokens) + " / " + num(r.output_tokens) + "</dd>" +
        "<dt>Кеш: чтение / запись</dt><dd>" + num(r.cache_read_tokens) + " / " + num(r.cache_write_tokens) + "</dd>" +
        "<dt>Цена за 1 млн: вход / выход / кеш</dt><dd>" +
        esc(price ? moneyText(price.input) + " / " + moneyText(price.output) + " / " + moneyText(price.cache_read)
                   : "модели нет в таблице цен") + "</dd>" +
        "<dt>Расход</dt><dd>" + (r.usd ? moneyText(r.usd) : "не определена") +
        (r.unknown ? ' <span class="muted">(' + r.unknown + " " +
          plural(r.unknown, "запись", "записи", "записей") + " без цены)</span>" : "") +
        ' <span class="muted">' + esc(quality || "неизвестно") + "</span></dd></dl></div>";
    }).join("") + "</div>";
  }
  function renderProblems(r) {
    if (!r) return;
    var incidents = list(r.incidents);
    var html = '<p class="story-text">' + esc(r.story) + "</p>";
    if (incidents.length) {
      html += '<div class="chips">' + list(r.kinds).map(function (k) {
        return '<span class="chip on">' + esc(k.title) + " · " + k.count + "</span>";
      }).join("") + "</div>";
      html += '<ul class="incidents">' + incidents.slice(0, 8).map(function (i) {
        return '<li class="' + (i.human_needed ? "open" : "closed") + '">' + icon(i.human_needed ? "alert" : "check") +
          "<div><div class='tt'>" + esc(i.title) +
          (i.task ? " <span class='muted'>· " + esc(i.task) + "</span>" : "") +
          (i.when ? " <time class='muted small'>" + esc(fmtClock(i.when)) + "</time>" : "") + "</div>" +
          (i.detail ? '<div class="dd">' + esc(i.detail) + "</div>" : "") +
          '<div class="dd">Система ' + esc(i.did.join(", ")) + ".</div>" +
          (i.saved ? '<div class="dd">Сохранено: ' + esc(i.saved) + ".</div>" : "") +
          '<div class="dd outcome">Итог: ' + esc(i.outcome) + (i.risk_of_loss ? ". Есть риск потери результата." : ".") + "</div>" +
          (i.human_needed ? '<div class="dd"><b>' + esc(i.action) + "</b></div>" : "") +
          "</div></li>";
      }).join("") + "</ul>";
      var repeated = Object.keys(r.repeated || {});
      if (repeated.length) {
        html += "<p class='small muted'>Повторялись: " + esc(repeated.map(function (t) {
          return t + " (" + r.repeated[t] + " " + plural(r.repeated[t], "раз", "раза", "раз") + ")";
        }).join(", ")) + ".</p>";
      }
    }
    html += techBlock(r);
    paint($("problemBody"), html);
  }

  function renderBuild(g) {
    if (!g) return;
    var steps = list(g.steps);
    paint($("buildBody"), '<p class="story-text">' + esc(g.story) + "</p>" +
      '<ol class="steps big">' + steps.map(function (s) {
        return '<li class="' + (s.done ? "on" : "") + '">' + icon(s.done ? "check" : "circle") + "<span><b>" + esc(s.title) +
          "</b><small>" + esc(s.text) + "</small></span></li>";
      }).join("") + "</ol>" +
      (g.conflict ? '<div class="remarks"><b>Что помешало</b>' + esc(g.conflict.title) + ". " + esc(g.conflict.detail) + "</div>" : "") +
      techBlock(g));
  }

  /* ---- comparing teams that ran one set of tasks (spec 19). The card
     exists only for a team that declared its benchmark; a sentence about
     speed or money comes from the engine, never made up here. ---- */
  function renderBenchmark(b) {
    var card = $("benchCard");
    if (!b) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    // A block per team: every measure is visible at any width, where a
    // table of eight columns would hide its last three behind an edge.
    var rows = list(b.rows);
    var measured = function (v) { return v == null ? "нет данных" : String(v); };
    var blocks = rows.length ? '<div class="records">' + rows.map(function (r) {
      var people = r.executors == null ? "специалистов: ?"
        : r.executors + " " + plural(Number(r.executors) || 0, "специалист", "специалиста", "специалистов");
      return '<div class="record ' + (r.finished ? "finished" : "unfinished") + '">' +
        '<div class="tt">' + esc(r.team) + ' <span class="muted">· ' + esc(people) + "</span> " +
        '<span class="state ' + (r.finished ? "done" : "idle") + '">' +
        (r.finished ? "завершена" : "не завершена") + "</span></div>" +
        "<dl><dt>Время</dt><dd>" + esc(r.finished ? fmtDur(Number(r.duration_s) || 0) : "не завершена") + "</dd>" +
        "<dt>Затраты</dt><dd>" + esc(r.cost_complete ? moneyText(r.spent_usd) : "не определены") + "</dd>" +
        "<dt>С первого раза</dt><dd>" + esc(r.judged ? r.first_time + " из " + r.judged : "нет проверок") + "</dd>" +
        "<dt>Цена принятого результата</dt><dd>" +
        esc(r.usd_per_result == null ? "не определена" : moneyText(r.usd_per_result)) + "</dd>" +
        "<dt>Проверок</dt><dd>" + esc(measured(r.attempts)) + "</dd>" +
        "<dt>Проблем</dt><dd>" + esc(measured(r.problems)) + "</dd>" +
        "<dt>Конфликтов объединения</dt><dd>" + esc(measured(r.conflicts)) + "</dd></dl></div>";
    }).join("") + "</div>" : "";
    paint($("benchBody"), (b.statement ? '<p class="story-text"><b>' + esc(b.statement) + "</b></p>" :
      '<p class="story-text">' + esc(b.note || "") + "</p>") + blocks + techBlock(b));
  }

  /* ---- the detail sheet: a specialist or a task (spec 5.1, 8) ---- */
  var CHECK_NAMES = ["Начата", "Передана на проверку", "Принята"];
  function eventList(events) {
    return events.length ? '<ol class="history">' + events.map(function (e) {
      return "<li><time>" + fmtClock(e.ts) + "</time><div><div class='tt'>" + esc(e.title) + "</div>" +
        (e.detail ? '<div class="dd">' + esc(e.detail) + "</div>" : "") + techBlock(e.tech) + "</div></li>";
    }).join("") + "</ol>" : '<div class="empty">Событий пока не было.</div>';
  }
  function renderSheet() {
    if (!sheetTarget || !lastSnap) return;
    var body = $("sheetBody"), title = "", html = "";
    if (sheetTarget.kind === "specialist") {
      var a = list(lastSnap.agents)[sheetTarget.key];
      if (!a) return;
      title = a.name;
      var mine = list(a.tasks);
      var history = list(lastSnap.events).filter(function (e) {
        return e.by === sheetTarget.key || (e.by == null && e.specialist === a.name);
      }).reverse();
      html = '<p class="does">' + esc(a.does) + "</p>" +
        '<div class="row"><span class="state ' + esc(a.kind || "idle") + '">' + icon(a.icon) + esc(a.state) + "</span>" +
        "<span>" + esc(a.state_hint) + "</span></div>" +
        '<h3>Текущая работа</h3><p>' + esc(a.doing) + "</p>" +
        (a.checks != null ? "<h3>Проверки</h3><p>Проверок выполнено: " + (Number(a.checks) || 0) +
          (Number(a.completed) > 0 ? ", принято по проверке: " + (Number(a.completed) || 0) : "") +
          "</p><p class='muted'>Результат, принятый руководителем своим решением, в счёт проверок не входит.</p>" : "") +
        (mine.length ? "<h3>Задачи специалиста</h3><ul class='tasks'>" + mine.map(function (t) {
          return "<li><span>" + esc(t.title) + (t.role && t.role !== a.name ? ' <span class="muted small">как ' + esc(t.role) + "</span>" : "") +
            '</span><span class="state ' + esc(t.kind) + '">' + esc(t.state) + "</span></li>";
        }).join("") + "</ul>" : "") +
        "<h3>История работы</h3>" + eventList(history) + techBlock(a.tech);
    } else {
      var t = list(lastSnap.tasks).filter(function (x) { return x.id === sheetTarget.key; })[0];
      if (!t) return;
      title = t.title;
      var owner = list(lastSnap.agents).filter(function (a) {
        return list(a.tasks).some(function (x) { return x.id === t.id; });
      })[0];
      html = "<h3>Что нужно сделать</h3>" +
        (t.description ? "<p>" + esc(t.description) + "</p>" : "") +
        '<div class="row"><span class="state ' + esc(t.kind) + '">' + icon(t.icon) + esc(t.state) + "</span>" +
        "<span>" + esc(t.state_hint) + "</span></div>" +
        "<p>Дальше: " + esc(t.next_step) + "</p>" +
        (list(t.expected).length ? "<p>Ожидаемый результат: " + esc(t.expected.join(", ")) + "</p>" : "") +
        (list(t.criteria).length ? "<h4>Как проверяется</h4><ul class='plain'>" + list(t.criteria).map(function (c) {
          return "<li>" + esc(c.text) + ' <span class="muted">(' + esc(c.how) + ")</span></li>";
        }).join("") + "</ul>" : "") +
        "<h3>Кто выполняет</h3><p>" + esc(t.role) +
        (owner ? ", " + esc(owner.state) : "") +
        (t.worked_s == null ? "" : (t.active ? ", в работе " : ", длилась ") +
         fmtDur(Number(t.worked_s) || 0)) + "</p>" +
        "<h3>Что уже сделано</h3>" + track(checkpoints(t), true) +
        '<ol class="steps">' + CHECK_NAMES.map(function (name, i) {
          return '<li class="' + (checkpoints(t)[i] ? "on" : "") + '">' + name + "</li>";
        }).join("") + "</ol>" +
        (list(t.changed_files).length ? "<p>Изменённые файлы: " +
          esc(list(t.changed_files).join(", ")) +
          (list(t.violations).length ? " <span class='bad-text'>(вне разрешённой зоны: " +
            esc(list(t.violations).join(", ")) + ")</span>" : "") + "</p>" : "") +
        (t.last_event && t.last_event.title ? "<p>Последнее обновление: " + esc(t.last_event.title) + "</p>" : "") +
        "<h3>Проверка результата</h3>" +
        (list(t.checks).length ? "<ol class='checks'>" + list(t.checks).map(function (c) {
          var verdict = c.passed ? (c.manual ? "Принято вручную, без проверки" : "Проверка пройдена") : "Проверка не пройдена";
          return "<li>" + icon(c.passed ? "check" : "alert") + "<div><div class='tt'>" + verdict + "</div>" +
            (c.feedback ? '<div class="dd">' + esc(c.feedback) + "</div>" : "") + "</div></li>";
        }).join("") + "</ol>" : '<p class="muted">Результат ещё не передавался на проверку.</p>') +
        "<h3>Готовый результат</h3>" + resultBlock(t) +
        "<h3>История этапа</h3><div id=\"taskHistory\">" +
        eventList(list(sheetTarget.history || [])) + "</div>" + techBlock(t.tech);
    }
    $("sheetTitle").textContent = title;
    body.innerHTML = html;
    applyPrefs();
    if (sheetTarget.openFile && sheetTarget.kind === "task") showResult(sheetTarget.key, sheetTarget.openFile);
  }
  /* The files a task produced, and one of them opened (spec 8.5). The
     text comes from the board's own address and is shown as text. */
  var resultCache = {};
  function resultBlock(t) {
    var files = list(t.result && t.result.files);
    if (!files.length) return "<p class='muted'>" + esc(
      t.result && t.result.handed_over
        ? "Результат передан, но файлов в нём нет: исполнитель ничего не подготовил."
        : "Результат ещё не передавался.") + "</p>";
    var times = Number(t.result.handed_over_count) || 0;
    return "<p>" + (t.result.ready ? "Принятая версия результата." : "Промежуточный результат, ещё не принят.") +
      (times > 1 ? " Результат передавался " + times + " " + plural(times, "раз", "раза", "раз") +
       ", показана последняя версия." : "") + " Файлы:</p>" +
      '<ul class="files">' + files.map(function (f) {
        var open = sheetTarget && sheetTarget.openFile === f;
        return '<li><button type="button" class="file-btn' + (open ? " on" : "") + '" data-result-file="' + esc(f) +
          '" aria-pressed="' + String(!!open) + '">' + esc(f) + "</button></li>";
      }).join("") + "</ul>" + '<div class="result" id="resultView"></div>';
  }
  function resultUrl(id, file) {
    return "/api/results/" + encodeURIComponent(id) + "/" + file.split("/").map(encodeURIComponent).join("/") + API;
  }
  function showResult(id, file) {
    var box = $("resultView");
    if (!box) return;
    var key = id + "/" + file;
    if (resultCache[key]) { box.innerHTML = resultCache[key]; return; }
    box.innerHTML = '<p class="muted">Открываю…</p>';
    fetch(resultUrl(id, file), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (text) {
        var picture = /\.(png|jpe?g|gif|webp|ico|bmp)$/i.test(file);
        resultCache[key] = picture
          ? '<img class="result-img" src="' + esc(resultUrl(id, file)) + '" alt="' + esc(file) + '">'
          : '<pre class="result-text">' + esc(text) + "</pre>";
        if (sheetTarget && sheetTarget.kind === "task" && sheetTarget.key === id && sheetTarget.openFile === file) {
          box.innerHTML = resultCache[key];
        }
      })
      .catch(function () { box.innerHTML = "<p class='muted'>Не удалось открыть файл. Он мог быть перемещён или слишком велик для показа.</p>"; });
  }
  $("sheetBody").addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-result-file]") : null;
    if (!button || !sheetTarget) return;
    var file = button.getAttribute("data-result-file");
    sheetTarget.openFile = sheetTarget.openFile === file ? null : file;
    renderSheet();
    if (sheetTarget.openFile) showResult(sheetTarget.key, sheetTarget.openFile);
  });

  function openSheet(kind, key) {
    sheetTarget = { kind: kind, key: key };
    renderSheet();
    if (!sheet.open) sheet.showModal();
  }
  function closeSheet() {
    sheetTarget = null;
    if (sheet.open) sheet.close();
  }
  $("sheetClose").addEventListener("click", closeSheet);
  sheet.addEventListener("close", function () { sheetTarget = null; });
  sheet.addEventListener("click", function (event) { if (event.target === sheet) closeSheet(); });

  /* Clicks and the keyboard open the same sheets. */
  function opener(container, attribute, open) {
    function find(event) {
      var node = event.target;
      return node && node.closest ? node.closest("[" + attribute + "]") : null;
    }
    container.addEventListener("click", function (event) {
      var target = find(event);
      if (target) open(target.getAttribute(attribute));
    });
    container.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var target = find(event);
      if (target) { event.preventDefault(); open(target.getAttribute(attribute)); }
    });
  }
  opener($("teamList"), "data-specialist", function (index) { openSheet("specialist", Number(index)); });
  opener($("planList"), "data-task", function (id) { openSheet("task", id); loadHistory(id); });

  function loadHistory(id) {
    fetch("/api/tasks/" + encodeURIComponent(id) + API + "&level=" + encodeURIComponent(store.mode),
      { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!sheetTarget || sheetTarget.kind !== "task" || sheetTarget.key !== id) return;  // the reader moved on
        sheetTarget.history = data.events;
        renderSheet();
      })
      .catch(function () { /* the sheet keeps what the snapshot already told */ });
  }

  /* The bento area has two columns and cards of very different heights (an
     empty attention card is one line, two blocked tasks are a page). A
     fixed placement leaves a hole under the shorter column, so the cards
     are split into the two most even groups, measured after every render,
     and each column keeps the reading priority. */
  var CARD_ORDER = ["actCard", "attCard", "nowCard", "teamCard", "planCard", "timeCard", "feedCard",
                    "qualityCard", "moneyCard", "problemCard", "buildCard", "benchCard"];
  function atBottom(feed) {
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
  }
  function layoutColumns(stick) {
    var a = $("colA"), b = $("colB"), feed = $("feed");
    if (stick === undefined) stick = atBottom(feed);  // a resize: keep where the reader is
    // A card the data does not fill (no benchmark declared) stays out.
    var cards = CARD_ORDER.filter(function (id) { return !$(id).classList.contains("hidden"); });
    if (window.matchMedia("(max-width: 900px)").matches) {
      cards.forEach(function (id) { a.appendChild($(id)); });
    } else {
      var height = {};
      cards.forEach(function (id) { height[id] = $(id).offsetHeight; });
      var bestMask = 0, bestDiff = Infinity;
      for (var mask = 0; mask < (1 << cards.length); mask++) {  // a set bit = column B
        var ha = 0, hb = 0;
        cards.forEach(function (id, j) { if ((mask >> j) & 1) hb += height[id]; else ha += height[id]; });
        if (Math.abs(ha - hb) < bestDiff) { bestDiff = Math.abs(ha - hb); bestMask = mask; }
      }
      cards.forEach(function (id, j) { ((bestMask >> j) & 1 ? b : a).appendChild($(id)); });
    }
    // A card moved to another column starts scrolled to its top. The
    // feed goes back to its newest line, unless the reader has scrolled
    // up to read the history.
    if (stick) feed.scrollTop = feed.scrollHeight;
  }

  function render(snap, knownSeq) {
    renderHeader(snap);
    $("updatedAt").textContent = "обновлено в " + fmtClock(snap.now);
    renderProgress(snap.progress);
    renderNow(snap.tasks, snap.cost);
    renderTeam(snap.agents, snap.tasks);
    renderPlan(snap.tasks);
    renderGraph(snap.tasks);
    renderTimeline(snap.timeline);
    renderActions(snap);
    renderAttention(snap.attention);
    renderQuality(snap.quality);
    renderMoney(snap.budget, snap.cost);
    renderProblems(snap.recovery);
    renderBuild(snap.integration);
    renderBenchmark(snap.benchmark);
    var stick = renderFeed(snap.events, knownSeq);
    applyPrefs();
    layoutColumns(stick);
    renderSheet();   // an open sheet follows the data, it is not closed by a refresh
  }

  /* ---- what the owner can do (spec 17). The engine offers, the page
     asks for a confirmation where there are consequences, and the server
     does it through the engine: the lead token is in the memory of the
     server and never in this page. ---- */
  var SAID = {
    pause: "Пауза поставлена: новые задачи не начинаются.",
    resume: "Пауза снята: новые задачи запускаются.",
    retry: "Этап возвращён в очередь.",
    cancel: "Этап отменён.",
    request_check: "Просьба передана руководителю команды: он проверит результат.",
    apply: "Изменения внесены в проект."
  };
  var NOT_DONE = {
    state: "Сейчас так нельзя: состояние команды изменилось. Доска обновится, и действие появится, когда оно снова будет возможно.",
    budget: "Бюджет исчерпан: новые задачи не запускаются.",
    forbidden: "Доска не имеет права на это действие: перезапустите её с правом управления.",
    not_found: "Этапа больше нет в плане.",
    origin: "Запрос пришёл не с этой страницы.",
    usage: "Действие не выполнено: запрос составлен неправильно."
  };
  var acting = false;
  function sayAction(text, bad) {
    var box = $("actResult");
    box.textContent = text;
    box.className = "action-result" + (bad ? " bad" : "");
  }
  function renderActions(snap) {
    var card = $("actCard");
    // Only a board started with the lead's authority can act.
    if (snap.control === false) store.control = false;
    if (snap.control === true) store.control = true;
    if (!store.control) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    var actions = list(snap.actions);
    if (!actions.length) {
      paint($("actList"), '<p class="muted">Сейчас от вас ничего не нужно.</p>');
      return;
    }
    paint($("actList"), actions.map(function (a) {
      return '<button type="button" class="action' + (a.confirm ? " danger" : "") + '" data-action="' +
        esc(a.key) + '" data-task="' + esc(a.task || "") + '" data-confirm="' + esc(a.confirm || "") +
        '" data-ask="' + esc(a.ask || "") + '">' + esc(a.label) + "</button>";
    }).join(""));
  }
  function doAction(button) {
    if (acting) return;
    var action = button.getAttribute("data-action");
    var body = {action: action, confirm: true};
    // An action with consequences is done only after the owner agrees to it.
    var asked = button.getAttribute("data-confirm");
    if (asked && !window.confirm(asked)) return;
    var question = button.getAttribute("data-ask");
    if (question) {
      var given = window.prompt(question, "");
      if (given == null || given.trim() === "") return;
      var usd = Number(String(given).replace(/\s|\$/g, "").replace(",", "."));
      if (!(usd > 0)) { sayAction("Нужно число больше нуля, в долларах.", true); return; }
      body.usd = usd;
    }
    var task = button.getAttribute("data-task");
    if (task) body.task = task;
    acting = true;
    button.disabled = true;
    sayAction("Выполняю…", false);
    fetch("/api/actions" + API, { method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) })
      .then(answer, function () { throw {kind: "connection"}; })
      .then(function (result) {
        var said = SAID[action] || "Готово.";
        if (action === "integrate") {
          said = result.ok === false
            ? "Объединить не удалось: найдено противоречие. Исходные версии на месте."
            : result.applied === true ? "Результаты объединены и применены к проекту."
            : "Результаты объединены и проверены. Осталось применить их к проекту.";
        }
        if (action === "budget") said = "Бюджет изменён: " + moneyText(result.budget_usd) + ".";
        if (store.mode === "advanced" && result.error) said += " (" + result.error + ")";
        sayAction(said, false);
      }, function (err) {
        var text = NOT_DONE[err && err.kind] ||
          "Действие не выполнено: доска не связалась с сервером. Попробуйте ещё раз.";
        if (store.mode === "advanced" && err && err.message) text += " (" + err.message + ")";
        sayAction(text, true);
      })
      .then(function () {
        acting = false;
        button.disabled = false;
        takeSnapshot(true);        // the state may have moved: show it as it is now
      });
  }
  $("actList").addEventListener("click", function (event) {
    var node = event.target;
    var button = node && node.closest ? node.closest("[data-action]") : null;
    if (button) doAction(button);
  });

  /* ---- polling: refresh data, keep user context (spec 16.4) ----
     Every few seconds the page asks a light "has anything changed?". The
     whole snapshot, with the whole feed, is taken only when something did
     (or once in a while, so the clocks stay true). */
  function show(snap, force) {
    var known = lastSnap ? lastSnap.last_seq : null;
    lastGood = Date.now(); snapshotAt = Date.now(); lastSnap = snap;
    setConn("", "");
    /* A quiet team has nothing new in the journal, but the two levels
       show different things: the level the reader picked redraws too. */
    if (!force && known !== null && snap.last_seq === known && snap.level === shownLevel) {
      timers();
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

  function answer(r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (!r.ok) {
        var err = new Error(body.error || ("HTTP " + r.status));
        err.status = r.status;
        err.kind = body.kind;        // why the server refused, for the owner's words
        throw err;
      }
      return body;
    });
  }

  function request(url) {
    var controller = new AbortController();
    clearTimeout(requestTimer);
    requestTimer = setTimeout(function () { controller.abort(); }, 8000);
    setRefreshing(true);
    return fetch(url, { signal: controller.signal, credentials: "same-origin", cache: "no-store" })
      .then(function (r) { clearTimeout(requestTimer); return answer(r); })
      .then(function (body) { setRefreshing(false); return body; },
            function (err) { setRefreshing(false); throw err; });
  }

  function takeSnapshot(force) {
    return request("/api/snapshot" + API + "&level=" + encodeURIComponent(store.mode))
      .then(function (snap) { show(snap, force); }, requestFailed);
  }

  function load() {
    if (!lastSnap) return takeSnapshot();   // nothing on screen yet
    request("/api/status" + API)
      .then(function (status) {
        lastGood = Date.now();
        setConn("", "");
        // The right to act can change only with a restart of the board,
        // and then the picture has to follow at once.
        var controlChanged = typeof status.control === "boolean" && status.control !== store.control;
        if (controlChanged) store.control = status.control;
        var changed = status.last_seq !== lastSnap.last_seq;
        if (changed || controlChanged || Date.now() - snapshotAt > RESYNC_MS) return takeSnapshot(controlChanged);
        $("updatedAt").textContent = "обновлено в " + fmtClock(status.now);
        timers();
      }, requestFailed);
  }
  applyPrefs();
  if (!TEAM) {
    setConn("lost", "Не указана команда: откройте адрес, который напечатала команда team.py ui");
    return;
  }
  load();
  setInterval(load, POLL_MS);
  setInterval(timers, 1000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
  window.addEventListener("resize", function () { layoutColumns(); });
})();
