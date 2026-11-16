"""Structural signals from tree paths (design section 4.3). Pure, table-driven.

One recursive git-tree call replaces cloning, so everything here is derived from
path strings. The rules are tables rather than scattered regexes for two reasons:
a reviewer can read the whole detection surface in one place, and a test can walk
the table instead of guessing which heuristic fired.

Two anti-false-positive rules carry most of the weight:

  - **Vendored trees do not count.** `node_modules/`, `vendor/`, `third_party/` and
    friends contain other people's tests and other people's CI config. Counting
    them would make a repository look better maintained than it is.
  - **CI is anchored to its real location.** `vendor/foo/.travis.yml` is a vendored
    file; `.travis.yml` at the root is this repository's CI. Detection matches the
    exact paths each service actually reads, so a nested copy cannot invent a
    pipeline that never ran.

No clock, no I/O, no environment (E4, E6).
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

#: Subtrees that belong to someone else. Anything under these is ignored for
#: every signal except the raw file count.
VENDORED_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    "vendor/",
    "third_party/",
    "thirdparty/",
    "bower_components/",
    "site-packages/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    ".git/",
    "external/",
    "externals/",
    "deps/",
)

#: Directory names that mean "tests live here".
TEST_DIR_SEGMENTS = frozenset(
    {"test", "tests", "spec", "specs", "__tests__", "testing", "test_suite"}
)

#: Filenames that are a test regardless of directory.
TEST_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^test_[^/]+\.py$", re.I),
    re.compile(r"^[^/]+_test\.py$", re.I),
    re.compile(r"^[^/]+_test\.go$", re.I),
    re.compile(r"^[^/]+\.test\.[jt]sx?$", re.I),
    re.compile(r"^[^/]+\.spec\.[jt]sx?$", re.I),
    re.compile(r"^[^/]+Test\.java$"),
    re.compile(r"^[^/]+Tests\.cs$", re.I),
    re.compile(r"^[^/]+_spec\.rb$", re.I),
    re.compile(r"^[^/]+_test\.rb$", re.I),
    re.compile(r"^test[^/]*\.c(?:pp)?$", re.I),
)

#: CI config, anchored. `glob` entries match a directory prefix plus extension.
#: `legacy` marks a service that has been shut down or superseded, which is a
#: different finding from "no CI at all" (design section 5.2, ci_absence).
CI_RULES: tuple[tuple[str, str, bool], ...] = (
    (".github/workflows/", "github-actions", False),
    (".gitlab-ci.yml", "gitlab-ci", False),
    (".circleci/config.yml", "circleci", False),
    ("azure-pipelines.yml", "azure-pipelines", False),
    ("jenkinsfile", "jenkins", False),
    (".drone.yml", "drone", False),
    ("wercker.yml", "wercker", True),
    (".travis.yml", "travis", True),
    ("appveyor.yml", "appveyor", True),
    (".appveyor.yml", "appveyor", True),
)

LEGACY_CI_SYSTEMS = frozenset({"travis", "appveyor", "wercker"})

#: Dependency manifests, highest priority first. The first match at the root is
#: the primary manifest; a match from a second ecosystem may become the secondary.
MANIFEST_SPEC: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("setup.py", "python"),
    ("pipfile", "python"),
    ("package.json", "npm"),
    ("gemfile", "ruby"),
    ("go.mod", "go"),
    ("cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("composer.json", "php"),
    ("mix.exs", "elixir"),
    ("pubspec.yaml", "dart"),
)

LOCKFILES = frozenset(
    {
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "gemfile.lock",
        "cargo.lock",
        "go.sum",
        "composer.lock",
        "pipfile.lock",
        "requirements.lock",
        "mix.lock",
        "pubspec.lock",
    }
)

LICENSE_STEMS = ("license", "licence", "copying", "unlicense")
DOC_DIR_PREFIXES = ("docs/", "doc/", "documentation/", "website/docs/")
DOC_CONFIG_FILES = frozenset({"mkdocs.yml", "mkdocs.yaml", "readthedocs.yml", ".readthedocs.yaml"})

#: README markers that mean the author said it was unfinished (R-7, P2).
WIP_PATTERN = re.compile(
    r"\b(work in progress|wip|unfinished|abandoned|no longer maintained|"
    r"not maintained|unmaintained|deprecated|not production ready|"
    r"pre-alpha|alpha quality|experimental|do not use)\b",
    re.I,
)

MAX_TEST_SAMPLE = 8
MAX_TOP_LEVEL_ENTRIES = 40


@dataclass(frozen=True)
class StructuralSignals:
    """Everything the tree can tell us. Every field is derived from path strings."""

    file_count: int = 0
    top_level_entries: list[str] = field(default_factory=list)
    has_tests: bool = False
    test_file_count: int = 0
    test_paths_sample: list[str] = field(default_factory=list)
    has_ci: bool = False
    ci_systems: list[str] = field(default_factory=list)
    ci_is_legacy_only: bool = False
    has_license_file: bool = False
    has_contributing: bool = False
    has_code_of_conduct: bool = False
    doc_page_count: int = 0
    has_changelog: bool = False
    has_dockerfile: bool = False
    has_lockfile: bool = False
    lockfile_paths: list[str] = field(default_factory=list)
    manifest_paths: list[str] = field(default_factory=list)
    ecosystems: list[str] = field(default_factory=list)


def is_vendored(path: str) -> bool:
    """True when a path lives in a dependency or build directory, at any depth."""
    lowered = path.lower()
    return any(
        lowered.startswith(prefix) or f"/{prefix}" in lowered for prefix in VENDORED_PREFIXES
    )


def is_test_path(path: str) -> bool:
    """A test file, by directory or by filename convention.

    Deliberately conservative: `contrib/testdata/notatest.py` matches neither a
    test directory segment (`testdata` is not one) nor a test filename pattern
    (`notatest.py` does not start with `test_`), so it is not a test.
    """
    if is_vendored(path):
        return False
    segments = path.lower().split("/")
    if any(segment in TEST_DIR_SEGMENTS for segment in segments[:-1]):
        return True
    filename = posixpath.basename(path)
    return any(pattern.match(filename) for pattern in TEST_FILE_PATTERNS)


def detect_ci(paths: list[str]) -> tuple[list[str], bool]:
    """Return `(ci_systems, legacy_only)` from root-anchored CI configuration."""
    found: list[str] = []
    for path in paths:
        if is_vendored(path):
            continue
        lowered = path.lower()
        for marker, system, _legacy in CI_RULES:
            if marker.endswith("/"):
                # A workflow directory: any YAML file directly inside it.
                matched = lowered.startswith(marker) and lowered.endswith((".yml", ".yaml"))
            else:
                # Anchored to the exact path the service reads, so a nested copy
                # such as vendor/foo/.travis.yml cannot invent a pipeline.
                matched = lowered == marker
            if matched and system not in found:
                found.append(system)
    legacy_only = bool(found) and all(system in LEGACY_CI_SYSTEMS for system in found)
    return found, legacy_only


def detect_signals(paths: list[str], repo_meta: dict[str, object] | None = None) -> StructuralSignals:
    """Derive every structural signal from a recursive tree listing.

    `repo_meta` is the call-2 payload; only `license` is consulted, and only to
    confirm a licence GitHub recognized when no LICENSE file is in the tree.
    """
    # `removeprefix("./")`, not `lstrip("./")`: lstrip takes a character *set*,
    # so it ate the leading dot of every dotfile and made `.github/workflows/*`,
    # `.travis.yml` and `.gitlab-ci.yml` invisible to the checks below.
    cleaned = [p for p in (path.strip().removeprefix("./") for path in paths) if p]
    lowered = [p.lower() for p in cleaned]

    top_level: list[str] = []
    for path in cleaned:
        head = path.split("/", 1)[0]
        if head and head not in top_level:
            top_level.append(head)
    top_level = sorted(top_level)[:MAX_TOP_LEVEL_ENTRIES]

    test_paths = [p for p in cleaned if is_test_path(p)]
    ci_systems, ci_legacy_only = detect_ci(cleaned)

    root_names = {p for p in lowered if "/" not in p}
    github_dir_names = {
        p.removeprefix(".github/") for p in lowered if p.startswith(".github/")
    }
    surface = root_names | github_dir_names

    has_license_file = any(
        name.startswith(LICENSE_STEMS) for name in surface
    ) or _license_from_meta(repo_meta)

    doc_pages = _count_doc_pages(cleaned, lowered)

    lockfile_paths = [p for p in cleaned if posixpath.basename(p).lower() in LOCKFILES]
    manifest_paths, ecosystems = _find_manifests(cleaned)

    return StructuralSignals(
        file_count=len(cleaned),
        top_level_entries=top_level,
        has_tests=bool(test_paths),
        test_file_count=len(test_paths),
        test_paths_sample=sorted(test_paths)[:MAX_TEST_SAMPLE],
        has_ci=bool(ci_systems),
        ci_systems=ci_systems,
        ci_is_legacy_only=ci_legacy_only,
        has_license_file=has_license_file,
        has_contributing=any(name.startswith("contributing") for name in surface),
        has_code_of_conduct=any(name.startswith("code_of_conduct") for name in surface)
        or any(name.startswith("code-of-conduct") for name in surface),
        doc_page_count=doc_pages,
        has_changelog=any(
            name.startswith(("changelog", "changes", "history", "news")) for name in root_names
        ),
        has_dockerfile=any(
            name == "dockerfile" or name.startswith("dockerfile.") or name == "docker-compose.yml"
            for name in root_names
        ),
        has_lockfile=bool(lockfile_paths),
        lockfile_paths=sorted(lockfile_paths)[:4],
        manifest_paths=manifest_paths,
        ecosystems=ecosystems,
    )


def _license_from_meta(repo_meta: dict[str, object] | None) -> bool:
    if not repo_meta:
        return False
    licence = repo_meta.get("license")
    return isinstance(licence, dict) and bool(licence.get("spdx_id"))


def _count_doc_pages(cleaned: list[str], lowered: list[str]) -> int:
    """Documentation beyond the README: a docs tree, or markdown outside the root."""
    count = 0
    for path, low in zip(cleaned, lowered, strict=True):
        if is_vendored(path):
            continue
        if low in DOC_CONFIG_FILES:
            count += 1
            continue
        in_doc_dir = low.startswith(DOC_DIR_PREFIXES)
        documented_in_tree = in_doc_dir and low.endswith(
            (".md", ".rst", ".adoc", ".txt", ".html")
        )
        # Markdown outside the root is documentation too, but only when it is not
        # already counted as part of a docs tree.
        nested_markdown = (
            not in_doc_dir and "/" in low and low.endswith((".md", ".rst", ".adoc"))
        )
        if documented_in_tree or nested_markdown:
            count += 1
    return count


def _find_manifests(cleaned: list[str]) -> tuple[list[str], list[str]]:
    """Pick the manifests worth spending a call on, best first.

    Root manifests outrank nested ones because a nested `package.json` is as
    likely to be a fixture as a dependency declaration. At most one manifest per
    ecosystem is returned, and the call plan reads at most the first two.
    """
    by_priority: list[tuple[int, int, str, str]] = []
    for path in cleaned:
        if is_vendored(path):
            continue
        name = posixpath.basename(path).lower()
        for priority, (manifest_name, ecosystem) in enumerate(MANIFEST_SPEC):
            if name == manifest_name:
                depth = path.count("/")
                by_priority.append((depth, priority, path, ecosystem))
                break

    by_priority.sort(key=lambda row: (row[0], row[1], row[2]))

    chosen: list[str] = []
    ecosystems: list[str] = []
    for _depth, _priority, path, ecosystem in by_priority:
        if ecosystem in ecosystems:
            continue
        ecosystems.append(ecosystem)
        chosen.append(path)
    return chosen[:4], ecosystems


def detect_declared_wip(readme_text: str | None) -> tuple[bool, str | None]:
    """Did the author say it was unfinished? Returns the matched phrase as evidence."""
    if not readme_text:
        return False, None
    match = WIP_PATTERN.search(readme_text)
    return (True, match.group(0).lower()) if match else (False, None)


# --------------------------------------------------------------------------
# dependency manifests and the offline deprecation table
# --------------------------------------------------------------------------
# Manifests are parsed as *data*. `setup.py` is matched with a regex and never
# executed, `pyproject.toml` goes through stdlib `tomllib`, JSON through
# `json.loads`. Nothing here imports, evals or runs repository content (E4).

DEPRECATION_TABLE_PATH = Path(__file__).parent / "deprecated.json"

#: Parsed once per process. The table is a committed file, so this is a
#: constant for the lifetime of the process, not a cache that can go stale.
_TABLE_CACHE: DeprecationTable | None = None

MAX_DEPENDENCIES = 200
_REQUIREMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<spec>.*)$")
_INSTALL_REQUIRES_RE = re.compile(
    r"install_requires\s*=\s*\[(?P<body>.*?)\]", re.S
)
_GEMFILE_RE = re.compile(
    r"^\s*gem\s+['\"](?P<name>[^'\"]+)['\"](?P<spec>[^\n#]*)", re.M
)
_GOMOD_REQUIRE_RE = re.compile(r"^\s*(?P<name>[\w./~-]+\.[\w./~-]+)\s+(?P<spec>v[\w.+-]+)", re.M)
_MAVEN_DEP_RE = re.compile(
    r"<artifactId>\s*(?P<name>[^<\s]+)\s*</artifactId>", re.I
)
_GRADLE_DEP_RE = re.compile(
    r"""['"](?P<group>[\w.\-]+):(?P<name>[\w.\-]+)(?::(?P<spec>[\w.\-]+))?['"]"""
)


