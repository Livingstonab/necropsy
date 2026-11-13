# Implementation Plan: NECROPSY

| Field | Value |
|---|---|
| Spec ID | `necropsy` |
| Implements | `requirements.md`, per `design.md` |
| Schedule | 3 weeks, solo |
| Priority legend | **D** demo-critical · **S** submission-critical · **P** polish (cuttable) |

## Overview

34 tasks across three weeks, solo, spec-first. The shape of the plan follows from one judgement: the things most likely to kill the demo are the rate limit and the cache, so they are built first and the presentation layer is built last. Week 1 ends with a working cache, a budget-guarded GitHub client, an offline graveyard and a live MCP server — no LLM and no UI. Week 2 adds the scorecard, its property tests, the single LLM call and the gallery. Week 3 is polish, evidence and submission.

Tasks are ordered for execution. Each task names the requirements it satisfies and its done-criteria. No task requires cloning a repository, executing repository code, or spawning a subprocess against analyzed content (R-16).

Every task is sized for a single sitting. A task is not done until its done-criteria are checkable by running `pytest` offline.

---

## Tasks

Grouped by week. Weeks are a schedule, not a dependency boundary; the binding order is in the dependency graph below. Task numbers map one-to-one onto the `T-x.y` identifiers used in the graph (task 1.4 is T-1.4).

- [ ] 1. Week 1: Skeleton, cache, GitHub client, graveyard, MCP
  - Goal at end of week 1: `python -m necropsy.cli seed-graveyard --offline` populates a database from committed fixtures, and Kiro can call `analyze_repo` over MCP and get a real post-mortem with a scorecard. No LLM, no UI yet.

- [x] 1.1 Repository scaffold and tooling (T-1.1, submission-critical)
  - `necropsy/` package tree per design §2.2 (empty modules with docstrings).
  - `requirements.txt` / `requirements-dev.txt`, `pyproject.toml` with Ruff + mypy config (strict on `necropsy/analysis/`, `necropsy/scoring/`).
  - `.env.example` naming every variable in R-18.1 with defaults and comments.
  - `.gitignore` that excludes `.env`, `*.db`, `__pycache__`, and **explicitly does not exclude `.kiro/`**.
  - Top-level `README.md` stub, OSI `LICENSE` (MIT).
  - `pytest.ini`: `-q`, no watch mode, `--strict-markers`.
  - Done when: `ruff check .`, `mypy necropsy`, and `pytest` all run and pass on an empty suite.
  - _Requirements: R-18, R-19.4, R-21.4_

- [x] 1.2 Config, error taxonomy, base models (T-1.2, submission-critical)
  - `config.py`: typed settings from env with the documented defaults; `__repr__` redacts secrets; one warning per absent optional capability.
  - `errors.py`: `ErrorCode` enum exactly matching design §6, `NecropsyError` hierarchy carrying `code`, `message`, `retryable`, `details`, `correlation_id`, and a `to_envelope()`.
  - `models/`: Pydantic models for `NormalizedFacts`, `ScorecardInput`, `Factor`, `Scorecard`, `LlmNarrative`, `Analysis`.
  - Done when: every §6 code has a test asserting its envelope fields; config test asserts no secret appears in `repr`, logs, or `dict()`.
  - _Requirements: R-13.10, R-18.1, R-18.2, R-18.3_
  - _Dependencies: 1.1_

- [x] 1.3 SQLite store and migrations (T-1.3, demo-critical)
  - `store/db.py`: connection factory (WAL, `foreign_keys=ON`, per-thread connections), idempotent migration runner.
  - `store/migrations/0001_init.sql` exactly as design §3.
  - `store/analyses.py`: `cache_key()` helper, `get_by_cache_key`, `get_latest_for_url`, `insert_on_conflict_do_nothing`, `get_by_id`.
  - `store/exhibits.py`, `store/ledger.py`, `store/jobs.py`.
  - Done when: migrations apply twice with no error; a round-trip test writes and reads an `Analysis` with byte-identical JSON payloads; `cache_key` is stable across processes.
  - _Requirements: R-2, R-18.5_
  - _Dependencies: 1.2_

