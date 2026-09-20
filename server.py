#!/usr/bin/env python3
"""Oyez MCP - U.S. Supreme Court cases, decisions, and oral-argument transcripts.

Exposes the (unofficial) Oyez Project data at api.oyez.org and the Oyez search
backend (beta-search.oyez.org) as Model Context Protocol tools. No account or
API key is required.

Tools
  search_cases              find cases by name, party, or docket number
  get_case                  full metadata: parties, citation, dates, facts,
                            question presented, holding, decision + votes
  list_term_cases           every case from a given Supreme Court Term
  get_oral_argument         oral-argument transcript, filterable by speaker
  get_opinion_announcement  opinion-announcement / dissent-from-the-bench audio

Run as a stdio MCP server:  python server.py
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import MCPServer

API = "https://api.oyez.org"
SEARCH_URL = "https://beta-search.oyez.org/elasticsearch_index_scotus_nodes/_search"

# The same fields oyez.org's own search box queries. best_fields ranking gives
# the closest match for case-name / party / docket queries.
SEARCH_FIELDS = [
    "field_docket_number^3",
    "field_additional_docket_numbers^3",
    "title^2",
    "field_court_term",
    "field_first_party",
    "field_second_party",
    "field_facts_of_the_case:value",
    "field_question:value",
    "field_conclusion:value",
]
USER_AGENT = "oyez-mcp/1.1 (Claude Code MCP server)"

# Oyez's search index runs about a Term behind its case data (in September
# 2026 it had no 2025 Term case), so search_cases also scans the most recent
# Terms' case lists. Those lists are the one thing kept in memory, briefly.
TERM_CACHE_TTL = 600.0  # seconds

app = MCPServer("oyez")

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    """Lazily create a shared AsyncClient inside the running event loop."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
    return _client


class OyezError(Exception):
    """A user-facing problem talking to Oyez (surfaced as tool text, not a crash)."""


async def _get(url: str, params: Optional[dict] = None) -> Any:
    try:
        r = await _http().get(url, params=params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise OyezError(f"Not found on Oyez: {url}") from e
        raise OyezError(f"Oyez API returned {e.response.status_code} for {url}") from e
    except httpx.RequestError as e:
        raise OyezError(f"Network error contacting Oyez: {e}") from e
    except ValueError as e:  # bad JSON
        raise OyezError(f"Oyez returned a non-JSON response for {url}") from e


async def _search(query: str, size: int) -> list[dict]:
    body = {
        "size": size,
        "query": {
            "multi_match": {
                "query": query,
                "type": "best_fields",
                "fields": SEARCH_FIELDS,
            }
        },
    }
    try:
        r = await _http().post(SEARCH_URL, json=body)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as e:
        raise OyezError(f"Oyez search error: {e}") from e
    except ValueError as e:
        raise OyezError(f"Oyez search returned malformed JSON: {e}") from e
    return [h.get("_source", {}) for h in data.get("hits", {}).get("hits", [])]


_TERM_CACHE: dict[str, tuple[float, list[dict]]] = {}


async def _term_cases(term: str) -> list[dict]:
    """All case summaries for a Term, cached for TERM_CACHE_TTL seconds."""
    now = time.monotonic()
    hit = _TERM_CACHE.get(term)
    if hit and now - hit[0] < TERM_CACHE_TTL:
        return hit[1]
    data = await _get(f"{API}/cases", params={"filter": f"term:{term}", "per_page": 0})
    if isinstance(data, dict):
        data = [data]
    cases = [c for c in (data or []) if isinstance(c, dict)]
    _TERM_CACHE[term] = (now, cases)
    return cases


def _recent_terms(today: Optional[datetime] = None) -> list[str]:
    """The Terms the search index tends to lag behind: the Term in progress, the
    one before it, and the coming Term whose grants Oyez already lists. A Term
    is named for the year it begins in October."""
    d = today or datetime.now(timezone.utc)
    ty = d.year if d.month >= 10 else d.year - 1
    return [str(ty + 1), str(ty), str(ty - 1)]


_WORD_RE = re.compile(r"[a-z0-9]+")
_DOCKET_RE = re.compile(r"^\d{1,3}-\d{1,5}$|^\d{2}[ao]\d{1,5}$")  # 25-332, 22a123, 22o145
_NAME_STOP = frozenset("v vs versus the of in re et al inc co corp llc ltd and a an".split())


def _norm_docket(value: Any) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"^(no\.?|docket)\s*", "", s)
    return re.sub(r"[\u2010\u2011\u2012\u2013\u2014]", "-", s)


