# Requirements Document

**NECROPSY — automated post-mortems for dead GitHub repositories.**

| Field | Value |
|---|---|
| Spec ID | `necropsy` |
| Status | Draft, approved for implementation |
| Owner | Solo maintainer |
| Target | Kiroween 2026, category: Frankenstein |
| Spec schema version | 1 |

---

## Introduction

*Section 1 — problem statement.*

A dead public GitHub repository holds information that is expensive for a human to extract: why it stopped, what it would cost to restart, and whether restarting is worth it. The information exists in commit history, issue threads, dependency manifests and the absence of files (no `LICENSE`, no CI config, no tests), but reading it takes 20–40 minutes of manual work per repo.

NECROPSY performs that extraction automatically from GitHub REST API metadata alone, and presents the result as a museum exhibit: a plaque, an epitaph, a transparent scorecard, and a three-step resurrection plan.

## 2. Scope

**In scope.** Public GitHub repositories. Read-only analysis via GitHub REST API. One LLM call per analysis for narrative fields. SQLite cache. A web gallery. An MCP server exposing the analysis to Kiro.

**Out of scope.** User accounts, authentication, private repositories, GitLab/Bitbucket/self-hosted Git, repository cloning, code execution, static analysis requiring a parser per language, writing to GitHub (no issues, no PRs, no forks), payment, multi-tenancy, background crawling of GitHub.

## Glossary

*Section 3.*

| Term | Definition |
|---|---|
| **Analysis** | The complete output for one repository at one commit SHA: normalized GitHub facts + scorecard + LLM narrative. |
| **Exhibit** | An analysis as presented in the gallery: spotlight, plaque, epitaph, score band. |
| **Graveyard** | The collection of exhibits rendered on the gallery wall. |
| **Demo graveyard** | 6–8 curated exhibits shipped pre-cached in the repository. Renders with zero network calls. |
| **Cache key** | `sha256(canonical_url + "@" + last_commit_sha + "@" + analysis_schema_version)`. |
| **`as_of`** | The UTC timestamp frozen at the moment GitHub data was fetched. All age arithmetic uses `as_of`, never wall-clock time at render. |
| **Call budget** | Hard ceiling of 10 GitHub REST calls per cache-miss analysis. |
| **Scorecard** | The revival difficulty computation: named factors, raw inputs, normalized values, weights, weighted contributions, total, band. |
| **Deficit input** | A scorecard input where a larger value means the repo is in worse condition. |
| **Asset input** | A scorecard input where a larger value means the repo is in better condition. |

## 4. Requirement notation

Each requirement has an ID (`R-n`), a user story, and numbered acceptance criteria. Acceptance criteria use `WHEN <trigger> THEN <system> SHALL <response>` or `GIVEN <precondition> WHEN <trigger> THEN <response>`. Every criterion is intended to be mechanically testable. Priority is one of:

- **D (demo-critical)** — the 3-minute demo video fails without it.
- **S (submission-critical)** — a stated hackathon requirement; must exist and work, may be unglamorous.
- **P (polish)** — improves the result, cuttable under time pressure.

---

## Requirements

*Section 5 — the 21 requirements. Non-functional requirements and assumptions follow in sections 6–8.*

### R-1 — Submit a repository URL and receive a post-mortem

**Priority: D**

> As a judge with no account and no setup, I paste a public GitHub URL into one field and get a complete post-mortem without any further input.

#### Acceptance criteria
1. WHEN a user loads `/` THEN the system SHALL render the gallery with a single visible text input and a submit control, and SHALL NOT require login, signup, cookie consent, or an API key.
2. WHEN a user submits a valid public GitHub repository URL THEN the system SHALL return a post-mortem containing all seven sections: `what_was_built`, `time_of_death`, `cause_of_death`, `whats_missing`, `scorecard`, `epitaph`, `resurrection_plan`.
3. WHEN any one of the seven sections cannot be produced THEN the system SHALL render the remaining sections and SHALL mark the failed section with an explicit `unavailable` state and a reason string, rather than failing the whole analysis.
4. WHEN a post-mortem is returned THEN the response SHALL include `cache_status` with value `hit` or `miss`, `as_of`, `last_commit_sha`, and `analysis_schema_version`.
5. WHEN the user submits the form THEN the system SHALL display progress state within 300 ms and SHALL NOT present an unresponsive page while the analysis runs.

