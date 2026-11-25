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
  html: {}, text: {}, attrs: {}, classes: {}, writes: {}, listeners: {},
  fetches: [], intervals: [], tech: [], errors: [], prefs: {}, now: 1_000_000,
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
  const el = {
    id, style: {}, offsetHeight: 100, scrollHeight: 1000, scrollTop: 0, clientHeight: 100,
    children: [], parent: null, classList: classList(id),
    setAttribute(k, v) { (state.attrs[id] = state.attrs[id] || {})[k] = String(v); },
    getAttribute(k) { return (state.attrs[id] || {})[k]; },
    addEventListener(type, fn) { (state.listeners[id] = state.listeners[id] || {})[type] = fn; },
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
    set: (v) => { state.html[id] = String(v); state.writes[id] = (state.writes[id] || 0) + 1; },
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
  getElementById: (id) => elements[id] || null,
  querySelectorAll: (selector) => (selector === ".tech" ? techBlocks()
    : selector === "[data-silent],[data-worked]" ? clockPlaces() : []),
  addEventListener: (type, fn) => { docListeners[type] = fn; },
};
global.setInterval = (fn) => { state.intervals.push(fn); return state.intervals.length; };
global.Date.now = () => state.now;
global.console.error = (...args) => state.errors.push(args.join(" "));
global.fetch = (url) => {
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
  if (/\/api\/status/.test(url)) {          // the light "has anything changed?"
    const { now, run, progress, last_seq, attention, cost } = next;
    return likeTheServer({ ok: true, now, run, progress, last_seq, attention: attention.length, cost });
  }
  if (/\/api\/tasks\//.test(url)) {         // the history of one task
    const id = decodeURIComponent(/\/api\/tasks\/([^?]+)/.exec(url)[1]);
    const task = next.tasks.find((t) => t.id === id);
    return task ? likeTheServer({ ok: true, task, level, now: next.now,
                                   events: (next.task_events || {})[id] || [] })
                : Promise.resolve({ ok: false, status: 404, json: async () => ({ ok: false, error: "task not found" }) });
  }
  // Like the server, the answer says which level it was asked for.
  return likeTheServer({ ...next, level });
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
    await click("modeAdvanced");
    out.advancedFetch = state.fetches.at(-1);
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
