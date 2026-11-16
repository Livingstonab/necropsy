"""NECROPSY — automated post-mortems for dead GitHub repositories.

Reads GitHub's REST API only. Never clones, never executes analyzed code (E4).
"""

__version__ = "1.1.0"

#: Bumped to 2 for `final_letter`; to 3 when the epitaph templates gained
#: per-repo variety so two repos sharing a top factor no longer read identically;
#: to 4 because dotfile CI configs stopped being invisible, which lowers the score
#: of every repository that has a pipeline; to 5 when active repositories gained
#: their own "still breathing" outcome instead of an autopsy; to 6 when that
#: outcome moved from a month of quiet to ten days.
#: A bump invalidates cached analyses by miss, never by deletion (E3), so old
#: rows survive and reseeding regenerates each exhibit with the new narrative.
ANALYSIS_SCHEMA_VERSION = 6

#: A repository committed to within this many days is alive, not dead (P6).
#: It gets a check-up instead of an autopsy: no epitaph, cause of death, letter
#: or revival plan, and it never hangs on the graveyard wall. An archived
#: repository is never living, however recent its last commit, because archiving
#: is the owner's own declaration that it is finished. Changing this changes what
#: is shown for every cached exhibit, so it travels with ANALYSIS_SCHEMA_VERSION.
LIVING_THRESHOLD_DAYS = 10

#: Bumped alongside: the prompt now asks for the letter and carries the evidence
#: set, so its template version changed too.
PROMPT_TEMPLATE_VERSION = 2
SCORECARD_VERSION = 1