- [-] 1.4 Cache layer semantics (T-1.4, demo-critical)
  - Cache read/write API with explicit `CacheStatus`.
  - Schema-version bump → miss, no deletion.
  - In-process single-flight map keyed by `cache_key`.
  - `served_at` / `cache_status` injected at response assembly, not stored.
  - Done when: tests prove identical payload on hit except the two volatile fields; bump causes miss while the old row survives; two threads requesting the same key run the pipeline once.
  - _Requirements: R-2.1, R-2.2, R-2.3, R-2.4, R-2.5, R-2.6, R-3.6_
  - _Dependencies: 1.3_

- [~] 1.5 URL canonicalization (T-1.5, submission-critical)
  - `github/urls.py: canonicalize(raw) -> RepoRef | raises`.
  - Handles every form in R-4.1, bare `owner/repo`, rejects other hosts and garbage with the right codes and zero network calls.
  - Done when: table test covers all R-4 forms; Hypothesis property asserts equal canonical identity across the equivalent forms of a generated `owner/repo`.
  - _Requirements: R-4_
  - _Dependencies: 1.2_

- [~] 1.6 GitHub client with rate-limit guard (T-1.6, demo-critical)
  - `github/client.py`: single `httpx.Client`, 5 s connect / 10 s read timeouts, bearer token when present, per-analysis call counter raising `CallBudgetExceeded` on call 11.
  - Ledger write on every response; `RATE_LIMIT_FLOOR` pre-check.
  - ETag store + `If-None-Match`; 304 returns the stored body.
  - At most one retry on 5xx/connection error or honored `Retry-After`, charged to the budget.
  - No `git`, no archive download, anywhere.
  - Done when: `respx` tests cover budget enforcement, 304 reuse, single retry, `Retry-After`, ledger rows, and 403-rate-limit → `RATE_LIMITED`. Import-guard test asserts no process-spawning module is imported by `necropsy.analysis` or `necropsy.scoring`.
  - _Requirements: R-14, R-16.1_
  - _Dependencies: 1.2_

- [~] 1.7 Endpoint wrappers and the fixed call plan (T-1.7, demo-critical)
  - `github/endpoints.py`: typed wrapper per endpoint in design §4.1.
  - `analysis/fetch.py`: the declared 9-call plan as data, with guard ordering from design §4.2 and tolerated 404s for README and latest-release.
  - Size/file/truncation guards abort before any LLM consideration.
  - Done when: fixture tests for all nine R-19.2 scenarios; a test asserts `github_calls_used <= 9` on the happy path and that `REPO_TOO_LARGE` spends at most 4 calls and writes no analysis row.
  - _Requirements: R-3.1, R-13.1, R-13.2, R-13.3, R-13.4, R-13.5, R-13.6, R-14.1_
  - _Dependencies: 1.4, 1.5, 1.6_

