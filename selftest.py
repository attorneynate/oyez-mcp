"""Quick check that the Oyez MCP server starts and answers over stdio.

Run it with the project's own venv Python:
    Windows:      .\\.venv\\Scripts\\python.exe selftest.py
    macOS/Linux:  .venv/bin/python selftest.py
"""
import asyncio
import os
import sys

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

    print("\nAll good. Now register it:\n"
          "  claude mcp add --scope user oyez -- <venv-python> <path-to-server.py>")


if __name__ == "__main__":
    asyncio.run(main())