@dataclass(frozen=True)
class ParsedDependency:
    """One declared direct dependency, before deprecation flagging."""

    name: str
    spec: str | None
    ecosystem: str


@dataclass(frozen=True)
class DeprecatedPackage:
    ecosystem: str
    name: str
    status: str
    since: str | None
    replacement: str | None
    source_url: str | None


@dataclass(frozen=True)
class EolRuntime:
    ecosystem: str
    marker: str
    label: str
    patterns: tuple[str, ...]
    source_url: str | None


@dataclass(frozen=True)
class DeprecationTable:
    """The committed, dated, versioned table (E13). Its version is always shown."""

    version: str
    compiled_at: str
    packages: dict[tuple[str, str], DeprecatedPackage]
    runtimes: tuple[EolRuntime, ...]

    @property
    def label(self) -> str:
        return f"offline deprecation table v{self.version}, compiled {self.compiled_at}"

    def lookup(self, ecosystem: str, name: str) -> DeprecatedPackage | None:
        return self.packages.get((ecosystem.lower(), name.strip().lower()))


def load_deprecation_table(path: Path | None = None) -> DeprecationTable:
    """Read the committed table. No network, ever (E13)."""
    global _TABLE_CACHE
    if path is None and _TABLE_CACHE is not None:
        return _TABLE_CACHE

    source = path or DEPRECATION_TABLE_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))
    packages: dict[tuple[str, str], DeprecatedPackage] = {}
    for entry in raw.get("packages", []):
        pkg = DeprecatedPackage(
            ecosystem=str(entry["ecosystem"]).lower(),
            name=str(entry["name"]).lower(),
            status=str(entry["status"]),
            since=entry.get("since"),
            replacement=entry.get("replacement"),
            source_url=entry.get("source_url"),
        )
        packages[(pkg.ecosystem, pkg.name)] = pkg

    runtimes = tuple(
        EolRuntime(
            ecosystem=str(entry["ecosystem"]).lower(),
            marker=str(entry["marker"]),
            label=str(entry["label"]),
            patterns=tuple(str(p).lower() for p in entry.get("patterns", [])),
            source_url=entry.get("source_url"),
        )
        for entry in raw.get("runtimes", [])
    )

    table = DeprecationTable(
        version=str(raw.get("table_version", "0")),
        compiled_at=str(raw.get("compiled_at", "unknown")),
        packages=packages,
        runtimes=runtimes,
    )
    if path is None:
        _TABLE_CACHE = table
    return table


