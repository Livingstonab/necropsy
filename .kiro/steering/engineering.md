# NECROPSY — Engineering Rules

Always included. These are hard rules, not preferences. A violation is a defect, reviewable and revertible, even if the feature works.

Rules E1–E5 are the five that prevent demo failure. They are non-negotiable.

---

## E1. Never call the LLM without a cache-hit check first.

The only code path that may reach `LlmClient` is `AnalysisService.analyze()` after a cache lookup has executed and missed.

- No module may import the LLM client except `necropsy/llm/` and `necropsy/analysis/service.py`.
- The narrator is invoked exactly once per cache-miss analysis, at pipeline step 8 of 10.
- A test injects an `LlmClient` that raises on call and asserts that the cache-hit path, `get_exhibit`, `list_graveyard`, and every gallery render complete successfully.
- Prompt construction is not permitted before the cache check. Building a prompt you then discard is wasted tokens and a sign the ordering is wrong.

## E2. Never exceed N GitHub API calls per analysis. N = 10.

`GITHUB_CALL_BUDGET = 10`, enforced in `GitHubClient` by a per-analysis counter, not by convention.

- The declared call plan uses at most 9. The tenth is reserved for exactly one retry.
- Call 11 raises `CallBudgetExceeded`, which surfaces as `CALL_BUDGET_EXCEEDED`. The analysis fails loudly. It never degrades silently and never continues unbudgeted.
- Adding a call to the pipeline requires removing one or amending the plan table in `design.md` §4.1 in the same change.
- Before starting a cache-miss analysis, the client checks the persisted ledger: if `remaining < RATE_LIMIT_FLOOR` (12), the analysis is refused with `RATE_LIMITED` and zero calls are spent. Cached exhibits continue to serve.
- Every response's rate-limit headers are written to `rate_limit_ledger`. Rate-limit state is observable, not guessed.
- Loops over paginated endpoints are forbidden. Every endpoint is called with an explicit `per_page` and read once.
- `GITHUB_TOKEN` is used when present and never required. The app must start, serve, and pass its tests without it.

## E3. Every analysis is cached by repo_url + last_commit_sha.

`cache_key = sha256(canonical_url_lowercased + "@" + last_commit_sha + "@" + analysis_schema_version)`, with a `UNIQUE` constraint on `analyses.cache_key`.

- Two inputs that canonicalize to the same repository identity at the same SHA must produce the same key. Canonicalization is tested, not assumed.
- Writes use `INSERT ... ON CONFLICT(cache_key) DO NOTHING`, then read back, so concurrent web and MCP processes cannot duplicate or race.
- A served cache hit is byte-identical to the stored payload except `cache_status` and `served_at`, which are assembled at response time and never persisted.
- Bumping `analysis_schema_version` invalidates by miss, never by deletion. Old rows survive for comparison.
- `raw_github_json` is stored so re-normalization and re-scoring are possible with zero API calls.
- Nothing is cached for a failed analysis except the job's error state. A partial success is never written.

## E4. No code execution, no cloning, ever.

- GitHub REST API only. No `git`, no `tarball`/`zipball` download, no archive extraction, no package-manager invocation, no container.
- `necropsy/analysis/` and `necropsy/scoring/` may not import `subprocess`, `os.system`, `pty`, `multiprocessing`, or `importlib` for dynamic loading. An import-guard test enforces this.
- No `eval`, no `exec`, no `pickle.loads` on anything derived from a repository.
- Repository-derived strings are untrusted data everywhere:
  - SQL uses parameterized queries only. String-formatted SQL is a defect.
  - Rendering relies on Jinja2 autoescaping. `|safe` on a repo-derived value is a defect.
  - Prompts place repository content inside the delimited untrusted block with an explicit non-directive instruction.
  - No repository-derived string is ever used to build a filesystem path or a command.
- Network egress is exactly two destinations: `api.github.com` and the configured LLM endpoint. A third destination is a defect.

## E5. The scorecard must expose its inputs in the API response and the UI — no opaque scores.

Every scorecard, in every surface, carries for each factor: `key`, `label`, `raw_inputs`, `normalized`, `weight`, `contribution`, `confidence`, `rationale`. Plus `total_unrounded`, `score`, `band`, `data_gaps`, `scorecard_version`, `weights_checksum`.

- The sum of contributions equals `total_unrounded` within 1e-9, and `score == round(total_unrounded)`. This identity is a property test.
- No factor may be computed outside `necropsy/scoring/`. Duplicated scoring logic in the web layer or the MCP layer is a defect (there is one scorer, used by both).
- The LLM is given the scorecard and may explain it. It may never produce, adjust, or override a number.
- Missing data uses a documented neutral default, sets `confidence = "low"`, records the fact key in `data_gaps`, and says so in that factor's `rationale`. Silent defaults are a defect.
- Any change to weights, ramps, bands, or normalization increments `scorecard_version` and `analysis_schema_version` in the same change, and updates the golden-value tests in the same commit.

---

## E6. The scorer is a pure function.

`score(ScorecardInput) -> Scorecard` reads nothing but its argument.

- No clock. `as_of` and all derived day counts are explicit input fields. A score must not change because a day passed.
- No RNG, no environment variables, no database, no network, no filesystem, no global mutable state.
- Identical inputs produce an identical score, breakdown and band, in the same process, in a fresh process, and on another machine.
- Property tests cover bounds, determinism, deficit monotonicity, asset monotonicity, band partition, contribution identity, and cache invariance (`requirements.md` R-20).

