# NECROPSY

Automated post-mortems for dead GitHub repositories, presented as a museum.

Paste a public GitHub URL and NECROPSY performs an autopsy from GitHub REST API
metadata alone — it never clones, never executes, never writes back. It returns a
plaque: an evidence-backed cause of death, a transparent revival-difficulty
scorecard you can audit by hand, a one-line epitaph grounded in a real fact, the
letter the repository never sent its creator, and a three-step resurrection plan.

Built for Kiroween 2026, category Frankenstein.

## Run it

```
pip install -r requirements.txt
python -m necropsy.cli init-db
python -m necropsy.cli seed-graveyard
python -m necropsy.cli serve
```

Open `http://localhost:5000`. No API keys are required for the demo graveyard.

Other commands: `analyze <url>` (one post-mortem to the terminal), `list`
(the wall as text), `mcp` (the MCP server over stdio), `health`.

## What "offline" means here

When the app runs without network access, it serves **cached analyses of real
data**, not fabricated demo content.

- The preloaded graveyard ships as committed fixtures. Each exhibit was produced
  by the same analysis pipeline a live paste uses — the same normalizer, the same
  scorer — over payloads committed under `necropsy/graveyard/fixtures/`.
- Every exhibit traces to a `repo_url`, a `commit_sha`, and a `fetched_at`
  timestamp, all recorded in `data/seed_log.json`, which is committed. Open that
  file to see the provenance of every exhibit on the wall.
- Each exhibit carries a visible tag showing where its data came from. An
  API-verified exhibit links to the repository at the exact commit SHA so you can
  check it on GitHub. A compiled specimen says so plainly — it is not passed off
  as a live capture.
- **The LLM is never called for a cached exhibit.** Opening a cached exhibit
  offline touches no network and calls no model. The letter and epitaph shown are
  the ones generated at fetch time and cached by commit SHA.
- No exhibit's numbers are invented. Every figure on a plaque is derived from the
  committed payload for that exhibit.

### A note on the shipped demo set

The eight exhibits that ship with this repository are **compiled specimens** under
the `necropsy-specimens` owner, each demonstrating one real failure mode (archived
by owner, dependency-era death, single-maintainer disappearance, declared WIP,
stale-but-healthy, no-licence, finished-and-quiet, abandoned monolith). They are
labelled as specimens in the manifest, in `seed_log.json`, and on every plaque.

To build a wall of **real** repositories instead, point
`necropsy/graveyard/manifest.json` at the repositories you want and run
`python -m necropsy.cli seed-graveyard --refresh` with a `GITHUB_TOKEN` set. That
fetches each one from the GitHub API, commits its raw payload as a fixture, and
flips its provenance to `api_verified` — after which every "verify on GitHub" link
resolves to a real commit.

## Narratives

The epitaph, the cause of death, and the letter are written by one LLM call per
analysis, cached by commit SHA, and validated before they are trusted: every
claim must cite a fact that matches the repository, the letter must quote at least
two concrete tokens from it and avoid a list of generic phrases, or it is rejected
and rebuilt from a deterministic, evidence-grounded template. With no LLM key
configured, NECROPSY uses those templates directly — still specific, still true,
just not model-written. Set `LLM_PROVIDER` and `LLM_API_KEY` to enable model
narratives; the gate is identical either way.

## Deploy

A `Dockerfile` and `render.yaml` ship with the project. The image bakes in the
seeded graveyard, so the site is live on first deploy with no configuration. Add
`GITHUB_TOKEN` (and optionally `LLM_PROVIDER` / `LLM_API_KEY`) in the platform's
environment to enable faster live analysis and model-written narratives.

## MCP

NECROPSY exposes its pipeline to Kiro over the Model Context Protocol:
`analyze_repo`, `get_exhibit`, `list_graveyard`, `write_revival_spec`. See
[`mcp/README.md`](mcp/README.md) and the committed example at
`.kiro/settings/mcp.json`.

## Engineering

GitHub REST API only, at most ten calls per analysis, one LLM call, everything
cached by `(repo_url, commit_sha, schema_version)` in SQLite. Read-only and
remote: no clone, no execution, no writes anywhere but the cache. The scorer is a
pure function with property-based tests; the suite runs offline with no keys.

MIT licensed.
