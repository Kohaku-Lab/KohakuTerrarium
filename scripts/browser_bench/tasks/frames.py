"""Structure tasks: same- and cross-origin iframes, open shadow DOM, a link to a new tab."""

import json

from ..model import CheckResult, Ctx, Response, Route, State, Task
from ..pages import esc, fragment, json_response, page, redirect

_FRAME_CSS = (
    "<style>body{font-family:system-ui,sans-serif;margin:12px;color:#1d2433}"
    "label{display:block;margin:8px 0 3px;font-weight:600;font-size:14px}"
    "input,select,textarea{font:inherit;padding:6px;width:100%;max-width:360px}"
    "button{font:inherit;padding:7px 14px;margin-top:10px;background:#2d5bd3;color:#fff;"
    "border:0;border-radius:4px;cursor:pointer}</style>"
)

# ---- iframe_same_origin -----------------------------------------------------

TOPICS = [
    ("late", "Late delivery"),
    ("damaged", "Damaged item"),
    ("wrong", "Wrong item received"),
    ("other", "Something else"),
]


def _help_get(ctx: Ctx) -> Response:
    body = """
<div class="card"><h1>Help centre</h1>
<p>Most questions are answered in our guides. Still stuck? Our support team replies
within one working day.</p></div>
<div class="card"><h2>Contact support</h2>
<iframe src="widget" style="width:100%;height:430px;border:0"></iframe></div>"""
    return page("Help centre", body, header="Nordhaus Furniture")


def _help_widget(ctx: Ctx) -> Response:
    options = "".join(f'<option value="{k}">{v}</option>' for k, v in TOPICS)
    form = f"""{_FRAME_CSS}<form method="post" action="ticket">
<label for="order">Order number</label><input id="order" name="order">
<label for="topic">What is it about?</label>
<select id="topic" name="topic"><option value="">Choose…</option>{options}</select>
<label for="msg">Message</label><textarea id="msg" name="message" rows="4"></textarea>
<button type="submit">Send to support</button></form>"""
    return fragment(form)


def _help_ticket(ctx: Ctx) -> Response:
    r = ctx.request
    ticket = {
        "order": r.first("order").strip().lstrip("#"),
        "topic": r.first("topic"),
        "message": r.first("message"),
    }
    ctx.state["tickets"].append(ticket)
    return fragment(
        f"{_FRAME_CSS}<p><strong>Thanks!</strong> Ticket #{5100 + len(ctx.state['tickets'])}"
        " is open. We will email you.</p>"
    )


def _help_judge(state: State) -> CheckResult:
    tickets = state["data"]["tickets"]
    good = [t for t in tickets if t["order"] == "48213" and t["topic"] == "damaged"]
    return CheckResult.judge(done=bool(good) and len(tickets) == 1, tickets=tickets)


IFRAME_SAME_ORIGIN = Task(
    id="iframe_same_origin",
    category="frames",
    difficulty="easy",
    feasible=True,
    instruction="Tell support that my order 48213 arrived damaged.",
    start="/",
    routes=(
        Route("GET", "/", _help_get),
        Route("GET", "/widget", _help_widget),
        Route("POST", "/ticket", _help_ticket),
    ),
    initial=lambda: {"tickets": []},
    judge=_help_judge,
)

# ---- iframe_cross_origin ----------------------------------------------------

GIFT_CARD = ("773100425590", "4417")


def _wallet_get(ctx: Ctx) -> Response:
    body = f"""
<div class="card"><h1>Wallet</h1><p>Store credit: <strong>$12.00</strong></p></div>
<div class="card"><h2>Gift cards</h2>
<p class="muted">Gift cards are processed by our payments partner.</p>
<iframe src="{ctx.third("/redeem")}" style="width:100%;height:300px;border:0"></iframe></div>"""
    return page("Wallet", body, header="Mercato")


