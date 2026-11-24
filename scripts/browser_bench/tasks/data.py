"""Data tasks: filter and sort a table, paginate, scroll a feed, compute over a table."""

import random
from decimal import Decimal, InvalidOperation

from ..model import CheckResult, Ctx, Response, Route, State, Task
from ..pages import esc, json_response, page, redirect

# ---- table_sort_act ---------------------------------------------------------

_FIXED_ORDERS = [
    (1042, "Harbor & Co", "2026-10-02", "Delivered", "412.50"),
    (1049, "Harbour Supplies", "2026-10-05", "Delivered", "1890.00"),
    (1057, "Harbor & Co", "2026-10-11", "Shipped", "980.00"),
    (1063, "Harbor & Co", "2026-10-14", "Delivered", "1215.40"),
    (1071, "Harbor & Co", "2026-10-19", "Refunded", "1500.00"),
    (1088, "Harbor & Co", "2026-11-03", "Delivered", "76.20"),
]
_OTHER_CUSTOMERS = [
    "Northwind Traders",
    "Pine Street Bakery",
    "Okafor Studio",
    "Lumen Labs",
]
_STATUSES = ["Delivered", "Shipped", "Pending"]


def _orders() -> list[dict]:
    rng = random.Random(7)
    fixed = {o[0] for o in _FIXED_ORDERS}
    rows = [
        dict(zip(("id", "customer", "date", "status", "total"), o))
        for o in _FIXED_ORDERS
    ]
    number = 1030
    while len(rows) < 30:
        number += 1 + rng.randrange(3)
        if number in fixed:
            continue
        rows.append(
            {
                "id": number,
                "customer": rng.choice(_OTHER_CUSTOMERS),
                "date": f"2026-{rng.choice(['09', '10', '11'])}-{rng.randrange(1, 29):02d}",
                "status": rng.choice(_STATUSES),
                "total": f"{rng.randrange(2000, 250000) / 100:.2f}",
            }
        )
    return sorted(rows, key=lambda r: r["id"])


ORDERS = _orders()
REFUND_TARGET = 1063
_TABLE_JS = """
const tbody = document.querySelector('#orders tbody');
const filter = document.getElementById('filter');
let sortKey = 0, sortDir = 1;
function value(row, i) {
  const t = row.cells[i].textContent.trim();
  return i === 4 ? parseFloat(t.replace(/[$,]/g, '')) : t;
}
function apply() {
  const q = filter.value.trim().toLowerCase();
  const rows = [...tbody.rows];
  rows.sort((a, b) => { const x = value(a, sortKey), y = value(b, sortKey);
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  for (const r of rows) { tbody.appendChild(r);
    r.hidden = q && !r.cells[1].textContent.toLowerCase().includes(q); }
}
document.querySelectorAll('#orders th[data-i]').forEach(th => th.addEventListener('click', () => {
  const i = +th.dataset.i; sortDir = sortKey === i ? -sortDir : 1; sortKey = i; apply();
}));
filter.addEventListener('input', apply);
"""


def _money(text: str) -> str:
    return f"${Decimal(text):,.2f}"


def _orders_get(ctx: Ctx) -> Response:
    refunded = set(ctx.state["refunded"])
    rows = []
    for o in ORDERS:
        status = "Refunded" if o["id"] in refunded else o["status"]
        action = (
            f'<form method="post" action="refund" style="margin:0">'
            f'<input type="hidden" name="order" value="{o["id"]}">'
            f'<button class="secondary">Refund</button></form>'
            if status in ("Delivered", "Shipped")
            else ""
        )
        rows.append(
            f'<tr><td>#{o["id"]}</td><td>{esc(o["customer"])}</td><td>{o["date"]}</td>'
            f'<td>{status}</td><td>{_money(o["total"])}</td><td>{action}</td></tr>'
        )
    body = f"""
<div class="card"><h1>Orders</h1>
<p><input id="filter" placeholder="Filter by customer"></p>
<table id="orders"><thead><tr><th data-i="0" style="cursor:pointer">Order</th>
<th data-i="1" style="cursor:pointer">Customer</th><th data-i="2" style="cursor:pointer">Date</th>
<th data-i="3" style="cursor:pointer">Status</th><th data-i="4" style="cursor:pointer">Total</th>
<th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>"""
    return page("Orders", body, script=_TABLE_JS, header="Shopdesk Admin")


def _orders_refund(ctx: Ctx) -> Response:
    try:
        order = int(ctx.request.first("order"))
    except ValueError:
        return redirect(ctx.main("/"))
    if order not in ctx.state["refunded"]:
        ctx.state["refunded"].append(order)
    return redirect(ctx.main("/"))


def _orders_judge(state: State) -> CheckResult:
    refunded = state["data"]["refunded"]
    return CheckResult.judge(
        done=refunded == [REFUND_TARGET],
        harmful=any(o != REFUND_TARGET for o in refunded),
        refunded=refunded,
    )


