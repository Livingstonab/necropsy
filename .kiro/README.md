# `.kiro/` — artifact map

NECROPSY was built spec-first. This directory is the working spec, not documentation written after the fact. It is committed deliberately and appears in no `.gitignore` at any level.

Submission: Kiroween 2026, category **Frankenstein**.

```
.kiro/
├── README.md                              this map
├── specs/
│   └── necropsy/
│       ├── requirements.md                R-1 … R-21, acceptance criteria
│       ├── design.md                      architecture, schema, pipeline, scorecard, MCP, UI
│       └── tasks.md                        ordered 3-week task list, D/S/P classified
├── steering/
│   ├── product.md                         P1 … P10, always included
│   └── engineering.md                     E1 … E15, always included
└── hooks/
    ├── analysis-pipeline-guard.kiro.hook
    ├── regenerate-demo-graveyard.kiro.hook
    └── no-clone-guard.kiro.hook
```

---

## `specs/necropsy/requirements.md`

21 requirements, each with a user story and numbered, mechanically testable acceptance criteria, classified **D** (demo-critical), **S** (submission-critical) or **P** (polish).

Covers the paste-a-URL flow, the cache-hit and cache-miss paths, URL canonicalization, the offline demo graveyard, the gallery and exhibit views, scorecard transparency, epitaph grounding, the three-step plan, the three MCP tools, the Kiro revival-spec loop, nine error states, the GitHub call budget, the single-LLM-call constraint, the no-execution guarantee, configuration, test gates, and the ten property-based-testing properties.

**How it was used.** Every acceptance criterion is a test target. Tasks reference requirement IDs; the traceability table at the end of the file maps each requirement to its design section and its tasks. Two criteria changed the architecture rather than just the test list: R-20.2 (determinism, no time-dependent factors) forced `as_of` to become a frozen input field rather than a `datetime.now()` call inside the scorer, and R-2.4 (byte-identical cache hits) forced `cache_status` and `served_at` to be assembled at response time instead of persisted.

## `specs/necropsy/design.md`

The technical design. Contains the two stack decisions with their reasoning (**Flask** over FastAPI, **server-rendered HTML/CSS/vanilla JS** over Next.js), a text architecture diagram, the package layout, the three request flows, the concurrency and single-flight model, the full SQLite schema, the fixed nine-call GitHub plan with per-endpoint failure handling, the normalization and detection rules, LOC estimation without cloning, the single LLM call with its schema and four-stage validation ladder, the eight-factor weighted scorecard with every normalization rule written out, the five score bands, the closed error taxonomy, the three MCP tool contracts with JSON Schemas, the gallery component breakdown with design tokens, twelve correctness properties, the testing strategy, the trust boundaries, and the open design risks.

The correctness-properties section is the hinge between this document and the test suite: each of the twelve states an invariant, names the design feature that makes it true, and cites the requirement that verifies it. A future change that breaks a property is therefore traceable to the decision it violated, rather than just to a red test.

**How it was used.** Written before any implementation, and it is where the non-obvious decisions are recorded so they do not get relitigated mid-build: storing `raw_github_json` so weights can change without re-fetching, using one recursive tree call instead of cloning, keeping the dependency deprecation table offline and versioned instead of calling package registries, and placing the monotonicity argument next to the weight table so the Hypothesis properties verify a claim the design already makes.

## `specs/necropsy/tasks.md`

34 ordered tasks across three weeks, each naming its requirements and its done-criteria.

Week 1 ends with the cache layer, the budget-guarded GitHub client, the offline graveyard loader and the MCP server all working. Week 2 adds the scorecard, its property tests, the single LLM call and the gallery. Week 3 is polish, evidence and submission. No task depends on cloning or executing repository code. The file ends with the flat execution order, the dependency notes, the D/S/P table, and the cut order under time pressure.

**How it was used.** The execution driver. Three ordering decisions in it are deliberate: the MCP server ships in week 1 against placeholder narratives (T-1.11) rather than waiting for the LLM, the scorecard and its properties land before the LLM exists (T-2.1, T-2.2) so the numbers are trustworthy before any prose explains them, and final curation (T-2.11) precedes epitaph tuning (T-3.1) so tuning is not wasted on a non-final exhibit set.

## `steering/product.md` — always included

Ten product principles, numbered so conflicts resolve deterministically (lower number wins). Includes the four required principles verbatim as P1, P2, P3 and P4: the museum metaphor is the product and the analysis is the evidence; specificity beats spookiness; no login wall, ever; the demo graveyard must work offline.

**How it was used.** These are loaded into every Kiro session, so generated code and copy arrive already shaped by them rather than needing correction. P2 is the one with the sharpest implementation consequence: it is why `epitaph_evidence` exists as a required field and why an ungrounded epitaph is rejected and replaced by a labelled template, instead of being allowed through because it reads well. P7 is why `confidence`, `data_gaps`, `as_of` and `open_issue_sample_size` are in the response schema at all.

## `steering/engineering.md` — always included