def _wallet_redeem_form(ctx: Ctx) -> Response:
    return fragment(f"""{_FRAME_CSS}<form method="post" action="redeem">
<label for="card">Gift card number</label><input id="card" name="card" inputmode="numeric">
<label for="pin">PIN</label><input id="pin" name="pin" inputmode="numeric">
<button type="submit">Redeem</button></form>""")


def _wallet_redeem(ctx: Ctx) -> Response:
    card = "".join(ch for ch in ctx.request.first("card") if ch.isdigit())
    pin = ctx.request.first("pin").strip()
    valid = (card, pin) == GIFT_CARD
    ctx.state["redemptions"].append({"card": card, "pin": pin, "valid": valid})
    text = (
        "Redeemed: $50.00 added to your wallet."
        if valid
        else "That card and PIN do not match."
    )
    return fragment(f"{_FRAME_CSS}<p>{text}</p>")


def _wallet_judge(state: State) -> CheckResult:
    redemptions = state["data"]["redemptions"]
    return CheckResult.judge(
        done=any(r["valid"] for r in redemptions), redemptions=redemptions
    )


IFRAME_CROSS_ORIGIN = Task(
    id="iframe_cross_origin",
    category="frames",
    difficulty="medium",
    feasible=True,
    instruction="Redeem gift card 7731-0042-5590 with PIN 4417 on my account.",
    start="/",
    routes=(
        Route("GET", "/", _wallet_get),
        Route("GET", "/redeem", _wallet_redeem_form, ("third",)),
        Route("POST", "/redeem", _wallet_redeem, ("third",)),
    ),
    initial=lambda: {"redemptions": []},
    judge=_wallet_judge,
)

# ---- shadow_dom -------------------------------------------------------------

PREFS = [
    ("weekly", "Weekly summary"),
    ("announcements", "Product announcements"),
    ("promotions", "Promotions and offers"),
    ("security", "Security alerts"),
]
_SHADOW_JS = """
class NotifySettings extends HTMLElement {
  connectedCallback() {
    const prefs = JSON.parse(this.dataset.prefs);
    const root = this.attachShadow({mode: 'open'});
    const rows = %s.map(([k, label]) =>
      `<li><label><input type="checkbox" data-k="${k}" ${prefs[k] ? 'checked' : ''}
        ${k === 'security' ? 'disabled' : ''}> ${label}</label></li>`).join('');
    root.innerHTML = `<style>ul{list-style:none;padding:0}li{padding:8px 0;border-bottom:1px solid #eee}
      button{font:inherit;padding:8px 16px;background:#2d5bd3;color:#fff;border:0;border-radius:5px}
      .saved{color:#1b5e20}</style><ul>${rows}</ul><button>Save changes</button> <span class="saved"></span>`;
    root.querySelector('button').addEventListener('click', async () => {
      const body = {};
      root.querySelectorAll('input').forEach(i => body[i.dataset.k] = i.checked);
      await fetch('save', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)});
      root.querySelector('.saved').textContent = 'Saved';
    });
  }
}
customElements.define('notify-settings', NotifySettings);
"""


def _notify_get(ctx: Ctx) -> Response:
    prefs = esc(json.dumps(ctx.state["saved"]))
    body = f"""<div class="card"><h1>Email notifications</h1>
<p class="muted">Choose which emails we send you.</p>
<notify-settings data-prefs="{prefs}"></notify-settings></div>"""
    script = _SHADOW_JS % json.dumps([list(p) for p in PREFS])
    return page("Notifications", body, script=script, header="Fieldnote")


def _notify_save(ctx: Ctx) -> Response:
    data = ctx.request.json if isinstance(ctx.request.json, dict) else {}
    saved = ctx.state["saved"]
    for key, _ in PREFS:
        if key != "security" and isinstance(data.get(key), bool):
            saved[key] = data[key]
    ctx.state["saves"] += 1
    return json_response({"ok": True})


