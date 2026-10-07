"""Quick check that the Oyez MCP server starts and answers over stdio, and
that the browser page's API (ui.py) reaches the same tools.

Run it with the project's own venv Python:
    Windows:      .\\.venv\\Scripts\\python.exe selftest.py
    macOS/Linux:  .venv/bin/python selftest.py
"""
import asyncio
import os
import re
import sys
from datetime import datetime, timezone

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")


def text_of(result):
    return "".join(getattr(b, "text", "") for b in result.content)


async def main():
    # Output is UTF-8 whatever the console or pipe would default to; on
    # Windows a piped run otherwise writes the server's em dashes as cp1252.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("OK  connected to MCP server:", init.server_info.name)

            tools = await session.list_tools()
            print("OK  tools:", ", ".join(t.name for t in tools.tools))

            if not init.server_info.version or not init.instructions:
                sys.exit("FAIL  the server sent no version or no instructions")
            for t in tools.tools:
                if not (t.annotations and t.annotations.read_only_hint):
                    sys.exit(f"FAIL  {t.name} is not marked read-only")
                if t.output_schema:
                    sys.exit(f"FAIL  {t.name} has an output schema, so answers go out twice")
            print(f"OK  version {init.server_info.version}, instructions, read-only tools")

            # The version is declared twice, and a mismatch would ship a package
            # whose User-Agent and metadata disagree.
            with open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8") as f:
                m = re.search(r'^version = "([^"]+)"', f.read(), re.M)
            if not m or m.group(1) != init.server_info.version:
                sys.exit(f"FAIL  pyproject.toml says version {m.group(1) if m else '?'} and "
                         f"the server says {init.server_info.version}; bump both together")
            print("OK  pyproject.toml and server.py agree on the version")

            r = await session.call_tool("search_cases", {"query": "obergefell", "limit": 1})
            # A str tool with structured output repeats its whole answer as
            # structuredContent {"result": ...}; transcripts went out twice.
            if r.structured_content:
                sys.exit("FAIL  search_cases sent its answer twice (structuredContent)")
            print("OK  search_cases ->\n" + text_of(r))

            r = await session.call_tool(
                "get_oral_argument",
                {"term": "2014", "docket": "14-556", "speaker": "Scalia", "max_chars": 400},
            )
            print("OK  transcript filter (Scalia, first 300 chars) ->\n" + text_of(r)[:300])

            # Oyez's search index lags its case data by about a Term, so a case
            # from the Term in progress must still be found by its docket number.
            now = datetime.now(timezone.utc)
            term_year = now.year if now.month >= 10 else now.year - 1
            found = None
            for term in (str(term_year), str(term_year - 1)):
                r = await session.call_tool("list_term_cases", {"term": term, "limit": 1})
                m = re.search(r"^- (.+?) \u2014 No\. (\S+)(?: -> .*)?$", text_of(r), re.M)
                if m:
                    found = (term, m.group(1), m.group(2))
                    break
            if not found:
                sys.exit("FAIL  no case could be read from the two most recent Term lists")
            term, name, docket = found
            out = text_of(await session.call_tool("search_cases", {"query": docket, "limit": 3}))
            if name not in out:
                sys.exit(f"FAIL  search_cases({docket!r}) did not return {name!r}:\n{out}")
            print(f"OK  search_cases finds a Term {term} case by docket ({name}, No. {docket})")

            # The index lags more than a Term: in September 2026 it held 21 of the
            # 62 cases from the Term two back. So that Term must stay in the scan
            # after the window rolls forward in October.
            back2 = str(term_year - 2)
            r = await session.call_tool("list_term_cases", {"term": back2, "limit": 1})
            m = re.search(r"^- (.+?) — No\. (\S+)(?: -> .*)?$", text_of(r), re.M)
            if not m:
                sys.exit(f"FAIL  no case could be read from the Term {back2} list")
            name, docket = m.group(1), m.group(2)
            out = text_of(await session.call_tool("search_cases", {"query": docket, "limit": 3}))
            scanned = re.search(r"^Terms (.+) were also scanned", out, re.M)
            if name not in out or not scanned or back2 not in scanned.group(1).split(", "):
                sys.exit(f"FAIL  search_cases({docket!r}) did not scan Term {back2}:\n{out}")
            print(f"OK  the Term two back ({back2}) is still scanned ({name}, No. {docket})")

            # oyez.org answers 200 with the same shell for every /cases/ path, so a
            # composed address renders an empty page instead of failing. The link
            # has to come from the API's href: Brown I is 1940-1955/347us483, and
            # its docket number ("1") would have built a live-looking dead link.
            out = text_of(await session.call_tool(
                "get_case", {"term": "1940-1955", "docket": "347us483"}))
            if "https://www.oyez.org/cases/1940-1955/347us483" not in out:
                sys.exit("FAIL  get_case did not link Brown I by its own path:\n" + out)
            print("OK  a bucketed-Term case links to its own path, not its docket number")

            # datetime.fromtimestamp raises on Windows for any date before 1970,
            # and that used to drop Brown's whole timeline without a word.
            if "Decided: May 17, 1954" not in out:
                sys.exit("FAIL  get_case(Brown I) lost its pre-1970 dates:\n" + out)
            print("OK  pre-1970 dates survive (Brown I, decided May 17, 1954)")
            if "Argued: December 9, 1952; December 10, 1952; December 11, 1952" not in out:
                sys.exit("FAIL  get_case(Brown I) did not give every day of argument:\n" + out)
            print("OK  a multi-day argument lists every day (Brown I, December 9-11, 1952)")

            # Brown I and II share "No. 1", so the Term list has to give the call
            # that reaches each one.
            out = text_of(await session.call_tool(
                "list_term_cases", {"term": "1940-1955", "limit": 400}))
            if 'get_case(term="1940-1955", docket="347us483")' not in out:
                sys.exit("FAIL  list_term_cases did not give Brown I's own get_case call:\n" + out)
            print("OK  a shared docket in a Term list comes with its own get_case call")

            out = text_of(await session.call_tool(
                "search_cases", {"query": "brown v board of education", "limit": 1}))
            if "Brown v. Board of Education of Topeka" not in out:
                sys.exit("FAIL  search_cases capitalized a small word in a case name:\n" + out)
            print("OK  small words stay lowercase in a searched case name")

            # Bakke reports "Term 1977" and lives at 1979/76-811, so the pair
            # search_cases prints does not resolve on its own.
            out = text_of(await session.call_tool(
                "get_case", {"term": "1977", "docket": "76-811"}))
            if "Bakke" not in out or "/cases/1979/76-811" not in out:
                sys.exit("FAIL  get_case(1977, 76-811) did not recover Bakke:\n" + out)
            print("OK  a Term/docket pair resolves even where the case path differs")

            # Brown I and Brown II are both "No. 1" in the same Term. There is no
            # right guess, so the error has to name both addresses.
            out = text_of(await session.call_tool(
                "get_case", {"term": "1940-1955", "docket": "1"}))
            if "347us483" not in out or "349us294" not in out:
                sys.exit("FAIL  an ambiguous docket did not name both cases:\n" + out)
            print("OK  an ambiguous docket names both addresses instead of guessing")

            # Oyez writes an original-jurisdiction docket "156-orig", and its
            # search index matches no other spelling of it.
            out = text_of(await session.call_tool(
                "search_cases", {"query": "No. 156, Orig.", "limit": 3}))
            if "New York v. New Jersey" not in out:
                sys.exit("FAIL  search_cases('No. 156, Orig.') missed New York v. New Jersey:\n" + out)
            out = text_of(await session.call_tool(
                "get_case", {"term": "2022", "docket": "22O156"}))
            if "New York v. New Jersey" not in out:
                sys.exit("FAIL  get_case(2022, 22O156) missed New York v. New Jersey:\n" + out)
            print("OK  an original-jurisdiction docket resolves however it is written (156, Orig.)")

            # The index matches an application docket only in lowercase, so
            # "20A87" as the Court writes it used to find nothing outside the
            # recent Terms the server scans itself.
            out = text_of(await session.call_tool("search_cases", {"query": "20A87", "limit": 3}))
            if "roman catholic diocese of brooklyn" not in out.lower():
                sys.exit("FAIL  search_cases('20A87') missed Roman Catholic Diocese v. Cuomo:\n" + out)
            print("OK  an application docket is found as the Court writes it (20A87)")

            # The majority author reads "majority" as both vote and opinion type,
            # which once hid the fact that Kennedy wrote Obergefell.
            out = text_of(await session.call_tool("get_case", {"term": "2014", "docket": "14-556"}))
            if not re.search(r"Anthony M\. Kennedy: majority \S+ wrote the majority opinion", out):
                sys.exit("FAIL  get_case(Obergefell) did not name the majority author:\n" + out)
            if not re.search(r"Ruth Bader Ginsburg: majority \S+ joined Anthony M\. Kennedy", out):
                sys.exit("FAIL  get_case(Obergefell) did not say who joined whom:\n" + out)
            print("OK  the majority author and the joins are named (Obergefell)")

            # Oyez gives Kagan her Justice role on a transcript from the year
            # before she joined the Court, where she argued as Solicitor General.
            out = text_of(await session.call_tool(
                "get_oral_argument",
                {"term": "2008", "docket": "08-205", "part": 2,
                 "speaker": "Kagan", "speaker_type": "advocate", "max_chars": 1000}))
            others = re.search(r"^Advocates/others: (.*)$", out, re.M)
            if not others or "Elena Kagan" not in others.group(1) or "Elena Kagan:" not in out:
                sys.exit("FAIL  Kagan as Solicitor General was not treated as an advocate:\n" + out)
            print("OK  an advocate who later joined the Court counts as an advocate (Kagan, Citizens United)")

            # A turn longer than max_chars used to be dropped whole, so a low cap on
            # one long announcement returned no text at all.
            r = await session.call_tool(
                "get_opinion_announcement",
                {"term": "2014", "docket": "14-556", "speaker": "Roberts", "max_chars": 1000},
            )
            if not re.search(r"^[^:\n]*Roberts[^:\n]*: \S", text_of(r), re.M):
                sys.exit("FAIL  a low max_chars returned no transcript text:\n" + text_of(r))
            print("OK  a low max_chars still returns text (Roberts, Obergefell announcement)")

            # Oyez's newer conclusions run past 3000 characters; a 2200 cap
            # cut Trump v. CASA off in the middle of the concurrences.
            out = text_of(await session.call_tool("get_case", {"term": "2024", "docket": "24a884"}))
            m = re.search(r"## Conclusion\n(.*?)(?=\n## |\Z)", out, re.S)
            if not m or m.group(1).rstrip().endswith("\u2026"):
                sys.exit("FAIL  get_case(Trump v. CASA) still clips the conclusion:\n" + out[-600:])
            print("OK  a long conclusion comes back whole (Trump v. CASA)")

            # A docket is one path segment and nothing more. Unquoted,
            # "../../people/..." fetched a person and rendered it as a case.
            out = text_of(await session.call_tool(
                "get_case", {"term": "2024", "docket": "../../people/john_g_roberts_jr"}))
            if out.startswith("# ") or "Roberts" in out.split("\n", 1)[0]:
                sys.exit("FAIL  a docket with '../' reached another API path:\n" + out[:300])
            print("OK  a docket cannot walk the API path")

            # Some Oyez docket numbers carry a trailing space ("23-1197 "), and
            # it used to reach the Term list and the search rows.
            out = text_of(await session.call_tool(
                "list_term_cases", {"term": str(term_year), "limit": 400}))
            bad = [l for l in out.splitlines() if l != l.rstrip()]
            if bad:
                sys.exit("FAIL  list_term_cases printed trailing whitespace:\n" + bad[0] + "|")
            out = text_of(await session.call_tool("search_cases", {"query": found[1], "limit": 3}))
            if any(l != l.rstrip() for l in out.splitlines()):
                sys.exit("FAIL  search_cases printed trailing whitespace:\n" + out)
            print("OK  docket numbers are printed without Oyez's trailing spaces")

            # Written opinions with Justia's link to each: the instructions send
            # the model to the opinion for what the Court held.
            out = text_of(await session.call_tool("get_case", {"term": "2014", "docket": "14-556"}))
            kennedy = ("Opinion of the Court \u2014 Anthony M. Kennedy: "
                       "https://supreme.justia.com/cases/federal/us/576/14-556/opinion3.html")
            if "## Written opinions" not in out or kennedy not in out:
                sys.exit("FAIL  get_case(Obergefell) did not list the written opinions:\n" + out[-1500:])
            print("OK  written opinions are listed with their Justia links (Obergefell)")

            # With include_summary, a Term list gives each case's stage and date
            # and its one-line holding.
            prev = str(term_year - 1)
            out = text_of(await session.call_tool(
                "list_term_cases", {"term": prev, "limit": 5, "include_summary": True}))
            row = re.search(
                r"^- (.+?) \u2014 No\. \S+.* \u00b7 (decided|argued|granted) \w+ \d{1,2}, \d{4}.*\n  (\S.+)$",
                out, re.M)
            if not row:
                sys.exit(f"FAIL  list_term_cases({prev}, include_summary) gave no stage or summary:\n{out}")
            print(f"OK  include_summary gives each case's stage and holding ({row.group(1)})")

            # In the recent Terms a query is matched against the holding and the
            # question presented too, so a topic finds a case the index lacks.
            words = sorted(re.findall(r"[A-Za-z]{6,}", row.group(3)), key=len, reverse=True)[:2]
            topic = " ".join(words)
            out = text_of(await session.call_tool("search_cases", {"query": topic, "limit": 50}))
            if row.group(1) not in out:
                sys.exit(f"FAIL  search_cases({topic!r}) did not find {row.group(1)!r} by its holding:\n{out}")
            print(f"OK  a topic finds a recent case by its holding ({topic!r} -> {row.group(1)})")

            # find keeps only the turns that contain the text.
            out = text_of(await session.call_tool(
                "get_oral_argument",
                {"term": "2014", "docket": "14-556", "find": "millennia", "max_chars": 4000}))
            turns = [l for l in out.split("\n## ", 1)[1].splitlines()[1:]
                     if re.match(r"^[^_\u2026#].*: ", l)]
            if not turns or any("millennia" not in l.lower() for l in turns):
                sys.exit("FAIL  find='millennia' returned turns without it:\n" + out)
            print(f"OK  find keeps only the matching turns ({len(turns)} with 'millennia', Obergefell)")

            # A truncated transcript names the part and start to continue from,
            # and that call resumes at the turn that was cut.
            args = {"term": "2014", "docket": "14-556", "max_chars": 1500, "include_timestamps": True}
            out = text_of(await session.call_tool("get_oral_argument", args))
            m = re.search(r'call again with part=(\d+), start="([\d:]+)"', out)
            if not m:
                sys.exit("FAIL  the truncation note did not say where to continue:\n" + out[-400:])
            out2 = text_of(await session.call_tool(
                "get_oral_argument", {**args, "part": int(m.group(1)), "start": m.group(2)}))
            first = re.search(r"^\[([\d:]+)\] ", out2, re.M)
            if not first or first.group(1) != m.group(2):
                sys.exit(f"FAIL  start={m.group(2)!r} did not resume at that turn:\n" + out2[:600])
            print(f"OK  a truncated transcript says where to continue, and start resumes there ({m.group(2)})")

    await check_ui()

    print("\nAll good. Now register it:\n"
          "  claude mcp add --scope user oyez -- <venv-python> <path-to-server.py>")