def ecosystem_for_manifest(path: str) -> str | None:
    name = posixpath.basename(path).lower()
    for manifest_name, ecosystem in MANIFEST_SPEC:
        if name == manifest_name:
            return ecosystem
    return None


def parse_manifest(path: str, text: str | None) -> list[ParsedDependency]:
    """Parse a manifest into direct dependencies. Best effort, never executed.

    An unparseable manifest returns an empty list, which lowers
    `dependency_data_confidence` rather than inventing dependencies (P7).
    """
    if not text:
        return []
    ecosystem = ecosystem_for_manifest(path)
    if ecosystem is None:
        return []
    name = posixpath.basename(path).lower()

    try:
        if name == "pyproject.toml":
            parsed = _parse_pyproject(text)
        elif name == "requirements.txt":
            parsed = _parse_requirements(text)
        elif name == "setup.py":
            parsed = _parse_setup_py(text)
        elif name == "pipfile":
            parsed = _parse_pipfile(text)
        elif name == "package.json":
            parsed = _parse_package_json(text)
        elif name == "gemfile":
            parsed = _parse_gemfile(text)
        elif name == "go.mod":
            parsed = _parse_go_mod(text)
        elif name == "cargo.toml":
            parsed = _parse_cargo_toml(text)
        elif name == "composer.json":
            parsed = _parse_composer_json(text)
        elif name == "pom.xml":
            parsed = _parse_pom_xml(text)
        elif name == "build.gradle":
            parsed = _parse_gradle(text)
        else:
            parsed = []
    except (ValueError, TypeError, tomllib.TOMLDecodeError, json.JSONDecodeError):
        return []

    seen: set[str] = set()
    out: list[ParsedDependency] = []
    for dep_name, spec in parsed:
        key = dep_name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            ParsedDependency(name=dep_name.strip(), spec=(spec or "").strip() or None,
                             ecosystem=ecosystem)
        )
        if len(out) >= MAX_DEPENDENCIES:
            break
    return out


