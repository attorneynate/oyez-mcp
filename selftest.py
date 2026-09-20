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

            r = await session.call_tool("search_cases", {"query": "obergefell", "limit": 1})
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
