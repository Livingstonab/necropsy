"""NECROPSY — automated post-mortems for dead GitHub repositories.

Reads GitHub's REST API only. Never clones, never executes analyzed code (E4).
"""

__version__ = "1.1.0"

#: Bumped to 2 for `final_letter`; to 3 when the epitaph templates gained
#: per-repo variety so two repos sharing a top factor no longer read identically;
#: to 4 because dotfile CI configs stopped being invisible, which lowers the score
#: of every repository that has a pipeline.
#: A bump invalidates cached analyses by miss, never by deletion (E3), so old
#: rows survive and reseeding regenerates each exhibit with the new narrative.
ANALYSIS_SCHEMA_VERSION = 4
#: Bumped alongside: the prompt now asks for the letter and carries the evidence
#: set, so its template version changed too.
PROMPT_TEMPLATE_VERSION = 2
SCORECARD_VERSION = 1
