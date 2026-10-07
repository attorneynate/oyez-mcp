# Oyez MCP

An MCP server that gives Claude — or any Model Context Protocol client — access to
the **Oyez** archive of U.S. Supreme Court cases: case metadata, decisions with vote
breakdowns, and full **oral-argument transcripts** you can filter down to a single
Justice.

Data comes from the public Oyez API (`api.oyez.org`) and Oyez's search backend
(`beta-search.oyez.org`). **No account or API key is required.**

No MCP client? The same tools also run as a [page in your browser](#use-it-in-a-browser).

> Using the oyez tools, pull the Obergefell oral argument and show me only Justice
> Scalia's questions.

## Tools

| Tool | What it does |
|------|--------------|
| `search_cases` | Find cases by **name, party, or docket number** (e.g. `"citizens united"`, `"14-556"`). Returns each case's Term + docket. |
| `get_case` | Full case record: parties, citation, key dates, facts, question presented, holding/conclusion, the decision with its **vote breakdown and opinion authors**, each **written opinion** with its Justia link, advocates, and the available audio. |
| `list_term_cases` | Every case from a given Term (e.g. `"2014"`), optionally with each case's stage, date, and one-line holding. |
| `get_oral_argument` | Oral-argument transcript, filterable by speaker, by text (`find`), and from a point in time (`start`). Handles multi-session arguments and timestamps. |
| `get_opinion_announcement` | Opinion-announcement / dissent-from-the-bench transcript, same filters. |

Every tool is keyed on the pair **Term + docket** (`"2014"`, `"14-556"`) — that is
what `search_cases` returns and what the other four take. Start there.

Older cases sit in bucketed Terms — `1789-1850`, `1850-1900`, `1900-1940`,
`1940-1955` — where Oyez addresses a case by volume-us-page rather than by docket
number: *Brown v. Board* is `1940-1955` + `347us483`. The docket number
`search_cases` prints is tried first and usually works anyway; where two cases share
one, as Brown I and Brown II both do at "No. 1", the error names each address so you
can pick.

**Cite the `Links` line from `get_case`, don't build a URL.** oyez.org serves the
same single-page shell for every path under `/cases/`, so an address you compose
yourself does not 404 when it is wrong — it answers `200` and renders an empty page.

### Parameters

**`search_cases`**

- `query` — case name, party name, or docket number. An original-jurisdiction docket
  can be written `"156-orig"`, `"No. 156, Orig."`, or `"22O156"`.
- `limit` — 1–50 (default 10)
- `include_people` — also return matching Justices and advocates (default false)

**`get_case`**

- `term` — the Term year, e.g. `"2014"`
- `docket` — e.g. `"14-556"`

**`list_term_cases`**

- `term` — the year the Term *began*, so `"2014"` means OT2014 (October 2014 through
  June/July 2015)
- `limit` — 1–400 (default 60)
- `include_summary` — also give each case's stage and date (decided, argued, or
  granted) and its one-line holding, or its question presented before a decision
  (default false). About 300 characters more per case, and the way to browse a Term
  by topic.

Where cases in a Term share a docket number, as Brown I and Brown II share "No. 1",
the line for each one gives the `get_case` call that reaches it.

**`get_oral_argument`**

- `term`, `docket` — as above
- `speaker` — case-insensitive substring of a name; returns only that person's turns
  (`"Scalia"`, `"Verrilli"`)
- `speaker_type` — `"justice"` or `"advocate"`. Someone who argued the case before
  joining the Court — Kagan as Solicitor General in *Citizens United* — counts as an
  advocate there.
- `find` — case-insensitive text; returns only the turns that contain it
  (`"personhood"`). Combine with `speaker_type` for what one side said about it.
- `start` — resume at this point in the recording, as `"H:MM:SS"`, `"M:SS"`, or
  seconds. Turns before it in the first selected session are skipped. A truncated
  transcript ends by naming the exact `part` and `start` to continue from.
- `part` — 1-based session index for arguments split across sessions (default: all)
- `include_timestamps` — prefix each turn with `H:MM:SS`
- `max_chars` — soft length cap, 1000–200000 (default 18000)

**`get_opinion_announcement`**

- `term`, `docket`, `speaker`, `find`, `start`, `part`, `include_timestamps`,
  `max_chars` — as above.
  No `speaker_type` here; `part` is how you pick between, say, the majority
  announcement and a dissent read from the bench.

A note on search: Oyez's index matches case names, parties, and docket numbers. It is
**not** a free-text topical search. `"brown v board of education"` and `"14-556"` work
well; a bare topic like `"abortion"` only finds cases with that word in the title. The
exception is the four recent Terms the server scans itself, where a query is also
matched against each case's one-line holding and question presented, so
`"universal injunction"` does find *Trump v. CASA*; such a result shows the line it
matched. For a topic in an older Term, list the Term with `include_summary`.

Oyez's own search index runs well behind its case data (in September 2026 it had no
2025 Term case at all, and only 21 of the 62 in the 2024 Term), so `search_cases` also
scans the four most recent Terms' case lists — the coming Term, the current one, and
the two before it — by name, docket number, holding, and question presented, and lists
those matches first. A case
decided this Term is found by its name or its docket number like any other.

Transcripts are long. A full argument can run a hundred thousand characters, so
filter with `speaker`, `speaker_type`, or `find` when you only need part of it. To
read one straight through, keep the default cap and follow the note at the end of a
truncated transcript: it names the `part` and `start` to continue from. Raise
`max_chars` deliberately rather than by habit.