def detect_eol_runtimes(
    manifest_texts: dict[str, str], table: DeprecationTable
) -> list[EolRuntime]:
    """Match the runtime patterns against raw manifest text, de-duplicated."""
    found: dict[str, EolRuntime] = {}
    for path, text in manifest_texts.items():
        ecosystem = ecosystem_for_manifest(path)
        if ecosystem is None or not text:
            continue
        haystack = re.sub(r"\s+", " ", text.lower())
        for runtime in table.runtimes:
            if runtime.ecosystem != ecosystem or runtime.marker in found:
                continue
            if any(pattern in haystack for pattern in runtime.patterns):
                found[runtime.marker] = runtime
    return list(found.values())


# -- per-ecosystem parsers -------------------------------------------------
def _parse_requirements(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http://", "https://")):
            continue
        line = line.split(";", 1)[0].strip()
        match = _REQUIREMENT_RE.match(line)
        if match:
            out.append((match.group("name"), match.group("spec")))
    return out


def _parse_pyproject(text: str) -> list[tuple[str, str]]:
    data = tomllib.loads(text)
    out: list[tuple[str, str]] = []
    project = data.get("project")
    if isinstance(project, dict):
        for item in project.get("dependencies", []) or []:
            if isinstance(item, str):
                match = _REQUIREMENT_RE.match(item.strip())
                if match:
                    out.append((match.group("name"), match.group("spec")))
    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
    if isinstance(poetry, dict):
        for name, spec in (poetry.get("dependencies") or {}).items():
            if name.lower() == "python":
                continue
            out.append((name, spec if isinstance(spec, str) else ""))
    return out


def _parse_setup_py(text: str) -> list[tuple[str, str]]:
    """Regex only. `setup.py` is never imported or executed (E4)."""
    match = _INSTALL_REQUIRES_RE.search(text)
    if not match:
        return []
    out: list[tuple[str, str]] = []
    for item in re.findall(r"""['"]([^'"]+)['"]""", match.group("body")):
        requirement = _REQUIREMENT_RE.match(item.strip())
        if requirement:
            out.append((requirement.group("name"), requirement.group("spec")))
    return out


def _parse_pipfile(text: str) -> list[tuple[str, str]]:
    data = tomllib.loads(text)
    out: list[tuple[str, str]] = []
    for section in ("packages", "dev-packages"):
        for name, spec in (data.get(section) or {}).items():
            out.append((name, spec if isinstance(spec, str) else ""))
    return out


def _parse_package_json(text: str) -> list[tuple[str, str]]:
    data = json.loads(text)
    out: list[tuple[str, str]] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            out.extend((str(k), str(v)) for k, v in block.items())
    return out


def _parse_gemfile(text: str) -> list[tuple[str, str]]:
    return [
        (m.group("name"), m.group("spec").strip(" ,"))
        for m in _GEMFILE_RE.finditer(text)
    ]


def _parse_go_mod(text: str) -> list[tuple[str, str]]:
    return [(m.group("name"), m.group("spec")) for m in _GOMOD_REQUIRE_RE.finditer(text)]


def _parse_cargo_toml(text: str) -> list[tuple[str, str]]:
    data = tomllib.loads(text)
    out: list[tuple[str, str]] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, spec in (data.get(section) or {}).items():
            if isinstance(spec, str):
                out.append((name, spec))
            elif isinstance(spec, dict):
                out.append((name, str(spec.get("version", ""))))
    return out


def _parse_composer_json(text: str) -> list[tuple[str, str]]:
    data = json.loads(text)
    out: list[tuple[str, str]] = []
    for section in ("require", "require-dev"):
        block = data.get(section)
        if isinstance(block, dict):
            out.extend((str(k), str(v)) for k, v in block.items())
    return out


def _parse_pom_xml(text: str) -> list[tuple[str, str]]:
    return [(name, "") for name in _MAVEN_DEP_RE.findall(text)]


def _parse_gradle(text: str) -> list[tuple[str, str]]:
    return [
        (m.group("name"), m.group("spec") or "")
        for m in _GRADLE_DEP_RE.finditer(text)
    ]
