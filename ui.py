#!/usr/bin/env python3
"""Oyez UI - the Oyez MCP tools as a web page on your own machine.

Starts a small web server on 127.0.0.1 and opens it in your browser:

    oyez-ui            (installed with uvx or pip)
    python ui.py       (from the repo)

The page calls the same five tool functions the MCP server registers, so it
shows what an MCP client would get, rendered for a person to read. It binds to
127.0.0.1 only, and answers only to that host name, so nothing else on the
network can reach it. Ctrl+C stops it.

starlette and uvicorn are not dependencies of their own here: mcp needs both
for its HTTP transports, so they are installed whenever the server is.
"""
from __future__ import annotations

import argparse
import inspect
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from typing import Any, Callable

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

import server

DEFAULT_PORT = 8765

# The tools the page may call, and the type of each argument it may pass. The
# query string is all text, so each value is converted before the call;
# anything not listed here is refused.
_MEDIA_ARGS = {
    "term": str, "docket": str, "speaker": str, "find": str, "start": str,
    "part": int, "include_timestamps": bool, "max_chars": int,
}
TOOLS: dict[str, tuple[Callable[..., Any], dict[str, type]]] = {
    "search_cases": (server.search_cases, {"query": str, "limit": int, "include_people": bool}),
    "get_case": (server.get_case, {"term": str, "docket": str}),
    "list_term_cases": (server.list_term_cases, {"term": str, "limit": int, "include_summary": bool}),
    "get_oral_argument": (server.get_oral_argument, {**_MEDIA_ARGS, "speaker_type": str}),
    "get_opinion_announcement": (server.get_opinion_announcement, _MEDIA_ARGS),
}

# Arguments without a default, read off each function so they cannot drift.
REQUIRED = {
    name: [p.name for p in inspect.signature(fn).parameters.values()
           if p.default is inspect.Parameter.empty]
    for name, (fn, _) in TOOLS.items()
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(kind: type, raw: str) -> Any:
    if kind is int:
        return int(raw)
    if kind is bool:
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"{raw!r} is not true or false")
    return raw


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def call_tool(request: Request) -> JSONResponse:
    name = request.path_params["tool"]
    if name not in TOOLS:
        return _error(f"There is no tool called {name!r}.", 404)
    fn, spec = TOOLS[name]

    kwargs: dict[str, Any] = {}
    for key, raw in request.query_params.items():
        if key not in spec:
            return _error(f"{name} takes no argument {key!r}.", 400)
        raw = raw.strip()
        if not raw:
            continue  # an empty filter box means no filter
        try:
            kwargs[key] = _coerce(spec[key], raw)
        except ValueError:
            return _error(f"{key} must be {spec[key].__name__}, not {raw!r}.", 400)
    missing = [p for p in REQUIRED[name] if p not in kwargs]
    if missing:
        return _error(f"{name} needs {', '.join(missing)}.", 400)

    # The tools return their own errors as text; this catches only what they
    # do not, such as a network failure, so the page can show it.
    try:
        markdown = await fn(**kwargs)
    except Exception as e:  # noqa: BLE001
        return _error(f"{type(e).__name__}: {e}", 502)
    return JSONResponse({"markdown": markdown}, headers={"Cache-Control": "no-store"})


# The page's script and styles are inline; everything it fetches is its own API.
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'")


async def page(request: Request) -> HTMLResponse:
    return HTMLResponse(PAGE, headers={"Content-Security-Policy": _CSP})


@asynccontextmanager
async def lifespan(app: Starlette):
    yield
    if server._client is not None and not server._client.is_closed:
        await server._client.aclose()


app = Starlette(
    routes=[Route("/", page), Route("/api/{tool}", call_tool)],
    # A page on another site cannot reach this one by pointing its own host
    # name at 127.0.0.1 (DNS rebinding): requests must name this host.
    middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])],
    lifespan=lifespan,
)