## E7. Monotonicity is designed in, then verified.

Every factor is written as a monotone non-decreasing function of its deficit inputs and monotone non-increasing in its asset inputs; all weights are non-negative; the total is a non-negative weighted sum.

- Marking the test suite missing can never lower the difficulty. Adding tests can never raise it. The same holds for CI, licence, docs, dependency freshness, maintainer responses, and staleness.
- A new factor ships with its monotonicity argument stated in `design.md` and its Hypothesis property added in the same change.
- Non-monotone heuristics ("very old repos are easier because they were simpler") are rejected, however plausible they sound.

## E8. One pipeline, shared by every surface.

`AnalysisService.analyze()` is the single entry point for analysis. The Flask routes and the MCP tools are thin adapters.

- A route or tool handler containing fetch, normalization, scoring, prompt, or cache logic is a defect.
- The web app and the MCP server share the same package, the same models, the same scorer and the same SQLite file.
- Every surface returns the same shared error envelope: `code`, `message`, `retryable`, `details`, `correlation_id`. An unhandled exception reaching a client is a defect; it becomes `INTERNAL_ERROR` with a correlation id and a server-side-only stack trace.

## E9. The LLM returns one JSON object, validated before it is trusted.

- Exactly one provider call per analysis, returning every narrative field in a single object. Three sequential calls is a defect, as is a second call for "just the epitaph".
- At most one repair retry on unparseable or schema-invalid output, then deterministic fallback.
- Validation ladder, in order: JSON parse → JSON Schema → evidence grounding (every cited `fact_key` exists in the normalized facts and every `fact_value` matches) → plan shape (exactly three steps, each addressing a factor with non-zero contribution).
- Ungrounded epitaph → template epitaph with `epitaph_source = "template"`. Ungrounded evidence item → dropped. Bad plan → deterministic fallback plan.
- The LLM sees normalized facts, the scorecard, a truncated README excerpt and truncated manifest excerpts. It never sees raw payloads or the full file contents.
- `llm_model`, `prompt_template_version`, `prompt_hash`, tokens, latency and outcome are persisted for every attempt.

## E10. Failures degrade per section, never per analysis.

- A failed section renders as `unavailable` with a reason. The other six still render (`requirements.md` R-1.3).
- An LLM failure still persists facts, scorecard and `llm_status = "failed"`, so a later request can retry only the narrative stage.
- Guard failures that spend budget pointlessly are avoided by ordering: parse → rate-limit floor → cache → size caps → fetch. A `REPO_TOO_LARGE` result costs at most 4 calls and zero LLM calls.
- Errors are a closed enumeration. A new failure mode gets a code in `errors.py`, a row in `design.md` §6, and a rendered state in `_error.html`, in the same change.

## E11. Tests run offline, single-run, with no keys.

- The entire suite passes with no network and no API keys. Network access in a test is a defect; `respx` mocks httpx, `FakeLlmClient` replaces the provider.
- Never watch mode. Every documented and hooked command is single-run (`pytest -q`).
- GitHub behaviour is exercised through committed fixtures covering at least: healthy, archived, oversized, 404, 403 rate-limited, empty, truncated tree, no README, no manifest.
- Hypothesis strategies live once in `tests/strategies.py` and are reused. A counterexample that exposed a real defect is committed with its fix.
- `necropsy/analysis/` and `necropsy/scoring/` are mypy-strict. Ruff and mypy are gates, not suggestions.

## E12. Secrets and configuration.

- `GITHUB_TOKEN` and `LLM_API_KEY` are read from the environment only. They never appear in logs, responses, `/api/health`, fixtures, committed files, error details, or a config `__repr__`.
- `/api/health` reports `token_present` as a boolean. Never a prefix, never a length, never a value.
- Every tunable is an environment variable with a documented default in `.env.example`. A missing optional variable logs one warning naming the degraded capability and the app continues.
- The app must start with an entirely empty environment.

## E13. Determinism of the artifact, not just the code.

- Curated exhibits are pinned to commit SHAs and their raw payloads are committed, so the demo is reproducible without GitHub.
- `seed-graveyard --offline` validates committed fixtures against the manifest and fails with a named diff rather than silently reaching for the network.
- The seed loader is idempotent and never modifies user-submitted analyses.
- The dependency deprecation table is a committed, dated, versioned file. Live registry lookups are forbidden: they would breach E2 and add a third egress destination.

## E14. Performance budgets are gates.

- Cache hit: p95 under 200 ms server-side. A cache hit performs indexed SQLite reads only.
- Cache miss: p95 under 20 s, hard abort at `ANALYSIS_TIMEOUT_SECONDS` (25) checked at every stage boundary.
- Cold start to first byte: under 2 s. Migrations run idempotently at startup; nothing else blocks it.
- Cache-miss work never runs on a request thread. A bounded two-worker pool runs it; the client polls a job.
- Single-flight coalescing means concurrent identical requests run the pipeline once.

## E15. Change discipline.

- Every change names the requirement ID it serves. Work that serves no requirement does not ship.
- The spec is the source of truth. When implementation reveals the spec is wrong, update the spec in the same commit as the code.
- `.kiro/` is committed and must never appear in any `.gitignore` at any level.
- No new runtime dependency without a one-line justification in `design.md`. No Node.js toolchain. No service beyond SQLite, GitHub and the LLM provider.
- Commits stay scoped: one task, one concern. No drive-by refactors inside a feature commit.
