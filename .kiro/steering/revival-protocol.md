---
inclusion: manual
---

# Revival protocol — how Kiro should use the NECROPSY MCP tools

When asked to revive a dead repository, follow this protocol exactly. It exists
so a revival spec is always grounded in NECROPSY's analysis and never in
invention.

1. **Analyze first.** Call `necropsy.analyze_repo` before writing anything. If the
   repository is already a cached exhibit, the call returns instantly with
   `cache_status: "hit"` and spends no GitHub or LLM budget.
2. **Read the revival plan.** The analysis contains a three-step
   `resurrection_plan`, each step naming the scorecard factor it addresses.
3. **Write `revival-spec.md`** mapping each plan step to a concrete task that
   names the exact file from the plan's `detail`. Prefer calling
   `necropsy.write_revival_spec`, which returns that document already assembled
   from the analysis.
4. **Order tasks by dependency.** Pin or replace dependencies before deleting
   files. A task that removes something must come after whatever proves it is
   safe to remove.
5. **Never delete a file without confirming it is unreferenced** in the analysis.
   If the analysis does not establish that, keep the file and say so.

Hard rules:

- **Never invent a step** that is not in the analysis's `resurrection_plan`.
- **Never call the LLM if a cached exhibit exists** — `get_exhibit` and
  `write_revival_spec` read the cache only and must be preferred for anything
  already analyzed.
- **Carry the evidence.** Every claim in the spec's cause of death cites a
  `fact_key`; keep those citations so a reviewer can check them.
- NECROPSY reads GitHub metadata only. It never cloned or executed the
  repository, so verify each task against the live repository before acting.
