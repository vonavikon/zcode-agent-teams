/* Runs the dashboard page script on a fake page (spec v2.1, section 16).
 *
 * The page must render real data without a single error and must not let
 * any value from the wire become markup. A browser is not needed for
 * that: the script is plain DOM code, so a small fake document is enough
 * to run it under Node and to read what it wrote.
 *
 * Usage: node page_harness.js <dashboard dir> <scenario>
 *        (snapshots, one per poll, arrive on stdin as a JSON array)
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
  snapshots: JSON.parse(fs.readFileSync(0, "utf8")), failWith: null, respond: null,
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
    children: [], classList: classList(id),
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
    parent: null,
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
  return el;
}

const elements = Object.fromEntries(ids.map((id) => [id, makeElement(id)]));
const htmlElement = makeElement("html");
const docListeners = {};

/* The page finds its technical blocks with a class selector. The fake
   page cannot parse markup, so it lists the blocks the page itself just
   wrote and records what is done to them. */
function techBlocks() {
  const written = Object.values(state.html).join("\n").match(/class="tech( hidden)?"/g) || [];
  return written.map(() => ({
    classList: { toggle: (name, force) => state.tech.push([name, !!force]) },
  }));
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
  querySelectorAll: (selector) => (selector === ".tech" ? techBlocks() : []),
  addEventListener: (type, fn) => { docListeners[type] = fn; },
};
global.setInterval = (fn) => { state.intervals.push(fn); return state.intervals.length; };
global.Date.now = () => state.now;
global.console.error = (...args) => state.errors.push(args.join(" "));
global.fetch = (url) => {
  state.fetches.push(url);
  if (state.failWith) return Promise.reject(state.failWith);
  if (state.respond) {
    const { ok, status, body } = state.respond;
    state.respond = null;
    return Promise.resolve({ ok, status, json: async () => body });
  }
  const snap = state.snapshots.shift();
  if (!snap) return new Promise(() => {});  // no more data: the request hangs
  // Like the server, the answer says which level it was asked for.
  const level = /level=(\w+)/.exec(url)[1];
  return Promise.resolve({ ok: true, status: 200, json: async () => ({ ...snap, level }) });
};

vm.runInThisContext(pageSource, { filename: "app.js" });

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => { for (let i = 0; i < 4; i++) await tick(); };
const poll = async () => { state.intervals[0](); await settle(); };
const click = async (id) => { state.listeners[id].click(); await settle(); };
const dom = () => Object.fromEntries(ids.map((id) => [id, {
  html: state.html[id] || "", text: state.text[id] || "", cls: (state.classes[id] || []).join(" "),
}]));
const feed = () => state.html.feed || "";

async function main() {
  await settle();
  const out = { fetches: state.fetches };
  if (scenario === "render") {
    out.dom = dom();
    out.tech = state.tech;
    out.theme = state.attrs.html;
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
    await poll();                       // a new event arrived
    out.writesAfterNew = state.writes.feed;
    out.feedAfterNew = feed();
    out.newLineMarked = /is-new/.test(feed());
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
    state.respond = { ok: false, status: 404, body: { ok: false, error: "no team state at /x/state.db" } };
    await poll();
    out.banner = [state.classes.conn.join(" "), state.text.conn];
    out.console = state.errors;
  } else if (scenario === "badrender") {
    out.storyBefore = state.text.storyLine;
    state.respond = { ok: true, status: 200, body: { team: "demo", level: "simple", last_seq: 7, tasks: null } };
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
    out.allCardsPlaced = ["attCard", "nowCard", "planCard", "feedCard"].every((id) =>
      ["colA", "colB"].some((c) => elements[c].children.some((card) => card.id === id)));
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({ crashed: String(error && error.stack || error) }));
  process.exitCode = 2;
});
