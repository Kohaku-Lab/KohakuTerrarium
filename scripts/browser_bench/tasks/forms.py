"""Form tasks: server-side validation, a native select with a custom combobox, date pickers."""

import re

from ..model import CheckResult, Ctx, Response, Route, State, Task
from ..pages import esc, page, redirect

# ---- form_validation --------------------------------------------------------

_DOB = re.compile(r"\d{2}/\d{2}/\d{4}")
_CARD = re.compile(r"[A-Z0-9]{8}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")


def _renew_form(values: dict[str, str], errors: list[str]) -> Response:
    problems = "".join(f"<li>{esc(e)}</li>" for e in errors)
    banner = f'<div class="error"><ul>{problems}</ul></div>' if errors else ""
    v = {k: esc(values.get(k, "")) for k in ("name", "email", "dob", "card")}
    body = f"""
<div class="card"><h1>Library card renewal</h1>
<p class="muted">Renew your Riverside Public Library card online. All fields are required.</p>
{banner}
<form method="post" action="submit">
<label for="f-name">Full name</label><input id="f-name" name="name" value="{v["name"]}">
<label for="f-email">Email</label><input id="f-email" name="email" value="{v["email"]}">
<label for="f-dob">Date of birth</label><input id="f-dob" name="dob" value="{v["dob"]}">
<p class="muted">Enter the date of birth as DD/MM/YYYY (day first).</p>
<label for="f-card">Card number</label><input id="f-card" name="card" value="{v["card"]}">
<p class="muted">Card numbers are 8 characters: capital letters and digits only, no spaces.</p>
<p><input type="checkbox" id="f-terms" name="terms" value="yes" style="min-width:0">
<span>I agree to the borrowing terms</span></p>
<p><button type="submit">Renew card</button></p>
</form></div>"""
    return page("Library card renewal", body, header="Riverside Public Library")


def _renew_get(ctx: Ctx) -> Response:
    return _renew_form({}, [])


def _renew_post(ctx: Ctx) -> Response:
    r = ctx.request
    values = {k: r.first(k).strip() for k in ("name", "email", "dob", "card")}
    errors = []
    if not values["name"]:
        errors.append("Please enter your full name.")
    if not _EMAIL.fullmatch(values["email"]):
        errors.append("Please enter a valid email address.")
    if not _DOB.fullmatch(values["dob"]):
        errors.append("Date of birth must be in DD/MM/YYYY format.")
    if not _CARD.fullmatch(values["card"]):
        errors.append("Card number must be 8 capital letters or digits.")
    if r.first("terms") != "yes":
        errors.append("You must agree to the borrowing terms.")
    ctx.state["attempts"].append({"values": values, "errors": errors})
    if errors:
        return _renew_form(values, errors)
    ctx.state["renewals"].append(values)
    return redirect(ctx.main("/done"))


def _renew_done(ctx: Ctx) -> Response:
    body = '<div class="card ok"><h1>Renewal received</h1><p>Your card is renewed for another year.</p></div>'
    return page("Renewal received", body, header="Riverside Public Library")


RENEW_TARGET = {
    "name": "dana whitfield",
    "email": "dana.whitfield@example.org",
    "dob": "04/03/1990",
    "card": "AB12CD34",
}


def _renew_judge(state: State) -> CheckResult:
    renewals = state["data"]["renewals"]

    def norm(entry: dict[str, str]) -> dict[str, str]:
        return {
            "name": " ".join(entry["name"].lower().split()),
            "email": entry["email"].lower(),
            "dob": entry["dob"],
            "card": entry["card"],
        }

    matching = [e for e in renewals if norm(e) == RENEW_TARGET]
    wrong = [e for e in renewals if norm(e) != RENEW_TARGET]
    return CheckResult.judge(
        done=bool(matching) and not wrong,
        renewals=len(renewals),
        wrong=wrong,
        rejected_attempts=sum(1 for a in state["data"]["attempts"] if a["errors"]),
    )


FORM_VALIDATION = Task(
    id="form_validation",
    category="forms",
    difficulty="medium",
    feasible=True,
    instruction=(
        "Renew my library card. My name is Dana Whitfield, my email is "
        "dana.whitfield@example.org, I was born on March 4, 1990, and my card "
        "number is ab12 cd34."
    ),
    start="/",
    routes=(
        Route("GET", "/", _renew_get),
        Route("POST", "/submit", _renew_post),
        Route("GET", "/done", _renew_done),
    ),
    initial=lambda: {"attempts": [], "renewals": []},
    judge=_renew_judge,
)

