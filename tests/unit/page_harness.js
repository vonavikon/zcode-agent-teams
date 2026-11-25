/* Runs the dashboard page script on a fake page (v2.1 spec, sections 4, 5,
 * 8 and 16).
 *
 * The page must render real data without a single error and must not let
 * any value from the wire become markup. A browser is not needed for
 * that: the script is plain DOM code, so a small fake document is enough
 * to run it under Node and to read what it wrote.
 *
 * Usage: node page_harness.js <dashboard dir> <scenario>
 *        (the states of the server arrive on stdin as a JSON array, in
 *        order: every poll moves the world to the next one)
 * Output: one JSON object with what the page wrote and did.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2];
const scenario = process.argv[3];
const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const pageSource = fs.readFileSync(path.join(dir, "app.js"), "utf8");

/* Only the ids the real page has: a script that asks for another one is
   a bug, and it must fail here rather than pass on a forgiving stub. */
const ids = Array.from(html.matchAll(/\bid="([^"]+)"/g), (m) => m[1]);

const state = {
  html: {}, text: {}, attrs: {}, classes: {}, writes: {}, listeners: {}, generation: 0,
  fetches: [], posts: [], intervals: [], tech: [], errors: [], prefs: {}, now: 1_000_000,
  confirmAnswer: true, promptAnswer: null,
  snapshots: JSON.parse(fs.readFileSync(0, "utf8")), cursor: 0, failWith: null,
  responses: [], timerTexts: [],
};

function classList(id) {
  const names = () => state.classes[id] || [];
  const set = (list) => { state.classes[id] = list; };
  return {
    toggle(name, force) {
      const on = force === undefined ? !names().includes(name) : !!force;
      set(on ? [...new Set([...names(), name])] : names().filter((n) => n !== name));
      return on;
    },
    add(name) { this.toggle(name, true); },
    remove(name) { this.toggle(name, false); },
    contains(name) { return names().includes(name); },
  };
}

function makeElement(id) {
  state.html[id] = "";          // a new element is empty, whoever held the id before
  state.text[id] = "";
  const el = {
    id, style: {}, value: "", offsetHeight: 100, scrollHeight: 1000, scrollTop: 0, clientHeight: 100,
    children: [], parent: null, classList: classList(id),
    setAttribute(k, v) { (state.attrs[id] = state.attrs[id] || {})[k] = String(v); },
    getAttribute(k) { return (state.attrs[id] || {})[k]; },
    addEventListener(type, fn) { (state.listeners[id] = state.listeners[id] || {})[type] = fn; },
    // The page searches inside a card for its bars; the fake page finds none.
    querySelectorAll() { return []; },
    appendChild(child) {
      // Like the real DOM: a node that already has a parent is moved to the end.
      if (child.parent) child.parent.children = child.parent.children.filter((c) => c !== child);
      el.children.push(child);
      child.parent = el;
      return child;
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get: () => state.html[id] || "",
    set: (v) => {
      state.html[id] = String(v); state.writes[id] = (state.writes[id] || 0) + 1;
      if (ids.includes(id)) state.generation += 1;   // a block of the page was drawn anew
    },
  });
  Object.defineProperty(el, "textContent", {
    get: () => state.text[id] || "", set: (v) => { state.text[id] = String(v); },
  });
  Object.defineProperty(el, "className", {
    get: () => (state.classes[id] || []).join(" "),
    set: (v) => { state.classes[id] = String(v).split(/\s+/).filter(Boolean); },
  });
  if (id === "sheet") {  // the detail sheet is a <dialog>
    el.open = false;
    el.showModal = () => { el.open = true; };
    el.close = () => {
      el.open = false;
      if (state.listeners.sheet && state.listeners.sheet.close) state.listeners.sheet.close();
    };
  }
  return el;
}

const elements = Object.fromEntries(ids.map((id) => [id, makeElement(id)]));
/* Ids the page writes into its own markup (a viewport, a box for a result):
   looked up only once that markup exists, so a typo still fails. */
