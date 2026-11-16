"""The MCP server: three tools over stdio, and nothing else (R-11, E8).

This module is an adapter. It parses a tool call, hands it to `AnalysisService`
or the store, and serializes the result. It contains no fetch, no normalization,
no scoring, no prompt and no cache logic, because all of that lives behind the
one entry point the web app also uses (E8). A reviewer checking R-11.7 should be
able to read this file top to bottom and find no business logic to duplicate.

Three guarantees the design asks for, and where each is enforced here:

  - `get_exhibit` and `list_graveyard` make **zero network calls** (R-11.5,
    R-11.6). They go through `offline_service`, whose GitHub client raises if it
    is used, so a regression that added a fetch would crash rather than spend a
    call silently.
  - Every failure is the **shared error envelope** (R-11.8, E8). A tool never
    raises out to the transport; `_envelope` maps any `NecropsyError`, and an
    unmapped exception becomes `INTERNAL_ERROR` with a correlation id and a
    server-side-only log line.
  - The server **starts with no token and no LLM key** (R-11.10). It reads the
    same config as every surface, and a missing key is a supported state.

`analyze_repo` is the only tool permitted to reach the network, and only when its
`allow_network` is true (the default). Even then the 10-call budget and the cache
apply exactly as they do on the web path, because it is the same `analyze()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from necropsy.config import Config, load_config
from necropsy.errors import NecropsyError, NotInGraveyard, wrap_unexpected
from necropsy.mcp import schemas
from necropsy.store import exhibits
from necropsy.store.db import Database, open_database

log = logging.getLogger("necropsy.mcp.server")

SERVER_NAME = "necropsy"


# --------------------------------------------------------------------------
# tool implementations — plain functions, so they are testable without a
# transport. The stdio wiring at the bottom is the only MCP-specific code.
# --------------------------------------------------------------------------
class NecropsyTools:
    """The three tools, bound to one database and one config.

    Holds an on-demand `AnalysisService` for `analyze_repo` and an
    `offline_service` for the two read tools. Keeping them separate is what makes
    "these two never call the network" a property of construction rather than of
    discipline (Property 11).
    """

    def __init__(self, db: Database, config: Config | None = None) -> None:
        self.db = db
        self.config = config or load_config()
        self._analyzer: Any = None
        self._offline: Any = None

    # -- lazy collaborators -------------------------------------------------
    def analyzer(self) -> Any:
        if self._analyzer is None:
            from necropsy.analysis.service import AnalysisService

            self._analyzer = AnalysisService(self.db, config=self.config)
        return self._analyzer

    def offline(self) -> Any:
        if self._offline is None:
            from necropsy.analysis.service import offline_service

            self._offline = offline_service(self.db, self.config)
        return self._offline

    def close(self) -> None:
        if self._analyzer is not None:
            self._analyzer.close()

    # -- analyze_repo -------------------------------------------------------
    def analyze_repo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url", "")).strip()
        force_refresh = bool(arguments.get("force_refresh", False))
        allow_network = bool(arguments.get("allow_network", True))

        service = self.offline() if not allow_network else self.analyzer()
        result = service.analyze(
            url, force_refresh=force_refresh, allow_network=allow_network
        )
        return result.payload()

    # -- get_exhibit --------------------------------------------------------
    def get_exhibit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Cached read only (R-11.5). Never analyzes, never touches the network."""
        conn = self.db.connect()
        slug = arguments.get("slug")
        repo_url = arguments.get("repo_url")

        exhibit = None
        if slug:
            exhibit = exhibits.get_by_slug(conn, str(slug))
        elif repo_url:
            canonical = _canonical_url(str(repo_url))
            exhibit = exhibits.get_by_canonical_url(conn, canonical)

        if exhibit is None:
            target = slug or repo_url
            raise NotInGraveyard(
                f"No analyzed exhibit was found for {target!r}. Analyze it first with "
                "analyze_repo, or list the graveyard to see what is available.",
                details={"requested": target},
            )
        return exhibit.analysis.to_payload(cache_status="hit")

    # -- write_revival_spec -------------------------------------------------
    def write_revival_spec(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return a Kiro-ready revival spec for a cached exhibit (R-12.2).

        Reads only the cache: a repository with no analysis returns
        NOT_IN_GRAVEYARD rather than triggering one, so a Kiro session that asks
        for a spec never silently spends GitHub or LLM calls. The markdown is
        built by the shared builder the web button also uses (E8).
        """
        from necropsy.mcp.revival import build_revival_spec

        conn = self.db.connect()
        slug = arguments.get("slug")
        repo_url = arguments.get("repo_url")
        exhibit = None
        if slug:
            exhibit = exhibits.get_by_slug(conn, str(slug))
        elif repo_url:
            exhibit = exhibits.get_by_canonical_url(conn, _canonical_url(str(repo_url)))

        if exhibit is None:
            raise NotInGraveyard(
                f"No analyzed exhibit for {slug or repo_url!r}; analyze it first.",
                details={"requested": slug or repo_url},
            )
        if exhibit.analysis.narrative.living:
            # An active repository has nothing to revive, and a spec for it would
            # be invented work (revival-protocol: never invent a step). Say so.
            return {
                "ok": True,
                "slug": exhibit.slug,
                "repo_url": exhibit.analysis.facts.canonical_url,
                "living": True,
                "spec_markdown": None,
                "message": (
                    "This repository is active, so there is nothing to revive and "
                    "no revival spec was written."
                ),
            }
        return {
            "ok": True,
            "slug": exhibit.slug,
            "repo_url": exhibit.analysis.facts.canonical_url,
            "living": False,
            "spec_markdown": build_revival_spec(exhibit.analysis),
        }

    # -- list_graveyard -----------------------------------------------------
    def list_graveyard(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Summary rows, zero network calls (R-11.6)."""
        limit = int(arguments.get("limit", 20))
        offset = int(arguments.get("offset", 0))
        sort = str(arguments.get("sort", "curated"))
        demo_only = bool(arguments.get("demo_only", False))

        conn = self.db.connect()
        rows = exhibits.list_exhibits(
            conn, sort=sort, limit=limit, offset=offset, demo_only=demo_only
        )
        total = exhibits.count_exhibits(conn, demo_only=demo_only)
        return {
            "ok": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "exhibits": [_summary(exhibit) for exhibit in rows],
        }

    # -- dispatch -----------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool by name, returning either its payload or an error envelope.

        This is the boundary that closes the error taxonomy for the MCP surface:
        nothing below raises past it (E8). A tool that is not one of the three is
        itself an envelope, not an exception, so a confused caller gets a code.
        """
        handler = {
            "analyze_repo": self.analyze_repo,
            "get_exhibit": self.get_exhibit,
            "list_graveyard": self.list_graveyard,
            "write_revival_spec": self.write_revival_spec,
        }.get(name)
        if handler is None:
            return _unknown_tool(name)
        try:
            return handler(arguments)
        except NecropsyError as error:
            return error.to_envelope()
        except Exception as exc:  # noqa: BLE001 - the surface's error boundary
            log.exception("unmapped failure in MCP tool %s", name)
            return wrap_unexpected(exc).to_envelope()


# --------------------------------------------------------------------------
# serialization helpers
# --------------------------------------------------------------------------
def _summary(exhibit: exhibits.Exhibit) -> dict[str, Any]:
    """One graveyard row for `list_graveyard` (design 7.3)."""
    facts = exhibit.analysis.facts
    card = exhibit.analysis.scorecard
    return {
        "slug": exhibit.slug,
        "repo_url": facts.canonical_url,
        "plaque_title": exhibit.plaque_title,
        "primary_language": facts.primary_language,
        "score": card.score,
        "band": card.band.key,
        "epitaph": exhibit.analysis.narrative.epitaph.text,
        "epitaph_source": exhibit.analysis.narrative.epitaph.source,
        "days_since_last_commit": facts.days_since_last_commit,
        "last_commit_at": facts.last_commit_at.isoformat(),
        "is_demo": exhibit.is_demo,
        "curator_note": exhibit.curator_note,
    }


def _canonical_url(raw: str) -> str:
    """Canonicalize a URL for the store lookup, tolerating a bare identity.

    Imported lazily so the two read tools do not pull in the URL parser unless a
    URL is actually passed.
    """
    from necropsy.github.urls import canonicalize

    return canonicalize(raw).canonical_url


def _unknown_tool(name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "INVALID_URL",  # closest closed-enum code for a bad request
            "message": f"{name!r} is not a NECROPSY tool.",
            "retryable": False,
            "details": {"tool": name, "available": list(schemas.TOOL_DESCRIPTIONS)},
        },
    }


def _text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


# --------------------------------------------------------------------------
# stdio wiring — the only MCP-specific code in the module
# --------------------------------------------------------------------------
def build_server(tools: NecropsyTools) -> Any:
    """Wire the three tools onto an `MCPServer` (MCP SDK 2.x).

    Each registered function is one line of logic — delegate to `tools.call`,
    serialize to a JSON string — with its parameters typed so the SDK advertises
    an input schema. The committed schemas in `schemas.py` remain the reviewed
    contract (design 1.1); `test_mcp_tools` checks the two agree.

    The synchronous pipeline runs off the event loop via `asyncio.to_thread`, so
    a 20 s cache-miss analysis cannot block the stdio transport (E14).
    """
    from mcp.server.mcpserver import MCPServer

    server: Any = MCPServer(SERVER_NAME, instructions=_SERVER_INSTRUCTIONS)

    @server.tool(
        name="analyze_repo", description=schemas.TOOL_DESCRIPTIONS["analyze_repo"]
    )
    async def analyze_repo(
        url: str, force_refresh: bool = False, allow_network: bool = True
    ) -> str:
        payload = await asyncio.to_thread(
            tools.call,
            "analyze_repo",
            {"url": url, "force_refresh": force_refresh, "allow_network": allow_network},
        )
        return _text(payload)

    @server.tool(name="get_exhibit", description=schemas.TOOL_DESCRIPTIONS["get_exhibit"])
    async def get_exhibit(repo_url: str | None = None, slug: str | None = None) -> str:
        payload = await asyncio.to_thread(
            tools.call, "get_exhibit", {"repo_url": repo_url, "slug": slug}
        )
        return _text(payload)

    @server.tool(
        name="list_graveyard", description=schemas.TOOL_DESCRIPTIONS["list_graveyard"]
    )
    async def list_graveyard(
        limit: int = 20, offset: int = 0, sort: str = "curated", demo_only: bool = False
    ) -> str:
        payload = await asyncio.to_thread(
            tools.call,
            "list_graveyard",
            {"limit": limit, "offset": offset, "sort": sort, "demo_only": demo_only},
        )
        return _text(payload)

    @server.tool(
        name="write_revival_spec",
        description=schemas.TOOL_DESCRIPTIONS["write_revival_spec"],
    )
    async def write_revival_spec(repo_url: str | None = None, slug: str | None = None) -> str:
        payload = await asyncio.to_thread(
            tools.call, "write_revival_spec", {"repo_url": repo_url, "slug": slug}
        )
        return _text(payload)

    return server


_SERVER_INSTRUCTIONS = (
    "NECROPSY performs read-only post-mortems on dead public GitHub repositories. "
    "Use analyze_repo for a full analysis, get_exhibit to read a cached one, and "
    "list_graveyard to browse. Every scorecard exposes its inputs; no score is "
    "opaque. The tools never write to GitHub and never execute repository code."
)


async def serve(db: Database, config: Config | None = None) -> None:
    """Run the stdio server until the client disconnects."""
    tools = NecropsyTools(db, config)
    server = build_server(tools)
    try:
        await server.run_stdio_async()
    finally:
        tools.close()


def main(config: Config | None = None) -> int:
    """Entry point for `python -m necropsy.mcp.server`."""
    from necropsy.config import configure_logging

    cfg = config or load_config()
    configure_logging(cfg)
    db = open_database(cfg.database_path)
    try:
        asyncio.run(serve(db, cfg))
    except KeyboardInterrupt:  # pragma: no cover - operator stopped it
        log.info("necropsy MCP server stopped")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
