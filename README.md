# Oyez MCP

An MCP server that gives Claude — or any Model Context Protocol client — access to
the **Oyez** archive of U.S. Supreme Court cases: case metadata, decisions with vote
breakdowns, and full **oral-argument transcripts** you can filter down to a single
Justice.

Data comes from the public Oyez API (`api.oyez.org`) and Oyez's search backend
(`beta-search.oyez.org`). **No account or API key is required.**

> Using the oyez tools, pull the Obergefell oral argument and show me only Justice
> Scalia's questions.

## Tools

| Tool | What it does |
|------|--------------|
| `search_cases` | Find cases by **name, party, or docket number** (e.g. `"citizens united"`, `"14-556"`). Returns each case's Term + docket. |
| `get_case` | Full case record: parties, citation, key dates, facts, question presented, holding/conclusion, the decision with its **vote breakdown and opinion authors**, advocates, and the available audio. |
| `list_term_cases` | Every case from a given Term (e.g. `"2014"`). |
| `get_oral_argument` | Oral-argument transcript, optionally filtered by speaker. Handles multi-session arguments and timestamps. |
| `get_opinion_announcement` | Opinion-announcement / dissent-from-the-bench transcript, same filters. |

Every tool is keyed on the pair **Term + docket** (`"2014"`, `"14-556"`) — that is
what `search_cases` returns and what the other four take. Start there.

### Parameters

**`search_cases`**

- `query` — case name, party name, or docket number
- `limit` — 1–50 (default 10)
- `include_people` — also return matching Justices and advocates (default false)

**`get_case`**

- `term` — the Term year, e.g. `"2014"`
- `docket` — e.g. `"14-556"`

**`list_term_cases`**

- `term` — the year the Term *began*, so `"2014"` means OT2014 (October 2014 through
  June/July 2015)
- `limit` — 1–400 (default 60)

**`get_oral_argument`**

- `term`, `docket` — as above
- `speaker` — case-insensitive substring of a name; returns only that person's turns
  (`"Scalia"`, `"Verrilli"`)
- `speaker_type` — `"justice"` or `"advocate"`
- `part` — 1-based session index for arguments split across sessions (default: all)
- `include_timestamps` — prefix each turn with `H:MM:SS`
- `max_chars` — soft length cap, 1000–200000 (default 18000)

**`get_opinion_announcement`**

- `term`, `docket`, `speaker`, `part`, `include_timestamps`, `max_chars` — as above.
  No `speaker_type` here; `part` is how you pick between, say, the majority
  announcement and a dissent read from the bench.

A note on search: it matches case names, parties, and docket numbers. It is **not** a
free-text topical search. `"brown v board of education"` and `"14-556"` work well; a
bare topic like `"abortion"` only finds cases with that word in the title.

Oyez's own search index runs about a Term behind its case data (in September 2026 it
had no 2025 Term case at all), so `search_cases` also scans the three most recent
Terms' case lists by name and docket number and lists those matches first. A case
decided this Term is found by its name or its docket number like any other.

Transcripts are long. A full argument can run tens of thousands of characters, so
filter with `speaker` or `speaker_type` when you only need part of it, and raise
`max_chars` deliberately rather than by habit.

## Requirements