---

### R-2 — Cache hit path

**Priority: D**

> As a judge re-running a demo repo, I get the identical result instantly, because an identical analysis is never recomputed.

#### Acceptance criteria
1. GIVEN an analysis row exists whose `cache_key` matches the requested repository's canonical URL, last commit SHA and current `analysis_schema_version`, WHEN the analysis is requested THEN the system SHALL return the stored analysis.
2. WHEN a cache hit occurs THEN the system SHALL make zero GitHub API calls beyond the single SHA-resolution call, and zero LLM calls.
3. WHEN a cache hit occurs THEN the end-to-end server response time SHALL be under 200 ms at the 95th percentile on the demo dataset.
4. WHEN a cache hit occurs THEN the returned payload SHALL be byte-identical to the payload originally stored for that `cache_key`, except for the `cache_status` and `served_at` fields.
5. GIVEN an exhibit is requested by slug or by canonical URL with no SHA resolution needed (gallery navigation), WHEN the exhibit is served THEN the system SHALL make zero network calls.
6. WHEN `analysis_schema_version` is incremented in code THEN previously stored analyses SHALL be treated as misses and SHALL NOT be deleted.

---

### R-3 — Cache miss path

**Priority: D**

> As a user analyzing a repo nobody has analyzed, I wait once, under 20 seconds, and everyone after me gets it instantly.

#### Acceptance criteria
1. WHEN no row matches the `cache_key` THEN the system SHALL execute the analysis pipeline: fetch → normalize → score → single LLM call → assemble → cache write.
2. WHEN a cache-miss analysis completes THEN total wall-clock time SHALL be under 20 seconds at the 95th percentile for repositories inside the size cap.
3. WHEN a cache-miss analysis completes successfully THEN the system SHALL write exactly one `analyses` row containing the raw GitHub payloads, the normalized facts, the scorecard, and the LLM JSON, before returning the response.
4. WHEN the LLM call fails or returns unparseable output after the configured retry THEN the system SHALL still persist the GitHub facts and the scorecard, SHALL mark the narrative fields `unavailable`, and SHALL mark the row `llm_status = "failed"` so a later request can retry only the LLM stage.
5. WHEN the analysis exceeds the configured hard timeout (default 25 s) THEN the system SHALL abort, return `ANALYSIS_TIMEOUT`, and SHALL NOT write a partial success row.
6. WHEN two concurrent requests target the same `cache_key` THEN the system SHALL perform the pipeline at most once and serve both callers from the single result.

---

### R-4 — URL parsing and canonicalization

**Priority: S**

> As a user I paste whatever form of the URL I happen to have, and NECROPSY resolves it to one repository identity.

#### Acceptance criteria
1. WHEN input matches any of `https://github.com/{owner}/{repo}`, with or without trailing slash, `.git` suffix, `www.` prefix, `http://` scheme, a `/tree/{ref}` or `/blob/{ref}/{path}` suffix, query string, or fragment, THEN the system SHALL canonicalize to `github.com/{owner}/{repo}` with original case preserved for display and lowercase used for the cache key.
2. WHEN input is the bare form `{owner}/{repo}` THEN the system SHALL accept it.
3. WHEN input references a host other than `github.com` THEN the system SHALL return `UNSUPPORTED_HOST` with the parsed host echoed back.
4. WHEN input is not parseable as a repository reference THEN the system SHALL return `INVALID_URL` and SHALL NOT make any network call.
5. WHEN two inputs canonicalize to the same identity THEN they SHALL produce the same `cache_key` for the same commit SHA.

