# NECROPSY — Product Principles

Always included. These principles decide product questions. When a principle conflicts with a feature idea, the principle wins. When two principles conflict, the lower-numbered one wins.

---

## P1. The museum metaphor is the product; the analysis is the evidence.

A visitor should experience NECROPSY as a gallery of dead software, not as a dashboard with a gravestone icon. The metaphor is load-bearing: exhibit cards, spotlights, plaques, epitaphs, a graveyard wall, a curator's note.

But the metaphor earns its keep only because the plaque is accurate. Every theatrical element must be anchored to a real fact from the repository. A spotlight with nothing under it is decoration; a spotlight on a verified cause of death is the product.

**Consequences.**
- Visual language is museum, not Halloween: brass, dim rooms, cast light, serif plaque type. No cartoon ghosts, no dripping fonts, no jump scares.
- Forensic content (evidence rows, scorecard numbers) uses a monospace register, visually distinct from the plaque. The two registers together are the point: theatre plus autopsy.
- Never ship a visual flourish that obscures a number a reader might want to check.

## P2. Specificity beats spookiness — every epitaph must reference a real, verifiable detail from the repo.

"Here lies a forgotten dream" is worthless. "Pinned to Rails 4.2, which reached EOL before its last commit" is the product.

**Consequences.**
- An epitaph must cite a fact key and value from the normalized facts, and that citation is machine-checked before the epitaph is shown.
- An ungrounded epitaph is rejected, not softened. The deterministic template epitaph that replaces it is duller and still true, and is labelled as a template.
- The same rule applies to cause of death: each claim carries its evidence. No atmospheric prose that cannot be traced to a field.
- If NECROPSY has no specific detail to offer, it says so. An honest "insufficient data for this factor" beats an invented flourish.

## P3. No login wall, ever.

A judge, a reviewer, or a stranger must go from landing page to a complete post-mortem with zero accounts, zero API keys, zero email, zero cookie banners, zero "start free trial".

**Consequences.**
- No user accounts, no sessions, no user table, no OAuth, not even optional.
- No feature may be gated behind identity. If a feature requires identity, it does not ship.
- A `GITHUB_TOKEN` may raise the operator's rate limit. It is never requested from the visitor and never required to run the app.

## P4. The demo graveyard must work offline.

The primary path is the curated graveyard: 6–8 exhibits shipped pre-analyzed and committed. It renders with the network unplugged, no GitHub token, and no LLM key.

**Consequences.**
- "Paste your own repo" is the bonus path. It is never the path the demo depends on.
- Curated exhibits are pinned to specific commit SHAs and their raw payloads are committed as fixtures, so an upstream repo being deleted cannot break the demo.
- Any feature that only works with live API access is, by definition, not demo-critical.
- Every release is verified in airplane mode before it is considered done.

## P5. The score is an argument, not a verdict.

Revival difficulty is a transparent, auditable claim that a reader is invited to disagree with. A reader must be able to see every input, every weight, and every contribution, and reconstruct the total by hand.

**Consequences.**
- No hidden factors, no undisclosed multipliers, no model-generated numbers.
- The LLM never produces or adjusts the score. It explains a score it was given.
- When data is missing, the substituted default and its effect are stated in the factor's own row, not buried in a footnote.
- Changing the weights is a versioned change, visible in the output.

## P6. Dead is a diagnosis, not an insult.

Many dead repositories are finished, superseded, or were someone's weekend. The tone is a coroner's: factual, dry, occasionally wry, never contemptuous. The target of any humour is the situation, never the maintainer.

**Consequences.**
- No ranking of "worst" repositories, no naming of individuals as responsible, no mockery of effort or skill.
- "Finished and stable" and "abandoned mid-rewrite" must be distinguishable in the output; a low-difficulty score on a complete project is a legitimate result, not a failure of the analyser.
- Single-maintainer repositories are described structurally ("no maintainer response in the sampled issues"), never personally.

## P7. Honest uncertainty over confident fabrication.

NECROPSY sees GitHub metadata, not the code's soul. It must state what it inferred, from what, and how confident it is.

**Consequences.**
- LOC is always labelled an estimate, with its derivation available.
- Sampled signals say they are sampled, with the sample size shown.
- The dependency deprecation table is offline and versioned; its version and compile date are shown wherever its conclusions appear.
- `as_of` is displayed on every exhibit. A reader always knows how old the data is.
- An unavailable section renders as explicitly unavailable with a reason, never as a plausible guess.

## P8. One screen, one decision.

The gallery answers "which of these is most dead?" The exhibit answers "what happened, and could I fix it?" Nothing else is added to either screen.

**Consequences.**
- No settings page, no filter sidebar, no comparison matrix, no charts beyond the language bar and the contribution bars.
- Sorting is three options, server-side: curated, deadest, hardest.
- Cuttable before anything else: features that do not serve one of those two questions.

## P9. The resurrection plan must be startable today.

Three steps, ordered, each addressing a factor that actually contributes to this repository's score, each small enough to begin in an afternoon.

**Consequences.**
- No step may say "consider modernizing the architecture". A step names a file to create, a dependency to replace, or a test to write.
- A step that references a file must say whether it exists or must be created.
- No step instructs the reader to run anything inside NECROPSY.

## P10. NECROPSY reads; it never touches.

Analysis is read-only and remote. NECROPSY does not clone, does not execute, does not fork, does not open issues, does not comment, does not write anything anywhere except its own cache.

**Consequences.**
- Repository content is untrusted data at all times, including inside the prompt.
- A README that tries to instruct the analyser is quoted, not obeyed.
- "Automatically open a revival PR" is permanently out of scope, however good a demo it would make.