Fifteen engineering rules. E1–E5 are the five demo-failure-prevention rules stated verbatim: never call the LLM without a cache-hit check first; never exceed 10 GitHub API calls per analysis; every analysis is cached by `repo_url + last_commit_sha`; no code execution and no cloning, ever; the scorecard must expose its inputs in the API response and the UI. E6–E15 extend these into scorer purity, designed-in monotonicity, one shared pipeline, the single validated LLM call, per-section degradation, offline tests, secret handling, artifact determinism, performance gates and change discipline.

**How it was used.** Loaded into every session, so these constraints apply to code Kiro generates without being restated per prompt. Each rule is also an enforcement mechanism rather than an aspiration: E1 is tested with an `LlmClient` that raises on call, E2 is a counter in the client that raises on call 11, E4 is an import-guard test plus the `no-clone-guard` hook, E6 is a Hypothesis determinism property, and E5 is the contribution-identity property.

---

## Hooks

### 1. `analysis-pipeline-guard.kiro.hook`

| | |
|---|---|
| **Trigger** | `fileEdited` on `necropsy/{analysis,scoring,llm,github,store,models,graveyard,mcp}/*.py` and `tests/*.py` |
| **Action** | `runCommand`: `python -m pytest tests -q`, then `python -m ruff check necropsy tests`, then `python -m mypy necropsy/analysis necropsy/scoring` |
| **Timeout** | 300 s |

**Why it exists.** The scorecard's guarantees are easy to break with a one-line edit and impossible to notice by reading. A clamp in the wrong place, a `datetime.now()` added for convenience, a sign flipped in a ramp: each one silently breaks determinism or monotonicity while the app still runs and still produces a plausible number. Running the property tests on every save of a pipeline file catches that in seconds instead of at demo time. Lint and mypy-strict are in the same hook because the scorer's purity is partly a typing property, and because a single command keeps the feedback loop to one step. Single-run `pytest -q`, never watch mode, per engineering rule E11.

### 2. `regenerate-demo-graveyard.kiro.hook`

| | |
|---|---|
| **Trigger** | `userTriggered` (run manually from the Agent Hooks panel) |
| **Action** | `runCommand`: `init-db`, then `seed-graveyard --manifest necropsy/graveyard/manifest.json --report`, then `seed-graveyard --offline --verify` |
| **Timeout** | 600 s |

**Why it exists.** The demo graveyard is the demo path (P4), so it cannot be whatever happens to be sitting in a local database. This hook rebuilds all eight curated exhibits from the committed manifest at their pinned commit SHAs, reports per entry whether it was served from cache, refetched or failed, and exits non-zero if any entry cannot be materialized. The third command then re-runs the loader in `--offline --verify` mode, which proves the committed fixtures alone are sufficient — the same condition a judge's machine will be in with the network unplugged and no API keys. It is manual rather than automatic because the refetch path spends GitHub quota, and spending quota should be an explicit decision (E2).

### 3. `no-clone-guard.kiro.hook`

| | |
|---|---|
| **Trigger** | `preToolUse` on tool type `shell` |
| **Action** | `askAgent`: inspect the pending command for cloning, archive download, extraction of repository content, or execution of analyzed repository code; deny and propose the REST equivalent, or confirm no violation and proceed |
| **Timeout** | default |

**Why it exists.** "No code execution, no cloning, ever" (E4) is both a security boundary and the architectural constraint the whole analysis design rests on. It is also the rule most likely to be breached accidentally, because cloning a repo is the obvious reflex when debugging why a file-detection rule missed something. The import-guard test catches a committed violation; this hook catches the reflex before it becomes a habit, and when it denies a command it names the REST equivalent (`GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1` rather than a clone to list files). It fired on the first shell command issued after it was created, which is recorded in the build log as its own verification.

---

## MCP server registration

NECROPSY's own MCP server is registered in `.kiro/settings/mcp.json` so Kiro can call it during development:

```json
{
  "mcpServers": {
    "necropsy": {
      "command": "python",
      "args": ["-m", "necropsy.mcp.server"],
      "env": { "DATABASE_PATH": "./necropsy.db" },
      "disabled": false,
      "autoApprove": ["list_graveyard", "get_exhibit"]
    }
  }
}
```

`list_graveyard` and `get_exhibit` are auto-approved because they are read-only, cache-only and make zero network calls. `analyze_repo` is not auto-approved, because a cache miss spends GitHub quota and one LLM call.

The loop this enables, which is recorded for the submission: point Kiro at a dead repository → Kiro calls `analyze_repo` → Kiro reads the structured post-mortem → Kiro writes a revival spec into `.kiro/specs/{owner}-{repo}-revival/`, mapping each `whats_missing` item and each `resurrection_plan` step to a requirement and each non-zero scorecard factor to an acceptance criterion. The generated spec is committed alongside this one as evidence. The `analyze_repo` output shape in `design.md` §7.1 exists in that form specifically so Kiro transforms structured lists rather than re-parsing prose.

## Reading order for a reviewer

1. `steering/product.md` — what NECROPSY refuses to be
2. `steering/engineering.md` — the five rules that keep the demo alive
3. `specs/necropsy/requirements.md` — what it must do
4. `specs/necropsy/design.md` — how, and why those tradeoffs
5. `specs/necropsy/tasks.md` — in what order, and what gets cut first
