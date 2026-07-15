#!/usr/bin/env python
"""Batch grader — one creature per submission folder, one shared engine.

The canonical batch pattern: N work folders, one Terrarium engine, one
creature per folder with its own working dir + session file. TurnResult
makes failures, timeouts, and token cost first-class.
"""

import asyncio
from pathlib import Path

from kohakuterrarium import Terrarium

SUBMISSIONS = Path(__file__).resolve().parent / "submissions"
AGENT = (
    "@kt-biome/creatures/general"  # Config paths and installed package refs both work.
)
PROMPT = "Read ./report.pdf and ./src, then write score.json with your grade."
MODEL = "default"  # Profiles resolve before the creature is added.
CONCURRENCY = 8


async def grade_one(engine: Terrarium, folder: Path, gate: asyncio.Semaphore):
    async with gate:
        creature = await engine.add_creature(
            AGENT,
            llm=MODEL,
            pwd=folder,  # Working directories remain isolated per creature.
            session=folder
            / "scoring_session.kohakutr",  # Persist each submission independently.
        )
        try:
            result = await creature.run(PROMPT, timeout=1800, raise_on_error=False)
            return folder.name, result
        finally:
            await engine.remove_creature(creature)


async def main() -> None:
    folders = [
        d
        for d in sorted(SUBMISSIONS.iterdir())
        if d.is_dir() and not (d / "score.json").exists()
    ]
    gate = asyncio.Semaphore(CONCURRENCY)
    async with Terrarium() as engine:
        results = await asyncio.gather(*(grade_one(engine, d, gate) for d in folders))
    for name, r in results:
        tokens = (r.usage or {}).get("total_tokens", "?")
        flag = "ok" if r.ok else "!!"
        print(f"  {flag} {r.status:<11} {r.duration_s:>6.0f}s {tokens:>8} tok  {name}")
    print(f"\n{sum(1 for _n, r in results if r.ok)}/{len(results)} ok.")


if __name__ == "__main__":
    asyncio.run(main())