---

### R-5 — Preloaded demo graveyard

**Priority: D**

> As the presenter, I demo NECROPSY with the network unplugged and no API keys set, and the gallery is full.

#### Acceptance criteria
1. WHEN the repository is cloned and the application is started with no `GITHUB_TOKEN`, no LLM key, and no network access THEN the gallery SHALL render between 6 and 8 complete exhibits.
2. WHEN a demo exhibit is opened THEN all seven post-mortem sections SHALL render from cache with zero network calls.
3. The demo graveyard SHALL be defined by a committed manifest file listing, per entry: `slug`, canonical repo URL, `pinned_commit_sha`, `curator_note`, and `display_order`.
4. The demo graveyard SHALL be materialized as a committed SQLite seed artifact or committed JSON fixtures loadable by a seed command, such that no GitHub or LLM call is required at startup.
5. WHEN the seed loader runs against an existing database THEN it SHALL be idempotent: re-running it SHALL NOT duplicate exhibits and SHALL NOT alter existing non-demo analyses.
6. Demo entries SHALL be marked `is_demo = 1` and `curated = 1` and SHALL be distinguishable in the API response from user-submitted exhibits.
7. The curated set SHALL cover at least four distinct failure modes (for example: archived by owner, dependency-era death, single-maintainer disappearance, declared-WIP abandonment) and at least three languages, so the scorecard visibly spreads across bands rather than clustering.

---

### R-6 — Gallery view (graveyard wall)

**Priority: D**

> As a visitor I see a dark gallery wall of spotlit exhibits and can tell at a glance which repos are most dead.

#### Acceptance criteria
1. WHEN the gallery loads THEN it SHALL render one card per exhibit, each with a spotlight treatment, a plaque showing repository name and primary language, the score band label, and the epitaph.
2. WHEN the viewport width is at or below 480 px THEN the gallery SHALL render as a single readable column with no horizontal scrolling.
3. WHEN an exhibit card is activated by click, `Enter`, or `Space` THEN the system SHALL open the full post-mortem view for that exhibit.
4. The gallery SHALL be navigable by keyboard alone, and all text SHALL meet a contrast ratio of at least 4.5:1 against its background despite the dark theme.
5. WHEN the user has `prefers-reduced-motion: reduce` set THEN spotlight and reveal animations SHALL be disabled while the layout remains intact.
6. The gallery SHALL support sorting by difficulty score and by days-since-death, computed server-side from stored values.
7. WHEN no exhibits exist THEN the gallery SHALL render an explicit empty state, not a blank page.

---

### R-7 — Exhibit detail view

**Priority: D**

> As a visitor reading one exhibit, I get the plaque, the epitaph, the evidence, and the plan, in that visual order.

#### Acceptance criteria
1. WHEN an exhibit detail view renders THEN it SHALL display, in order: plaque (name, owner, language, LOC estimate, date of death), epitaph, cause of death with its evidence list, what's missing, scorecard, resurrection plan.
2. WHEN the cause of death is displayed THEN each claim SHALL be accompanied by at least one evidence item naming its source field (for example `last_commit.date`, `tree.paths`, `manifest.dependencies`, `issues.open_count`).
3. WHEN a repository has a last release THEN the plaque SHALL show the release tag and date; WHEN it has none THEN the plaque SHALL show `no releases`.
4. The detail view SHALL expose a link to the original GitHub repository and SHALL display `as_of` so the reader knows when the data was captured.
5. The detail view SHALL have a stable shareable URL of the form `/exhibit/{slug}`.

---

### R-8 — Transparent revival difficulty scorecard

**Priority: D**

> As a skeptical reader I can audit the score: I see every factor, its raw input, its weight, and its contribution.