- [~] 1.8 Normalization, detection, LOC estimation (T-1.8, demo-critical)
  - `analysis/normalize.py`: raw payloads → `NormalizedFacts`, with `as_of` frozen and all age fields derived from it only.
  - `analysis/detect.py`: the table-driven signal rules from design §4.3.
  - `detect/deprecated.json`: offline deprecation table v1, each entry with `status`, `since`, `replacement`, `source_url`; a compile date recorded in the file.
  - `analysis/loc.py`: bytes-per-line constants, confidence rules.
  - Done when: idempotence property passes on every fixture; adversarial path table test passes (`contrib/testdata/notatest.py` is not a test, `vendor/.travis.yml` is not this repo's CI); `data_gaps` is populated whenever a default substitutes.
  - _Requirements: R-1.2, R-7.2, R-20.8_
  - _Dependencies: 1.7_

- [~] 1.9 Graveyard manifest and seed loader (T-1.9, demo-critical)
  - `graveyard/manifest.json`: the 8 curated entries per design §8.4 with `slug`, `canonical_url`, `pinned_commit_sha`, `curator_note`, `display_order`, `expected_band`.
  - `graveyard/manifest.py`: parse + validate (unique slugs, resolvable URLs, orders).
  - `graveyard/seed.py`: materialize each entry; `--offline` validates committed fixtures against the manifest and fails with a diff rather than calling GitHub; per-entry report of `cached | refetched | failed`; non-zero exit if any entry cannot be materialized.
  - `graveyard/fixtures/`: committed raw payloads per entry.
  - `cli.py`: `init-db`, `seed-graveyard`, `analyze`, `serve`, `mcp`.
  - Done when: on a clean checkout with no network and no keys, `init-db` + `seed-graveyard --offline` yields 8 exhibits; re-running changes nothing (idempotent); deleting one fixture makes `--offline` fail with a named diff.
  - _Requirements: R-5, R-17_
  - _Dependencies: 1.8_

- [~] 1.10 AnalysisService orchestrator (T-1.10, demo-critical)
  - `analysis/service.py: analyze(url, force_refresh, allow_network)` implementing the 10 ordered steps in design §2.1, with a `NullNarrator` standing in for the LLM this week.
  - Per-section `unavailable` degradation rather than whole-analysis failure.
  - Stage timestamps recorded on the `jobs` row for honest progress copy.
  - Hard timeout checked at every stage boundary.
  - Done when: an end-to-end offline test analyzes a fixture repo and returns all seven sections with deterministic placeholders for the narrative; a test asserts the LLM client is unreachable on any path that did not first miss the cache.
  - _Requirements: R-1.3, R-3.1, R-3.2, R-3.3, R-3.4, R-3.5, R-15.5_
  - _Dependencies: 1.8_

- [~] 1.11 MCP server with all three tools (T-1.11, submission-critical)
  - `mcp/server.py`: stdio server, three tools, adapter only.
  - `mcp/schemas.py`: input/output schemas exactly as design §7.
  - `get_exhibit` and `list_graveyard` constructed with a network client that raises if used.
  - Shared error envelope on every failure path.
  - Committed `.kiro/settings/mcp.json` example block in the README.
  - Done when: in-process tests invoke each tool and validate the response against the committed schema; `get_exhibit` on an unknown repo returns `NOT_IN_GRAVEYARD`; both read tools pass with the raising client injected; the server starts with no `GITHUB_TOKEN` and no LLM key.
  - _Requirements: R-11_
  - _Dependencies: 1.9, 1.10_

- [~] 1.12 Hooks and quality gates wired (T-1.12, submission-critical)
  - `.kiro/hooks/analysis-pipeline-guard.kiro.hook` (save → single-run tests + lint).
  - `.kiro/hooks/regenerate-demo-graveyard.kiro.hook` (manual → seed regeneration from manifest).
  - `.kiro/hooks/no-clone-guard.kiro.hook` (pre-shell-tool → block clone/archive commands).
  - Each hook's trigger, action and rationale documented in `.kiro/README.md`.
  - Done when: editing a file under `necropsy/analysis/` runs the suite and lint; the manual hook regenerates the graveyard and reports per-entry status; the guard hook fires on a `git clone` attempt.
  - _Requirements: R-17.5, R-19.4, R-19.5_
  - _Dependencies: 1.9, 1.10_

- [~] 1.13 Register MCP server in Kiro and smoke-test the loop (T-1.13, submission-critical)
  - Configure `.kiro/settings/mcp.json`, confirm Kiro lists three tools, call each from a Kiro session, capture the transcript.
  - Done when: a Kiro session transcript showing all three tool calls succeeding is saved under `docs/evidence/`.
  - _Requirements: R-11.9, R-12.1_
  - _Dependencies: 1.11_

- [ ] 2. Week 2: Scorecard, LLM call, property tests, gallery
  - Goal at end of week 2: the full product works end to end in a browser, with real narratives and an auditable scorecard.

- [~] 2.1 Scorecard ramps, factors, bands (T-2.1, demo-critical)
  - `scoring/weights.py`: the eight-factor weight table (sums to 100) + `weights_checksum`.
  - `scoring/scorecard.py`: `score(ScorecardInput) -> Scorecard`, pure; `linear_ramp`, `log_ramp`; per-factor `raw_inputs`, `normalized`, `contribution`, `confidence`, `rationale`.
  - `scoring/bands.py`: the five bands partitioning `[0,100]`.
  - Neutral defaults for every unavailable input, each recorded in `data_gaps` with `confidence = "low"`.
  - Done when: golden-value unit tests pin the score of all eight demo repos; a test asserts the module imports nothing from `datetime.now`, `random`, `os.environ`, or the store.
  - _Requirements: R-8.1, R-8.2, R-8.3, R-8.4, R-8.5, R-8.7_
  - _Dependencies: 1.9, 1.10_

- [~] 2.2 Property-based tests for the scorecard (T-2.2, submission-critical)
  - `tests/strategies.py`: one shared `scorecard_inputs()` strategy plus targeted deficit/asset mutators.
  - Properties: bounds, determinism (same process and fresh subprocess), deficit monotonicity across all nine deficit inputs, asset monotonicity across all seven asset inputs, band partition, contribution identity.
  - `max_examples=200`; failing examples that exposed real defects are committed to `.hypothesis/examples` and written up.
  - Done when: all properties pass; a defect log is started in `docs/pbt-findings.md` recording each property, the defect it found, and the fix.
  - _Requirements: R-20.1, R-20.2, R-20.3, R-20.4, R-20.6, R-20.7, R-20.9_
  - _Dependencies: 2.1_

- [~] 2.3 Cache-invariance and normalization properties (T-2.3, submission-critical)
  - Property: for every fixture, `score(normalize(raw))` equals `score(rehydrate_from_sqlite(analysis_id))` including the full factor breakdown, for the same SHA and schema version.
  - Property: `normalize(normalize_input)` is idempotent.
  - Done when: both properties pass with `deadline=None` and a temp database per example.
  - _Requirements: R-20.5, R-20.8_
  - _Dependencies: 2.1_

- [~] 2.4 LLM client, prompt, schema, validation ladder (T-2.4, demo-critical)
  - `llm/client.py`: `LlmClient` protocol, one provider implementation, `FakeLlmClient`.
  - `llm/prompt.py`: versioned template with the delimited untrusted block and the "facts only, cite fact keys" rules.
  - `llm/schema.py`: the single-object response schema from design §4.5.
  - `llm/validate.py`: parse → schema → grounding → plan checks, one repair retry, deterministic fallbacks for epitaph and plan.
  - `llm_calls` accounting rows.
  - Done when: `FakeLlmClient` scenarios (valid, invalid JSON, schema violation, ungrounded epitaph, four-step plan, provider error) each produce the specified outcome; a test asserts exactly one provider call per successful analysis and at most two including the repair retry; a prompt-injection fixture README cannot inject a claim.
  - _Requirements: R-15, R-9.1, R-9.2, R-9.3, R-9.4, R-9.5, R-10.1, R-10.2, R-10.3, R-10.4, R-10.5_
  - _Dependencies: 2.1_

- [~] 2.5 Wire narration into the pipeline (T-2.5, demo-critical)
  - Replace `NullNarrator` with the real narrator; persist `narrative_json`, `llm_status`, model and prompt metadata.
  - Keyless mode produces template epitaph + fallback plan, labelled.
  - Done when: an offline keyless end-to-end test produces all seven sections with `llm_status = "skipped"`; an LLM-error test produces `llm_status = "failed"` with facts and scorecard intact and a retry-only-the-LLM path available.
  - _Requirements: R-3.3, R-3.4, R-13.7, R-13.8_
  - _Dependencies: 2.4_

- [~] 2.6 Flask app, routes, job model (T-2.6, demo-critical)
  - `web/app.py`, `web/routes.py`: `GET /`, `GET /exhibit/{slug}`, `POST /api/analyze`, `GET /api/jobs/{id}`, `GET /api/exhibits`, `GET /api/health`.
  - Bounded `ThreadPoolExecutor(max_workers=2)`; request threads never run a cache-miss analysis.
  - `/api/health` exposes budget, cache and demo counts, `token_present` boolean only.
  - Done when: Flask test client covers each route offline against the seeded database; `/api/health` never contains a secret.
  - _Requirements: R-1.1, R-1.4, R-1.5, R-14.8_
  - _Dependencies: 2.5_

- [~] 2.7 Gallery wall (T-2.7, demo-critical)
  - `base.html`, `gallery.html`, `_card.html`, `necropsy.css`: dark room, CSS-only spotlight, brass plaque, band chip with text label, epitaph, deathmark.
  - Server-side sort (`curated | deadest | hardest`) via a GET form.
  - Empty state; single-column at ≤480 px; keyboard-activatable cards; reduced-motion support.
  - Done when: the wall renders 8 exhibits from the seed with no network; contrast values for `--ink` and `--ink-dim` are recorded in a comment and verified; the page is fully usable with JavaScript disabled.
  - _Requirements: R-6_
  - _Dependencies: 2.6_

- [~] 2.8 Exhibit detail view (T-2.8, demo-critical)
  - `exhibit.html` sections in the design §8.2 order.
  - `_scorecard.html`: the factor table with raw inputs, normalized value, weight, contribution bar, confidence, rationale, and a footer proving Σ contributions = total; `weights_checksum` shown.
  - Evidence table in monospace with `fact_key | fact_value | claim`.
  - Provenance footer: `as_of`, calls used, `llm_status`, versions, deprecation-table version, GitHub link.
  - Done when: every factor is visible without interaction (or behind one disclosure control), and a test asserts the rendered page contains every factor key present in the API response.
  - _Requirements: R-7, R-8.2_
  - _Dependencies: 2.6_

- [~] 2.9 Submit-and-poll flow and error rendering (T-2.9, demo-critical)
  - `submit.js`: POST → poll → navigate; `aria-live` status; stage copy driven by the job row; no-JS form fallback.
  - `_error.html` renders every §6 code with a next action, inside the gallery frame.
  - Done when: a manual pass over each error code shows an in-frame message; progress state appears within 300 ms.
  - _Requirements: R-1.5, R-13.9_
  - _Dependencies: 2.6_

- [~] 2.10 Rate-limit and degraded-mode UX (T-2.10, submission-critical)
  - `RATE_LIMITED` surface showing `remaining`, `reset_at`, `retry_after_seconds`, a statement that cached exhibits still work, and a link to the demo graveyard.
  - Header indicator reflecting `/api/health`.
  - Done when: a forced-floor test renders the degraded state while all cached exhibits remain reachable.
  - _Requirements: R-13.3, R-14.3_
  - _Dependencies: 2.7, 2.8, 2.9_

- [~] 2.11 Curate and lock the demo graveyard (T-2.11, demo-critical)
  - Select the final eight repos against the design §8.4 failure-mode matrix; verify at least three languages and spread across at least four bands.
  - Pin SHAs, regenerate fixtures, record `expected_band`, write `curator_note` for each.
  - Done when: `seed-graveyard --offline` reproduces all eight with no band drift against `expected_band`, and the band distribution is recorded in `.kiro/README.md`.
  - _Requirements: R-5.7, R-17.2_
  - _Dependencies: 2.7, 2.8, 2.9_

- [ ] 3. Week 3: Polish, evidence, submission

- [~] 3.1 Epitaph and plan quality pass (T-3.1, polish)
  - Iterate `prompt_template_version` against the eight demo repos; measure the share of epitaphs passing the grounding check on the first attempt and record it.
  - Improve template fallbacks so a keyless run still reads well.
  - Tighten plan ordering and effort estimates.
  - Done when: all eight demo epitaphs are grounded, under 140 characters, and each names a different concrete detail; every plan step references a factor with non-zero contribution.
  - _Requirements: R-9, R-10_
  - _Dependencies: 2.10, 2.11_

- [~] 3.2 Visual polish (T-3.2, polish)
  - Spotlight falloff, plaque engraving, type scale, language bar, band chips, focus rings.
  - Verify contrast and reduced-motion once more after styling changes.
  - Done when: the wall reads as a gallery at 1440 px, 1024 px and 390 px widths with no layout break.
  - _Requirements: R-6, R-7_
  - _Dependencies: 2.10, 2.11_

- [~] 3.3 Kiro revival-spec workflow, verified and committed (T-3.3, submission-critical)
  - Run the §7.4 loop against one real dead repo; have Kiro write `.kiro/specs/{owner}-{repo}-revival/requirements.md` from the `analyze_repo` output; commit it as evidence.
  - Document the exact prompts used.
  - Done when: a generated revival spec is committed and its requirements trace back to specific `whats_missing` items and scorecard factors.
  - _Requirements: R-12_
  - _Dependencies: 2.10, 2.11_

- [~] 3.4 PBT findings write-up (T-3.4, submission-critical)
  - Finish `docs/pbt-findings.md`: for each property, the defect it caught, why the unit tests missed it, the minimal failing example, the fix.
  - Done when: at least three distinct defects are documented with their Hypothesis counterexamples.
  - _Requirements: R-20.10_
  - _Dependencies: 2.10, 2.11_

- [~] 3.5 Performance verification (T-3.5, submission-critical)
  - Measure cache-hit p95, cache-miss p95, cold start; record `github_calls_used` distribution across the eight demo analyses.
  - Done when: numbers are recorded in `docs/evidence/performance.md` and meet the targets, or a documented explanation exists.
  - _Requirements: NFR-1, NFR-2, NFR-5_
  - _Dependencies: 2.10, 2.11_

- [~] 3.6 Documentation (T-3.6, submission-critical)
  - Top-level `README.md`: what it is, 60-second setup, `init-db` + `seed-graveyard --offline`, `serve`, `pytest`, the MCP config block, env table, explicit statements of the no-execution / no-clone / no-login constraints.
  - `.kiro/README.md` finalized with the artifact map and hook rationale.
  - Done when: a clean clone, following only the README, reaches a populated gallery with no network and no keys.
  - _Requirements: R-21.4, R-11.9_
  - _Dependencies: 2.10, 2.11_

- [~] 3.7 Accessibility and offline audit (T-3.7, polish)
  - Keyboard-only pass, contrast check, reduced-motion check, airplane-mode run of the full demo.
  - Done when: all four pass with findings recorded.
  - _Requirements: R-6.4, R-6.5, R-5.1_
  - _Dependencies: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [~] 3.8 Demo video (T-3.8, demo-critical)
  - 3 minutes: gallery wall → one exhibit (plaque, epitaph, evidence, scorecard, plan) → paste a live URL → Kiro calling the MCP tools and writing a revival spec → hooks firing.
  - Script written first; demo graveyard used for everything except the single live paste.
  - Done when: recorded, under the time limit, with no live-API dependency except the one deliberate paste.
  - _Dependencies: 3.7_

- [~] 3.9 Submission write-up (T-3.9, submission-critical)
  - How Kiro was used: spec-driven flow, the steering files and their effect on generated code, the three hooks with trigger/action/rationale, the MCP server as both product surface and Kiro integration, PBT findings.
  - Done when: submitted with links to `.kiro/` artifacts and the generated revival spec.
  - _Dependencies: 3.8_

- [~] 3.10 Freeze (T-3.10, demo-critical)
  - Tag, re-run the full suite offline, re-run `seed-graveyard --offline`, verify `.kiro/` is committed and not ignored at any level, confirm no secret in history.
  - Done when: a fresh clone on a different machine reproduces the demo.
  - _Dependencies: 3.9_

---

## Task Dependency Graph

### Waves

Tasks within a wave have no dependency on each other and may be done in any order. A wave starts only when every wave it depends on is complete. Solo execution follows the flat order below; the waves record where that order is a choice rather than a constraint.

```json
{
  "waves": [
    { "wave": 1,  "name": "Scaffold",            "tasks": ["T-1.1"],                                      "depends_on": [] },
    { "wave": 2,  "name": "Contracts",           "tasks": ["T-1.2"],                                      "depends_on": [1] },
    { "wave": 3,  "name": "Primitives",          "tasks": ["T-1.3", "T-1.5", "T-1.6"],                    "depends_on": [2] },
    { "wave": 4,  "name": "Cache and call plan", "tasks": ["T-1.4", "T-1.7"],                             "depends_on": [3] },
    { "wave": 5,  "name": "Facts",               "tasks": ["T-1.8"],                                      "depends_on": [4] },
    { "wave": 6,  "name": "Pipeline and seed",   "tasks": ["T-1.9", "T-1.10"],                            "depends_on": [5] },
    { "wave": 7,  "name": "Surfaces and score",  "tasks": ["T-1.11", "T-1.12", "T-2.1"],                  "depends_on": [6] },
    { "wave": 8,  "name": "Proof and narrative", "tasks": ["T-1.13", "T-2.2", "T-2.3", "T-2.4"],          "depends_on": [7] },
    { "wave": 9,  "name": "Wire narration",      "tasks": ["T-2.5"],                                      "depends_on": [8] },
    { "wave": 10, "name": "Web app",             "tasks": ["T-2.6"],                                      "depends_on": [9] },
    { "wave": 11, "name": "Gallery",             "tasks": ["T-2.7", "T-2.8", "T-2.9"],                    "depends_on": [10] },
    { "wave": 12, "name": "Degraded UX, curate", "tasks": ["T-2.10", "T-2.11"],                           "depends_on": [11] },
    { "wave": 13, "name": "Polish and evidence", "tasks": ["T-3.1", "T-3.2", "T-3.3", "T-3.4", "T-3.5", "T-3.6"], "depends_on": [12] },
    { "wave": 14, "name": "Audit",               "tasks": ["T-3.7"],                                      "depends_on": [13] },
    { "wave": 15, "name": "Demo video",          "tasks": ["T-3.8"],                                      "depends_on": [14] },
    { "wave": 16, "name": "Write-up",            "tasks": ["T-3.9"],                                      "depends_on": [15] },
    { "wave": 17, "name": "Freeze",              "tasks": ["T-3.10"],                                     "depends_on": [16] }
  ]
}
```

### Flat execution order (exact)

**Week 1 (foundation):**
T-1.1 → T-1.2 → T-1.3 → T-1.4 → T-1.5 → T-1.6 → T-1.7 → T-1.8 → T-1.9 → T-1.10 → T-1.11 → T-1.12 → T-1.13

**Week 2 (product):**
T-2.1 → T-2.2 → T-2.3 → T-2.4 → T-2.5 → T-2.6 → T-2.7 → T-2.8 → T-2.9 → T-2.10 → T-2.11

**Week 3 (evidence and polish):**
T-3.1 → T-3.2 → T-3.3 → T-3.4 → T-3.5 → T-3.6 → T-3.7 → T-3.8 → T-3.9 → T-3.10

### Graph

```
T-1.1 scaffold
  └─> T-1.2 config / errors / models
        ├─> T-1.3 store + migrations
        │     └─> T-1.4 cache semantics ────────────────┐
        ├─> T-1.5 url canonicalization ─────────────┐    │
        └─> T-1.6 github client + budget guard ──┐  │    │
                                                 v  v    v
                                            T-1.7 fixed call plan
                                                 │
                                                 v
                                            T-1.8 normalize / detect / loc
                                                 │
                     ┌───────────────────────────┴───────────┐
                     v                                       v
              T-1.9 graveyard seed                  T-1.10 AnalysisService
                     │                                       │
                     │                     ┌─────────────────┴──────────┐
                     │                     v                            v
                     │              T-1.11 MCP server            T-2.1 scorecard
                     │                     │                      │   │   │
                     │                     v                      │   │   └─> T-2.2 PBT
                     │              T-1.13 Kiro smoke-test        │   └─────> T-2.3 cache-invariance PBT
                     │                                            │
                     │                                            v
                     │                                      T-2.4 LLM call + validation
                     │                                            │
                     └────────────────┬───────────────────────────┤
                                      v                           v
                                 T-2.6 Flask app <──────── T-2.5 wire narration
                                      │
                 ┌────────────────────┼────────────────────┐
                 v                    v                    v
           T-2.7 gallery        T-2.8 exhibit        T-2.9 submit+errors
                 └────────────────────┼────────────────────┘
                                      v
                                 T-2.10 degraded UX
                                      v
                                 T-2.11 curate + lock demo set
                                      │
          ┌──────────┬────────────┬────┴─────┬───────────┬──────────┐
          v          v            v          v           v          v
      T-3.1      T-3.2        T-3.3      T-3.4       T-3.5      T-3.6
      epitaph    visual       Kiro       PBT         perf       docs
                              workflow   write-up
          └──────────┴────────────┴──────────┴───────────┴──────────┘
                                      v
                            T-3.7 a11y/offline audit
                                      v
                            T-3.8 demo video
                                      v
                            T-3.9 write-up ──> T-3.10 freeze

T-1.12 hooks: depends on T-1.1 only; scheduled after T-1.11 so the
              regeneration hook has a seed command to call.
```

### Dependency notes

- T-2.1 (scorecard) has no dependency on T-2.4 (LLM). The score is built and property-tested before any narrative exists, so the numbers are trustworthy before prose explains them.
- T-1.11 (MCP) depends only on T-1.10, which is why the MCP server ships in week 1 against placeholder narratives rather than waiting for the LLM.
- T-2.11 (final curation) must precede T-3.1 (epitaph tuning): tuning prompts against a non-final exhibit set wastes the effort.
- T-3.3 (Kiro revival-spec workflow) depends on T-2.5, because the generated spec is only convincing with real narratives.
- T-1.9 (graveyard seed) and T-1.10 (service) are independent after T-1.8 and can be interleaved.
- Nothing in week 3 blocks anything else in week 3 except the final three: T-3.7 → T-3.8 → T-3.9 → T-3.10.

## Notes

### Classification

| Demo-critical (D) | Submission-critical (S) | Polish (P) |
|---|---|---|
| T-1.3, T-1.4, T-1.6, T-1.7, T-1.8, T-1.9, T-1.10, T-2.1, T-2.4, T-2.5, T-2.6, T-2.7, T-2.8, T-2.9, T-2.11, T-3.8, T-3.10 | T-1.1, T-1.2, T-1.5, T-1.11, T-1.12, T-1.13, T-2.2, T-2.3, T-2.10, T-3.3, T-3.4, T-3.5, T-3.6, T-3.9 | T-3.1, T-3.2, T-3.7 |

### Cut order under time pressure

Cut from the bottom up: T-3.7 → T-3.2 → T-3.1 → T-2.10 → T-1.13. Nothing above T-2.11 in the execution order may be cut; cutting any of it breaks either the demo or a stated submission requirement.
