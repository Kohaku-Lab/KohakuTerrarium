"""Oracles for the iframe, shadow DOM and new-tab tasks."""

import asyncio

from .bot import Bot, OracleError, container_with, ref
from .runs import oracle


async def iframe_same_origin(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {"kind": "type", "ref": ref(text, "textbox", "Order number"), "text": "48213"},
        {
            "kind": "select",
            "ref": ref(text, "combobox", "What is it about?"),
            "values": ["Damaged item"],
        },
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Message"),
            "text": "My order 48213 arrived damaged.",
        },
        {"kind": "click", "ref": ref(text, "button", "Send to support")},
    )


async def iframe_cross_origin(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Gift card number"),
            "text": "7731-0042-5590",
        },
        {"kind": "type", "ref": ref(text, "textbox", "PIN"), "text": "4417"},
        {"kind": "click", "ref": ref(text, "button", "Redeem")},
    )
    text = await bot.snap()
    if "Redeemed" not in text:
        raise OracleError(f"the frame did not confirm the redemption:\n{text}")


async def shadow_dom(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {
            "kind": "check",
            "ref": ref(text, "checkbox", "Weekly summary"),
            "checked": True,
        },
        {
            "kind": "check",
            "ref": ref(text, "checkbox", "Promotions and offers"),
            "checked": False,
        },
        {"kind": "click", "ref": ref(text, "button", "Save changes")},
    )


async def new_tab(bot: Bot) -> None:
    text = await bot.go()
    row = container_with(text, "row", "Pine Street Bakery", exact=True)
    result = await bot.act({"kind": "click", "ref": ref(row, "link", "View")})
    if "t2" not in result.text:
        bot.note(
            "clicking a target=_blank link returned before the new tab registered: "
            "the act result does not mention the tab, so a model must poll tabs"
        )
        for _ in range(50):
            if "t2" in await bot.tabs():
                break
            await asyncio.sleep(0.1)
        else:
            raise OracleError("the invoice never opened in a new tab")
    text = await bot.snap()
    if not text.startswith("[t2]"):
        raise OracleError(f"the active tab is not the new one:\n{text[:300]}")
    await bot.act({"kind": "click", "ref": ref(text, "button", "Approve invoice")})


RUNS = (
    oracle("iframe_same_origin", iframe_same_origin),
    oracle("iframe_cross_origin", iframe_cross_origin),
    oracle("shadow_dom", shadow_dom),
    oracle("new_tab", new_tab),
)