# ---- select_combobox --------------------------------------------------------

COUNTRIES = [
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("DK", "Denmark"),
    ("FR", "France"),
    ("DE", "Germany"),
    ("IE", "Ireland"),
    ("IT", "Italy"),
    ("NL", "Netherlands"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("ES", "Spain"),
    ("SE", "Sweden"),
]
COURIERS = [
    ("atlas", "Atlas Freight"),
    ("express-lane", "Express Lane Couriers"),
    ("parcelnet", "Parcelnet"),
    ("swift", "Swift Parcel"),
    ("swift-express", "Swift Parcel Express"),
    ("swiftpost", "SwiftPost International"),
    ("tern", "Tern Logistics"),
]
_COMBO_CSS = """
.combo{position:relative;width:320px}
.combo ul{position:absolute;left:0;right:0;top:100%;margin:2px 0 0;padding:4px 0;
list-style:none;background:#fff;border:1px solid #b9c0cc;border-radius:5px;z-index:5}
.combo li{padding:6px 10px;cursor:pointer}
.combo li:hover{background:#eef1f6}
"""
_COMBO_JS = """
const couriers = %s;
const box = document.getElementById('courier');
const list = document.getElementById('courier-list');
const hidden = document.getElementById('courier-id');
function render() {
  const q = box.value.trim().toLowerCase();
  list.innerHTML = '';
  hidden.value = '';
  const hits = couriers.filter(c => !q || c[1].toLowerCase().includes(q));
  for (const [id, name] of hits) {
    const li = document.createElement('li');
    li.textContent = name;
    li.addEventListener('click', () => {
      box.value = name; hidden.value = id; list.hidden = true;
    });
    list.appendChild(li);
  }
  list.hidden = hits.length === 0;
}
box.addEventListener('input', render);
box.addEventListener('focus', render);
document.addEventListener('click', e => {
  if (!e.target.closest('.combo')) list.hidden = true;
});
"""


def _prefs_page(state: State, message: str = "") -> Response:
    saved = state["saved"]
    options = "".join(
        f'<option value="{code}"{" selected" if saved["country"] == code else ""}>{name}</option>'
        for code, name in COUNTRIES
    )
    current = dict(COURIERS).get(saved["courier"], "")
    body = f"""
<div class="card"><h1>Delivery preferences</h1>{message}
<form method="post" action="save">
<label for="country">Delivery country</label>
<select id="country" name="country">{options}</select>
<label for="courier">Preferred courier</label>
<div class="combo"><input id="courier" autocomplete="off" placeholder="Start typing a courier"
 value="{esc(current)}">
<ul id="courier-list" hidden></ul></div>
<input type="hidden" id="courier-id" name="courier" value="{esc(saved["courier"])}">
<p><button type="submit">Save preferences</button></p>
</form></div>"""
    script = _COMBO_JS % [list(c) for c in COURIERS]
    return page(
        "Delivery preferences", body, css=_COMBO_CSS, script=script, header="ParcelPal"
    )


def _prefs_get(ctx: Ctx) -> Response:
    return _prefs_page(ctx.state)


def _prefs_save(ctx: Ctx) -> Response:
    country, courier = ctx.request.first("country"), ctx.request.first("courier")
    ctx.state["posts"].append({"country": country, "courier": courier})
    if courier not in dict(COURIERS):
        message = '<p class="error">Choose a courier from the list.</p>'
        return _prefs_page(ctx.state, message)
    ctx.state["saved"] = {"country": country, "courier": courier}
    return _prefs_page(ctx.state, '<p class="ok">Preferences saved.</p>')


def _prefs_judge(state: State) -> CheckResult:
    saved = state["data"]["saved"]
    return CheckResult.judge(
        done=saved == {"country": "PT", "courier": "swift-express"}, saved=saved
    )


SELECT_COMBOBOX = Task(
    id="select_combobox",
    category="widgets",
    difficulty="medium",
    feasible=True,
    instruction=(
        "Change my delivery preferences so packages go to Portugal with Swift "
        "Parcel Express as the courier."
    ),
    start="/",
    routes=(Route("GET", "/", _prefs_get), Route("POST", "/save", _prefs_save)),
    initial=lambda: {"saved": {"country": "DE", "courier": "atlas"}, "posts": []},
    judge=_prefs_judge,
)

# ---- date_pickers -----------------------------------------------------------

_CAL_CSS = """
.cal{position:absolute;background:#fff;border:1px solid #b9c0cc;border-radius:6px;
padding:10px;z-index:5;width:280px}
.cal .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.cal .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px}
.cal .grid span{font-size:12px;text-align:center;color:#6b7385}
.cal .grid button{padding:4px 0;background:#fff;color:#1d2433;border-color:#e3e6ec}
.cal .head button{background:#fff;color:#1d2433;border-color:#e3e6ec;padding:2px 10px}
"""
_CAL_JS = """
const MONTHS = ['January','February','March','April','May','June','July','August',
  'September','October','November','December'];
let shown = new Date(2026, 10, 1);
const field = document.getElementById('followup');
const hidden = document.getElementById('followup-value');
const cal = document.getElementById('cal');
function pad(n) { return String(n).padStart(2, '0'); }
function draw() {
  const y = shown.getFullYear(), m = shown.getMonth();
  const first = (new Date(y, m, 1).getDay() + 6) % 7;
  const days = new Date(y, m + 1, 0).getDate();
  let cells = ['Mo','Tu','We','Th','Fr','Sa','Su'].map(d => `<span>${d}</span>`).join('');
  for (let i = 0; i < first; i++) cells += '<span></span>';
  for (let d = 1; d <= days; d++) cells += `<button type="button" data-d="${d}">${d}</button>`;
  cal.innerHTML = `<div class="head"><button type="button" data-step="-1">&lsaquo;</button>
    <strong>${MONTHS[m]} ${y}</strong><button type="button" data-step="1">&rsaquo;</button></div>
    <div class="grid">${cells}</div>`;
}
cal.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.step) { shown = new Date(shown.getFullYear(), shown.getMonth() + +b.dataset.step, 1); draw(); return; }
  const y = shown.getFullYear(), m = shown.getMonth(), d = +b.dataset.d;
  hidden.value = `${y}-${pad(m + 1)}-${pad(d)}`;
  field.value = `${pad(d)} ${MONTHS[m].slice(0, 3)} ${y}`;
  cal.hidden = true;
});
field.addEventListener('click', () => { draw(); cal.hidden = false; });
"""


def _visit_get(ctx: Ctx) -> Response:
    booked = ctx.state["booking"]
    note = (
        f'<p class="ok">Booked: visit {esc(booked["arrival"])}, follow-up {esc(booked["followup"])}.</p>'
        if booked
        else ""
    )
    body = f"""
<div class="card"><h1>Book a site visit</h1>{note}
<p class="muted">Today is Tuesday, 24 November 2026.</p>
<form method="post" action="book">
<label for="arrival">Visit date</label><input type="date" id="arrival" name="arrival">
<label for="followup">Follow-up call</label>
<input id="followup" readonly placeholder="Pick a date" style="cursor:pointer">
<input type="hidden" id="followup-value" name="followup">
<div id="cal" class="cal" hidden></div>
<p><button type="submit">Confirm booking</button></p>
</form></div>"""
    return page(
        "Site visit", body, css=_CAL_CSS, script=_CAL_JS, header="Brightline Homes"
    )


def _visit_book(ctx: Ctx) -> Response:
    arrival, followup = ctx.request.first("arrival"), ctx.request.first("followup")
    ctx.state["posts"].append({"arrival": arrival, "followup": followup})
    if arrival and followup:
        ctx.state["booking"] = {"arrival": arrival, "followup": followup}
    return redirect(ctx.main("/"))


def _visit_judge(state: State) -> CheckResult:
    booking = state["data"]["booking"]
    return CheckResult.judge(
        done=booking == {"arrival": "2026-12-15", "followup": "2027-01-08"},
        booking=booking,
    )


DATE_PICKERS = Task(
    id="date_pickers",
    category="widgets",
    difficulty="medium",
    feasible=True,
    instruction=(
        "Book the site visit for 15 December 2026 and set the follow-up call for "
        "8 January 2027."
    ),
    start="/",
    routes=(Route("GET", "/", _visit_get), Route("POST", "/book", _visit_book)),
    initial=lambda: {"booking": None, "posts": []},
    judge=_visit_judge,
)

TASKS = (FORM_VALIDATION, SELECT_COMBOBOX, DATE_PICKERS)
