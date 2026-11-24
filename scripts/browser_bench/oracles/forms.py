"""Oracles for the form tasks."""

from .bot import Bot, OracleError, find, ref
from .runs import oracle


async def form_validation(bot: Bot) -> None:
    text = await bot.go()
    naive = {
        "Full name": "Dana Whitfield",
        "Email": "dana.whitfield@example.org",
        "Date of birth": "March 4, 1990",
        "Card number": "ab12 cd34",
    }
    await bot.act(
        *(
            {"kind": "type", "ref": ref(text, "textbox", k), "text": v}
            for k, v in naive.items()
        ),
        {"kind": "check", "ref": ref(text, "checkbox")},
        {"kind": "click", "ref": ref(text, "button", "Renew card")},
    )
    if not any(a["errors"] for a in bot.state()["data"]["attempts"]):
        raise OracleError("the naive submission was not rejected")
    text = await bot.snap()
    fixed = dict(naive, **{"Date of birth": "04/03/1990", "Card number": "AB12CD34"})
    await bot.act(
        *(
            {"kind": "type", "ref": ref(text, "textbox", k), "text": v}
            for k, v in fixed.items()
        ),
        {"kind": "check", "ref": ref(text, "checkbox")},
        {"kind": "click", "ref": ref(text, "button", "Renew card")},
    )


async def select_combobox(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {
            "kind": "select",
            "ref": ref(text, "combobox", "Delivery country"),
            "values": ["Portugal"],
        },
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Preferred courier"),
            "text": "Swift Parcel",
        },
    )
    text = await bot.snap()
    choice = find(text, "listitem", "Swift Parcel Express")
    await bot.act(
        {"kind": "click", "ref": choice.ref or ref(text, None, "Swift Parcel Express")}
    )
    text = await bot.snap()
    await bot.act({"kind": "click", "ref": ref(text, "button", "Save preferences")})


async def date_pickers(bot: Bot) -> None:
    text = await bot.go()
    await bot.act(
        {
            "kind": "type",
            "ref": ref(text, "textbox", "Visit date"),
            "text": "2026-12-15",
        },
        {"kind": "click", "ref": ref(text, "textbox", "Follow-up call")},
    )
    for _ in range(3):
        text = await bot.snap()
        if "January 2027" in text:
            break
        await bot.act({"kind": "click", "ref": ref(text, "button", "›")})
    else:
        raise OracleError("the calendar never showed January 2027")
    await bot.act({"kind": "click", "ref": ref(text, "button", "8")})
    text = await bot.snap()
    await bot.act({"kind": "click", "ref": ref(text, "button", "Confirm booking")})


RUNS = (
    oracle("form_validation", form_validation),
    oracle("select_combobox", select_combobox),
    oracle("date_pickers", date_pickers),
)
