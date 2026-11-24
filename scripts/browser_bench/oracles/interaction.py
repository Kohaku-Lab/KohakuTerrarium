"""Oracles for dialogs, upload, drag, hover menu and the keyboard widget."""

import time

from ..tasks.interaction import PLAYLIST_TARGET, UPLOAD_NAME
from .bot import Bot, OracleError, container_with, find, parse, ref
from .runs import oracle


async def dialogs(bot: Bot) -> None:
    text = await bot.go()
    row = container_with(text, "row", "Orion", exact=True)
    result = await bot.act(
        {"kind": "click", "ref": ref(row, "button", "Archive")}, ok=False
    )
    answers = [{"kind": "dialog", "accept": True}, {"kind": "dialog", "text": "Orion"}]
    for answer in answers:
        if result.meta.get("dialog") is None:
            raise OracleError(f"expected a pending dialog:\n{result.text}")
        started = time.monotonic()
        result = await bot.act(answer, ok=False)
        if result.meta.get("dialog") is not None and time.monotonic() - started > 3:
            bot.note(
                "answering a confirm that is followed at once by a prompt took "
                f"{time.monotonic() - started:.1f} s before the prompt was reported"
            )
    if result.meta.get("dialog") is not None or not result.meta.get("ok"):
        raise OracleError(f"the dialogs did not finish:\n{result.text}")


async def file_upload(bot: Bot) -> None:
    text = await bot.go()
    path = bot.files[UPLOAD_NAME]
    result = await bot.act(
        {"kind": "upload", "ref": ref(text, "button", "Choose document"), "files": [0]},
        files=[path],
    )
    if "file dialog" not in result.text:
        bot.note("upload on the styled button did not report the file-chooser path")
    text = await bot.snap()
    await bot.act({"kind": "click", "ref": ref(text, "button", "Submit for review")})


def _centre(box: str, *, top: bool = False) -> tuple[float, float]:
    x, y, w, h = (float(v) for v in box.split(","))
    return x + w / 2, y + (h / 4 if top else h / 2)


async def drag_reorder(bot: Bot) -> None:
    await bot.go()
    for index, title in enumerate(PLAYLIST_TARGET):
        nodes = [n for n in parse(await bot.snap(boxes=True)) if n.role == "listitem"]
        if title in nodes[index].label:
            continue
        moving = next(n for n in nodes if title in n.label)
        if "box" not in moving.attrs or "box" not in nodes[index].attrs:
            raise OracleError("snapshot with boxes did not give list items a box")
        x, y = _centre(moving.attrs["box"])
        to_x, to_y = _centre(nodes[index].attrs["box"], top=True)
        result = await bot.act(
            {"kind": "drag", "x": x, "y": y, "to_x": to_x, "to_y": to_y}
        )
        if "(moved)" not in result.text:
            bot.note("act did not mark reordered list items as (moved)")
    text = await bot.snap()
    order = [n.label.lstrip("⋮ ") for n in parse(text) if n.role == "listitem"]
    if order != PLAYLIST_TARGET:
        raise OracleError(f"the list order after dragging is {order}")
    await bot.act({"kind": "click", "ref": ref(text, "button", "Save order")})


async def hover_menu(bot: Bot) -> None:
    text = await bot.go()
    if any(n.label == "Billing" for n in parse(text)):
        raise OracleError("the billing link is visible before hovering")
    await bot.act({"kind": "hover", "ref": ref(text, "link", "Account ▾")})
    text = await bot.snap()
    await bot.act({"kind": "click", "ref": ref(text, "link", "Billing")})
    text = await bot.snap()
    button = find(text, "button", contains="Switch to annual billing")
    await bot.act({"kind": "click", "ref": button.ref})


async def keyboard_widget(bot: Bot) -> None:
    text = await bot.go()
    slider = ref(text, "slider", "Night-time")
    await bot.act({"kind": "click", "ref": slider})
    if "17.5" in await bot.snap():
        raise OracleError("a click alone changed the slider")
    await bot.act(
        *({"kind": "press", "ref": slider, "key": "ArrowLeft"} for _ in range(5)),
    )
    text = await bot.snap()
    if "17.5 °C" not in text:
        raise OracleError(f"the slider does not show 17.5 °C:\n{text}")
    await bot.act({"kind": "click", "ref": ref(text, "button", "Save schedule")})


RUNS = (
    oracle("dialogs", dialogs),
    oracle("file_upload", file_upload),
    oracle("drag_reorder", drag_reorder),
    oracle("hover_menu", hover_menu),
    oracle("keyboard_widget", keyboard_widget),
)
