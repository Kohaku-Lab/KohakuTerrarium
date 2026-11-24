"""Oracles for dialogs, upload, drag, hover menu and the keyboard widget."""

import json
import time

from ..tasks.interaction import PLAYLIST_TARGET, UPLOAD_NAME
from .bot import Bot, OracleError, container_with, find, parse, ref
from .runs import oracle

_DROP_JS = """(list) => {
  const order = %s;
  for (let i = 0; i < order.length; i++) {
    const items = [...list.children];
    const moving = items.find(li => li.textContent.includes(order[i]));
    const target = items[i];
    if (moving === target) continue;
    const data = new DataTransfer();
    moving.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: data}));
    const box = target.getBoundingClientRect();
    const at = {bubbles: true, cancelable: true, dataTransfer: data, clientX: box.left + 5, clientY: box.top + 2};
    target.dispatchEvent(new DragEvent('dragover', at));
    target.dispatchEvent(new DragEvent('drop', at));
    moving.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: data}));
  }
  return [...list.children].map(li => li.textContent.replace(/^\\W+/, ''));
}"""


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


async def drag_reorder(bot: Bot) -> None:
    text = await bot.go()
    items = [n for n in parse(text) if n.role == "listitem"]
    if items and all(n.ref for n in items):
        for index, title in enumerate(PLAYLIST_TARGET):
            text = await bot.snap()
            nodes = [n for n in parse(text) if n.role == "listitem"]
            if title in nodes[index].label:
                continue
            moving = next(n for n in nodes if title in n.label)
            await bot.act({"kind": "drag", "ref": moving.ref, "to": nodes[index].ref})
    else:
        bot.note(
            "draggable list items (cursor: grab, no ARIA role) carry no ref, and drag "
            "has no x/y form, so drag-to-reorder is impossible through act; the oracle "
            "falls back to dispatching HTML5 drag events through evaluate"
        )
        order = await bot.js(
            _DROP_JS % json.dumps(PLAYLIST_TARGET), element=ref(text, "list")
        )
        if order != PLAYLIST_TARGET:
            raise OracleError(f"the list order after the drag events is {order}")
        result = await bot.act(
            {"kind": "click", "ref": ref(text, "button", "Save order")}
        )
        if "No change in the page snapshot" in result.text:
            bot.note(
                "act reports 'No change in the page snapshot' after list items were reordered"
            )
        return
    text = await bot.snap()
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