def _name_tokens(value: Any) -> list[str]:
    return [w for w in _WORD_RE.findall(str(value or "").lower()) if w not in _NAME_STOP]


def _case_matches(case: dict, query: str) -> bool:
    """True when `query` is the case's docket number, or every word of it
    (ignoring "v.", "Inc.", and the like) appears in the case name."""
    qd = _norm_docket(query)
    if _DOCKET_RE.match(qd):
        return _norm_docket(case.get("docket_number")) == qd
    wanted = _name_tokens(query)
    if not wanted:
        return False
    have = set(_name_tokens(case.get("name")))
    return all(w in have for w in wanted)


async def _scan_recent_terms(query: str) -> tuple[list[dict], list[str]]:
    """Name-and-docket matches for `query` in the most recent Terms' case lists.

    One request per Term, in turn, and never a raise: a Term that cannot be
    fetched is skipped. The second value names the Terms that were scanned
    and had cases.
    """
    matches: list[dict] = []
    scanned: list[str] = []
    for term in _recent_terms():
        try:
            cases = await _term_cases(term)
        except OyezError:
            continue
        if not cases:
            continue
        scanned.append(term)
        matches.extend(c for c in cases if _case_matches(c, query))
    return matches, scanned


def _result_row(name: Any, term: Any, docket: Any, year: Any) -> str:
    suffix = f", {year}" if year else ""
    return f"- **{name or '(untitled)'}** \u2014 Term {term or '?'}, No. {docket or '?'}{suffix}"


async def _fetch_case(term: str, docket: str) -> dict:
    """Fetch and validate a single case object.

    Oyez answers a missing Term/docket with HTTP 200 and a JSON array (not a
    404), so a plain _get would hand back a list. Normalize that to a clear
    'not found' error.
    """
    data = await _get(f"{API}/cases/{term}/{docket}")
    if not isinstance(data, dict) or not (data.get("name") or data.get("ID")):
        raise OyezError(f"No case found at Term {term}, docket {docket}.")
    return data


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)</(p|div|li|h[1-6]|tr)>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_SPACES_RE = re.compile(r"[ \t]+")
_BLANKS_RE = re.compile(r"\n\s*\n+")


def _text(value: Any) -> str:
    """Coerce an Oyez field (str, None, or {'value': ...}) to clean plain text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("value") or value.get("text") or ""
    s = str(value)
    s = _BLOCK_RE.sub("\n", s)
    s = _BR_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = _SPACES_RE.sub(" ", s)
    s = _BLANKS_RE.sub("\n\n", s)
    return s.strip()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …"


def _epoch_to_date(epoch: Any) -> str:
    try:
        dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    # %-d / %e are not portable to Windows, so build the day by hand.
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _hms(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _is_justice(speaker: dict) -> bool:
    for role in speaker.get("roles") or []:
        if "justice" in str(role.get("type", "")).lower():
            return True
    return False


def _nice_title(raw: str) -> str:
    """Title-case an Oyez search title ("obergefell v. hodges") for display."""
    out = []
    for w in (raw or "").split():
        low = w.lower()
        if low in ("v.", "v"):
            out.append("v.")
        elif w.isupper():
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _citation(cit: Optional[dict]) -> str:
    if not cit:
        return ""
    vol, page, year = cit.get("volume"), cit.get("page"), cit.get("year")
    if vol and page:
        base = f"{vol} U.S. {page}"
    elif vol:
        base = f"{vol} U.S. ___"
    else:
        base = ""
    if year:
        base = (f"{base} ({year})").strip()
    return base


def _first(value: Any) -> Optional[dict]:
    """Oyez returns some relations as either an object or a one-element list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@app.tool()
