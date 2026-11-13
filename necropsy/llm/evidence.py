"""The evidence set: the tokens a narrative is allowed to be built from (R-9, P2).

This is the spine of the uniqueness guarantee. Before any narrative is written —
by a model or by a template — we assemble the concrete, verifiable tokens this
repository actually yields: its last commit message, its dependency names, its
top-level files, its languages, its dates, its declared-WIP marker. A letter or
an epitaph is then only trusted if it quotes from this set.

That is what makes "specificity beats spookiness" (product.md P2) a mechanical
check rather than a hope: a sentence that references nothing in this set
references nothing about *this* repo, so it is rejected. Generic copy — "here
lies a forgotten dream" — contains no evidence token and cannot pass.

The set is derived purely from `NormalizedFacts`, so it adds no data and needs no
network. It adapts to thin repos (R edge cases): a repo with no README leans on
file names and deps, a repo with no commit sample leans on files, a repo with no
TODOs simply never offers a TODO token, so nothing downstream can invent one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from necropsy.models.facts import NormalizedFacts

#: Cap on how many tokens we carry. A prompt does not need the whole repo, and a
#: validator only needs enough to prove groundedness.
MAX_TOKENS = 50

#: The shortest token worth matching. One- and two-character tokens ("a", "id")
#: match everything and prove nothing.
MIN_TOKEN_LEN = 3

#: Generic words a model reaches for when it has nothing specific to say. A
#: letter containing any of these is rejected before a reader sees it (R-9).
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "journey",
    "rest in peace",
    "gone but not forgotten",
    "in loving memory",
    "you will be missed",
    "dearly departed",
    "forever in our hearts",
    "sadly",
    "tragically",
    "unfortunately",
)

_TODO_RE = re.compile(r"\b(?:todo|fixme|hack|xxx)\b[:\s-]*(.{0,60})", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}")


@dataclass(frozen=True)
class EvidenceSet:
    """The concrete tokens this repository yields, grouped by where they came from.

    `tokens` is the flat, lowercased match set the validator uses. The grouped
    fields are for the prompt and the template, so each can cite the right kind
    of detail (a commit message reads differently from a filename).
    """

    tokens: tuple[str, ...] = ()
    commit_messages: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    top_level_files: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    todos: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    wip_marker: str | None = None

    def contains(self, text: str) -> int:
        """How many distinct evidence tokens appear in `text` (case-insensitive).

        Substring match, because a letter says "847 days" not "847"; the token
        "847" is still present in the sentence. Returns a count so callers can
        require "at least N".
        """
        low = text.lower()
        return sum(1 for token in set(self.tokens) if token in low)

    @property
    def is_thin(self) -> bool:
        """A repo that yielded almost nothing. The templates lean on files then."""
        return len(self.tokens) < 3

    def sample(self, limit: int = MAX_TOKENS) -> list[str]:
        return list(self.tokens[:limit])


def build_evidence(facts: NormalizedFacts) -> EvidenceSet:
    """Assemble the evidence set from the normalized facts. Pure; adds no data."""
    commit_messages = _clean_messages(facts)
    dependencies = tuple(d.name for d in facts.dependencies if d.name)
    top_level = tuple(facts.top_level_entries[:12])
    languages = tuple(facts.languages.keys())
    todos = _todos(facts.readme_excerpt)
    dates = _dates(facts)

    tokens: list[str] = []
    _add(tokens, (facts.name, facts.owner))
    _add(tokens, languages)
    _add(tokens, dependencies)
    _add(tokens, top_level)
    _add(tokens, dates)
    # Commit messages and TODOs are phrases; pull their meaningful words in too.
    for message in commit_messages:
        _add(tokens, _words(message))
    for todo in todos:
        _add(tokens, _words(todo))
    if facts.wip_marker:
        _add(tokens, (facts.wip_marker,))
    if facts.last_release_tag:
        _add(tokens, (facts.last_release_tag,))

    return EvidenceSet(
        tokens=tuple(tokens[:MAX_TOKENS]),
        commit_messages=commit_messages,
        dependencies=dependencies,
        top_level_files=top_level,
        languages=languages,
        todos=todos,
        dates=dates,
        wip_marker=facts.wip_marker,
    )


def contains_forbidden_phrase(text: str) -> str | None:
    """Return the first forbidden phrase found, or None. The generic-tell check."""
    low = text.lower()
    return next((phrase for phrase in FORBIDDEN_PHRASES if phrase in low), None)


def count_sentences(text: str) -> int:
    """Rough sentence count for the 3–5 / 1–2 length gates.

    Terminal punctuation, not newlines: a letter is prose, and we care that it is
    composed of a few sentences, not how it was wrapped.
    """
    parts = [p for p in re.split(r"[.!?]+", text) if p.strip()]
    return len(parts)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _clean_messages(facts: NormalizedFacts) -> tuple[str, ...]:
    """The commit message(s) we have. Only the head commit is stored today, but
    this is the seam where a 100-commit sample would feed in unchanged."""
    out: list[str] = []
    if facts.last_commit_message:
        out.append(facts.last_commit_message.strip())
    return tuple(out)


def _todos(readme: str | None) -> tuple[str, ...]:
    """TODO/FIXME strings from the README excerpt. Empty when the repo has none,
    so nothing downstream can mention a TODO that does not exist (R edge case)."""
    if not readme:
        return ()
    found = [m.group(1).strip() for m in _TODO_RE.finditer(readme) if m.group(1).strip()]
    return tuple(dict.fromkeys(found))[:8]


def _dates(facts: NormalizedFacts) -> tuple[str, ...]:
    """Verifiable date tokens: the day count, the commit year, the release year."""
    out: list[str] = [str(facts.days_since_last_commit)]
    out.append(str(facts.last_commit_at.year))
    if facts.last_release_at:
        out.append(str(facts.last_release_at.year))
    if facts.created_at:
        out.append(str(facts.created_at.year))
    return tuple(dict.fromkeys(out))


def _words(phrase: str) -> list[str]:
    """Meaningful words from a phrase, lowercased, deduped, stopwords dropped."""
    return [
        w.lower()
        for w in _WORD_RE.findall(phrase)
        if len(w) >= MIN_TOKEN_LEN and w.lower() not in _STOPWORDS
    ]


def _add(acc: list[str], items: tuple[str, ...] | list[str]) -> None:
    for item in items:
        token = str(item).strip().lower()
        if len(token) >= MIN_TOKEN_LEN and token not in acc:
            acc.append(token)


#: Words too common to count as evidence of anything.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "this", "that", "from", "into", "add", "fix",
        "bump", "update", "merge", "wip", "version", "release", "initial", "commit",
        "not", "but", "you", "are", "was", "were", "has", "have", "had", "its",
    }
)