const dynamic = {};
function writtenIds(id) {
  /* Like the real DOM: when the page rewrites the markup that holds the
     element, a new empty element takes its place. */
  if (!new RegExp('id="' + id + '"').test(written())) return null;
  if (!dynamic[id] || dynamic[id].generation !== state.generation) {
    const el = makeElement(id);
    dynamic[id] = { el, generation: state.generation };
  }
  return dynamic[id].el;
}
/* Chip buttons have no id: they are found by their attribute. */
function chips(attribute) {
  const values = Array.from(html.matchAll(new RegExp('data-' + attribute.slice(5) + '="([^"]+)"', "g")), (m) => m[1]);
  return values.map((value) => {
    const key = "chip:" + attribute + "=" + value;
    return {
      value,
      classList: classList(key),
      getAttribute: (name) => (name === attribute ? value : null),
      setAttribute: (name, v) => { (state.attrs[key] = state.attrs[key] || {})[name] = String(v); },
      addEventListener: (type, fn) => { (state.listeners[key] = state.listeners[key] || {})[type] = fn; },
    };
  });
}
const htmlElement = makeElement("html");
const docListeners = {};

const written = () => Object.values(state.html).join("\n");

/* The page finds its technical blocks with a class selector, and the
   places its clocks are shown with an attribute selector. The fake page
   cannot parse markup, so it lists what the page itself just wrote and
   records what is done to them. */
function techBlocks() {
  return (written().match(/class="tech( hidden)?"/g) || []).map(() => ({
    classList: { toggle: (name, force) => state.tech.push([name, !!force]) },
  }));
}
function clockPlaces() {
  const found = [];
  for (const m of written().matchAll(/<(\w+)[^>]*\bdata-(silent|worked)="(\d+)"[^>]*>/g)) {
    const kind = m[2];
    found.push({
      getAttribute: (name) => (name === "data-" + kind ? m[3] : name === "data-live" ? (kind === "worked" ? "1" : null) : null),
      set textContent(v) { state.timerTexts.push([kind, String(v)]); },
    });
  }
  return found;
}

global.window = global;
global.innerWidth = 1440;
global.matchMedia = () => ({ matches: false });
global.addEventListener = (type, fn) => { (state.listeners.window = state.listeners.window || {})[type] = fn; };
global.location = { search: scenario === "noteam" ? "" : "?team=demo" };
global.localStorage = {
  getItem: (k) => (k in state.prefs ? state.prefs[k] : null),
  setItem: (k, v) => { state.prefs[k] = String(v); },
};
global.document = {
  hidden: false,
  documentElement: htmlElement,
  getElementById: (id) => elements[id] || (ids.includes(id) ? null : writtenIds(id)),
  querySelectorAll: (selector) => (selector === ".tech" ? techBlocks()
    : selector === "[data-silent],[data-worked]" ? clockPlaces()
    : /^\[data-(plan-filter|feed-kind)\]$/.test(selector) ? chips(selector.slice(1, -1)) : []),
  addEventListener: (type, fn) => { docListeners[type] = fn; },
};
global.setInterval = (fn) => { state.intervals.push(fn); return state.intervals.length; };
global.Date.now = () => state.now;
global.console.error = (...args) => state.errors.push(args.join(" "));
global.confirm = () => state.confirmAnswer;
global.prompt = () => state.promptAnswer;
global.fetch = (url, options) => {
  if (options && options.method === "POST") {     // a control action (spec 17)
    const body = JSON.parse(options.body);
    state.posts.push({ url, body });
    const hit = state.responses.find((r) => r.match === "POST");
    if (hit) {
      state.responses.splice(state.responses.indexOf(hit), 1);
      const status = hit.status || 200;
      return Promise.resolve({ ok: status < 400, status, json: async () => hit.body });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true, ...body }) });
  }
  state.fetches.push(url);
  if (state.failWith) return Promise.reject(state.failWith);
  const hit = state.responses.find((r) => url.includes(r.match));
  if (hit) {   // a scripted answer, well-formed or not
    state.responses.splice(state.responses.indexOf(hit), 1);
    return Promise.resolve({ ok: hit.ok, status: hit.status, json: async () => hit.body });
  }
  const level = (/level=(\w+)/.exec(url) || [, "simple"])[1];
  const next = state.snapshots[state.cursor];   // what the server is like right now
  const likeTheServer = (body) => Promise.resolve({ ok: true, status: 200, json: async () => body });
  if (/before=/.test(url)) {                // reading further back in the journal
    const before = Number(/before=(\d+)/.exec(url)[1]);
    return likeTheServer({ ok: true, last_seq: next.last_seq, now: next.now,
                           events: (next.older_events || []).filter((e) => e.seq < before) });
  }
  if (/\/api\/status/.test(url)) {          // the light "has anything changed?"
    const { now, run, progress, last_seq, attention, cost } = next;
    const status = { ok: true, now, run, progress, last_seq, attention: attention.length, cost };
    if (next.control !== undefined) status.control = next.control;
    return likeTheServer(status);
  }
  if (/\/api\/results\//.test(url)) {       // one file of a task's result
    const match = /\/api\/results\/([^/]+)\/(.+)$/.exec(url.split("?")[0]);
    const files = (next.results || {})[decodeURIComponent(match[1])] || {};
    const content = files[decodeURIComponent(match[2])];
    if (content === undefined) return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    if (content && content.tooLarge) return Promise.resolve({ ok: false, status: 413, json: async () => ({}) });
    return Promise.resolve({ ok: true, status: 200, text: async () => (typeof content === "string" ? content : "") });
  }
  if (/\/api\/tasks\//.test(url)) {         // the history of one task
    const id = decodeURIComponent(/\/api\/tasks\/([^?]+)/.exec(url)[1]);
    const task = next.tasks.find((t) => t.id === id);
    return task ? likeTheServer({ ok: true, task, level, now: next.now,
                                   events: (next.task_events || {})[id] || [] })
                : Promise.resolve({ ok: false, status: 404, json: async () => ({ ok: false, error: "task not found" }) });
  }
  // Like the server, the answer says which level it was asked for, and
  // (the snapshot alone) whether the owner can act.
  const whole = { ...next, level };
  if (next.control !== undefined && /\/api\/snapshot/.test(url)) whole.control = next.control;
  return likeTheServer(whole);
};

