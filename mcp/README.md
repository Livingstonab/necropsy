# NECROPSY as an MCP server

NECROPSY exposes its post-mortem pipeline to Kiro (and any MCP client) over a
stdio server with three tools. The server is a thin adapter over the same
`AnalysisService`, scorer and SQLite file the web app uses — there is one
pipeline, not two (engineering rule E8).

## Tools

| Tool | What it does | Network |
|---|---|---|
| `analyze_repo` | Full post-mortem of a public GitHub repo: facts, transparent scorecard, evidence-backed cause of death, three-step revival plan. Cached by commit SHA. | Only on a cache miss with `allow_network: true` (the default). At most 10 GitHub calls, one LLM call. |
| `get_exhibit` | Read a cached post-mortem by `repo_url` or `slug`. Returns `NOT_IN_GRAVEYARD` on a miss. | None, ever. |
| `list_graveyard` | List exhibits as summaries (slug, score, band, epitaph, days since last commit). Sort `curated` \| `deadest` \| `hardest`. | None, ever. |

Every scorecard, on every tool, carries each factor's `key`, `raw_inputs`,
`normalized`, `weight`, `contribution`, `confidence` and `rationale`, plus
`total_unrounded`, `score`, `band` and `weights_checksum`. No tool returns an
opaque number (E5).

## Register it in Kiro

Add this to `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json`
(user). A committed example lives at
[`.kiro/settings/mcp.json`](../.kiro/settings/mcp.json).

```json
{
  "mcpServers": {
    "necropsy": {
      "command": "python",
      "args": ["-m", "necropsy.cli", "mcp"],
      "env": {
        "DATABASE_PATH": "./necropsy.db"
      },
      "disabled": false,
      "autoApprove": ["list_graveyard", "get_exhibit"]
    }
  }
}
```

Notes.

- `command` must resolve to a Python 3.11+ interpreter with NECROPSY's
  dependencies installed. If you use a virtualenv, point `command` at that
  environment's `python` (for example `.venv/Scripts/python.exe` on Windows, or
  `.venv/bin/python` elsewhere), or set `"args"` to run through your runner.
- `DATABASE_PATH` should point at a database that has been seeded
  (`python -m necropsy.cli init-db && python -m necropsy.cli seed-graveyard`), so
  `list_graveyard` and `get_exhibit` have the curated wall to serve with no
  network.
- `autoApprove` lists the two read-only, zero-network tools. `analyze_repo` is
  left off deliberately, because it is the one tool that may spend GitHub and LLM
  calls; approving it per use keeps that spend visible.
- A `GITHUB_TOKEN` raises the operator's rate limit for live `analyze_repo`
  calls. It is never required, and the read tools work without it. Put it in the
  `env` block only if you want live analysis to use it.
- An `LLM_PROVIDER` / `LLM_API_KEY` pair enables model-written narratives for
  live analyses. Without them, narratives are deterministic templates, labelled
  as such. The demo wall is pre-narrated either way.

Reconnect the server from the MCP Server view in Kiro's feature panel, or via the
command palette (search "MCP"), after editing the config.

## Verify it

In a terminal, round-trip the server over stdio against the seeded database:

```
python -m necropsy.cli init-db
python -m necropsy.cli seed-graveyard
python scripts/mcp_smoke.py
```

Expected tail:

```
tools: ['analyze_repo', 'get_exhibit', 'list_graveyard']
list_graveyard: 8 exhibits, hardest first
   97  necropsy-specimens--monolith-erp  — ...
   ...
MCP stdio round-trip OK
```

## Use it from Kiro (the revival-spec workflow)

1. Register the server as above and reconnect it.
2. Ask Kiro: *"Analyze `github.com/owner/dead-repo` with NECROPSY, then write a
   revival spec."*
3. Kiro calls `analyze_repo` and receives the structured post-mortem.
4. Kiro writes `.kiro/specs/{owner}-{repo}-revival/` mapping each `whats_missing`
   item and each `resurrection_plan` step to a requirement, and each
   `scorecard.factors[*]` entry with a non-zero `contribution` to an acceptance
   criterion.

The output shape is designed for this: `cause_of_death.evidence`,
`whats_missing.items`, `scorecard.factors` and `resurrection_plan` are lists of
objects with stable keys, so Kiro transforms them rather than re-parsing prose.

## What it will not do

NECROPSY reads the GitHub REST API and nothing else. It does not clone, does not
execute repository code, does not open issues, and does not write anywhere except
its own SQLite cache (engineering rule E4, product principle P10). "Analyze this
repo" is the whole contract.