## Requirements

- An MCP client — Claude Code (CLI or the desktop app's Code tab), Claude Desktop, or
  anything else that speaks MCP over stdio. Or none at all: the
  [browser page](#use-it-in-a-browser) needs only a web browser.
- [uv](https://docs.astral.sh/uv/), or Python 3.10 or newer

## Install

### With uv

Nothing to clone and no paths to spell out. `uvx` fetches the repo, builds it,
installs its two dependencies into an isolated environment of its own, and runs the
server's `oyez-mcp` command. The first start does that work and can take a minute;
later starts come from the cache.

For Claude Code, run this in a normal terminal, not inside a Claude Code session:

```bash
claude mcp add --scope user oyez -- uvx --from git+https://github.com/attorneynate/oyez-mcp oyez-mcp
```

`--scope user` registers the server once for **all** your projects; drop the flag to
register it in the current project only. Oyez's data spans everything, so user scope
usually makes sense. Check it with `claude mcp list`, then start Claude Code and run
`/mcp` in the session — `oyez` should show as connected with its five tools.

For Claude Desktop and other MCP clients, add a stdio server to the client's config.
For Claude Desktop that is `claude_desktop_config.json`, reachable from
**Settings → Developer → Edit Config**:

```json
{
  "mcpServers": {
    "oyez": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/attorneynate/oyez-mcp", "oyez-mcp"]
    }
  }
}
```

Restart the client afterward. If it reports that it cannot find `uvx`, give the
command's full path instead; `where uvx` on Windows or `which uvx` elsewhere prints
it.

### From source

This is the path for working on the server, or for a machine without uv. Clone it,
make a virtual environment, install two dependencies.

#### Windows (PowerShell)

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

#### macOS / Linux

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

Then point the client at the **venv's** Python and the absolute path to `server.py`.
There is no activation step — the interpreter path is the activation.

For Claude Code, from the repo folder in a normal terminal:

```bash
claude mcp add --scope user oyez -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
```

On Windows, spell out the absolute paths:

```powershell
claude mcp add --scope user oyez -- "C:\path\to\oyez-mcp\.venv\Scripts\python.exe" "C:\path\to\oyez-mcp\server.py"
```

For Claude Desktop and other clients:

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

## Use it in a browser

The same five tools also come as a web page that runs on your own computer, for
reading cases and transcripts without an MCP client:

```bash
uvx --from git+https://github.com/attorneynate/oyez-mcp oyez-ui
```

From a clone, run `python ui.py` with the venv's Python instead. Your browser opens
on the page, and Ctrl+C in the terminal stops it.

- **Search** by case name, party, or docket number, or **browse a Term** with each
  case's one-line holding. Click a case to open it.
- **Case** shows the facts, question, conclusion, each Justice's vote, the written
  opinions, and links to Oyez and Justia.
- **Oral argument** and **Announcement** show the transcripts. Filter by speaker
  (or click a name in the roster), by Justices or advocates, or by a word, and show
  timestamps if you want them. A long transcript loads a chunk at a time; **Load
  more** picks up where it stopped.
- The address bar holds the case and tab, so a view can be bookmarked.

The page listens on `127.0.0.1` only, port 8765 or a free one if that is taken
(`--port` picks another, `--no-browser` skips opening a tab). Nothing else on your
network can reach it, and it has no login because it needs none.

## Example prompts

- "Using the oyez tools, pull the Obergefell oral argument and show me only Justice Scalia's questions."
- "Search Oyez for New York Times v. Sullivan and summarize the holding and the vote."
- "Get the opinion announcement for Obergefell and quote the part Roberts read from the bench."
- "List the 2014 Term cases, then give me the vote breakdown for Glossip v. Gross."
- "In the Citizens United argument, what did the advocates say about corporate personhood? Advocates only."
- "List the 2024 Term with summaries and pick out the First Amendment cases."
- "Read me the Obergefell argument from the start, a chunk at a time."

## Troubleshooting

- **Slow first start.** A stdio server's first launch can exceed Claude Code's
  30-second startup timeout, and `uvx`'s first start also fetches and builds the
  package. Raise it with the `MCP_TIMEOUT` environment variable, in milliseconds —
  `MCP_TIMEOUT=60000` — before starting Claude Code.
- **`uvx` fails to fetch or build.** It needs network access the first time, for the
  repo and the two dependencies. Run
  `uvx --from git+https://github.com/attorneynate/oyez-mcp oyez-mcp` in a terminal to
  see the error; a server that starts then sits waiting for input, so end it with
  Ctrl+C.
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

`server.py` is a single-file stdio MCP server; `pyproject.toml` packages it so
`uvx` can fetch and run it, and the `oyez-mcp` command it defines is `server.main()`,
the same thing `python server.py` runs. It queries the same endpoints
oyez.org's own front end uses, then formats the JSON into Markdown for the model
rather than passing raw API responses through: a decision becomes a list of each
Justice's vote, what they wrote, and whose opinions they joined, and transcripts
become `Speaker: text` turns.

`ui.py` is the browser page: a small Starlette app, served by uvicorn (both come
with `mcp`), that calls those same tool functions and renders their Markdown. It
adds no Oyez logic of its own.

There is no database, and every tool call is a live HTTP request, with one exception:
a Term's case list, once fetched, is kept in memory for ten minutes. That covers the
four recent Terms `search_cases` scans, any Term `list_term_cases` lists, and the Term
`get_case` searches when a docket number is not the case's address. `max_chars`
exists because full transcripts are big enough to matter to a context window.

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