vm.runInThisContext(pageSource, { filename: "app.js" });

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
const poll = async () => {   // the world moves on, then the page asks
  state.cursor = Math.min(state.cursor + 1, state.snapshots.length - 1);
  state.intervals[0]();
  await settle();
};
const click = async (id) => { state.listeners[id].click(); await settle(); };
const dom = () => Object.fromEntries(ids.map((id) => [id, {
  html: state.html[id] || "", text: state.text[id] || "", cls: (state.classes[id] || []).join(" "),
}]));
const feed = () => state.html.feed || "";
const card = (attribute, value) => ({ target: { closest: (selector) => (selector === "[" + attribute + "]" ? { getAttribute: () => value } : null) } });
/* A button of the actions card as the page just wrote it. */
function actionTarget(key) {
  const tag = (state.html.actList.match(/<button[^>]*>/g) || [])
    .find((t) => t.includes('data-action="' + key + '"'));
  if (!tag) throw new Error("no action button " + key);
  const attrs = Object.fromEntries(Array.from(tag.matchAll(/data-([a-z]+)="([^"]*)"/g), (m) => ["data-" + m[1], m[2]]));
  const button = { getAttribute: (name) => (name in attrs ? attrs[name] : null), disabled: false };
  return { target: { closest: (selector) => (selector === "[data-action]" ? button : null) } };
}

async function main() {
  await settle();
  const out = { fetches: state.fetches };
  if (scenario === "render") {
    out.dom = dom();
    out.tech = state.tech;
    out.theme = state.attrs.html;
    out.console = state.errors.map((line) => line.split("\n").slice(0, 3).join(" | "));
  } else if (scenario === "modes") {
    out.simpleTech = state.tech.filter(([, hidden]) => hidden === false).length;
    out.simpleMoney = state.html.moneyBody;
    await click("modeAdvanced");
    out.advancedFetch = state.fetches.at(-1);
    out.advancedMoney = state.html.moneyBody;
    out.advancedFeed = feed();
    out.advancedTechShown = state.tech.filter(([, hidden]) => hidden === false).length;
    out.advancedPlan = state.html.planList;
    out.advancedWrites = state.writes.planList;
    await click("modeSimple");
    out.simpleAgainFetch = state.fetches.at(-1);
    out.simpleFeed = feed();
    await click("themeBtn");
    out.themeAfterClick = { ...state.attrs.html };
    await click("themeBtn");
    out.themeAfterSecondClick = { ...state.attrs.html };
    out.prefs = state.prefs;
  } else if (scenario === "control") {
    /* The owner's actions (spec 17): steps script the answers of the
       confirmation and of the question for a number, and the server's. */
    const shown = () => ({ hidden: (state.classes.actCard || []).includes("hidden"),
                           buttons: state.html.actList, result: state.text.actResult,
                           resultCls: (state.classes.actResult || []).join(" "),
                           posts: state.posts.map((p) => p.body),
                           snapshots: state.fetches.filter((u) => u.includes("/api/snapshot")).length,
                           header: state.text.runStateText, dot: state.classes.runDot });
    out.steps = [{ name: "start", ...shown() }];
    for (const step of state.snapshots[0]._control || []) {
      const [kind, a] = step;
      if (kind === "confirm") state.confirmAnswer = a;
      else if (kind === "prompt") state.promptAnswer = a;
      else if (kind === "mode") await click(a === "advanced" ? "modeAdvanced" : "modeSimple");
      else if (kind === "answer") state.responses.push({ match: "POST", ...a });
      else if (kind === "press") {
        state.listeners.actList.click(actionTarget(a));
        await settle(); await settle(); await settle(); await settle();
      } else if (kind === "poll") await poll();
      out.steps.push(Object.assign({ name: step.join(" ") }, shown()));
    }
  } else if (scenario === "polls") {
    out.first = feed();
    out.writesAfterFirst = state.writes.feed;
    await poll();                       // same last_seq: nothing new
    out.writesAfterSame = state.writes.feed;
    out.feedAfterSame = feed();
    out.snapshotFetchesAfterSame = state.fetches.filter((u) => u.includes("/api/snapshot")).length;
    await poll();                       // a new event arrived
    out.writesAfterNew = state.writes.feed;
    out.feedAfterNew = feed();
    out.snapshotFetchesAfterNew = state.fetches.filter((u) => u.includes("/api/snapshot")).length;
    out.newLineMarked = (feed().match(/is-new/g) || []).length;
    await poll();                       // another one
    out.markedAfterNext = (feed().match(/is-new/g) || []).length;
  } else if (scenario === "outage") {
    out.storyBefore = state.text.storyLine;
    state.failWith = new TypeError("Failed to fetch");
    state.now += 5000;
    await poll();
    out.afterFiveSeconds = [state.classes.conn.join(" "), state.text.conn];
    state.now += 20000;
    await poll();
    out.afterALongTime = [state.classes.conn.join(" "), state.text.conn];
    out.storyAfter = state.text.storyLine;
    out.keptContent = state.html.attList;
  } else if (scenario === "badrequest") {
    state.responses.push({ match: "/api/status", ok: false, status: 404,
      body: { ok: false, error: "no team state at /x/state.db" } });
    await poll();
    out.banner = [state.classes.conn.join(" "), state.text.conn];
    out.console = state.errors;
  } else if (scenario === "badrender") {
    out.storyBefore = state.text.storyLine;
    // The status says something changed; the snapshot that follows is broken.
    state.responses.push({ match: "/api/snapshot", ok: true, status: 200,
      body: { team: "demo", level: "simple", last_seq: 7, tasks: null } });
    await poll();
    out.banner = [state.classes.conn.join(" "), state.text.conn];
    out.storyAfter = state.text.storyLine;
    out.console = state.errors.map((line) => line.split("\n")[0]);
  } else if (scenario === "noteam") {
    out.banner = [state.classes.conn.join(" "), state.text.conn];
    out.intervals = state.intervals.length;
  } else if (scenario === "resize") {
    out.columnsBefore = ["colA", "colB"].map((id) => elements[id].children.map((c) => c.id));
    global.innerWidth = 390;
    global.matchMedia = (q) => ({ matches: q === "(max-width: 900px)" });
    state.listeners.window.resize();
    out.columnsNarrow = ["colA", "colB"].map((id) => elements[id].children.map((c) => c.id));
    global.innerWidth = 1440;
    global.matchMedia = () => ({ matches: false });
    state.listeners.window.resize();
    out.columnsWide = ["colA", "colB"].map((id) => elements[id].children.map((c) => c.id));
    out.allCardsPlaced = ["attCard", "nowCard", "teamCard", "planCard", "feedCard"].every((id) =>
      ["colA", "colB"].some((c) => elements[c].children.some((x) => x.id === id)));
  } else if (scenario === "sheet") {
    state.listeners.teamList.click(card("data-specialist", "1"));
    await settle();
    out.specialist = { open: elements.sheet.open, title: state.text.sheetTitle, body: state.html.sheetBody };
    // A task opens from the keyboard as well as from a click.
    let prevented = false;
    state.listeners.planList.keydown({ key: "Enter", preventDefault: () => { prevented = true; },
                                       ...card("data-task", "a") });
    await settle(); await settle();
    out.task = { prevented, open: elements.sheet.open, title: state.text.sheetTitle,
                 body: state.html.sheetBody, lastFetch: state.fetches.at(-1) };
    await poll();                        // new data: the sheet stays open and follows it
    out.afterRefresh = { open: elements.sheet.open, title: state.text.sheetTitle };
    await click("sheetClose");
    out.closed = elements.sheet.open;
    out.closedByBackdrop = (() => {
      state.listeners.planList.click(card("data-task", "a"));
      return elements.sheet.open;
    })();
    state.listeners.sheet.click({ target: elements.sheet });
    out.afterBackdropClick = elements.sheet.open;
  } else if (scenario === "actions") {
    /* A list of steps the test wants performed, each followed by what the
       page shows: zoom and pan of the graph, the feed's filters and pause,
       opening a file of a result. */
    const shown = () => ({
      transform: state.attrs.graphViewport ? state.attrs.graphViewport.transform : null,
      graph: state.html.graph || "",
      nodes: (state.html.graph.match(/class="node /g) || []).length,
      feed: state.html.feed, pause: state.text.feedPause,
      olderHidden: (state.classes.feedOlder || []).includes("hidden"),
      sheet: state.html.sheetBody, sheetTitle: state.text.sheetTitle,
      // looked up, so that a redrawn sheet yields a fresh empty box like in a browser
      result: writtenIds("resultView") ? state.html.resultView : "",
      fetches: state.fetches.slice(),
    });
    out.steps = [];
    const record = (name) => out.steps.push(Object.assign({ name }, shown()));
    record("start");                       // steps[0] is before anything is done
    for (const step of state.snapshots[0]._actions || []) {
      const [kind, a, b, c] = step;
      if (kind === "click") await click(a);
      else if (kind === "set") elements[a].value = b;
      else if (kind === "input") { state.listeners[a].input(); await settle(); }
      else if (kind === "change") { state.listeners[a].change(); await settle(); }
      else if (kind === "chip") {
        const chip = chips(a).find((x) => x.value === b);
        state.listeners["chip:" + a + "=" + b].click(); await settle();
      } else if (kind === "pan") {
        const target = { closest: () => null };
        state.listeners.graphWrap.pointerdown({ clientX: 100, clientY: 100, target, pointerId: 1 });
        state.listeners.graphWrap.pointermove({ clientX: 100 + a, clientY: 100 + b, target });
        state.listeners.graphWrap.pointerup({});
      } else if (kind === "node") {         // press a stage on the graph
        state.listeners.graph.click(card("data-task", a)); await settle(); await settle();
      } else if (kind === "feedrow") {      // press a line of the feed about a stage
        state.listeners.feed.click(card("data-task", a)); await settle(); await settle();
      } else if (kind === "trow") {         // press a row of the timeline
        state.listeners.timeline.click(card("data-task", a)); await settle(); await settle();
      } else if (kind === "sheet") {        // open a task from the plan list
        state.listeners.planList.click(card("data-task", a)); await settle(); await settle();
      } else if (kind === "file") {         // press a file of the result
        state.listeners.sheetBody.click({ target: { closest: (sel) => (sel === "[data-result-file]" ? { getAttribute: () => a } : null) } });
        await settle(); await settle();
      } else if (kind === "older") { await click("feedOlder"); await settle(); }
      else if (kind === "poll") { await poll(); }
      record(step.join(" "));
    }
  } else if (scenario === "timers") {
    state.timerTexts.length = 0;
    state.now += 65_000;                 // a little over a minute passes
    state.intervals[1]();                // the one-second clock tick
    out.clock = state.timerTexts.slice();
    out.elapsed = state.text.elapsed;
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({ crashed: String(error && error.stack || error) }));
  process.exitCode = 2;
});
