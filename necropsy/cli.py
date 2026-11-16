"""Command line entry points (`python -m necropsy.cli <command>`).

Thin adapters, like every other surface: each command parses arguments, calls
into the package, and renders a result. No analysis logic lives here (E8).

    init-db           apply migrations, idempotently
    seed-graveyard    materialize the curated wall from committed fixtures
    analyze           analyze one repository and print its post-mortem
    list              print the graveyard wall as text
    health            print budget, cache and configuration state

`serve` and `mcp` are registered by the web and MCP packages when those land;
this module stays the single place a command is declared.

Secrets are never printed. `health` reports `token_present` as a boolean, which
is the most anything is allowed to learn about a token (E12).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from necropsy import ANALYSIS_SCHEMA_VERSION, __version__
from necropsy.config import Config, configure_logging, load_config
from necropsy.errors import NecropsyError
from necropsy.graveyard import manifest as manifest_module
from necropsy.graveyard.seed import refresh as seed_refresh
from necropsy.graveyard.seed import seed
from necropsy.store import analyses, exhibits, ledger
from necropsy.store.db import Database, open_database

log = logging.getLogger("necropsy.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="necropsy",
        description="Automated post-mortems for dead GitHub repositories.",
    )
    parser.add_argument("--version", action="version", version=f"necropsy {__version__}")
    parser.add_argument(
        "--database",
        default=None,
        help="SQLite path (defaults to DATABASE_PATH, then ./necropsy.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="apply database migrations (idempotent)")

    seed_parser = sub.add_parser(
        "seed-graveyard", help="load the curated graveyard from committed fixtures"
    )
    mode = seed_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="load from committed fixtures, making no network call (default)",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="validate fixtures against the manifest and change nothing",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="capture each manifest entry live from the GitHub API, rewrite its "
        "fixture as api_verified, then seed the wall from the captures",
    )

    analyze_parser = sub.add_parser("analyze", help="analyze one repository")
    analyze_parser.add_argument("url", help="GitHub URL or owner/name")
    analyze_parser.add_argument(
        "--offline",
        action="store_true",
        help="serve from cache only; never call GitHub or the LLM",
    )
    analyze_parser.add_argument(
        "--force-refresh", action="store_true", help="bypass the cache read"
    )
    analyze_parser.add_argument("--json", action="store_true", help="print the full payload")

    list_parser = sub.add_parser("list", help="print the graveyard wall")
    list_parser.add_argument(
        "--sort", choices=sorted(exhibits.SORTS), default="curated"
    )

    sub.add_parser("health", help="print budget, cache and configuration state")
    sub.add_parser("mcp", help="run the MCP server over stdio")

    serve_parser = sub.add_parser("serve", help="run the museum web app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=None)
    return parser


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_init_db(db: Database) -> int:
    applied = db.migrate()
    if applied:
        print(f"applied migration(s): {', '.join(str(v) for v in applied)}")
    else:
        print("database is already up to date")
    print(f"database: {db.dsn}")
    return 0


def cmd_seed_graveyard(db: Database, cfg: Config, args: argparse.Namespace) -> int:
    if args.refresh:
        if not cfg.github_token:
            print(
                "refresh needs GITHUB_TOKEN: it captures real payloads from the "
                "GitHub API. Unset, the wall stays as it is.",
                file=sys.stderr,
            )
            return 1
        report = seed_refresh(db, config=cfg)
        print(report.render())
        return report.exit_code

    report = seed(db, config=cfg, verify_only=bool(args.verify))
    print(report.render())
    return report.exit_code


def cmd_analyze(db: Database, cfg: Config, args: argparse.Namespace) -> int:
    # Imported here so `init-db` and `list` do not pay for the pipeline's imports.
    from necropsy.analysis.service import AnalysisService, offline_service

    service = (
        offline_service(db, cfg)
        if args.offline
        else AnalysisService(db, config=cfg)
    )
    try:
        result = service.analyze(
            args.url,
            force_refresh=bool(args.force_refresh),
            allow_network=not args.offline,
            on_stage=lambda stage: print(f"  ... {stage.replace('_', ' ')}", file=sys.stderr),
        )
    except NecropsyError as error:
        envelope = error.to_envelope()["error"]
        print(f"{envelope['code']}: {envelope['message']}", file=sys.stderr)
        print(f"  next: {envelope['next_action']}", file=sys.stderr)
        return 1
    finally:
        service.close()

    payload = result.payload()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    _print_exhibit(payload)
    return 0


def cmd_list(db: Database, args: argparse.Namespace) -> int:
    rows = exhibits.list_exhibits(db.connect(), sort=args.sort, limit=50)
    if not rows:
        print("The graveyard is empty. Run: python -m necropsy.cli seed-graveyard")
        return 0
    print(f"{len(rows)} exhibit(s), sorted by {args.sort}:\n")
    for exhibit in rows:
        card = exhibit.analysis.scorecard
        facts = exhibit.analysis.facts
        marker = "demo" if exhibit.is_demo else "user"
        print(f"  [{marker}] {exhibit.slug:<22} {card.score:>3}  {card.band.label}")
        print(f"         {facts.primary_language or 'unknown'}, "
              f"died {facts.days_since_last_commit} days before capture")
        print(f"         {exhibit.analysis.narrative.epitaph.text}")
        print()
    return 0


def cmd_mcp(db: Database, cfg: Config) -> int:
    """Run the stdio MCP server. Imported here so other commands pay nothing."""
    import asyncio

    from necropsy.mcp.server import serve

    log.info("starting the NECROPSY MCP server over stdio")
    try:
        asyncio.run(serve(db, cfg))
    except KeyboardInterrupt:  # pragma: no cover - operator stopped it
        log.info("necropsy MCP server stopped")
    return 0


def cmd_serve(db: Database, cfg: Config, args: argparse.Namespace) -> int:
    """Run the museum web app with Waitress (works on Windows, unlike Gunicorn)."""
    from waitress import serve as waitress_serve

    from necropsy.web.app import create_app

    port = args.port or cfg.port
    app = create_app(db=db, config=cfg)
    count = exhibits.count_exhibits(db.connect())
    if count == 0:
        log.warning(
            "the graveyard is empty; run seed-graveyard so the wall has exhibits"
        )
    log.info("NECROPSY museum open at http://%s:%d (%d exhibits)", args.host, port, count)
    waitress_serve(app, host=args.host, port=port, threads=6)
    return 0


def cmd_health(db: Database, cfg: Config) -> int:
    conn = db.connect()
    state = ledger.latest(conn)
    payload = {
        "version": __version__,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "database_path": db.dsn,
        "cache_entries": analyses.count_analyses(conn),
        "exhibits": exhibits.count_exhibits(conn),
        "demo_exhibits": exhibits.count_exhibits(conn, demo_only=True),
        "token_present": cfg.token_present,
        "llm_enabled": cfg.llm_enabled,
        "rate_limit_remaining": state.remaining if state else None,
        "rate_limit_limit": state.limit if state else None,
        "reset_at": state.reset_at if state else None,
    }
    print(json.dumps(payload, indent=2))
    return 0


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _c(text: object) -> str:
    """Render arbitrary repository text for the console encoding.

    Real-world READMEs carry zero-width spaces and emoji; cp1252 terminals
    would raise UnicodeEncodeError on them.
    """
    s = str(text)
    try:
        s.encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        s = s.encode(sys.stdout.encoding or "ascii", "replace").decode(
            sys.stdout.encoding or "ascii", "ignore"
        )
    return s


def _print_exhibit(payload: dict[str, object]) -> None:
    """A plain-text plaque. The same seven sections the web exhibit renders."""
    repo = payload["repo"]
    assert isinstance(repo, dict)
    card = payload["scorecard"]
    assert isinstance(card, dict)
    death = payload["time_of_death"]
    assert isinstance(death, dict)
    cause = payload["cause_of_death"]
    assert isinstance(cause, dict)
    epitaph = payload["epitaph"]
    assert isinstance(epitaph, dict)
    built = payload["what_was_built"]
    assert isinstance(built, dict)

    print()
    print(f"  {repo['owner']}/{repo['name']}  ({payload['cache_status']})")
    print(f"  {'-' * 68}")
    print(f"  {_c(epitaph['text'])}")
    print(f"  [epitaph source: {epitaph['source']}]")
    print()
    print(f"  {_c(built['summary'])}")
    print(
        f"  {built['primary_language'] or 'unknown'} · "
        f"~{built['loc_estimate']:,} lines (estimate, {built['loc_confidence']} confidence) · "
        f"{built['file_count']} files"
    )
    print(
        f"  last commit {death['last_commit_at']} "
        f"({death['days_since_last_commit']} days before capture)"
    )
    print()
    print(f"  CAUSE OF DEATH  [{cause['source']}]")
    print(f"  {_c(cause['headline'])}")
    print(f"  {_c(cause['narrative'])}")
    evidence = cause.get("evidence") or []
    assert isinstance(evidence, list)
    for item in evidence:
        print(f"    - {item['fact_key']} = {item['fact_value']}")
        print(f"      {_c(item['claim'])}")
    print()
    print(f"  REVIVAL DIFFICULTY  {card['score']}/100 — {card['band']['label']}")
    for factor in card["factors"]:
        bar = "#" * int(round(factor["normalized"] * 20))
        print(
            f"    {factor['label']:<24} {bar:<20} "
            f"{factor['contribution']:>5.1f} / {factor['weight']:<3} "
            f"({factor['confidence']})"
        )
    print(f"    {'sum of contributions':<24} {'':<20} {card['total_unrounded']:>5.1f} / 100")
    print(f"    weights: {card['weights_checksum']}")
    if card["data_gaps"]:
        print(f"    data gaps: {', '.join(card['data_gaps'])}")
    print()
    print("  RESURRECTION PLAN")
    plan = payload["resurrection_plan"]
    assert isinstance(plan, list)
    for step in plan:
        print(f"    {step['order']}. {_c(step['title'])}  [{step['estimated_effort']}]")
        print(f"       {_c(step['detail'])}")
        print(f"       addresses: {step['addresses_factor']}")
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    print()
    print(
        f"  as of {payload['as_of']} · {provenance['github_calls_used']} GitHub call(s) · "
        f"llm: {provenance['llm_status']} · {provenance['deprecation_table_version']}"
    )
    print()


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()
    configure_logging(cfg)

    path = Path(args.database) if args.database else Path(cfg.database_path)
    db = open_database(path)
    try:
        if args.command == "init-db":
            return cmd_init_db(db)
        if args.command == "seed-graveyard":
            return cmd_seed_graveyard(db, cfg, args)
        if args.command == "analyze":
            return cmd_analyze(db, cfg, args)
        if args.command == "list":
            return cmd_list(db, args)
        if args.command == "health":
            return cmd_health(db, cfg)
        if args.command == "mcp":
            return cmd_mcp(db, cfg)
        if args.command == "serve":
            return cmd_serve(db, cfg, args)
        raise AssertionError(f"unhandled command {args.command!r}")
    except manifest_module.ManifestError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
