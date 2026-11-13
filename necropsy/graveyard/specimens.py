"""Descriptors for the curated graveyard, and the payload builder behind them.

**What these are, stated plainly.** Each entry below is a *specimen*: a compiled
GitHub payload set that exhibits one real failure mode from design section 8.4.
They are not captures of named third-party repositories. NECROPSY cannot verify a
live repository's numbers without the API, and printing invented counts under a
real project's name would be exactly the confident fabrication P7 forbids — a
footnote does not fix a false number on a plaque.

So the demo wall is honest about itself. Every specimen lives under the
`necropsy-specimens` owner, every exhibit carries `fixture_provenance:
"compiled_specimen"`, and the UI shows that label next to the data. The real
repositories go through the live path: a judge pastes a URL and watches an actual
analysis, with actual API numbers, in under twenty seconds.

**What makes them useful anyway.** The payloads are internally consistent and
exercise the whole pipeline: `normalize` reads them exactly as it reads GitHub,
`detect` derives every structural signal from these path strings, and the scorer
produces a genuine spread across bands (design 8.4 wants spread, not a column of
identical scores). Nothing is special-cased for them.

**Replacing them with real repositories** needs one command and a token:
`seed-graveyard --refresh` re-analyzes each manifest entry against the API and
rewrites its fixture with `fixture_provenance: "api_verified"`. Point the manifest
at the repositories you want and the wall becomes a wall of real post-mortems.

Everything here is data plus one pure builder. No clock: `as_of` and every date
are literals, so a committed fixture is byte-stable forever (E13, Property 2).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any

#: Frozen capture moment for every specimen. All age arithmetic derives from it,
#: so the wall reads the same today as it will in a year (E6).
AS_OF = "2026-11-01T00:00:00+00:00"

SPECIMEN_OWNER = "necropsy-specimens"

#: Written into every generated fixture and shown in the UI (P7).
PROVENANCE = "compiled_specimen"


@dataclass(frozen=True)
class IssueSpec:
    """A sampled open-issue set, described by its shape rather than listed."""

    sample: int = 0
    unanswered: int = 0
    reported_open: int = 0
    newest_activity: str = AS_OF
    oldest: str = "2016-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class Specimen:
    """One exhibit's complete input, before it becomes a GitHub payload set."""

    slug: str
    name: str
    description: str
    failure_mode: str
    curator_note: str
    display_order: int
    last_commit_at: str
    created_at: str
    last_commit_message: str
    languages: dict[str, int]
    primary_language: str
    tree: tuple[str, ...]
    readme: str | None
    manifests: dict[str, str] = field(default_factory=dict)
    license_spdx: str | None = None
    license_name: str | None = None
    archived: bool = False
    is_fork: bool = False
    size_kb: int = 1_000
    stars: int = 0
    forks: int = 0
    topics: tuple[str, ...] = ()
    release: tuple[str, str] | None = None  # (tag, published_at)
    issues: IssueSpec = field(default_factory=IssueSpec)
    sha: str = ""

    @property
    def canonical_url(self) -> str:
        return f"github.com/{SPECIMEN_OWNER}/{self.name}".lower()

    @property
    def commit_sha(self) -> str:
        """A deterministic pinned SHA (R-5.3), legible as a specimen marker.

        It begins `5pec1men` so nobody mistakes it for a git object id, and the
        remainder is a hash of the slug so it is stable across machines.
        """
        if self.sha:
            return self.sha
        digest = hashlib.sha256(self.slug.encode("utf-8")).hexdigest()
        return ("5pec1men" + digest)[:40]


# --------------------------------------------------------------------------
# the eight slots (design 8.4)
# --------------------------------------------------------------------------
_PY_TESTS = (
    "tests/__init__.py",
    "tests/test_atlas.py",
    "tests/test_projection.py",
    "tests/test_tiles.py",
)