- Python 3.10 or newer
- An MCP client — Claude Code (CLI or the desktop app's Code tab), Claude Desktop, or
  anything else that speaks MCP over stdio

## Install

Clone it, make a virtual environment, install two dependencies.

### Windows (PowerShell)

Keep the folder somewhere **outside `AppData`**. The Windows Store build of Python
redirects `AppData` paths, which breaks `venv` creation there; your home directory is
fine.

```powershell
git clone https://github.com/attorneynate/oyez-mcp.git
cd oyez-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optional, and worth it — confirm it works end to end before registering:

```powershell
.\.venv\Scripts\python.exe selftest.py
```

### macOS / Linux

```bash
git clone https://github.com/attorneynate/oyez-mcp.git
cd oyez-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python selftest.py
```

`selftest.py` starts the server over stdio, lists its tools, and pulls a real
transcript. If it prints `All good`, the server itself works and anything that goes
wrong next is configuration.

## Register it with your client

Point the client at the **venv's** Python and the absolute path to `server.py`. There
is no activation step — the interpreter path is the activation.

### Claude Code

Run this in a normal terminal, not inside a Claude Code session:

```bash
claude mcp add --scope user oyez -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
```

On Windows, spell out the absolute paths:

```powershell
claude mcp add --scope user oyez -- "C:\path\to\oyez-mcp\.venv\Scripts\python.exe" "C:\path\to\oyez-mcp\server.py"
```

`--scope user` registers the server once for **all** your projects; drop the flag to
register it in the current project only. Oyez's data spans everything, so user scope
usually makes sense.

Check it with `claude mcp list`, then start Claude Code and run `/mcp` in the
session — `oyez` should show as connected with its five tools.

### Claude Desktop and other MCP clients

Add a stdio server to the client's config. For Claude Desktop that is
`claude_desktop_config.json`, reachable from **Settings → Developer → Edit Config**:

```json
{
  "mcpServers": {
    "oyez": {
      "command": "/absolute/path/to/oyez-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/oyez-mcp/server.py"]
    }
  }
}
```

On Windows, JSON needs the backslashes doubled:

```json
{
  "mcpServers": {
    "oyez": {
      "command": "C:\\path\\to\\oyez-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\oyez-mcp\\server.py"]
    }
  }
}
```

Restart the client afterward. Neither config expands `~` or other shell shortcuts —
use full absolute paths.

## Example prompts

- "Using the oyez tools, pull the Obergefell oral argument and show me only Justice Scalia's questions."
- "Search Oyez for New York Times v. Sullivan and summarize the holding and the vote."
- "Get the opinion announcement for Obergefell and quote the part Roberts read from the bench."
- "List the 2014 Term cases, then give me the vote breakdown for Glossip v. Gross."
- "In the Citizens United argument, what did the advocates say about corporate personhood? Advocates only."

## Troubleshooting

- **Slow first start.** A stdio server's first launch can exceed Claude Code's
  30-second startup timeout. Raise it with the `MCP_TIMEOUT` environment variable, in
  milliseconds — `MCP_TIMEOUT=60000` — before starting Claude Code.
- **`No module named 'mcp'`.** You registered a system Python instead of the venv's.
  Re-run `claude mcp add`, or fix the config, pointing at the `.venv` interpreter.
- **"already exists at that scope"** when re-adding. Remove the old entry first with
  `claude mcp remove oyez --scope user`, then add it again.
- **`venv` creation fails on Windows** with a message about redirects or junctions.
  The folder is under `AppData`; move it to a normal path and recreate the venv.
- **A tool answers `Not found on Oyez`.** The Term/docket pair is wrong. Tools return
  errors as readable text instead of crashing, so run `search_cases` and use the Term
  and docket it hands back.
- **Missing or garbled speaker names in a transcript.** Oyez's speaker attribution is
  thinner for older cases. That is the source data, not the server.

## How it works

`server.py` is a single-file stdio MCP server. It queries the same endpoints
oyez.org's own front end uses, then formats the JSON into Markdown for the model
rather than passing raw API responses through: vote breakdowns become a table,
transcripts become `Speaker: text` turns.

There is no database, and every tool call is a live HTTP request, with one exception:
the three most recent Terms' case lists, which `search_cases` scans, are kept in memory
for ten minutes. `max_chars` exists because full transcripts are big enough to matter
to a context window.

## Contributing

Bug reports and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

## Disclaimer

This uses **unofficial** Oyez endpoints. Oyez is a free law project, a collaboration
of Cornell's Legal Information Institute, Justia, and Chicago-Kent College of Law;
this server is not affiliated with it, endorsed by it, or supported by it. The
endpoints can change or rate-limit without notice, so use this for research and
education and be a considerate client.

Nothing here is legal advice. Oyez's case summaries are secondary sources written for
a general audience — if you are citing the Court, go to the opinion.
