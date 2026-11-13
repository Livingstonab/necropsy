"""A stdio round-trip against the NECROPSY MCP server, for manual verification.

Spawns `python -m necropsy.cli mcp` as a subprocess, speaks MCP over its stdio,
lists the tools, calls `list_graveyard`, and calls `get_exhibit` on the first
result. Prints a short summary. This is an operator smoke test, not part of the
pytest suite (the suite drives the tools in-process; see test_mcp_tools.py).

    python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> int:
    from mcp.client.stdio import stdio_client

    from mcp import ClientSession, StdioServerParameters

    env = dict(os.environ)
    env.setdefault("DATABASE_PATH", str(ROOT / "necropsy.db"))

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "necropsy.cli", "mcp"],
        env=env,
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        names = sorted(t.name for t in tools.tools)
        print(f"tools: {names}")
        assert names == ["analyze_repo", "get_exhibit", "list_graveyard"], names

        listed = await session.call_tool("list_graveyard", {"sort": "hardest"})
        payload = json.loads(listed.content[0].text)
        exhibits = payload["exhibits"]
        print(f"list_graveyard: {payload['total']} exhibits, hardest first")
        for exhibit in exhibits[:3]:
            print(f"  {exhibit['score']:>3}  {exhibit['slug']}  — {exhibit['epitaph']}")

        first = exhibits[0]["slug"]
        got = await session.call_tool("get_exhibit", {"slug": first})
        exhibit_payload = json.loads(got.content[0].text)
        card = exhibit_payload["scorecard"]
        print(
            f"get_exhibit({first}): score {card['score']}, "
            f"{len(card['factors'])} factors, "
            f"{len(exhibit_payload['resurrection_plan'])} plan steps"
        )
        assert card["score"] == round(card["total_unrounded"])
    print("MCP stdio round-trip OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