#### Acceptance criteria
1. WHEN a scorecard is returned by the API THEN it SHALL include, for every factor: `key`, `label`, `raw_inputs`, `normalized` in `[0,1]`, `weight`, `contribution`, `confidence`, and a one-line `rationale`.
2. WHEN a scorecard is rendered in the UI THEN every factor row SHALL be visible without interaction or SHALL be reachable by a single disclosure control, and the sum of contributions SHALL be displayed next to the total.
3. The total score SHALL be an integer in `[0,100]` and SHALL be mapped to exactly one named band.
4. WHEN a factor's underlying data is unavailable THEN the factor SHALL use its documented neutral default, SHALL report `confidence = "low"`, and SHALL state the substitution in its `rationale`.
5. The score SHALL be a pure function of the declared scorecard input structure. It SHALL NOT read wall-clock time, random numbers, environment variables, or the database.
6. The score SHALL satisfy the properties in R-20.
7. WHEN the scoring weights change THEN `analysis_schema_version` SHALL be incremented in the same change.

---

### R-9 — Epitaph specificity

**Priority: D**

> As a reader I get one line that is funny because it is true about *this* repo, not because it is spooky.

#### Acceptance criteria
1. WHEN an epitaph is generated THEN it SHALL be a single line of at most 140 characters.
2. WHEN an epitaph is generated THEN it SHALL reference at least one concrete, verifiable detail drawn from the normalized facts, and the LLM SHALL return that detail in an `epitaph_evidence` field naming the source fact key and its value.
3. WHEN `epitaph_evidence` is absent, or names a fact key not present in the normalized facts, or quotes a value that does not match the normalized facts, THEN the system SHALL reject the epitaph and SHALL fall back to a deterministic template epitaph built from the top-weighted scorecard factor.
4. The epitaph SHALL NOT be drawn from a static list of generic lines. A fallback epitaph SHALL be visibly labelled as a template epitaph in the API response via `epitaph_source = "template"`.
5. WHEN the same `cache_key` is served twice THEN the epitaph SHALL be identical, because it is cached rather than regenerated.

---

### R-10 — Three-step resurrection plan

**Priority: D**

> As someone considering reviving the repo I get three ordered, concrete steps I could start today.

#### Acceptance criteria
1. WHEN a resurrection plan is generated THEN it SHALL contain exactly three steps, ordered, each with `order`, `title` at most 80 characters, `detail` at most 400 characters, `addresses_factor` referencing a scorecard factor key, and `estimated_effort` from the enumeration `{"hours", "days", "weeks"}`.
2. Each step's `addresses_factor` SHALL reference a factor whose `contribution` is greater than zero for this repository.
3. WHEN the plan is generated THEN step 1 SHALL address the factor with the largest contribution, unless a prerequisite ordering is stated in that step's `detail`.
4. A step SHALL NOT instruct the reader to run code inside NECROPSY, and SHALL NOT reference files absent from the repository tree without stating that they must be created.
5. WHEN the LLM returns fewer or more than three steps THEN the system SHALL reject the plan and apply the deterministic fallback plan derived from the top three scorecard contributions.

---

### R-11 — MCP server tool surface

**Priority: S**

> As a Kiro user I point Kiro at a dead repo, Kiro calls NECROPSY's tools, and Kiro reads the post-mortem.

