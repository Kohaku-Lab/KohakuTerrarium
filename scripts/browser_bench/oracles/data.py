"""Oracles for the data tasks: table, pagination, infinite scroll, compute."""

from decimal import Decimal

from .bot import Bot, OracleError, container_with, parse, ref, subtree
from .runs import oracle


def rows(text: str) -> list[tuple[list[str], list]]:
    """``(cell texts, row subtree)`` for every body row of every table in the snapshot."""
    nodes = parse(text)
    out = []
    for node in nodes:
        if node.role != "row":
            continue
        tree = subtree(nodes, node)
        cells = [n.label for n in tree if n.role == "cell"]
        if cells:
            out.append((cells, tree))
    return out


def money(text: str) -> Decimal:
    return Decimal(text.replace("$", "").replace(",", ""))


async def table_sort_act(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Filter by customer"),
            "text": "Harbor & Co",
        },
        {"kind": "click", "ref": ref(text, "columnheader", "Total")},
        {"kind": "click", "ref": ref(text, "columnheader", "Total")},
    )
    text = await bot.snap()
    candidates = [
        (money(cells[4]), tree)
        for cells, tree in rows(text)
        if cells[1] == "Harbor & Co" and cells[3] == "Delivered"
    ]
    if not candidates:
        raise OracleError("no delivered Harbor & Co order in the filtered table")
    _, tree = max(candidates, key=lambda c: c[0])
    await bot.act({"kind": "click", "ref": ref(tree, "button", "Refund")})


async def pagination(bot: Bot) -> None:
    text = await bot.go()
    for _ in range(8):
        try:
            card = container_with(
                text, "generic", "Copper Pour-Over Kettle", exact=True
            )
        except OracleError:
            await bot.act({"kind": "click", "ref": ref(text, "link", "Next ›")})
            text = await bot.snap()
            continue
        await bot.act({"kind": "click", "ref": ref(card, "button", "Save for later")})
        return
    raise OracleError("the kettle was not found on any page")


async def infinite_scroll(bot: Bot) -> None:
    text = await bot.go()
    for _ in range(10):
        nodes = parse(text)
        for node in nodes:
            if node.role != "article":
                continue
            tree = subtree(nodes, node)
            words = " ".join(n.label for n in tree).lower()
            if "marlow k." in words and "lighthouse restoration" in words:
                await bot.act({"kind": "click", "ref": ref(tree, "button", "Like")})
                return
        await bot.act({"kind": "scroll", "dy": 3000})
        text = await bot.snap()
    raise OracleError("the lighthouse post never loaded")


async def read_compute(bot: Bot) -> None:
    text = await bot.go()
    amounts = [money(cells[2]) for cells, _ in rows(text) if len(cells) == 3]
    if len(amounts) != 50:
        raise OracleError(
            f"expected 50 expense rows in the snapshot, read {len(amounts)}"
        )
    await bot.act(
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Report total"),
            "text": f"{sum(amounts):.2f}",
        },
        {"kind": "click", "ref": ref(text, "button", "Submit for approval")},
    )


RUNS = (
    oracle("table_sort_act", table_sort_act),
    oracle("pagination", pagination),
    oracle("infinite_scroll", infinite_scroll),
    oracle("read_compute", read_compute),
)