async def search_cases(query: str, limit: int = 10, include_people: bool = False) -> str:
    """Search Oyez for U.S. Supreme Court cases by case name, party, or docket number.

    This mirrors the search box on oyez.org: it matches case names, party names,
    and docket numbers - not free-form topics. "citizens united", "brown v board
    of education", and "14-556" all work well; a bare topic like "abortion" only
    finds cases with that word in the title. Every result lists the Term and
    docket number you pass to get_case, get_oral_argument, or list_term_cases.

    Oyez's search index runs about a Term behind its case data, so this tool
    also scans the three most recent Terms' case lists by name and docket
    number and lists those matches first.

    Args:
        query: Case name, party name, or docket number (e.g. "Obergefell",
            "new york times v sullivan", "14-556").
        limit: Maximum number of cases to return, 1-50 (default 10).
        include_people: Also include matching Justices/advocates (default False).
    """
    limit = max(1, min(int(limit), 50))
    query = (query or "").strip()
    if not query:
        return "Give search_cases a case name, a party name, or a docket number."

    index_err: Optional[OyezError] = None
    try:
        sources = await _search(query, size=limit * 3 + 5)
    except OyezError as e:
        sources, index_err = [], e
    recent, scanned = await _scan_recent_terms(query)

    seen: set[tuple[str, str]] = set()
    rows: list[str] = []
    for c in recent:
        key = (str(c.get("term")), str(c.get("docket_number")))
        if key in seen:
            continue
        seen.add(key)
        year = (c.get("citation") or {}).get("year")
        rows.append(_result_row(c.get("name"), c.get("term"), c.get("docket_number"), year))
        if len(rows) >= limit:
            break
    for s in sources:
        if len(rows) >= limit:
            break
        typ = s.get("type")
        if typ == "case":
            term = s.get("field_court_term") or "?"
            docket = s.get("field_docket_number") or "?"
            key = (str(term), str(docket))
            if key in seen:
                continue
            seen.add(key)
            name = _nice_title(s.get("title", "")) or "(untitled)"
            rows.append(_result_row(name, term, docket, s.get("field_citation:field_year")))
        elif include_people and typ == "person":
            rows.append(f"- _(person)_ {s.get('title', '?')} \u2014 {s.get('url', '')}")

    scanned_note = ""
    if scanned:
        scanned_note = (
            f"Terms {', '.join(scanned)} were also scanned by name and docket, "
            "since Oyez's search index lags them."
        )
    if not rows:
        if index_err is not None:
            return f"\u26a0\ufe0f {index_err}" + (f"\n{scanned_note} No match there either." if scanned_note else "")
        return (
            f"No cases found for {query!r}. Oyez search matches case names, "
            "parties, and docket numbers - try a party name or the docket number, "
            "or list_term_cases for a Term." + (f"\n{scanned_note}" if scanned_note else "")
        )
    header = ""
    if index_err is not None:
        header = (
            f"\u26a0\ufe0f Oyez's search index was unavailable ({index_err}); "
            "these come from the recent Term lists only.\n"
        )
    header += f"Found {len(rows)} result(s) for {query!r}:\n"
    footer = "\n\nUse get_case(term, docket) for full details."
    if scanned_note:
        footer += "\n" + scanned_note
    return header + "\n".join(rows) + footer


@app.tool()
async def get_case(term: str, docket: str) -> str:
    """Full details for one Supreme Court case: parties, citation, key dates,
    facts, the question presented, the holding/conclusion, the decision with its
    vote breakdown and opinion authors, the advocates, and a list of the
    available oral-argument and opinion-announcement audio.

    Args:
        term: The Term year, e.g. "2014" (from search_cases).
        docket: The docket number, e.g. "14-556" (from search_cases).
    """
    try:
        c = await _fetch_case(term, docket)
    except OyezError as e:
        return f"⚠️ {e}\nTip: use search_cases to find the correct Term and docket."
    return _format_case(c, term, docket)