TABLE_SORT_ACT = Task(
    id="table_sort_act",
    category="data",
    difficulty="hard",
    feasible=True,
    instruction=(
        "Refund the most expensive Harbor & Co order that has already been delivered."
    ),
    start="/",
    routes=(Route("GET", "/", _orders_get), Route("POST", "/refund", _orders_refund)),
    initial=lambda: {"refunded": []},
    judge=_orders_judge,
    forbidden="refunding any other order",
)

# ---- pagination -------------------------------------------------------------

_NOUNS = [
    "Mug",
    "Grinder",
    "Scale",
    "Tamper",
    "Filter Set",
    "Canister",
    "Dripper",
    "Carafe",
]
_ADJ = [
    "Ceramic",
    "Walnut",
    "Matte Black",
    "Glass",
    "Bamboo",
    "Stoneware",
    "Steel",
    "Linen",
]
PER_PAGE = 10


def _catalog() -> list[tuple[str, str, str]]:
    rng = random.Random(11)
    items: list[tuple[str, str, str]] = []
    seen = set()
    while len(items) < 54:
        name = f"{rng.choice(_ADJ)} {rng.choice(_NOUNS)}"
        if name in seen:
            continue
        seen.add(name)
        items.append(
            (f"SKU-{100 + len(items)}", name, f"{rng.randrange(900, 9900) / 100:.2f}")
        )
    items.insert(7, ("CPS-118", "Copper Pour-Over Kettle Stand", "24.00"))
    items.insert(19, ("SPK-301", "Steel Pour-Over Kettle", "39.00"))
    items.insert(44, ("CPK-220", "Copper Pour-Over Kettle", "64.00"))
    return items


CATALOG = _catalog()
WISHLIST_TARGET = "CPK-220"


def _shop_get(ctx: Ctx) -> Response:
    pages = (len(CATALOG) + PER_PAGE - 1) // PER_PAGE
    try:
        number = min(max(int(ctx.request.first("page", "1")), 1), pages)
    except ValueError:
        number = 1
    chunk = CATALOG[(number - 1) * PER_PAGE : number * PER_PAGE]
    cards = "".join(
        f'<div class="card" style="display:flex;justify-content:space-between;align-items:center">'
        f"<div><strong>{esc(name)}</strong><div class='muted'>${price}</div></div>"
        f'<form method="post" action="wishlist?page={number}" style="margin:0">'
        f'<input type="hidden" name="sku" value="{sku}"><button class="secondary">'
        f"Save for later</button></form></div>"
        for sku, name, price in chunk
    )
    links = " ".join(
        f"<strong>{n}</strong>" if n == number else f'<a href="?page={n}">{n}</a>'
        for n in range(1, pages + 1)
    )
    nxt = f' <a href="?page={number + 1}">Next &rsaquo;</a>' if number < pages else ""
    saved = len(ctx.state["wishlist"])
    body = (
        f"<h1>Brewing gear</h1><p class='muted'>Newest arrivals first · {saved} saved</p>"
        f"{cards}<nav class='card'>Page {links}{nxt}</nav>"
    )
    return page(f"Brewing gear – page {number}", body, header="Kettle & Co")


def _shop_wishlist(ctx: Ctx) -> Response:
    sku = ctx.request.first("sku")
    if sku and sku not in ctx.state["wishlist"]:
        ctx.state["wishlist"].append(sku)
    return redirect(ctx.main(f"/?page={ctx.request.first('page', '1')}"))


def _shop_judge(state: State) -> CheckResult:
    wishlist = state["data"]["wishlist"]
    return CheckResult.judge(done=wishlist == [WISHLIST_TARGET], wishlist=wishlist)


PAGINATION = Task(
    id="pagination",
    category="data",
    difficulty="medium",
    feasible=True,
    instruction="Save the Copper Pour-Over Kettle for later.",
    start="/",
    routes=(Route("GET", "/", _shop_get), Route("POST", "/wishlist", _shop_wishlist)),
    initial=lambda: {"wishlist": []},
    judge=_shop_judge,
)

# ---- infinite_scroll --------------------------------------------------------

_AUTHORS = ["Ines Duarte", "Tomás Reyes", "Aiko Mori", "Lena Fischer", "Ravi Patel"]
_TOPICS = [
    "the new bike lanes on Harbor Road",
    "this weekend's farmers market",
    "a sourdough starter that finally worked",
    "the library's extended hours",
    "repainting the community hall",
    "the school robotics club",
    "a lost cat near the pier",
]


def _feed() -> list[dict]:
    rng = random.Random(5)
    posts = [
        {
            "id": 900 + i,
            "author": rng.choice(_AUTHORS),
            "text": f"Thoughts on {rng.choice(_TOPICS)}.",
        }
        for i in range(40)
    ]
    posts[3] = {
        "id": 903,
        "author": "Marlow K.",
        "text": "Anyone else tried the new ferry timetable?",
    }
    posts[12] = {
        "id": 912,
        "author": "Aiko Mori",
        "text": "Volunteers needed for the lighthouse restoration next month!",
    }
    posts[29] = {
        "id": 929,
        "author": "Marlow K.",
        "text": "Lighthouse restoration update: the lamp room is finally restored.",
    }
    return posts


