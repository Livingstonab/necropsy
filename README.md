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
  check it on GitHub. An exhibit captured any other way says so plainly on its
  plaque — it is never passed off as a live capture.
- **The LLM is never called for a cached exhibit.** Opening a cached exhibit
  offline touches no network and calls no model. The letter and epitaph shown are
  the ones generated at fetch time and cached by commit SHA.
- No exhibit's numbers are invented. Every figure on a plaque is derived from the
  committed payload for that exhibit.

### A note on the shipped demo set

The eight exhibits that ship with this repository are **real repositories**, each
captured from the GitHub API and committed as a fixture. Every one is genuinely
archived by its own owner — the app never asserts a death GitHub has not
confirmed — and each plaque's "verify on GitHub" link resolves to the exact
commit the analysis was run against. They span Python, Ruby, Java and JavaScript,
and the reasons they stopped range from "absorbed by the platform it extended" to
"the work was finished and published".

The synthetic set the project was developed against
(`necropsy/graveyard/specimens.py`) still drives the test suite: those
descriptors let the tests pin behaviour at both score extremes, and across every
failure mode, without depending on what a live repository happens to look like
this week. They are fixtures for tests, not exhibits on the wall.

To re-capture the wall, or to curate different repositories, point
`necropsy/graveyard/manifest.json` at the ones you want and run
`python -m necropsy.cli seed-graveyard --refresh` with a `GITHUB_TOKEN` set. That
fetches each one from the GitHub API, commits its raw payload as a fixture, and
rewrites the manifest's pinned commit SHA and provenance to `api_verified` —
after which every "verify on GitHub" link resolves to a real commit. Repositories
outside the size guards (over 50 MB, over 5,000 files, or a tree GitHub truncates)
are reported per entry and left as they were, so a partial refresh cannot leave
the wall unloadable.

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

On Render's free plan there is no persistent disk, so the wall is rebuilt from the
image at every boot and a visitor's own analyses live only as long as the
container instance does. The free plan also spins the service down after 15
minutes without traffic, so the first visitor after a quiet spell waits out a cold
start. Neither affects the demo wall, which needs no network and no keys.

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