def _format_case(c: dict, term: str, docket: str) -> str:
    name = c.get("name") or _nice_title(c.get("docket_number", ""))
    out: list[str] = [f"# {name}"]

    meta: list[str] = []
    cit = _citation(c.get("citation"))
    if cit:
        meta.append(cit)
    meta.append(f"Docket No. {c.get('docket_number', docket)}")
    meta.append(f"Term {c.get('term', term)}")
    extra_dockets = c.get("additional_docket_numbers") or []
    if extra_dockets:
        meta.append("also " + ", ".join(str(d) for d in extra_dockets))
    out.append(" · ".join(meta))

    court = _first(c.get("decided_by")) or _first(c.get("heard_by"))
    if court and court.get("name"):
        out.append(f"Court: {court['name']}")
    lc = c.get("lower_court")
    if isinstance(lc, dict) and lc.get("name"):
        out.append(f"Lower court: {lc['name']}")

    # Timeline (cert granted / argued / decided ...)
    events = []
    for ev in c.get("timeline") or []:
        if not ev:
            continue
        dates = ev.get("dates") or []
        when = _epoch_to_date(dates[0]) if dates else ""
        if ev.get("event") and when:
            events.append(f"{ev['event']}: {when}")
    if events:
        out.append("\n".join(events))

    # Parties
    fp = c.get("first_party")
    sp = c.get("second_party")
    if fp or sp:
        fl = c.get("first_party_label")
        sl = c.get("second_party_label")
        line = "\n## Parties\n"
        line += f"- {fp}" + (f" ({fl})" if fl else "") + "\n"
        line += f"- {sp}" + (f" ({sl})" if sl else "")
        out.append(line)

    for key, heading in (
        ("facts_of_the_case", "Facts"),
        ("question", "Question presented"),
        ("conclusion", "Conclusion"),
    ):
        body = _text(c.get(key))
        if body:
            out.append(f"\n## {heading}\n{_clip(body, 2200)}")

    # Decision(s)
    for dec in c.get("decisions") or []:
        if not dec:
            continue
        parts = ["\n## Decision"]
        head = []
        if dec.get("winning_party"):
            head.append(f"Winning party: {dec['winning_party']}")
        maj, minn = dec.get("majority_vote"), dec.get("minority_vote")
        if maj is not None or minn is not None:
            head.append(f"Vote: {maj}-{minn}")
        if dec.get("decision_type"):
            head.append(f"({dec['decision_type']})")
        if head:
            parts.append(" · ".join(head))
        desc = _text(dec.get("description"))
        if desc:
            parts.append(_clip(desc, 2200))
        votes = dec.get("votes") or []
        if votes:
            vlines = []
            for v in votes:
                member = (v.get("member") or {}).get("name") or "?"
                vote = v.get("vote") or "?"
                op = v.get("opinion_type")
                tag = f" [{op}]" if op and op not in ("none", vote) else ""
                vlines.append(f"  - {member}: {vote}{tag}")
            parts.append("Votes:\n" + "\n".join(vlines))
        out.append("\n".join(parts))

    # Advocates
    advs = c.get("advocates") or []
    if advs:
        alines = []
        for a in advs:
            if not a:
                continue
            person = a.get("advocate") or {}
            nm = person.get("name") or "?"
            desc = a.get("advocate_description") or ""
            alines.append(f"- {nm}" + (f" — {desc}" if desc else ""))
        if alines:
            out.append("\n## Advocates\n" + "\n".join(alines))

    # Available audio
    oa = c.get("oral_argument_audio") or []
    op = c.get("opinion_announcement") or []
    audio = []
    if oa:
        titles = "; ".join(m.get("title", "Oral Argument") for m in oa if m)
        audio.append(
            f"- Oral argument ({len(oa)} session(s)): {titles}\n"
            f"  -> get_oral_argument(term=\"{c.get('term', term)}\", docket=\"{c.get('docket_number', docket)}\")"
        )
    if op:
        titles = "; ".join(m.get("title", "Opinion Announcement") for m in op if m)
        audio.append(
            f"- Opinion announcement ({len(op)}): {titles}\n"
            f"  -> get_opinion_announcement(term=\"{c.get('term', term)}\", docket=\"{c.get('docket_number', docket)}\")"
        )
    if audio:
        out.append("\n## Audio & transcripts\n" + "\n".join(audio))

    # Links
    links = []
    t = c.get("term", term)
    d = c.get("docket_number", docket)
    links.append(f"- Oyez: https://www.oyez.org/cases/{t}/{d}")
    if c.get("justia_url"):
        links.append(f"- Justia: {c['justia_url']}")
    out.append("\n## Links\n" + "\n".join(links))

    return "\n".join(out)