#### Acceptance criteria
1. The system SHALL ship an MCP server over stdio transport exposing exactly three tools: `analyze_repo`, `get_exhibit`, `list_graveyard`.
2. Each tool SHALL declare a JSON Schema for its input and SHALL return a single JSON object conforming to the response contract in `design.md`.
3. WHEN `analyze_repo` is called with a URL whose analysis is cached THEN it SHALL return the cached analysis and SHALL report `cache_status = "hit"`.
4. WHEN `analyze_repo` is called with `force_refresh = true` THEN it SHALL bypass the cache read, SHALL respect the 10-call GitHub budget, and SHALL write a new row only if the commit SHA or schema version differs.
5. WHEN `get_exhibit` is called for a repository with no cached analysis THEN it SHALL return the error code `NOT_IN_GRAVEYARD` and SHALL NOT trigger an analysis.
6. WHEN `list_graveyard` is called THEN it SHALL return exhibit summaries with `slug`, `repo_url`, `score`, `band`, `epitaph`, `days_since_last_commit`, `is_demo`, honoring `limit`, `offset` and `sort`, and SHALL make zero network calls.
7. The MCP server and the web application SHALL share the same analysis pipeline module, the same scorecard module, and the same SQLite database file. Duplicated scoring logic is a defect.
8. Every tool SHALL return errors using the shared error envelope defined in `design.md`, never as an unhandled exception or a free-text string.
9. The repository SHALL include a committed example MCP client configuration block for `.kiro/settings/mcp.json`.
10. The MCP server SHALL start and serve `list_graveyard` and `get_exhibit` with no `GITHUB_TOKEN` and no LLM key configured.

---

### R-12 — Kiro revival-spec workflow

**Priority: S**

> As a Kiro user I use the post-mortem as input to a Kiro spec session that writes the revival plan for the dead repo.

#### Acceptance criteria
1. The repository SHALL document a reproducible sequence: configure the MCP server → ask Kiro to analyze a specific dead repo → Kiro calls `analyze_repo` → Kiro writes a revival spec into `.kiro/specs/{target-repo}/`.
2. `analyze_repo` output SHALL be structured so that `cause_of_death.evidence`, `whats_missing`, `scorecard.factors` and `resurrection_plan` can be consumed directly as spec requirements without re-parsing prose.
3. The documented workflow SHALL be verified at least once against a real dead repository and the resulting generated spec SHALL be committed as evidence under `.kiro/specs/`.
4. The workflow SHALL be captured in the demo recording.

---

### R-13 — Error states

**Priority: S**

> As a user who pasted something unusual I get a specific, honest explanation and a next action.

#### Acceptance criteria
1. WHEN GitHub returns 404 for the repository THEN the system SHALL return `REPO_NOT_FOUND` with a message distinguishing "does not exist" from "may be private", and SHALL NOT call the LLM.
2. WHEN GitHub returns 403 or 451 for the repository THEN the system SHALL return `REPO_INACCESSIBLE` with the GitHub reason echoed.
3. WHEN the GitHub rate limit is exhausted or insufficient for a full analysis THEN the system SHALL return `RATE_LIMITED` including `remaining`, `limit`, `reset_at` and `retry_after_seconds`, SHALL state that cached exhibits remain available, and SHALL link to the demo graveyard.
4. WHEN the repository's reported size exceeds `MAX_REPO_SIZE_KB` (default 51200) or its tree exceeds `MAX_REPO_FILES` (default 5000) or the tree API reports truncation THEN the system SHALL return `REPO_TOO_LARGE` with the measured value and the configured cap, SHALL NOT call the LLM, and SHALL NOT write an analysis row.
5. WHEN the repository has zero commits THEN the system SHALL return `REPO_EMPTY`.
6. WHEN the repository is a fork THEN the system SHALL proceed and SHALL flag `is_fork = true` in the normalized facts, because fork status affects maintainer-absence interpretation.
7. WHEN the LLM provider is unreachable, unauthorized, or rate-limited THEN the system SHALL degrade per R-3.4 and SHALL surface `llm_status` in the response.
8. WHEN no LLM key is configured at all THEN the system SHALL still produce facts, scorecard, template epitaph and fallback plan, and SHALL label the narrative as deterministic fallback.
9. Every error response SHALL carry `code`, `message`, `retryable` (boolean), and optional `details`, and the UI SHALL render the message inside the gallery frame rather than as a browser alert or a stack trace.
10. Error codes SHALL be a closed enumeration documented in `design.md`; an unmapped exception SHALL surface as `INTERNAL_ERROR` with a correlation id and SHALL be logged with a stack trace server-side only.

---

### R-14 — GitHub call budget and rate-limit guard