def _free_port(preferred: int) -> int:
    """The preferred port if nothing is listening on it, else one the OS picks."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise SystemExit("Could not find a free port on 127.0.0.1.")


def _open_when_up(srv: uvicorn.Server, url: str) -> None:
    for _ in range(100):  # up to ten seconds
        if srv.started:
            webbrowser.open(url)
            return
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oyez-ui", description="Browse Oyez's Supreme Court archive in a local web page.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port on 127.0.0.1 (default {DEFAULT_PORT}; another is used if it is taken)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    if not args.no_browser:
        threading.Thread(target=_open_when_up, args=(srv, url), daemon=True).start()
    print(f"Oyez UI running at {url}\nPress Ctrl+C to stop.", flush=True)
    srv.run()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Oyez Browser</title>
<style>
:root {
  --bg: #fbfaf7; --panel: #ffffff; --ink: #1d1d1f; --muted: #66645e;
  --line: #e4e1d9; --accent: #7a2e1f; --accent-ink: #ffffff; --hover: #f3efe6;
  --justice: #7a2e1f; --advocate: #1f4f7a; --warn: #8a5a00;
  --serif: Georgia, "Times New Roman", serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161514; --panel: #1f1e1c; --ink: #ecebe7; --muted: #a29f97;
    --line: #34322e; --accent: #e08a6f; --accent-ink: #161514; --hover: #2a2825;
    --justice: #e08a6f; --advocate: #8fb8e0; --warn: #e0b45c;
  }
}
:root[data-theme="dark"] {
  --bg: #161514; --panel: #1f1e1c; --ink: #ecebe7; --muted: #a29f97;
  --line: #34322e; --accent: #e08a6f; --accent-ink: #161514; --hover: #2a2825;
  --justice: #e08a6f; --advocate: #8fb8e0; --warn: #e0b45c;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; }
body { background: var(--bg); color: var(--ink); font: 15px/1.55 var(--sans); }
a { color: var(--accent); }
header {
  display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px;
  padding: 12px 20px; border-bottom: 1px solid var(--line); background: var(--panel);
}
header h1 { margin: 0; font: 600 20px/1 var(--serif); letter-spacing: .01em; }
header form { display: flex; gap: 6px; }
#search-form { flex: 1 1 320px; }
#search-form input { flex: 1; min-width: 0; }
#term-form input { width: 9.5em; }
input, select, button { font: inherit; color: inherit; }
input, select {
  padding: 6px 9px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg);
}
button {
  padding: 6px 12px; border: 1px solid var(--accent); border-radius: 6px;
  background: var(--accent); color: var(--accent-ink); cursor: pointer;
}
button.quiet { background: transparent; color: var(--accent); }
main { display: grid; grid-template-columns: minmax(260px, 360px) 1fr; min-height: calc(100vh - 62px); }
#results { border-right: 1px solid var(--line); padding: 14px 16px; overflow-y: auto; max-height: calc(100vh - 62px); position: sticky; top: 0; }
#case { padding: 14px 28px 40px; min-width: 0; }
#viewer { max-width: 60em; }
.hint { color: var(--muted); }
.md h2 { font: 600 24px/1.25 var(--serif); margin: 6px 0 8px; }
.md h3 { font: 600 17px/1.3 var(--serif); margin: 22px 0 6px; padding-bottom: 3px; border-bottom: 1px solid var(--line); }
.md p { margin: 4px 0; }
.md p.warn { color: var(--warn); }
.md ul { margin: 6px 0; padding-left: 20px; }
.md li { margin: 3px 0; }
.md li.indent { margin-left: 18px; }
.md .sub { color: var(--muted); font-size: 14px; }
#results ul { list-style: none; padding: 0; }
#results li.result a { display: block; padding: 7px 9px; margin: 0 -9px; border-radius: 6px; color: inherit; text-decoration: none; }
#results li.result a:hover { background: var(--hover); }
#results li.result a[aria-current="true"] { background: var(--hover); box-shadow: inset 3px 0 var(--accent); }
#results li.result strong { color: var(--accent); }
#results p { font-size: 14px; color: var(--muted); }
nav.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 14px; flex-wrap: wrap; align-items: end; }
nav.tabs a { padding: 8px 14px; text-decoration: none; color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -1px; }
nav.tabs a[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
nav.tabs .where { margin-left: auto; padding: 8px 0; color: var(--muted); font-size: 13px; }
form.filters { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: end; padding: 10px 12px; margin-bottom: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }
form.filters label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
form.filters label.check { flex-direction: row; align-items: center; gap: 6px; font-size: 14px; color: var(--ink); padding-bottom: 7px; }
.roster { margin: 4px 0; }
.roster .label { color: var(--muted); margin-right: 4px; }
.chip { padding: 2px 8px; margin: 2px 2px; border-radius: 999px; font-size: 13px; background: transparent; color: var(--ink); border: 1px solid var(--line); }
.chip:hover { border-color: var(--accent); }
.chip span { color: var(--muted); }
.turn { margin: 8px 0; max-width: 72em; }
.turn .ts { color: var(--muted); font: 12px var(--sans); font-variant-numeric: tabular-nums; margin-right: 6px; }
.turn .who { font-weight: 600; color: var(--advocate); }
.turn .who.j { color: var(--justice); }
.more { margin: 18px 0; }
.status { color: var(--muted); padding: 8px 0; }
.error { color: var(--warn); }
.examples button { margin: 4px 4px 0 0; }
@media (max-width: 800px) {
  main { grid-template-columns: 1fr; align-content: start; min-height: 0; }
  #results { border-right: 0; border-bottom: 1px solid var(--line); max-height: 45vh; position: static; }
  #case { padding: 14px 16px 40px; }
  header { padding: 12px 16px; }
}
</style>
</head>
<body>
<header>
  <h1>Oyez Browser</h1>
  <form id="search-form" role="search">
    <input name="q" placeholder="Case name, party, or docket number" aria-label="Search cases">
    <button>Search</button>
  </form>
  <form id="term-form">
    <input name="term" placeholder="Term, e.g. 2024" aria-label="Term year">
    <button class="quiet">Browse Term</button>
  </form>
</header>
<main>
  <aside id="results" class="md" aria-label="Results">
    <p class="hint">Search for a case by name, party, or docket number, or browse a whole Term.</p>
  </aside>
  <section id="case" aria-live="polite">
    <div id="welcome">
      <h2 style="font:600 24px/1.25 var(--serif)">Supreme Court cases, decisions, and arguments</h2>
      <p class="hint">Pick a case from the results to read its summary and votes, the oral argument, and the opinion announcement. Data comes live from Oyez.</p>
      <div class="examples">
        <button class="quiet" data-q="Obergefell">Obergefell v. Hodges</button>
        <button class="quiet" data-q="Citizens United">Citizens United</button>
        <button class="quiet" data-q="New York Times v. Sullivan">NYT v. Sullivan</button>
        <button class="quiet" data-term="2024">Browse the 2024 Term</button>
      </div>
    </div>
    <div id="viewer" hidden>
      <nav class="tabs" role="tablist">
        <a role="tab" data-tab="case">Case</a>
        <a role="tab" data-tab="argument">Oral argument</a>
        <a role="tab" data-tab="announcement">Announcement</a>
        <span class="where"></span>
      </nav>
      <div class="pane md" data-pane="case"></div>
      <div class="pane" data-pane="argument"></div>
      <div class="pane" data-pane="announcement"></div>
    </div>
  </section>
</main>
<script>
"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const attr = s => esc(s).replace(/"/g, "&quot;");
const caseHref = (term, docket, tab) =>
  "#case/" + encodeURIComponent(term) + "/" + encodeURIComponent(docket) + (tab && tab !== "case" ? "/" + tab : "");

// Each transcript pane runs this many characters a chunk; Load more fetches the next.
const CHUNK = 40000;
const MEDIA = {
  argument: { tool: "get_oral_argument", whoFilter: true },
  announcement: { tool: "get_opinion_announcement", whoFilter: false },
};

async function call(tool, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  }
  const r = await fetch("/api/" + tool + "?" + q);
  let data;
  try { data = await r.json(); } catch { throw new Error("The local server answered " + r.status + "."); }
  if (!r.ok) throw new Error(data.error || "The local server answered " + r.status + ".");
  return data.markdown;
}

// ---- Markdown -------------------------------------------------------------
// The tools write a small, fixed kind of Markdown for a model: headings,
// bullets, indented follow-on lines, **bold**, _italic_, bare links, and
// tool calls like get_case(term="...", docket="..."). This renders that and
// turns the tool calls into links. Text is escaped before any markup is added.

// Lines written for a model about how to call the tools, which a reader of
// this page does not need.
const HIDE = [/^Use get_case\(term, docket\)/, /^Tip: use search_cases/];

function inline(text, ctx = {}) {
  let s = esc(text);
  s = s.replace(/https?:\/\/[^\s<"]+/g, url => {
    const tail = (url.match(/[.,;:)\]]+$/) || [""])[0];
    url = url.slice(0, url.length - tail.length);
    return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + "</a>" + tail;
  });
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|\s)_([^_]+)_(?=\s|$)/g, "$1<em>$2</em>");
  s = s.replace(/(?:-&gt;\s*)?get_(oral_argument|opinion_announcement)\(term="([^"]+)", docket="([^"]+)"\)/g,
    (_, kind, t, d) => '<a href="' + caseHref(t, d, kind === "oral_argument" ? "argument" : "announcement") +
      '">Read the transcript</a>');
  s = s.replace(/(?:-&gt;\s*)?get_case\(term="([^"]+)", docket="([^"]+)"\)/g,
    (_, t, d) => '<a href="' + caseHref(t, d) + '">' + esc(t) + " / " + esc(d) + "</a>");
  // A shared docket's error names each case's own address: "Name -> 347us483".
  if (ctx.term) {
    s = s.replace(/-&gt; (\d+us\d+)/g, (_, d) => '&rarr; <a href="' + caseHref(ctx.term, d) + '">' + d + "</a>");
  }
  return s;
}

// A row in search or Term results, and the Term + docket it opens.
function resultPair(text, ctx) {
  // Term lists don't bold the case name the way search results do.
  if (!text.includes("**")) text = text.replace(/^(.+?) — /, "**$1** — ");
  let m = text.match(/get_case\(term="([^"]+)", docket="([^"]+)"\)/);
  if (m) return [m[1], m[2], text.replace(/\s*->\s*get_case\([^)]*\)/, "")];
  m = text.match(/— Term (\S+), No\. ([^,\s]+)/);
  if (m && m[2] !== "?") return [m[1], m[2], text];
  m = text.match(/— No\. (\S+)/);
  if (m && ctx.term && m[1] !== "?") return [ctx.term, m[1], text];
  return null;
}

const TURN_RE = /^(?:\[([\d:]+)\] )?([^_:\[\]][^:\[\]]{0,79}): (.+)$/;
const ROSTER_RE = /^(Justices|Advocates\/others): (.+)$/;

function rosterHtml(label, list, ctx) {
  const chips = [];
  for (const m of list.matchAll(/\s*([^()]+?) \((\d+)\)(?:,|$)/g)) {
    if (label === "Justices") ctx.justices.add(m[1]);
    chips.push('<button type="button" class="chip" data-speaker="' + attr(m[1]) + '" title="Show only ' +
      attr(m[1]) + '">' + esc(m[1]) + " <span>" + m[2] + "</span></button>");
  }
  return '<div class="roster"><span class="label">' + (label === "Justices" ? "Justices" : "Advocates and others") +
    ":</span>" + chips.join("") + "</div>";
}

// mode: "case", "results", or "transcript". ctx carries the Term (for links)
// and collects what the caller needs back, such as the truncation note.
function render(md, mode, ctx = {}) {
  ctx.justices = ctx.justices || new Set();
  const blocks = [];
  let items = null;
  const flush = () => {
    if (!items) return;
    blocks.push("<ul>" + items.map(it => {
      const subs = it.sub.map(s => '<div class="sub">' + s + "</div>").join("");
      if (it.href) return '<li class="result"><a href="' + it.href + '">' + it.html + subs + "</a></li>";
      return "<li" + (it.indent ? ' class="indent"' : "") + ">" + it.html + subs + "</li>";
    }).join("") + "</ul>");
    items = null;
  };
  for (const raw of md.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { flush(); continue; }
    if (HIDE.some(re => re.test(line))) continue;
    let m;
    if ((m = line.match(/^(#{1,3}) (.*)$/))) {
      flush();
      const h = m[1].length + 1;
      blocks.push("<h" + h + ">" + inline(m[2], ctx) + "</h" + h + ">");
      continue;
    }
    if ((m = line.match(/^(\s*)- (.*)$/))) {
      items = items || [];
      const pair = mode === "results" ? resultPair(m[2], ctx) : null;
      if (pair) items.push({ html: inline(pair[2], ctx), sub: [], href: caseHref(pair[0], pair[1]) });
      else items.push({ html: inline(m[2], ctx), sub: [], indent: m[1].length > 0 });
      continue;
    }
    if (items && /^\s{2,}\S/.test(line)) { items[items.length - 1].sub.push(inline(line.trim(), ctx)); continue; }
    flush();
    if (mode === "results" && (m = line.match(/^Term (\S+): \d+ case/))) ctx.term = m[1];
    if (mode === "transcript") {
      if (/^… truncated/.test(line)) { ctx.truncated = line; continue; }
      if ((m = line.match(ROSTER_RE))) { blocks.push(rosterHtml(m[1], m[2], ctx)); continue; }
      if ((m = line.match(TURN_RE))) {
        const j = ctx.justices.has(m[2]) ? " j" : "";
        blocks.push('<div class="turn">' + (m[1] ? '<span class="ts">' + m[1] + "</span>" : "") +
          '<span class="who' + j + '">' + esc(m[2]) + ":</span> " + inline(m[3], ctx) + "</div>");
        continue;
      }
    }
    blocks.push(/^⚠/.test(line) ? '<p class="warn">' + inline(line, ctx) + "</p>" : "<p>" + inline(line, ctx) + "</p>");
  }
  flush();
  return blocks.join("");
}

// ---- Results --------------------------------------------------------------
const results = $("#results");

async function showResults(tool, params, label) {
  results.innerHTML = '<p class="status">' + esc(label) + "…</p>";
  try {
    const md = await call(tool, params);
    results.innerHTML = render(md, "results", { term: params.term });
    markCurrent();
  } catch (e) {
    results.innerHTML = '<p class="error">' + esc(e.message) + "</p>";
  }
}

function search(q) {
  q = q.trim();
  if (!q) return;
  $("#search-form").q.value = q;
  showResults("search_cases", { query: q, limit: 25 }, "Searching for " + q);
}

function browseTerm(term) {
  term = term.trim();
  if (!term) return;
  $("#term-form").term.value = term;
  showResults("list_term_cases", { term, limit: 400, include_summary: true }, "Loading the " + term + " Term");
}

$("#search-form").addEventListener("submit", e => { e.preventDefault(); search(e.target.q.value); });
$("#term-form").addEventListener("submit", e => { e.preventDefault(); browseTerm(e.target.term.value); });
$("#welcome").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.q) search(b.dataset.q);
  if (b.dataset.term) browseTerm(b.dataset.term);
});

function markCurrent() {
  const here = current ? caseHref(current.term, current.docket) : null;
  for (const a of results.querySelectorAll("li.result a")) {
    a.setAttribute("aria-current", String(a.getAttribute("href") === here));
  }
}

// ---- Case viewer ------------------------------------------------------------
let current = null;   // { term, docket, key }
const loaded = {};    // pane name -> true once loaded for the current case
const media = {};     // transcript pane -> { filters, next, lastHeading, sessions }

function parseHash() {
  const m = location.hash.match(/^#case\/([^/]+)\/([^/]+)(?:\/(argument|announcement))?$/);
  if (!m) return null;
  return { term: decodeURIComponent(m[1]), docket: decodeURIComponent(m[2]), tab: m[3] || "case" };
}

function route() {
  const h = parseHash();
  if (!h) { $("#welcome").hidden = false; $("#viewer").hidden = true; current = null; markCurrent(); return; }
  $("#welcome").hidden = true;
  $("#viewer").hidden = false;
  const key = h.term + "/" + h.docket;
  if (!current || current.key !== key) {
    current = { term: h.term, docket: h.docket, key };
    for (const p of document.querySelectorAll(".pane")) p.innerHTML = "";
    for (const k of Object.keys(loaded)) delete loaded[k];
    for (const k of Object.keys(media)) delete media[k];
    $(".where").textContent = "Term " + h.term + " · No. " + h.docket;
    for (const a of document.querySelectorAll("nav.tabs a")) a.href = caseHref(h.term, h.docket, a.dataset.tab);
    markCurrent();
    if (window.innerWidth <= 800) $("#case").scrollIntoView({ behavior: "smooth" });
  }
  // A new tab starts at its top, not wherever the last one was scrolled to.
  const viewer = $("#viewer");
  if (viewer.getBoundingClientRect().top < 0) viewer.scrollIntoView();
  for (const a of document.querySelectorAll("nav.tabs a")) a.setAttribute("aria-selected", String(a.dataset.tab === h.tab));
  for (const p of document.querySelectorAll(".pane")) p.hidden = p.dataset.pane !== h.tab;
  if (!loaded[h.tab]) {
    loaded[h.tab] = true;
    if (h.tab === "case") loadCase(); else setupMedia(h.tab);
  }
}

async function loadCase() {
  const pane = $('.pane[data-pane="case"]');
  const key = current.key;
  pane.innerHTML = '<p class="status">Loading the case…</p>';
  try {
    const md = await call("get_case", { term: current.term, docket: current.docket });
    if (current && current.key === key) pane.innerHTML = render(md, "case", { term: current.term });
  } catch (e) {
    if (current && current.key === key) { pane.innerHTML = '<p class="error">' + esc(e.message) + "</p>"; loaded.case = false; }
  }
}

function setupMedia(tab) {
  const pane = $('.pane[data-pane="' + tab + '"]');
  pane.innerHTML =
    '<form class="filters">' +
      '<label>Speaker<input name="speaker" placeholder="e.g. Scalia"></label>' +
      (MEDIA[tab].whoFilter ? '<label>Who<select name="speaker_type"><option value="">Everyone</option>' +
        '<option value="justice">Justices</option><option value="advocate">Advocates</option></select></label>' : "") +
      '<label>Find<input name="find" placeholder="word or phrase"></label>' +
      '<label class="check"><input type="checkbox" name="include_timestamps"> Times</label>' +
      "<button>Apply</button>" +
      '<button type="button" class="quiet" data-clear>Clear</button>' +
    "</form>" +
    '<div class="md tx-head"></div><div class="md tx-body"></div><div class="more"></div>';
  const form = $("form", pane);
  form.addEventListener("submit", e => { e.preventDefault(); loadMedia(tab); });
  $("[data-clear]", form).addEventListener("click", () => { form.reset(); loadMedia(tab); });
  pane.addEventListener("click", e => {
    const chip = e.target.closest(".chip");
    if (chip) { form.speaker.value = chip.dataset.speaker; loadMedia(tab); return; }
    const more = e.target.closest("[data-more]");
    if (more) loadMedia(tab, true);
  });
  loadMedia(tab);
}

function filtersOf(form) {
  return {
    speaker: form.speaker.value,
    speaker_type: form.speaker_type ? form.speaker_type.value : "",
    find: form.find.value,
    include_timestamps: form.include_timestamps.checked,
  };
}

async function loadMedia(tab, append = false) {
  const pane = $('.pane[data-pane="' + tab + '"]');
  const head = $(".tx-head", pane), body = $(".tx-body", pane), more = $(".more", pane);
  const key = current.key;
  let st = media[tab];
  if (!append || !st) {
    st = media[tab] = { filters: filtersOf($("form", pane)), next: null, lastHeading: null, sessions: 1 };
    head.innerHTML = "";
    body.innerHTML = '<p class="status">Loading the transcript…</p>';
  }
  const params = { term: current.term, docket: current.docket, ...st.filters, max_chars: CHUNK };
  if (append) Object.assign(params, { part: st.next.part, start: st.next.start });
  more.innerHTML = '<p class="status">Loading…</p>';

  let md;
  try {
    md = await call(MEDIA[tab].tool, params);
  } catch (e) {
    if (current && current.key === key) more.innerHTML = '<p class="error">' + esc(e.message) + "</p>";
    return;
  }
  if (!current || current.key !== key || media[tab] !== st) return;  // the reader moved on
  // With no audio there is nothing to filter.
  $("form", pane).hidden = /^No .* audio is available/.test(md);

  // The header (title, roster, active filters) is everything before the
  // first session heading; a continuation repeats it, so it is shown once.
  const at = md.indexOf("\n## ");
  const headMd = at < 0 ? md : md.slice(0, at);
  let bodyMd = at < 0 ? "" : md.slice(at + 1);
  const ctx = { term: current.term };
  const headHtml = render(headMd, "transcript", ctx);
  if (!append) {
    head.innerHTML = headHtml;
    body.innerHTML = "";
    const s = headMd.match(/^(\d+) session/m);
    st.sessions = s ? +s[1] : 1;
  }
  if (append) {
    // The cut turn ended in " …", and the continuation starts with it whole.
    const turns = body.querySelectorAll(".turn"), last = turns[turns.length - 1];
    if (last && /…$/.test(last.textContent.trim())) last.remove();
    // Don't repeat the heading of the session already being read, or a
    // "nothing matched" note for a later session.
    const lines = bodyMd.split("\n");
    if (lines[0] === st.lastHeading) lines.shift();
    bodyMd = lines.filter(l => !/^_\(no transcript text found/.test(l)).join("\n");
  }
  const headings = bodyMd.match(/^## .*$/gm);
  if (headings) st.lastHeading = headings[headings.length - 1];
  body.insertAdjacentHTML("beforeend", render(bodyMd, "transcript", ctx));

  // What to load next: the note names the part and start of a cut turn. With
  // no cut, a run that was reading one session at a time goes on to the next.
  st.next = null;
  if (ctx.truncated) {
    const m = ctx.truncated.match(/(?:part=(\d+), )?start="([^"]+)"/);
    if (m) st.next = { part: m[1] ? +m[1] : null, start: m[2] };
  } else if (params.part && params.part < st.sessions) {
    st.next = { part: params.part + 1, start: null };
  }
  more.innerHTML = st.next
    ? '<button type="button" data-more>' + (st.next.start ? "Load more" : "Continue to part " + st.next.part) + "</button>"
    : ctx.truncated ? '<p class="status">This is as far as it goes from here; narrow it with a filter.</p>'
    : append ? '<p class="status">End of transcript.</p>' : "";
}

window.addEventListener("hashchange", route);
route();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