@app.tool()
async def list_term_cases(term: str, limit: int = 60) -> str:
    """List the Supreme Court cases from a given Term.

    Args:
        term: The Term year - the year the Term began, e.g. "2014" for OT2014
            (October 2014 through June/July 2015).
        limit: Maximum cases to list, 1-400 (default 60).
    """
    limit = max(1, min(int(limit), 400))
    try:
        data = await _term_cases(str(term).strip())
    except OyezError as e:
        return f"\u26a0\ufe0f {e}"
    if not data:
        return f"No cases found for Term {term}."

    total = len(data)
    rows = []
    for case in data[:limit]:
        if not case:
            continue
        nm = case.get("name") or "(untitled)"
        dk = case.get("docket_number") or "?"
        rows.append(f"- {nm} — No. {dk}")
    shown = len(rows)
    header = f"Term {term}: {total} case(s)"
    header += f" (showing {shown})" if shown < total else ""
    footer = "\n\nUse get_case(term, docket) for any of these."
    return header + ":\n" + "\n".join(rows) + footer


@app.tool()
async def get_oral_argument(
    term: str,
    docket: str,
    speaker: Optional[str] = None,
    speaker_type: Optional[str] = None,
    part: Optional[int] = None,
    include_timestamps: bool = False,
    max_chars: int = 18000,
) -> str:
    """Fetch the oral-argument transcript for a case, optionally filtered by speaker.

    The transcript is returned as "Speaker: text" turns. Use the filters to zero
    in on, for example, one Justice's questions ("only Justice Scalia's
    questions" -> speaker="Scalia").

    Args:
        term: The Term year, e.g. "2014".
        docket: The docket number, e.g. "14-556".
        speaker: Case-insensitive substring of a speaker's name; include only
            their turns (e.g. "Scalia", "Verrilli", "Roberts").
        speaker_type: "justice" or "advocate" to include only that group.
        part: For arguments split into sessions, the 1-based session index;
            default is all sessions.
        include_timestamps: Prefix each turn with its start time (H:MM:SS).
        max_chars: Soft cap on transcript length, 1000-200000 (default 18000).
            When hit, output is truncated with a note; narrow it with `speaker`.
    """
    try:
        c = await _fetch_case(term, docket)
    except OyezError as e:
        return f"⚠️ {e}\nTip: use search_cases to find the correct Term and docket."
    return await _render_media(
        c, c.get("oral_argument_audio") or [], "oral argument",
        speaker, speaker_type, part, include_timestamps, max_chars,
    )


@app.tool()
async def get_opinion_announcement(
    term: str,
    docket: str,
    speaker: Optional[str] = None,
    part: Optional[int] = None,
    include_timestamps: bool = False,
    max_chars: int = 18000,
) -> str:
    """Fetch the opinion-announcement transcript(s) for a case - the Justices
    announcing the decision (and any dissents read from the bench).

    Args:
        term: The Term year, e.g. "2014".
        docket: The docket number, e.g. "14-556".
        speaker: Case-insensitive substring of a speaker's name to include only
            their turns.
        part: 1-based index when several announcements exist (e.g. the majority
            announcement and a separate dissent); default is all.
        include_timestamps: Prefix each turn with its start time.
        max_chars: Soft cap on length, 1000-200000 (default 18000).
    """
    try:
        c = await _fetch_case(term, docket)
    except OyezError as e:
        return f"⚠️ {e}\nTip: use search_cases to find the correct Term and docket."
    return await _render_media(
        c, c.get("opinion_announcement") or [], "opinion announcement",
        speaker, None, part, include_timestamps, max_chars,
    )


