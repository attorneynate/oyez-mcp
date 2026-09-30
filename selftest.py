"""Quick check that the Oyez MCP server starts and answers over stdio.

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
                m = re.search(r"^- (.+?) \u2014 No\. (\S+)$", text_of(r), re.M)
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
            m = re.search(r"^- (.+?) — No\. (\S+)$", text_of(r), re.M)
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

    print("\nAll good. Now register it:\n"
          "  claude mcp add --scope user oyez -- <venv-python> <path-to-server.py>")


if __name__ == "__main__":
    asyncio.run(main())