async def check_ui():
    """The web page's API, called in-process: it reaches the same tools and
    refuses what it should."""
    import httpx
    import ui

    transport = httpx.ASGITransport(app=ui.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as web:
        r = await web.get("/")
        if r.status_code != 200 or "<title>Oyez Browser</title>" not in r.text:
            sys.exit(f"FAIL  the UI page did not load ({r.status_code})")

        r = await web.get("/api/get_case", params={"term": "2014", "docket": "14-556"})
        if r.status_code != 200 or "Obergefell" not in r.json().get("markdown", ""):
            sys.exit(f"FAIL  the UI's get_case did not return Obergefell ({r.status_code}):\n{r.text[:400]}")
        r = await web.get("/api/get_oral_argument", params={
            "term": "2014", "docket": "14-556", "speaker": "Scalia",
            "include_timestamps": "true", "max_chars": "1000"})
        md = r.json().get("markdown", "") if r.status_code == 200 else ""
        if not re.search(r"^\[[\d:]+\] Antonin Scalia: ", md, re.M):
            sys.exit(f"FAIL  the UI's get_oral_argument did not pass its filters ({r.status_code}):\n{md[:400]}")
        print("OK  the UI page loads and its API reaches the tools")

        refused = {
            "an unknown tool": await web.get("/api/delete_everything"),
            "a missing argument": await web.get("/api/get_case", params={"term": "2014"}),
            "an unknown argument": await web.get("/api/get_case", params={"term": "2014", "docket": "1", "x": "1"}),
            "a bad number": await web.get("/api/search_cases", params={"query": "x", "limit": "ten"}),
            "another host name": await web.get("/", headers={"Host": "attacker.example"}),
        }
        for what, resp in refused.items():
            if resp.status_code < 400:
                sys.exit(f"FAIL  the UI accepted {what} ({resp.status_code})")
        print("OK  the UI refuses unknown tools and arguments, bad values, and other host names")


if __name__ == "__main__":
    asyncio.run(main())
