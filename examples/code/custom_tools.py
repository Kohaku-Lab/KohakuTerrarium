"""
Custom tools from plain functions — no config files required.

``@kt.tool`` turns a function into an agent tool (schema derived from
the type hints, description from the docstring). Inject tools at build
time with ``tools=[...]``, or live with ``agent.add_tool`` — the system
prompt updates either way. Afterwards, ``SessionReader`` shows what the
agent actually did with them.
"""

import asyncio
from pathlib import Path

import kohakuterrarium as kt

INVENTORY = {"moss": 12, "fern": 3, "springtail culture": 1}


@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    count = INVENTORY.get(item.lower())
    return f"{item}: {count} in stock" if count is not None else f"{item}: not found"


@kt.tool
async def reserve(item: str, quantity: int) -> str:
    """Reserve a quantity of an item for the customer."""
    have = INVENTORY.get(item.lower(), 0)
    if have < quantity:
        return f"cannot reserve {quantity} x {item}: only {have} left"
    INVENTORY[item.lower()] = have - quantity
    return f"reserved {quantity} x {item}"


async def main() -> None:
    session_file = Path("shop_session.kohakutr")
    async with kt.Terrarium() as engine:
        clerk = await engine.add_creature(
            "@kt-biome/creatures/general",
            tools=[check_stock, reserve],
            session=session_file,
        )
        result = await clerk.run(
            "A customer wants two ferns and some moss. Check stock and "
            "reserve what's available, then summarize."
        )
        print(result.text)
        print(f"\n[tools used: {[t.detail for t in result.tool_calls]}]")

    # SessionReader inspects the persisted run without a live engine or provider.
    with kt.SessionReader(session_file) as reader:
        print(f"\nSession {reader.meta.get('session_id')} replay:")
        for turn in reader.turns():
            tools_used = [tc["name"] for tc in turn.tool_calls]
            print(f"  - tools {tools_used}: {turn.assistant_text[:60]}...")


if __name__ == "__main__":
    asyncio.run(main())
