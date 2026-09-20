# Contributing

Bug reports and pull requests are welcome. This is a small single-file server, so
there is not much ceremony.

## Setup

```bash
git clone https://github.com/attorneynate/oyez-mcp.git
cd oyez-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python selftest.py
```

On Windows use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`, and keep
the folder outside `AppData` (the Store build of Python redirects those paths and
`venv` creation fails).

`selftest.py` is the whole test suite: it launches the server over stdio, lists the
tools, and pulls a real transcript from Oyez. It hits the live API, so it needs a
network connection and it will fail if Oyez is down or rate-limiting you. Run it
before and after your change.

## Reporting a bug

Include the tool call that went wrong — the tool name and its arguments, especially
the Term and docket — plus what you got back and what you expected. Two failures
look alike but are not: a wrong Term/docket pair (the server says
`Not found on Oyez`) versus the server mis-parsing a real Oyez response.

Thin or missing speaker attribution in older transcripts is usually the source data
rather than a bug. It is still worth reporting if the same case looks right on
oyez.org and wrong here.

## Pull requests

- Match the style already in `server.py`: type hints, small helpers, no new
  dependencies unless there is no other way. It is currently `mcp` and `httpx`, and
  keeping it there is a feature.
- Tool docstrings are the model-facing documentation. If you change a tool's
  arguments or behavior, update the docstring in the same commit — the model reads
  it, so a stale one is a real bug.
- Errors reach the model as readable text, not exceptions. Raise `OyezError` for
  anything a user can act on and let the tool render it; do not let a traceback kill
  the server.
- Keep formatting Markdown. The output is read by a model and often pasted by a
  human, so favor short headers and tables over dumping raw JSON.
- Be a considerate client of a free service: no new retry loops, no parallel
  fan-out, no polling. Add a `max_chars`-style cap to anything that can return an
  unbounded amount of text.
- Update `README.md` if you add or rename a tool or change its parameters.

## Scope

This server exposes Oyez. Wanting SCOTUS data that Oyez does not have — slip
opinions, dockets, the merits briefs — is reasonable, but it belongs in a different
server rather than a second data source bolted onto this one.