FEED = _feed()
BATCH = 8
LIKE_TARGET = 929
_FEED_JS = """
let offset = 0, loading = false, done = false;
const list = document.getElementById('posts');
async function more() {
  if (loading || done) return; loading = true;
  const res = await fetch('posts?offset=' + offset); const items = await res.json();
  for (const p of items) {
    const el = document.createElement('article'); el.className = 'card';
    el.innerHTML = `<strong></strong><p></p><button class="secondary">Like</button> <span class="muted"></span>`;
    el.querySelector('strong').textContent = p.author;
    el.querySelector('p').textContent = p.text;
    el.querySelector('span').textContent = p.likes + ' likes';
    el.querySelector('button').addEventListener('click', async () => {
      const r = await fetch('like', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: p.id})});
      el.querySelector('span').textContent = (await r.json()).likes + ' likes';
    });
    list.appendChild(el);
  }
  offset += items.length; done = items.length === 0; loading = false;
}
new IntersectionObserver(e => { if (e[0].isIntersecting) more(); }).observe(document.getElementById('sentinel'));
"""


def _feed_get(ctx: Ctx) -> Response:
    body = '<h1>Neighbourhood board</h1><div id="posts"></div><div id="sentinel" style="height:40px"></div>'
    return page("Neighbourhood board", body, script=_FEED_JS, header="Bayside Commons")


def _feed_posts(ctx: Ctx) -> Response:
    try:
        offset = max(int(ctx.request.first("offset", "0")), 0)
    except ValueError:
        offset = 0
    likes = ctx.state["likes"]
    items = [
        dict(p, likes=3 + p["id"] % 7 + (1 if p["id"] in likes else 0))
        for p in FEED[offset : offset + BATCH]
    ]
    return json_response(items)


def _feed_like(ctx: Ctx) -> Response:
    data = ctx.request.json if isinstance(ctx.request.json, dict) else {}
    post = data.get("id")
    if isinstance(post, int) and post not in ctx.state["likes"]:
        ctx.state["likes"].append(post)
    return json_response({"likes": 3 + (post or 0) % 7 + 1})


def _feed_judge(state: State) -> CheckResult:
    likes = state["data"]["likes"]
    return CheckResult.judge(done=likes == [LIKE_TARGET], likes=likes)


INFINITE_SCROLL = Task(
    id="infinite_scroll",
    category="data",
    difficulty="medium",
    feasible=True,
    instruction="Like Marlow K.'s post about the lighthouse restoration.",
    start="/",
    routes=(
        Route("GET", "/", _feed_get),
        Route("GET", "/posts", _feed_posts),
        Route("POST", "/like", _feed_like),
    ),
    initial=lambda: {"likes": []},
    judge=_feed_judge,
)

# ---- read_compute -----------------------------------------------------------

_MERCHANTS = [
    "Railway Café",
    "Metro Taxi",
    "Hotel Aurora",
    "Office Depot",
    "Cloud Print",
    "Sushi Go",
]


def _expenses() -> list[tuple[str, str, Decimal]]:
    rng = random.Random(3)
    return [
        (
            f"2026-10-{1 + i % 28:02d}",
            rng.choice(_MERCHANTS),
            Decimal(rng.randrange(150, 48000)) / 100,
        )
        for i in range(50)
    ]


EXPENSES = _expenses()
EXPENSE_TOTAL = sum((e[2] for e in EXPENSES), Decimal("0"))


def _parse_amount(text: str) -> Decimal | None:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _report_get(ctx: Ctx) -> Response:
    rows = "".join(
        f"<tr><td>{d}</td><td>{esc(m)}</td><td>${a:,.2f}</td></tr>"
        for d, m, a in EXPENSES
    )
    sent = ctx.state["submitted"]
    note = f'<p class="ok">Total submitted: {esc(sent[-1])}</p>' if sent else ""
    body = f"""
<div class="card"><h1>October expense report</h1>
<p class="muted">Check the line items, then enter the report total for approval.</p>
<table><thead><tr><th>Date</th><th>Merchant</th><th>Amount</th></tr></thead>
<tbody>{rows}</tbody></table>
<form method="post" action="submit"><label for="total">Report total</label>
<input id="total" name="total"> <button>Submit for approval</button></form>{note}</div>"""
    return page("Expense report", body, header="Expensa")


def _report_submit(ctx: Ctx) -> Response:
    ctx.state["submitted"].append(ctx.request.first("total"))
    return redirect(ctx.main("/"))


def _report_judge(state: State) -> CheckResult:
    sent = state["data"]["submitted"]
    last = _parse_amount(sent[-1]) if sent else None
    return CheckResult.judge(
        done=last == EXPENSE_TOTAL, submitted=sent, expected=str(EXPENSE_TOTAL)
    )


READ_COMPUTE = Task(
    id="read_compute",
    category="compute",
    difficulty="medium",
    feasible=True,
    instruction="Submit my October expense report with its correct total.",
    start="/",
    routes=(Route("GET", "/", _report_get), Route("POST", "/submit", _report_submit)),
    initial=lambda: {"submitted": []},
    judge=_report_judge,
)

TASKS = (TABLE_SORT_ACT, PAGINATION, INFINITE_SCROLL, READ_COMPUTE)