async def _render_media(
    case: dict,
    media_list: list,
    label: str,
    speaker: Optional[str],
    speaker_type: Optional[str],
    part: Optional[int],
    include_timestamps: bool,
    max_chars: int,
) -> str:
    name = case.get("name") or "this case"
    if not media_list:
        return f"No {label} audio is available on Oyez for {name}."

    # Select session(s)
    chosen = media_list
    if part is not None:
        idx = int(part) - 1
        if idx < 0 or idx >= len(media_list):
            return f"part {part} is out of range; {len(media_list)} session(s) available (1-{len(media_list)})."
        chosen = [media_list[idx]]

    # Normalize filters
    st = (speaker_type or "").strip().lower() or None
    if st in ("justice", "justices", "j"):
        st = "justice"
    elif st in ("advocate", "advocates", "attorney", "counsel", "a"):
        st = "advocate"
    elif st is not None:
        return "speaker_type must be 'justice' or 'advocate'."
    sp = (speaker or "").strip().lower() or None
    max_chars = max(1000, min(int(max_chars), 200000))

    # Fetch every selected session up front (so the speaker roster is complete)
    fetched = []
    for m in chosen:
        href = (m or {}).get("href")
        title = (m or {}).get("title") or (m or {}).get("display_title") or label.title()
        if not href:
            fetched.append((title, None))
            continue
        try:
            media = await _get(href)
            fetched.append((media.get("title") or title, media))
        except OyezError as e:
            fetched.append((title, {"_error": str(e)}))

    # Build the full speaker roster from all turns
    roster: dict[str, list] = {}
    total_duration = 0.0
    for _title, media in fetched:
        if not media or media.get("_error"):
            continue
        tr = media.get("transcript") or {}
        try:
            total_duration += float(tr.get("duration") or 0)
        except (TypeError, ValueError):
            pass
        for sec in tr.get("sections") or []:
            for turn in sec.get("turns") or []:
                spk = turn.get("speaker") or {}
                nm = spk.get("name") or "Unknown"
                entry = roster.setdefault(nm, [0, _is_justice(spk)])
                entry[0] += 1

    # Header
    head = [f"# {label.title()} — {name}"]
    sub = f"{len(fetched)} session(s)"
    if total_duration:
        sub += f" · total {_hms(total_duration)}"
    head.append(sub)
    if roster:
        justices = sorted((n for n, v in roster.items() if v[1]), key=str.lower)
        others = sorted((n for n, v in roster.items() if not v[1]), key=str.lower)
        if justices:
            head.append("Justices: " + ", ".join(f"{n} ({roster[n][0]})" for n in justices))
        if others:
            head.append("Advocates/others: " + ", ".join(f"{n} ({roster[n][0]})" for n in others))
    active = []
    if sp:
        active.append(f"speaker~={speaker!r}")
    if st:
        active.append(f"speaker_type={st}")
    if part is not None:
        active.append(f"part={part}")
    if active:
        head.append("_Filter: " + ", ".join(active) + "_")

    # Body
    body: list[str] = []
    used = 0
    truncated = False
    matched = 0
    for title, media in fetched:
        if truncated:
            break
        body.append(f"\n## {title}")
        if not media:
            body.append("_(no transcript link)_")
            continue
        if media.get("_error"):
            body.append(f"_(could not load: {media['_error']})_")
            continue
        tr = media.get("transcript")
        if not tr or media.get("unavailable"):
            note = _text((media.get("public_note") or "")) or "transcript unavailable"
            body.append(f"_({note})_")
            continue
        for sec in tr.get("sections") or []:
            if truncated:
                break
            for turn in sec.get("turns") or []:
                spk = turn.get("speaker") or {}
                nm = spk.get("name") or "Unknown"
                if sp and sp not in nm.lower():
                    continue
                if st == "justice" and not _is_justice(spk):
                    continue
                if st == "advocate" and _is_justice(spk):
                    continue
                text = " ".join(
                    _text(tb.get("text"))
                    for tb in (turn.get("text_blocks") or [])
                    if tb.get("text")
                ).strip()
                if not text:
                    continue
                prefix = f"[{_hms(turn.get('start'))}] " if include_timestamps else ""
                line = f"{prefix}{nm}: {text}"
                remaining = max_chars - used
                if len(line) > remaining:
                    # Never drop a turn whole. Under a low cap, one long turn
                    # (a dissent read from the bench) used to come back as no
                    # text at all; keep the part that fits instead.
                    if matched == 0 or remaining >= 200:
                        body.append(_clip(line, remaining))
                        matched += 1
                    truncated = True
                    break
                body.append(line)
                used += len(line) + 1
                matched += 1

    if matched == 0 and not truncated:
        hint = ""
        if sp or st:
            hint = " No turns matched the filter - check the speaker roster above."
        body.append(f"\n_(no transcript text found.{hint})_")
    if truncated:
        body.append(
            f"\n\u2026 truncated at ~{max_chars} characters; the last turn shown may be "
            "cut short. Narrow it with speaker=\"<name>\", pick a part=, or raise max_chars."
        )

    return "\n".join(head) + "\n" + "\n".join(body)


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