**Priority: S**

> As the operator I cannot be rate-limited into a failed demo, because the client refuses to overspend.

#### Acceptance criteria
1. The GitHub client SHALL enforce a hard ceiling of 10 REST calls per analysis. WHEN an eleventh call is attempted within one analysis THEN the client SHALL raise `CallBudgetExceeded` and the analysis SHALL fail loudly rather than silently continuing.
2. The client SHALL record `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` and `X-RateLimit-Used` from every response into a persistent ledger.
3. WHEN the last observed `remaining` is below `RATE_LIMIT_FLOOR` (default 12) THEN the system SHALL refuse to start a new cache-miss analysis and SHALL return `RATE_LIMITED`, while continuing to serve all cached exhibits.
4. WHEN `GITHUB_TOKEN` is present in the environment THEN the client SHALL send it as a bearer token; WHEN it is absent THEN the client SHALL operate unauthenticated and SHALL NOT fail at startup.
5. WHEN GitHub returns 403 with a `Retry-After` header or a secondary-rate-limit body THEN the client SHALL honor `Retry-After` for at most one retry per call and SHALL count the retry against the 10-call budget.
6. The client SHALL set a per-call connect timeout of 5 s and read timeout of 10 s, and SHALL retry at most once on connection error or 5xx.
7. The client SHALL send `If-None-Match` when a stored ETag exists for the endpoint, and SHALL treat a 304 response as a non-chargeable call against the GitHub quota while still counting it against the local budget.
8. The system SHALL expose current budget state at `GET /api/health` including `rate_limit_remaining`, `reset_at`, `token_present` (boolean, never the token), `cache_entries`, and `demo_exhibits`.

---

### R-15 — Single structured LLM call

**Priority: S**

> As the operator I pay once per analysis and I can replay the exact prompt.

#### Acceptance criteria
1. WHEN a cache-miss analysis runs THEN the system SHALL make exactly one LLM call that returns all narrative fields — `cause_of_death`, `whats_missing_narrative`, `epitaph`, `epitaph_evidence`, `resurrection_plan`, `what_was_built_summary` — as a single JSON object.
2. The system SHALL validate the LLM response against a committed JSON Schema before persisting it. WHEN validation fails THEN the system SHALL retry at most once with a repair instruction, and SHALL then fall back per R-9.3 and R-10.5.
3. The LLM SHALL receive only normalized facts, never raw file contents beyond a truncated README excerpt (`README_EXCERPT_CHARS`, default 4000) and truncated manifest excerpts (`MANIFEST_EXCERPT_CHARS`, default 2000 each).
4. The system SHALL persist `llm_model`, `prompt_template_version`, `prompt_hash`, `tokens_in`, `tokens_out` and `latency_ms` for every call.
5. The system SHALL NOT call the LLM before a cache lookup has been performed and missed. A code path that reaches the LLM client without a preceding cache check is a defect.
6. The LLM SHALL NOT be used to compute the numeric score, the score band, or any scorecard factor value.
7. The provider SHALL be selected by environment configuration behind a single `LlmClient` interface with a `FakeLlmClient` used in tests, so the test suite requires no network and no key.

---

### R-16 — No code execution, no cloning

**Priority: S**

> As a security-conscious reviewer I can confirm NECROPSY never executes or downloads repository code as code.

#### Acceptance criteria
1. The system SHALL obtain all repository information from the GitHub REST API. It SHALL NOT invoke `git`, SHALL NOT download archives or tarballs, and SHALL NOT write repository content to disk outside the SQLite cache.
2. The system SHALL NOT invoke a subprocess, shell, container, `eval`, `exec`, or dynamic import on any content derived from an analyzed repository.
3. Repository-derived strings SHALL be treated as untrusted data. They SHALL be HTML-escaped on render and SHALL NOT be interpolated into shell commands, SQL (parameterized queries only), or prompt instruction regions without delimiting.
4. Instruction-like text inside a README or issue body SHALL be treated as data for the LLM call, enclosed in a delimited untrusted-content block with an explicit instruction that its contents are not directives.
5. The test suite SHALL include a guard test asserting that the analysis package imports no subprocess or process-spawning module.

