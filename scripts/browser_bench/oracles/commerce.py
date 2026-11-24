"""Oracles for the overlay, checkout, delayed-content and download tasks."""

import csv
import io
import re
from pathlib import Path

from .bot import Bot, OracleError, container_with, find, parse, ref
from .runs import oracle

_DOWNLOADED = re.compile(r"^Downloaded: .+? → (?P<path>.+)$", re.MULTILINE)


async def overlay_banner(bot: Bot) -> None:
    text = await bot.go()
    size, add = ref(text, "combobox", "EU size"), ref(text, "button", "Add to cart")
    blocked = await bot.act(
        {"kind": "select", "ref": size, "values": ["42"]},
        {"kind": "click", "ref": add},
        ok=False,
    )
    if blocked.meta.get("ok"):
        raise OracleError("Add to cart worked under the newsletter modal")
    await bot.act({"kind": "click", "ref": ref(text, "button", "No thanks")})
    text = await bot.snap()
    covered = await bot.act(
        {"kind": "click", "ref": ref(text, "button", "Add to cart")}, ok=False
    )
    if covered.meta.get("ok"):
        raise OracleError("Add to cart worked under the cookie banner")
    if "another element covers it" not in covered.text:
        bot.note("a click on a covered button did not say what covers it")
    if bot.state()["data"]["cart"]:
        raise OracleError("an intercepted click still added to the cart")
    await bot.act({"kind": "click", "ref": ref(text, "button", "Essential only")})
    text = await bot.snap()
    await bot.act(
        {"kind": "select", "ref": ref(text, "combobox", "EU size"), "values": ["42"]},
        {"kind": "click", "ref": ref(text, "button", "Add to cart")},
    )


async def checkout_distractors(bot: Bot) -> None:
    text = await bot.go()
    standard = find(text, "radio", contains="Standard")
    protection = find(text, "checkbox", contains="accident protection")
    if "checked" not in protection.attrs:
        raise OracleError("the protection plan is not pre-ticked")
    await bot.act(
        {"kind": "check", "ref": standard.ref, "checked": True},
        {"kind": "check", "ref": protection.ref, "checked": False},
        {"kind": "click", "ref": ref(text, "button", "Place order")},
    )


async def delayed_content(bot: Bot) -> None:
    text = await bot.go()
    if any(n.label == "Claim reward" for n in parse(text)):
        raise OracleError("the reward button was there before the delay")
    await bot.act({"kind": "wait", "text": "Claim reward"})
    text = await bot.snap()
    await bot.act({"kind": "click", "ref": ref(text, "button", "Claim reward")})


async def download_value(bot: Bot) -> None:
    text = await bot.go()
    row = container_with(text, "row", "March 2026", exact=True)
    result = await bot.act({"kind": "click", "ref": ref(row, "link", "Download CSV")})
    match = _DOWNLOADED.search(result.text)
    if match is None:
        raise OracleError(f"no download reported:\n{result.text}")
    path = Path(match.group("path").strip())
    if path.parent != bot.downloads:
        bot.note(f"download saved outside the session downloads_dir: {path}")
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    closing = next(r[-1] for r in rows if r and r[0] == "Closing balance")
    if closing != bot.task.answer:
        raise OracleError(
            f"the statement says {closing}, the task expects {bot.task.answer}"
        )


RUNS = (
    oracle("overlay_banner", overlay_banner),
    oracle("checkout_distractors", checkout_distractors),
    oracle("delayed_content", delayed_content),
    oracle("download_value", download_value),
)