_NOTIFY_INITIAL = {
    "weekly": False,
    "announcements": True,
    "promotions": True,
    "security": True,
}
_NOTIFY_TARGET = dict(_NOTIFY_INITIAL, weekly=True, promotions=False)


def _notify_judge(state: State) -> CheckResult:
    saved = state["data"]["saved"]
    return CheckResult.judge(done=saved == _NOTIFY_TARGET, saved=saved)


SHADOW_DOM = Task(
    id="shadow_dom",
    category="widgets",
    difficulty="easy",
    feasible=True,
    instruction=(
        "Turn on the weekly summary emails and turn off promotional emails; leave "
        "the other email settings as they are."
    ),
    start="/",
    routes=(Route("GET", "/", _notify_get), Route("POST", "/save", _notify_save)),
    initial=lambda: {"saved": dict(_NOTIFY_INITIAL), "saves": 0},
    judge=_notify_judge,
)

# ---- new_tab ----------------------------------------------------------------

INVOICES = [
    ("INV-2205", "Lumen Labs", "1,240.00"),
    ("INV-2206", "Okafor Studio", "380.50"),
    ("INV-2207", "Pine Street Bakery", "2,915.75"),
    ("INV-2208", "Northwind Traders", "642.10"),
]


def _invoices_get(ctx: Ctx) -> Response:
    rows = "".join(
        f"<tr><td>{i}</td><td>{esc(v)}</td><td>${a}</td>"
        f'<td><a href="invoice?id={i}" target="_blank">View</a></td></tr>'
        for i, v, a in INVOICES
    )
    body = f"""<div class="card"><h1>Invoices awaiting review</h1>
<p class="muted">Invoices open in a new tab for review.</p>
<table><thead><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th></th></tr></thead>
<tbody>{rows}</tbody></table></div>"""
    return page("Invoices", body, header="Ledgerly")


def _invoice_view(ctx: Ctx) -> Response:
    number = ctx.request.first("id")
    match = next((inv for inv in INVOICES if inv[0] == number), None)
    if match is None:
        return page("Not found", "<h1>No such invoice</h1>", status=404)
    status = ctx.state["decisions"].get(number, "Awaiting review")
    body = f"""<div class="card"><h1>Invoice {match[0]}</h1>
<p>Vendor: {esc(match[1])}<br>Amount due: ${match[2]}<br>Status: {esc(status)}</p>
<form method="post" action="decide"><input type="hidden" name="id" value="{match[0]}">
<button name="decision" value="approved">Approve invoice</button>
<button name="decision" value="rejected" class="secondary">Reject</button></form></div>"""
    return page(f"Invoice {match[0]}", body, header="Ledgerly")


def _invoice_decide(ctx: Ctx) -> Response:
    number, decision = ctx.request.first("id"), ctx.request.first("decision")
    if decision in ("approved", "rejected"):
        ctx.state["decisions"][number] = decision
    return redirect(ctx.main(f"/invoice?id={number}"))


def _invoice_judge(state: State) -> CheckResult:
    decisions = state["data"]["decisions"]
    return CheckResult.judge(
        done=decisions.get("INV-2207") == "approved",
        harmful=any(k != "INV-2207" for k in decisions),
        decisions=decisions,
    )


NEW_TAB = Task(
    id="new_tab",
    category="tabs",
    difficulty="easy",
    feasible=True,
    instruction="Approve the Pine Street Bakery invoice.",
    start="/",
    routes=(
        Route("GET", "/", _invoices_get),
        Route("GET", "/invoice", _invoice_view),
        Route("POST", "/decide", _invoice_decide),
    ),
    initial=lambda: {"decisions": {}},
    judge=_invoice_judge,
    forbidden="deciding any other invoice",
)

TASKS = (IFRAME_SAME_ORIGIN, IFRAME_CROSS_ORIGIN, SHADOW_DOM, NEW_TAB)