SPECIMENS: tuple[Specimen, ...] = (
    Specimen(
        slug="atlas-mapper",
        name="atlas-mapper",
        description="Tile-based map projection toolkit for scientific plotting.",
        failure_mode="archived by owner with a farewell README",
        curator_note=(
            "The clearest death there is: the owner archived it and said why in the "
            "README. Archived status floors the abandonment factor at 0.85, so this "
            "exhibit shows what that floor does to a score."
        ),
        display_order=10,
        last_commit_at="2019-03-12T14:22:09+00:00",
        created_at="2013-06-01T10:00:00+00:00",
        last_commit_message="Add deprecation notice to README",
        languages={"Python": 486_000, "C": 94_000, "Makefile": 4_200},
        primary_language="Python",
        tree=(
            "README.md",
            "LICENSE",
            "setup.py",
            "requirements.txt",
            ".travis.yml",
            "atlas/__init__.py",
            "atlas/projection.py",
            "atlas/tiles.py",
            "atlas/raster.py",
            "atlas/cli.py",
            "docs/index.md",
            "docs/projections.md",
            *_PY_TESTS,
            "examples/demo.py",
        ),
        readme=(
            "# atlas-mapper\n\n"
            "**This project is no longer maintained.** I wrote it for a thesis that "
            "finished in 2019, and I have not used it since. It still works on "
            "Python 2.7 and early 3.x; it will not work on a modern interpreter "
            "without changes to the C extension.\n\n"
            "If you need this, fork it. I will not be reviewing pull requests.\n\n"
            "## What it does\n\n"
            "Projects raster tiles between coordinate systems without a GDAL "
            "dependency. The projection maths is in `atlas/projection.py` and is the "
            "only part I would still defend.\n\n"
            "## Installation\n\n"
            "    pip install atlas-mapper\n\n"
            "## Status\n\n"
            "Archived. Tests pass on the last commit under Python 2.7.\n"
        ),
        manifests={
            "requirements.txt": (
                "nose==1.3.7\n"
                "mock==2.0.0\n"
                "numpy>=1.11,<1.17\n"
                "six==1.11.0\n"
                "Pillow==5.4.1\n"
            )
        },
        license_spdx="MIT",
        license_name="MIT License",
        archived=True,
        size_kb=6_400,
        stars=312,
        forks=41,
        topics=("gis", "projection", "raster"),
        release=("v1.4.0", "2018-11-20T00:00:00+00:00"),
        issues=IssueSpec(
            sample=14,
            unanswered=12,
            reported_open=14,
            newest_activity="2021-08-02T09:14:00+00:00",
            oldest="2017-02-11T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="gemstone-cart",
        name="gemstone-cart",
        description="Shopping cart engine for Rails applications.",
        failure_mode="dependency-era death: framework major version reached EOL",
        curator_note=(
            "Pinned to a framework release that went out of support before the final "
            "commit. The dependency-rot factor carries this exhibit, and every "
            "flagged package is named by the offline deprecation table."
        ),
        display_order=20,
        last_commit_at="2019-06-14T09:30:00+00:00",
        created_at="2014-02-02T08:00:00+00:00",
        last_commit_message="Bump version to 2.3.1",
        languages={"Ruby": 412_000, "JavaScript": 61_000, "SCSS": 18_400},
        primary_language="Ruby",
        tree=(
            "README.md",
            "LICENSE.txt",
            "Gemfile",
            "Gemfile.lock",
            "gemstone-cart.gemspec",
            ".travis.yml",
            "app/models/cart.rb",
            "app/models/line_item.rb",
            "app/controllers/carts_controller.rb",
            "lib/gemstone/cart.rb",
            "lib/gemstone/pricing.rb",
            "spec/spec_helper.rb",
            "spec/models/cart_spec.rb",
            "spec/models/line_item_spec.rb",
            "spec/controllers/carts_controller_spec.rb",
        ),
        readme=(
            "# gemstone-cart\n\n"
            "A mountable shopping cart for Rails 4.2 applications. Handles line "
            "items, quantity rules and tax bands; deliberately does not handle "
            "payment.\n\n"
            "## Installation\n\n"
            "Add to your Gemfile:\n\n"
            "    gem 'gemstone-cart', '~> 2.3'\n\n"
            "Then run the installer:\n\n"
            "    rails generate gemstone:install\n\n"
            "## Compatibility\n\n"
            "Tested against Rails 4.2 and Ruby 2.3. Rails 5 support is on the "
            "roadmap.\n"
        ),
        manifests={
            "Gemfile": (
                "source 'https://rubygems.org'\n\n"
                "gem 'rails', '~> 4.2.11'\n"
                "gem 'sass-rails', '~> 5.0'\n"
                "gem 'coffee-rails', '~> 4.1.0'\n"
                "gem 'jquery-rails'\n"
                "gem 'therubyracer'\n\n"
                "group :development, :test do\n"
                "  gem 'rspec-rails', '~> 3.5'\n"
                "end\n"
            )
        },
        license_spdx="MIT",
        license_name="MIT License",
        size_kb=8_412,
        stars=94,
        forks=12,
        topics=("rails", "ecommerce"),
        release=("v2.3.1", "2019-06-14T12:00:00+00:00"),
        issues=IssueSpec(
            sample=23,
            unanswered=17,
            reported_open=23,
            newest_activity="2022-12-01T11:00:00+00:00",
            oldest="2016-05-20T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="solo-sync",
        name="solo-sync",
        description="Two-way file synchroniser with conflict resolution.",
        failure_mode="single-maintainer disappearance, high unanswered-issue ratio",
        curator_note=(
            "Healthy code, absent maintainer. Twenty-eight of the thirty sampled "
            "issues have no reply. Described structurally, never personally: the "
            "finding is the sample, not the person (P6)."
        ),
        display_order=30,
        last_commit_at="2021-01-19T22:05:41+00:00",
        created_at="2017-09-14T12:00:00+00:00",
        last_commit_message="Fix race in watcher shutdown",
        languages={"Go": 298_000, "Shell": 6_100},
        primary_language="Go",
        tree=(
            "README.md",
            "LICENSE",
            "go.mod",
            "go.sum",
            ".github/workflows/ci.yml",
            "cmd/solosync/main.go",
            "internal/watcher/watcher.go",
            "internal/watcher/watcher_test.go",
            "internal/sync/engine.go",
            "internal/sync/engine_test.go",
            "internal/sync/conflict.go",
            "internal/sync/conflict_test.go",
        ),
        readme=(
            "# solo-sync\n\n"
            "Two-way directory synchronisation with explicit conflict resolution. "
            "No daemon, no cloud account, no telemetry.\n\n"
            "## Usage\n\n"
            "    solosync --left ./a --right ./b --on-conflict newer\n\n"
            "## Design\n\n"
            "The watcher and the sync engine are separate packages so the engine can "
            "be tested without touching the filesystem. Conflict strategies are "
            "pluggable; `newer`, `larger` and `ask` ship in the box.\n"
        ),
        manifests={
            "go.mod": (
                "module github.com/necropsy-specimens/solo-sync\n\n"
                "go 1.15\n\n"
                "require (\n"
                "\tgithub.com/fsnotify/fsnotify v1.4.9\n"
                "\tgithub.com/spf13/cobra v1.1.1\n"
                "\tgopkg.in/yaml.v2 v2.4.0\n"
                ")\n"
            )
        },
        license_spdx="Apache-2.0",
        license_name="Apache License 2.0",
        size_kb=2_100,
        stars=1_840,
        forks=96,
        topics=("sync", "cli", "golang"),
        release=("v0.9.2", "2020-11-02T00:00:00+00:00"),
        issues=IssueSpec(
            sample=30,
            unanswered=28,
            reported_open=147,
            newest_activity="2023-06-18T07:42:00+00:00",
            oldest="2018-03-02T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="half-built-parser",
        name="half-built-parser",
        description="An incremental parser for a configuration language. Work in progress.",
        failure_mode="declared work-in-progress, never finished, tiny codebase",
        curator_note=(
            "The author said it was unfinished and was telling the truth. Small, "
            "licensed, no tests. Low difficulty is the right answer here, and it is "
            "a different diagnosis from abandonment."
        ),
        display_order=40,
        last_commit_at="2024-09-15T16:11:00+00:00",
        created_at="2024-07-28T19:30:00+00:00",
        last_commit_message="WIP: start lexer rewrite",
        languages={"Rust": 41_200},
        primary_language="Rust",
        tree=(
            "README.md",
            "LICENSE",
            "Cargo.toml",
            "Cargo.lock",
            "src/main.rs",
            "src/lexer.rs",
            "src/parser.rs",
        ),
        readme=(
            "# half-built-parser\n\n"
            "**Work in progress. Not production ready.** I am learning Rust by "
            "writing an incremental parser for a small configuration language. The "
            "lexer works. The parser handles maps and scalars and falls over on "
            "nested lists.\n\n"
            "Do not depend on this.\n"
        ),
        manifests={
            "Cargo.toml": (
                "[package]\n"
                'name = "half-built-parser"\n'
                'version = "0.1.0"\n'
                'edition = "2021"\n\n'
                "[dependencies]\n"
                'anyhow = "1.0"\n'
                'clap = "3.1"\n'
            )
        },
        license_spdx="MIT",
        license_name="MIT License",
        size_kb=180,
        stars=7,
        forks=0,
        topics=("rust", "parser", "wip"),
        issues=IssueSpec(sample=0, unanswered=0, reported_open=0),
    ),
    Specimen(
        slug="legacy-ledger",
        name="legacy-ledger",
        description="Double-entry bookkeeping library with a decimal-safe money type.",
        failure_mode="large, well-tested, simply stale",
        curator_note=(
            "The counter-example the wall needs. Tests, CI, a licence, documentation, "
            "a changelog, and no commit in four years. Stale is not the same as "
            "rotten, and the score says so."
        ),
        display_order=50,
        last_commit_at="2022-07-28T08:45:12+00:00",
        created_at="2015-01-12T09:00:00+00:00",
        last_commit_message="Release 4.2.0",
        languages={"Java": 1_240_000, "Kotlin": 88_000, "XML": 31_000},
        primary_language="Java",
        tree=(
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "pom.xml",
            ".github/workflows/build.yml",
            ".github/workflows/release.yml",
            "docs/getting-started.md",
            "docs/money.md",
            "docs/accounts.md",
            "docs/migration-3-to-4.md",
            "src/main/java/ledger/Account.java",
            "src/main/java/ledger/Entry.java",
            "src/main/java/ledger/Money.java",
            "src/main/java/ledger/Journal.java",
            "src/main/java/ledger/Posting.java",
            "src/test/java/ledger/AccountTest.java",
            "src/test/java/ledger/MoneyTest.java",
            "src/test/java/ledger/JournalTest.java",
            "src/test/java/ledger/PostingTest.java",
            "src/test/java/ledger/EntryTest.java",
            "src/test/java/ledger/RoundingTest.java",
        ),
        readme=(
            "# legacy-ledger\n\n"
            "A double-entry bookkeeping library for the JVM. Decimal-safe money, "
            "immutable postings, and a journal that refuses to balance incorrectly.\n\n"
            "## Installation\n\n"
            "    <dependency>\n"
            "      <groupId>io.ledger</groupId>\n"
            "      <artifactId>legacy-ledger</artifactId>\n"
            "      <version>4.2.0</version>\n"
            "    </dependency>\n\n"
            "## Why another one\n\n"
            "Because `double` is not a money type and most libraries that know that "
            "also insist on owning your persistence layer. This one does not touch a "
            "database.\n\n"
            "## Documentation\n\n"
            "See `docs/`. The migration guide from 3.x is in "
            "`docs/migration-3-to-4.md`.\n\n"
            "## Status\n\n"
            "Feature complete for our purposes. Issues are read; features are "
            "unlikely to be added.\n"
        ),
        manifests={
            "pom.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<project>\n"
                "  <dependencies>\n"
                "    <dependency>\n"
                "      <groupId>org.junit.jupiter</groupId>\n"
                "      <artifactId>junit-jupiter</artifactId>\n"
                "    </dependency>\n"
                "    <dependency>\n"
                "      <groupId>org.assertj</groupId>\n"
                "      <artifactId>assertj-core</artifactId>\n"
                "    </dependency>\n"
                "  </dependencies>\n"
                "</project>\n"
            )
        },
        license_spdx="Apache-2.0",
        license_name="Apache License 2.0",
        size_kb=14_800,
        stars=2_410,
        forks=188,
        topics=("accounting", "jvm", "money"),
        release=("4.2.0", "2022-07-28T10:00:00+00:00"),
        issues=IssueSpec(
            sample=9,
            unanswered=3,
            reported_open=9,
            newest_activity="2024-02-14T13:20:00+00:00",
            oldest="2021-06-01T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="quickshare",
        name="quickshare",
        description="Drag-and-drop file sharing with expiring links.",
        failure_mode="no licence, no tests, no CI, moderate size",
        curator_note=(
            "Nothing legally reusable, nothing that proves it works, nothing that "
            "runs. Three of the eight factors sit at 1.0, which is what a legal and "
            "verification vacuum looks like on the scorecard."
        ),
        display_order=60,
        last_commit_at="2020-09-02T03:17:44+00:00",
        created_at="2018-11-30T21:00:00+00:00",
        last_commit_message="quick fix for upload size",
        languages={"PHP": 214_000, "JavaScript": 72_000, "CSS": 29_000},
        primary_language="PHP",
        tree=(
            "README.md",
            "composer.json",
            "index.php",
            "upload.php",
            "download.php",
            "src/Storage.php",
            "src/Link.php",
            "src/Expiry.php",
            "public/js/app.js",
            "public/css/style.css",
        ),
        readme=(
            "# quickshare\n\n"
            "Upload a file, get a link that dies in 24 hours. Runs on any PHP host "
            "with write access to a directory.\n\n"
            "Set `STORAGE_PATH` in `config.php` and point a vhost at `index.php`.\n"
        ),
        manifests={
            "composer.json": (
                "{\n"
                '  "name": "quickshare/quickshare",\n'
                '  "require": {\n'
                '    "php": ">=7.1",\n'
                '    "guzzlehttp/guzzle": "^6.3",\n'
                '    "ramsey/uuid": "^3.8"\n'
                "  }\n"
                "}\n"
            )
        },
        license_spdx=None,
        license_name=None,
        size_kb=3_900,
        stars=58,
        forks=22,
        topics=(),
        issues=IssueSpec(
            sample=11,
            unanswered=9,
            reported_open=11,
            newest_activity="2022-01-08T15:00:00+00:00",
            oldest="2019-04-04T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="closing-time",
        name="closing-time",
        description="CLI that archives a project: freezes dependencies, writes a final changelog.",
        failure_mode="successful but finished: dead, and legitimately cheap to revive",
        curator_note=(
            "Finished is a diagnosis, not an insult (P6). Complete tests, modern CI, "
            "an OSI licence, real documentation, a lockfile, no deprecated "
            "dependencies, and no reason to commit again. It is genuinely dead and "
            "genuinely easy to pick up, which is what the lowest band is for."
        ),
        display_order=70,
        last_commit_at="2026-01-09T11:02:00+00:00",
        created_at="2023-08-19T07:00:00+00:00",
        last_commit_message="Release 2.0.0: freeze the dependency set",
        languages={"Python": 181_400, "Jinja": 6_200},
        primary_language="Python",
        tree=(
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "pyproject.toml",
            "poetry.lock",
            ".github/workflows/test.yml",
            ".github/workflows/release.yml",
            "docs/index.md",
            "docs/usage.md",
            "docs/archive-format.md",
            "docs/why-archive.md",
            "closing_time/__init__.py",
            "closing_time/cli.py",
            "closing_time/freeze.py",
            "closing_time/changelog.py",
            "closing_time/manifest.py",
            "closing_time/templates/final.md.j2",
            *[f"tests/test_{name}.py" for name in (
                "cli", "freeze", "changelog", "manifest", "templates",
                "roundtrip", "errors", "integration",
            )],
            *[f"tests/unit/test_{name}.py" for name in (
                "freeze_pins", "freeze_extras", "changelog_sections",
                "changelog_dates", "manifest_parse", "manifest_write",
                "cli_flags", "cli_exit_codes", "templates_render",
                "templates_escape", "errors_messages", "errors_codes",
                "integration_smoke", "integration_idempotent",
            )],
        ),
        readme=(
            "# closing-time\n\n"
            "A command-line tool for closing a project down properly. It freezes "
            "your dependency set to exact versions, writes a final changelog entry "
            "from your commit history, and generates an ARCHIVED notice for the "
            "README so the next reader knows where they stand.\n\n"
            "## Installation\n\n"
            "    pipx install closing-time\n\n"
            "## Usage\n\n"
            "    closing-time freeze          # pin every dependency to an exact version\n"
            "    closing-time changelog       # write the final CHANGELOG entry\n"
            "    closing-time notice --apply  # add the ARCHIVED banner to README.md\n\n"
            "Each subcommand prints what it would do and asks before writing. Pass "
            "`--yes` in CI.\n\n"
            "## Why archive deliberately\n\n"
            "An unmaintained project with floating version specifiers stops building "
            "silently, months after anyone is watching. Pinning on the way out means "
            "the last known-good state stays buildable. The reasoning is written up "
            "in `docs/why-archive.md`.\n\n"
            "## Scope\n\n"
            "This project is finished. It does the three things above and will not "
            "grow a fourth. Bug reports are read and fixed; feature requests are "
            "closed with thanks. The archive format is specified in "
            "`docs/archive-format.md` and is frozen at version 2.\n\n"
            "## Testing\n\n"
            "    poetry install && poetry run pytest\n\n"
            "CI runs the suite on every push against Python 3.9 through 3.13. The "
            "integration tests archive a throwaway repository end to end.\n\n"
            "## Licence\n\n"
            "BSD 3-Clause. See LICENSE.\n"
        ),
        manifests={
            "pyproject.toml": (
                "[project]\n"
                'name = "closing-time"\n'
                'version = "2.0.0"\n'
                'requires-python = ">=3.9"\n'
                "dependencies = [\n"
                '  "click==8.1.7",\n'
                '  "jinja2==3.1.4",\n'
                '  "tomlkit==0.13.2",\n'
                "]\n"
            )
        },
        license_spdx="BSD-3-Clause",
        license_name='BSD 3-Clause "New" or "Revised" License',
        size_kb=1_180,
        stars=903,
        forks=31,
        topics=("cli", "archival", "maintenance"),
        release=("2.0.0", "2026-01-09T12:00:00+00:00"),
        issues=IssueSpec(
            sample=3,
            unanswered=0,
            reported_open=3,
            newest_activity="2026-08-01T18:00:00+00:00",
            oldest="2025-10-10T00:00:00+00:00",
        ),
    ),
    Specimen(
        slug="monolith-erp",
        name="monolith-erp",
        description="Enterprise resource planning platform. Abandoned mid-rewrite.",
        failure_mode="huge abandoned monolith, abandoned during a migration",
        curator_note=(
            "Four thousand files, two frameworks halfway swapped, and a branch note "
            "where the migration stopped. The top of the scale exists for this "
            "shape of repository."
        ),
        display_order=80,
        last_commit_at="2018-02-21T17:48:33+00:00",
        created_at="2011-04-05T09:00:00+00:00",
        last_commit_message="WIP: migrate billing module to v2 API (do not merge)",
        languages={
            "JavaScript": 3_900_000,
            "TypeScript": 1_100_000,
            "Python": 890_000,
            "HTML": 640_000,
            "CSS": 210_000,
            "Shell": 24_000,
        },
        primary_language="JavaScript",
        tree=tuple(
            [
                "README.md",
                "package.json",
                "requirements.txt",
                "bower.json",
                ".travis.yml",
            ]
            + [f"legacy/modules/module_{i:03d}/index.js" for i in range(1, 220)]
            + [f"legacy/modules/module_{i:03d}/views.html" for i in range(1, 220)]
            + [f"v2/src/billing/{name}.ts" for name in (
                "invoice", "ledger", "tax", "dunning", "credit", "refund"
            )]
            + [f"v2/src/inventory/{name}.ts" for name in (
                "stock", "warehouse", "transfer", "count"
            )]
            + [f"server/api/handler_{i:02d}.py" for i in range(1, 41)]
            + ["vendor/jquery/jquery.min.js", "vendor/angular/angular.min.js"]
        ),
        readme=(
            "# monolith-erp\n\n"
            "Enterprise resource planning. Billing, inventory, procurement, payroll.\n\n"
            "## Status\n\n"
            "The v2 rewrite is in progress. `legacy/` is the running system; `v2/` is "
            "the replacement. Do not add features to `legacy/`.\n\n"
            "## Build\n\n"
            "    npm install && bower install && pip install -r requirements.txt\n\n"
            "See `legacy/README` for the old build. The v2 build is not documented "
            "yet.\n"
        ),
        manifests={
            "package.json": (
                "{\n"
                '  "name": "monolith-erp",\n'
                '  "dependencies": {\n'
                '    "angular": "1.5.11",\n'
                '    "request": "^2.81.0",\n'
                '    "moment": "^2.18.1",\n'
                '    "lodash": "^3.10.1",\n'
                '    "grunt": "^0.4.5",\n'
                '    "bower": "^1.8.0"\n'
                "  }\n"
                "}\n"
            ),
            "requirements.txt": "Django==1.8.19\nsouth==1.0.2\nMySQL-python==1.2.5\n",
        },
        license_spdx=None,
        license_name=None,
        size_kb=48_200,
        stars=131,
        forks=64,
        topics=("erp", "monolith"),
        issues=IssueSpec(
            sample=30,
            unanswered=26,
            reported_open=612,
            newest_activity="2020-05-05T05:05:00+00:00",
            oldest="2013-01-15T00:00:00+00:00",
        ),
    ),
)


def by_slug() -> dict[str, Specimen]:
    return {s.slug: s for s in SPECIMENS}


# --------------------------------------------------------------------------
# payload construction
# --------------------------------------------------------------------------
def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build_payloads(spec: Specimen) -> dict[str, Any]:
    """Turn a descriptor into the payload map `RawPayloads.from_mapping` reads.

    The shape is GitHub's, exactly: this is what makes the specimens exercise the
    real `normalize` and `detect` code paths rather than a parallel one.
    """
    repo: dict[str, Any] = {
        "name": spec.name,
        "full_name": f"{SPECIMEN_OWNER}/{spec.name}",
        "owner": {"login": SPECIMEN_OWNER},
        "html_url": f"https://github.com/{SPECIMEN_OWNER}/{spec.name}",
        "description": spec.description,
        "topics": list(spec.topics),
        "fork": spec.is_fork,
        "archived": spec.archived,
        "size": spec.size_kb,
        "stargazers_count": spec.stars,
        "forks_count": spec.forks,
        "open_issues_count": spec.issues.reported_open,
        "language": spec.primary_language,
        "default_branch": "main",
        "created_at": spec.created_at,
        "pushed_at": spec.last_commit_at,
        "license": (
            {"spdx_id": spec.license_spdx, "name": spec.license_name}
            if spec.license_spdx
            else None
        ),
    }

    # The single head-commit object, shaped as `fetch.resolve_head` stores it
    # (`data["head_commit"] = head.payload`), so `normalize` reads a specimen
    # exactly as it reads a live fetch. Storing the raw commits *list* here would
    # leave `normalize` unable to find the SHA, which is the bug this shape fixes.
    head_commit = {
        "sha": spec.commit_sha,
        "commit": {
            "message": spec.last_commit_message,
            "committer": {"date": spec.last_commit_at},
            "author": {"date": spec.last_commit_at},
        },
    }

    tree = {
        "sha": spec.commit_sha,
        "truncated": False,
        "tree": [
            {"path": path, "type": "blob", "mode": "100644", "size": 1_024}
            for path in spec.tree
        ],
    }

    payloads: dict[str, Any] = {
        "head_commit": head_commit,
        "repo": repo,
        "languages": dict(spec.languages),
        "tree": tree,
        "readme": (
            {"name": "README.md", "encoding": "base64", "content": _b64(spec.readme)}
            if spec.readme
            else None
        ),
        "latest_release": (
            {"tag_name": spec.release[0], "published_at": spec.release[1]}
            if spec.release
            else None
        ),
        "contents": {
            path: {"path": path, "encoding": "base64", "content": _b64(text)}
            for path, text in sorted(spec.manifests.items())
        },
        "open_issues": _issue_payload(spec.issues),
    }
    return {
        "payloads": payloads,
        "calls_used": 9,
        "tolerated": [] if spec.readme else ["readme:404"],
    }


def _issue_payload(issues: IssueSpec) -> list[dict[str, Any]]:
    """A sampled issue list whose comment counts match the described ratio."""
    out: list[dict[str, Any]] = []
    for index in range(issues.sample):
        unanswered = index < issues.unanswered
        out.append(
            {
                "number": index + 1,
                "title": f"Open issue {index + 1}",
                "comments": 0 if unanswered else 2,
                "created_at": issues.oldest,
                "updated_at": issues.newest_activity,
                "state": "open",
            }
        )
    return out