---

### R-17 — Reproducible demo graveyard regeneration

**Priority: S**

> As the maintainer I can regenerate the curated exhibits from the manifest and get the same exhibits back.

#### Acceptance criteria
1. The system SHALL provide a command that reads the graveyard manifest and produces the seed artifact for every entry.
2. WHEN an entry specifies `pinned_commit_sha` THEN regeneration SHALL analyze that SHA's cache key so the exhibit text does not change as the upstream repo changes.
3. WHEN regeneration runs with `--offline` THEN it SHALL validate existing seed fixtures against the manifest and SHALL fail with a diff if an entry is missing, rather than calling GitHub.
4. Regeneration SHALL report, per entry, whether it was served from cache, refetched, or failed, and SHALL exit non-zero if any manifest entry cannot be materialized.
5. Regeneration SHALL be exposed as a Kiro hook (see `.kiro/hooks/`).

---

### R-18 — Configuration and operability

**Priority: S**

#### Acceptance criteria
1. All tunables SHALL be environment variables with documented defaults: `GITHUB_TOKEN`, `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `DATABASE_PATH`, `MAX_REPO_SIZE_KB`, `MAX_REPO_FILES`, `GITHUB_CALL_BUDGET`, `RATE_LIMIT_FLOOR`, `ANALYSIS_TIMEOUT_SECONDS`, `README_EXCERPT_CHARS`, `MANIFEST_EXCERPT_CHARS`, `LOG_LEVEL`.
2. WHEN a required-for-feature variable is absent THEN the system SHALL start, SHALL log one warning naming the degraded capability, and SHALL continue.
3. The system SHALL NOT log the value of `GITHUB_TOKEN` or `LLM_API_KEY`, nor include them in any API response.
4. The system SHALL run on Windows, macOS and Linux with Python 3.11+ and SHALL require no Node.js toolchain.
5. Startup SHALL apply database migrations idempotently.

---

### R-19 — Test and quality gates

**Priority: S**

#### Acceptance criteria
1. The full test suite SHALL pass with no network access and no API keys.
2. GitHub responses SHALL be exercised through committed fixtures covering: healthy repo, archived repo, oversized repo, 404, 403 rate-limited, empty repo, truncated tree, repo with no README, repo with no manifest.
3. The scorecard SHALL have property-based tests per R-20.
4. The analysis pipeline SHALL have a lint and type-check gate wired to a save hook (see `.kiro/hooks/`).
5. Test execution SHALL be single-run, never watch mode, in every hook and documented command.

---

### R-20 — Property-based tests for the scorecard

**Priority: S**

> As a reviewer I trust the score because its invariants are machine-checked across generated inputs, not three hand-written cases.

#### Acceptance criteria
1. **Bounds.** FOR ALL valid scorecard inputs, the total score SHALL be an integer in `[0,100]` and every factor's `normalized` value SHALL be in `[0,1]`.
2. **Determinism.** FOR ALL valid scorecard inputs, scoring the same input twice in the same process and in a fresh process SHALL produce identical totals, factor breakdowns and bands. The scorecard SHALL contain no call to a clock or RNG; `as_of` SHALL be an explicit input field.
3. **Deficit monotonicity.** FOR ALL valid inputs and for each deficit input (`test_suite_missing`, `ci_missing`, `license_missing`, `contributing_missing`, `docs_missing`, `days_since_last_commit`, `deprecated_dependency_count`, `unanswered_issue_ratio`, `archived`), increasing that input while holding all others fixed SHALL NOT decrease the total score.
4. **Asset monotonicity.** FOR ALL valid inputs and for each asset input (`test_file_count`, `has_ci`, `has_license`, `has_contributing`, `doc_page_count`, `fresh_dependency_count`, `maintainer_response_count`), increasing that input while holding all others fixed SHALL NOT increase the total score.
5. **Cache invariance.** FOR ALL analyses, the score recomputed from the normalized facts rehydrated out of SQLite SHALL equal the score computed from the in-memory normalized facts for the same `last_commit_sha` and `analysis_schema_version`, including the full factor breakdown.
6. **Band consistency.** FOR ALL valid inputs, the band SHALL be a pure function of the total score, and band boundaries SHALL partition `[0,100]` with no gap and no overlap.
7. **Contribution identity.** FOR ALL valid inputs, the sum of factor contributions SHALL equal the unrounded total within a tolerance of 1e-9, and the displayed integer total SHALL equal `round(unrounded_total)`.
8. **Idempotent normalization.** FOR ALL raw GitHub fixture payloads, normalizing twice SHALL produce an equal normalized-facts structure.
9. Hypothesis strategies SHALL be defined once in a shared module and reused by all scorecard property tests. A failing example SHALL be recorded in the Hypothesis example database and committed if it exposed a real defect.
10. The write-up SHALL name the specific defects found by these properties and not by unit tests.

---

### R-21 — Submission artifacts

**Priority: S**

#### Acceptance criteria
1. `.kiro/` SHALL be committed and SHALL NOT appear in `.gitignore`, at any level.
2. `.kiro/` SHALL contain `specs/necropsy/{requirements,design,tasks}.md`, `steering/{product,engineering}.md`, at least two hook definitions under `hooks/`, and `README.md`.
3. `.kiro/README.md` SHALL map each artifact to how it was used.
4. The repository SHALL contain an OSI license file and a top-level `README.md` with setup, run, test, seed and MCP configuration instructions.

---

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Cache hit p95 < 200 ms server-side; cache miss p95 < 20 s. |
| NFR-2 | At most 10 GitHub REST calls and exactly 1 LLM call per cache-miss analysis. |
| NFR-3 | Zero network calls on the demo path. |
| NFR-4 | Single process for web, single process for MCP, one SQLite file, no external service dependency besides GitHub and the LLM provider. |
| NFR-5 | Cold start to first byte under 2 s. |
| NFR-6 | No secret in logs, responses, fixtures, or committed files. |
| NFR-7 | Deterministic scoring: identical inputs always yield identical scores across processes and machines. |
| NFR-8 | Total repository install footprint installable from a single `requirements.txt` with no compilation step. |

## 7. Assumptions

1. GitHub REST API v3 remains available unauthenticated at 60 requests/hour per IP.
2. Judges will run the demo graveyard, not arbitrary repositories, as the primary path.
3. LOC is an estimate derived from the languages byte counts; it is labelled as an estimate everywhere it appears.
4. Curated demo repositories are public and will remain public through judging; pinned SHAs protect text stability but not availability, which is why fixtures are committed.

## 8. Traceability summary

| Requirement | Primary design section | Primary tasks |
|---|---|---|
| R-1, R-6, R-7 | Gallery UI component breakdown | T-2.5 … T-2.9 |
| R-2, R-3 | Analysis pipeline, cache layer | T-1.3, T-1.4, T-2.1 |
| R-4 | URL canonicalization | T-1.5 |
| R-5, R-17 | Demo graveyard loader | T-1.8, T-1.9 |
| R-8, R-20 | Scorecard formula | T-2.3, T-2.4 |
| R-9, R-10, R-15 | LLM contract | T-2.1, T-2.2, T-3.1 |
| R-11, R-12 | MCP tool contracts | T-1.10, T-1.11, T-3.3 |
| R-13, R-14 | Error taxonomy, rate-limit guard | T-1.6, T-1.7, T-2.10 |
| R-16, R-18, R-19 | Safety constraints, config | T-1.1, T-1.2, T-1.12 |
| R-21 | — | T-3.6 … T-3.9 |
