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
    return d.toLocaleDateString("ru-RU", {day: "numeric", month: "long"}) + ", " + fmtClock(iso);
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
    var rsKind = !rs.state ? "idle" : /внимание/.test(rs.state) ? "problem" : /Завершено/.test(rs.state) ? "done" : "busy";
    $("runState").className = "pill";
    $("runDot").className = "dot " + rsKind;
    $("elapsed").classList.remove("hidden");
    var started = $("startedAt");
    started.classList.remove("hidden");
    started.textContent = "началось " + fmtStart(project.started_at);
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
    $("nowList").innerHTML = html;
  }

  function renderTeam(agents, tasks) {
    $("teamList").innerHTML = agents.map(function (a, index) {
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
    }).join("") || '<div class="empty">Специалистов пока нет: план ещё не сформирован.</div>';
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
        (expected.length ? '<div class="files"><span class="label">результат:</span>' +
          expected.map(function (f) { return fileChip(f, false); }).join("") + "</div>" : "") +
        (files ? '<div class="files">' + files + "</div>" : "") +
        techBlock(t.tech) +
        "</div>" +
        '<div class="who">' + esc(t.role) + (failed ? "<br>" + failed + " " + plural(failed, "раз", "раза", "раз") + " возвращена на доработку" : "") + "</div>" +
        "</div>";
    }).join("") || '<div class="empty">План ещё не сформирован.</div>';
  }

  function renderFeed(events, knownSeq) {
    $("feed").innerHTML = events.map(function (e) {
      var isNew = knownSeq !== null && e.seq > knownSeq;
      var cls = "feed-item " + esc(e.kind || "normal") + (isNew ? " is-new" : "");
      var sub = join([e.specialist, e.task]);
      return '<div class="' + cls + '"><time>' + fmtClock(e.ts) + '</time><div class="mark" aria-hidden="true">' + icon(e.icon) + '</div><div>' +
        '<div class="tt">' + esc(e.title) + '</div>' +
        (sub ? '<div class="sub">' + esc(sub) + '</div>' : '') +
        (e.detail ? '<div class="dd">' + esc(e.detail) + '</div>' : '') +
        techBlock(e.tech) +
        "</div></div>";
    }).join("") || '<div class="empty">Событий пока нет.</div>';
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
        return e.by === sheetTarget.key || (e.by === undefined && e.specialist === a.name);
      }).reverse();
      html = '<p class="does">' + esc(a.does) + "</p>" +
        '<div class="row"><span class="state ' + esc(a.kind || "idle") + '">' + icon(a.icon) + esc(a.state) + "</span>" +
        "<span>" + esc(a.state_hint) + "</span></div>" +
        '<h3>Текущая работа</h3><p>' + esc(a.doing) + "</p>" +
        (a.checks != null ? "<h3>Проверки</h3><p>Проверок выполнено: " + (Number(a.checks) || 0) +
          (Number(a.completed) > 0 ? ", принято по проверке: " + (Number(a.completed) || 0) : "") +
          "</p><p class='muted'>Результат, принятый руководителем своим решением, в счёт проверок не входит.</p>" : "") +
        (mine.length ? "<h3>Задачи специалиста</h3><ul class='tasks'>" + mine.map(function (t) {
          return "<li><span>" + esc(t.title) + '</span><span class="state ' + esc(t.kind) + '">' + esc(t.state) + "</span></li>";
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
        "<h3>История этапа</h3><div id=\"taskHistory\">" +
        eventList(list(sheetTarget.history || [])) + "</div>" + techBlock(t.tech);
    }
    $("sheetTitle").textContent = title;
    body.innerHTML = html;
    applyPrefs();
  }
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
  var CARD_ORDER = ["attCard", "nowCard", "teamCard", "planCard", "feedCard"];
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
    renderHeader(snap);
    $("updatedAt").textContent = "обновлено в " + fmtClock(snap.now);
    renderProgress(snap.progress);
    renderNow(snap.tasks, snap.cost);
    renderTeam(snap.agents, snap.tasks);
    renderPlan(snap.tasks);
    renderAttention(snap.attention);
    var stick = renderFeed(snap.events, knownSeq);
    applyPrefs();
    layoutColumns(stick);
    renderSheet();   // an open sheet follows the data, it is not closed by a refresh
  }

  /* ---- polling: refresh data, keep user context (spec 16.4) ----
     Every few seconds the page asks a light "has anything changed?". The
     whole snapshot, with the whole feed, is taken only when something did
     (or once in a while, so the clocks stay true). */
  function show(snap) {
    var known = lastSnap ? lastSnap.last_seq : null;
    lastGood = Date.now(); snapshotAt = Date.now(); lastSnap = snap;
    setConn("", "");
    /* A quiet team has nothing new in the journal, but the two levels
       show different things: the level the reader picked redraws too. */
    if (known !== null && snap.last_seq === known && snap.level === shownLevel) {
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
      if (!r.ok) { var err = new Error(body.error || ("HTTP " + r.status)); err.status = r.status; throw err; }
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

  function takeSnapshot() {
    return request("/api/snapshot" + API + "&level=" + encodeURIComponent(store.mode))
      .then(show, requestFailed);
  }

  function load() {
    if (!lastSnap) return takeSnapshot();   // nothing on screen yet
    request("/api/status" + API)
      .then(function (status) {
        lastGood = Date.now();
        setConn("", "");
        var changed = status.last_seq !== lastSnap.last_seq;
        if (changed || Date.now() - snapshotAt > RESYNC_MS) return takeSnapshot();
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
