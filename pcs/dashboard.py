"""Step 9 -- the dashboard.

A single self-contained HTML file, rewritten on every run, showing the paper
account, open positions with their management state, the current proposal
queue, and the rules the agent is operating under. No external assets, so it
opens straight off disk.
"""

from __future__ import annotations

import datetime as dt
import html

from .config import DASHBOARD_HTML, STRATEGY, Settings
from .exits import decide
from .ledger import Ledger, Position
from .proposer import Proposal
from .readiness import assess
from .session import SessionState

CSS = """
:root{--bg:#0b0e13;--panel:#141922;--panel2:#1a202b;--line:#242c38;--ink:#e9edf3;
--dim:#8792a4;--pos:#3fb950;--neg:#f85149;--warn:#d29922;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;
font:14px/1.55 ui-sans-serif,-apple-system,"SF Pro Text",Segoe UI,Inter,sans-serif;
background-image:radial-gradient(900px 380px at 15% -8%,rgba(88,166,255,.09),transparent),
radial-gradient(700px 320px at 92% -4%,rgba(63,185,80,.06),transparent)}
.wrap{max-width:1180px;margin:0 auto;padding:30px 20px 70px}
h1{font-size:21px;margin:0 0 5px;letter-spacing:-.015em}
h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--dim);
margin:30px 0 12px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.banner{background:linear-gradient(90deg,rgba(210,153,34,.13),rgba(210,153,34,.03));
border-left:3px solid var(--warn);padding:11px 15px;border-radius:0 8px 8px 0;
margin:16px 0;color:#dfe4ec;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(152px,1fr));gap:11px}
.card{background:linear-gradient(180deg,var(--panel2),var(--panel));
border:1px solid var(--line);border-radius:11px;padding:15px 16px;
transition:border-color .15s,transform .15s}
.card:hover{border-color:#313d4e;transform:translateY(-1px)}
.card .k{color:var(--dim);font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;
font-weight:600}
.card .v{font-size:22px;margin-top:6px;font-variant-numeric:tabular-nums;
letter-spacing:-.02em}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;min-width:760px;border-collapse:separate;border-spacing:0;
background:var(--panel);border:1px solid var(--line);border-radius:11px;overflow:hidden}
th{text-align:left;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;
color:var(--dim);padding:11px 13px;background:#11161e;
border-bottom:1px solid var(--line);font-weight:600;white-space:nowrap}
td{padding:11px 13px;border-bottom:1px solid #1b212b;font-variant-numeric:tabular-nums;
vertical-align:top}
tbody tr:last-child td{border-bottom:none}
tbody tr{transition:background .12s} tbody tr:hover{background:#171d27}
.pos{color:var(--pos)} .neg{color:var(--neg)} .dim{color:var(--dim)}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
background:#212936;color:var(--dim);border:1px solid var(--line);white-space:nowrap}
.tag.ok{color:var(--pos);border-color:#20402a;background:rgba(63,185,80,.09)}
.tag.blocked{color:var(--neg);border-color:#4a2224;background:rgba(248,81,73,.09)}
.tag.act{color:var(--warn);border-color:#463a17;background:rgba(210,153,34,.1)}
.note{font-size:12px;color:var(--warn);padding:3px 0;line-height:1.45}
.dte{display:inline-block;padding:1.5px 8px;border-radius:999px;font-size:11px;
font-weight:600;background:#212936;border:1px solid var(--line);color:var(--dim);
white-space:nowrap}
.dte.soon{color:var(--warn);border-color:#463a17;background:rgba(210,153,34,.11)}
.dte.now{color:var(--neg);border-color:#4a2224;background:rgba(248,81,73,.11)}
.exp{font-variant-numeric:tabular-nums;white-space:nowrap}
.rules{background:var(--panel);border:1px solid var(--line);border-radius:11px;
padding:16px 20px}
.rules li{margin:5px 0;color:#c6cedb;font-size:13px}
.rules.dev{border-color:#8a6a1f;background:linear-gradient(180deg,#241d0d,var(--panel))}
.devh{font-weight:650;color:#f0c85a;font-size:13px;margin-bottom:6px;
  letter-spacing:.02em}
.devf{color:#93a0b4;font-size:12px;margin-top:8px}
.devlink{color:#f0c85a;text-decoration:none;border-bottom:1px dotted #8a6a1f}
.empty{color:var(--dim);padding:18px;background:var(--panel);
border:1px solid var(--line);border-radius:11px;font-size:13px}
code{background:#212936;padding:1.5px 6px;border-radius:4px;font-size:12px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.tabs{display:flex;gap:2px;margin:24px 0 16px;border-bottom:1px solid var(--line);
flex-wrap:wrap}
.tabs button{appearance:none;background:none;border:none;border-bottom:2px solid transparent;
color:var(--dim);font:inherit;font-weight:600;font-size:13px;padding:10px 15px;
cursor:pointer;margin-bottom:-1px;transition:color .12s}
.tabs button:hover{color:var(--ink)}
.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
.tabs .pill{display:inline-block;margin-left:7px;padding:1px 7px;border-radius:999px;
background:#212936;font-size:10.5px;color:var(--dim);font-weight:600}
.panel[hidden]{display:none}
.meter{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:18px 20px}
.meter .top{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
flex-wrap:wrap;margin-bottom:12px}
.meter .score{font-size:26px;font-weight:600;letter-spacing:-.02em;
font-variant-numeric:tabular-nums}
.bar{height:9px;border-radius:999px;background:#212936;overflow:hidden;
border:1px solid var(--line)}
.bar span{display:block;height:100%;border-radius:999px;transition:width .4s ease}
.bar span.low{background:linear-gradient(90deg,#a8352f,#f85149)}
.bar span.mid{background:linear-gradient(90deg,#9a7016,#d29922)}
.bar span.high{background:linear-gradient(90deg,#2b8f3d,#3fb950)}
.checks{margin-top:16px;display:grid;gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:9px;overflow:hidden}
.chk{display:grid;grid-template-columns:22px 1fr;gap:11px;padding:11px 14px;
background:var(--panel);align-items:start;font-size:13px}
.chk .m{font-weight:700;line-height:1.3}
.chk .m.y{color:var(--pos)} .chk .m.n{color:var(--neg)} .chk .m.w{color:var(--warn)}
.chk .d{color:var(--dim);font-size:12px;margin-top:2px;line-height:1.45}
.evt{display:grid;grid-template-columns:148px 118px 1fr;gap:12px;padding:9px 0;
border-bottom:1px solid #1b212b;font-size:13px;align-items:baseline}
.evt:last-child{border-bottom:none}
.evt time{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px}
.evt .what{font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase}
.evt .what.open{color:var(--accent)} .evt .what.close{color:var(--warn)}
.evt .what.exit{color:var(--pos)} .evt .what.dim{color:var(--dim)}
.c{display:block}

@media (max-width:700px){
.wrap{padding:18px 13px 52px}
h1{font-size:17px} h2{margin:24px 0 10px}
.cards{grid-template-columns:repeat(auto-fit,minmax(142px,1fr));gap:8px}
.card{padding:12px 13px;border-radius:10px} .card .v{font-size:19px}
.tabs{gap:0;margin:18px 0 14px} .tabs button{padding:10px 12px;font-size:12.5px;flex:1}
.scroll{overflow-x:visible}
table{min-width:0;border:none;background:none;overflow:visible}
thead{display:none}
tbody tr{display:block;background:linear-gradient(180deg,var(--panel2),var(--panel));
border:1px solid var(--line);border-radius:11px;padding:5px 14px;margin-bottom:10px}
tbody tr:hover{background:var(--panel)}
tbody td{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
padding:8px 0;text-align:right;border-bottom:1px solid #1b212b}
tbody tr td:last-child{border-bottom:none}
tbody td::before{content:attr(data-l);color:var(--dim);font-size:10.5px;letter-spacing:.06em;
text-transform:uppercase;text-align:left;flex:0 0 auto;padding-top:3px;font-weight:600}
tbody td .c{text-align:right;min-width:0;overflow-wrap:anywhere}
.note{text-align:right}
.evt{grid-template-columns:1fr;gap:3px;padding:11px 0}
.meter{padding:15px 16px} .meter .score{font-size:22px}
.chk{grid-template-columns:20px 1fr;padding:10px 12px}
.rules{padding:14px 17px} .rules ul{padding-left:19px}
}
"""

def _e(x) -> str:
    return html.escape(str(x))


def _sign(v: float, fmt: str = ",.0f", money: bool = True) -> str:
    cls = "pos" if v > 0 else ("neg" if v < 0 else "dim")
    txt = f"{'$' if money else ''}{v:{fmt}}"
    if v > 0:
        txt = "+" + txt
    return f'<span class="{cls}">{txt}</span>'


def _dte_badge(dte: int, strategy=STRATEGY) -> str:
    """Days to expiration, coloured by which exit rule is next to bite.

    This strategy is entirely DTE-driven -- management starts at 21 days and a
    tested short strike is defended at 7 -- so the clock deserves the same
    visual weight as the money. The thresholds come from STRATEGY so the badge
    cannot drift away from what the exit engine actually fires on.
    """
    if dte <= strategy.defend_dte:
        cls, hint = "dte now", "defend"
    elif dte <= strategy.manage_dte:
        cls, hint = "dte soon", "manage"
    else:
        cls, hint = "dte", ""
    label = f"{dte}d" + (f" \u00b7 {hint}" if hint else "")
    return f'<span class="{cls}">{_e(label)}</span>'


def _next_expiry(rows: list[Position]) -> str:
    """The nearest expiration across the open book, with its DTE badge.

    Every exit rule here is a function of days remaining, so the soonest expiry
    is the most useful number on the page that is not a dollar amount.
    """
    if not rows:
        return '<span class="dim">-</span>'
    nxt = min(rows, key=lambda p: p.expiration)
    same = sum(1 for p in rows if p.expiration == nxt.expiration)
    more = f'<span class="dim" style="font-size:12px"> x{same}</span>' if same > 1 else ""
    return (f'<span style="font-size:20px">{_e(nxt.expiration)}</span>{more}'
            f'<div style="margin-top:6px">{_dte_badge(nxt.dte)}</div>')


def _expiry_cell(expiration: str, dte: int) -> str:
    return (f"<span class='exp'><b>{_e(expiration)}</b></span>"
            f"<div style='margin-top:3px'>{_dte_badge(dte)}</div>")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """One table builder for all three tables.

    Every cell carries its own column label as `data-l`. On a phone the table
    collapses to a stack of cards and that attribute becomes the row label, so
    headers and labels cannot drift apart -- they are the same list.
    """
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td data-l="{_e(h)}"><span class="c">{c}</span></td>'
                         for h, c in zip(headers, r, strict=True)) + "</tr>"
        for r in rows)
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _positions_table(rows: list[Position], settings: Settings) -> str:
    if not rows:
        return '<div class="empty">No open positions.</div>'
    out = []
    for p in rows:
        d = decide(p, settings)
        note = d.reason
        tag = (f'<span class="tag act">{_e(d.headline)}</span>'
               if d.act else (f'<span class="tag">{_e(d.headline)}</span>' if note else ""))
        out.append([
            f"<b>{_e(p.symbol)}</b>"
            f"<div class='dim' style='font-size:11px'>{_e(p.sector)}</div>",
            f"{p.short_strike:g}/{p.long_strike:g}p",
            _expiry_cell(p.expiration, p.dte),
            f"{p.contracts}",
            f"${p.credit_dollars:,.0f}",
            f"${p.collateral:,.0f}",
            f"${p.mark_cost_to_close * 100 * p.contracts:,.0f}",
            _sign(p.open_pl),
            f"{p.pct_of_max_credit:.0%} {tag}"
            + (f"<div class='note'>{_e(note)}</div>" if note else ""),
        ])
    return _table(["ticker", "spread", "expiration", "qty", "credit",
                   "collateral", "cost to close", "P&L", "% of max credit"], out)


def _closed_table(rows: list[Position]) -> str:
    if not rows:
        return '<div class="empty">No closed positions yet.</div>'
    out = [[
        f"<code>{_e(p.id)}</code>",
        f"<b>{_e(p.symbol)}</b>",
        f"{p.short_strike:g}/{p.long_strike:g}p",
        f"<span class='exp'><b>{_e(p.expiration)}</b></span>",
        f"${p.credit_dollars:,.0f}",
        f"${p.close_debit * 100 * p.contracts:,.0f}",
        _sign(p.realized_pl),
        f"<span class='dim'>{_e(p.close_reason)}</span>",
    ] for p in sorted(rows, key=lambda x: x.closed_at, reverse=True)]
    return _table(["id", "ticker", "spread", "expiration", "credit", "debit",
                   "realized", "outcome"], out)


def _proposals_table(props: list[Proposal]) -> str:
    pending = [p for p in props if p.status == "pending"]
    if not pending:
        return '<div class="empty">No pending proposals. Run <code>./run.py propose</code>.</div>'
    out = []
    for p in pending:
        s = p.spread
        tag = ('<span class="tag ok">clear</span>' if p.risk_ok
               else '<span class="tag blocked">blocked</span>')
        notes = "".join(f"<div class='note'>{_e(w)}</div>"
                        for w in (p.risk_warnings if p.risk_ok else p.risk_reasons))
        out.append([
            f"<code>{_e(p.id)}</code>",
            f"<b>{_e(p.symbol)}</b>"
            f"<div class='dim' style='font-size:11px'>{_e(p.sector)}</div>",
            f"{s['short_strike']:g}/{s['long_strike']:g}p"
            f"<div class='dim' style='font-size:11px'>${s['width']:g} wide</div>",
            _expiry_cell(s["expiration"], s["dte"]),
            f"${s['credit_dollars']:.0f}"
            f"<div class='dim' style='font-size:11px'>nat ${s['credit_nat_dollars']:.0f}</div>",
            f"${s['collateral']:.0f}",
            f"{s['roc']:.0%}",
            f"{s['cushion']:.1%}",
            "-" if s["pop_est"] is None else format(s["pop_est"], ".0%"),
            f"{tag}{notes}",
        ])
    return _table(["proposal", "ticker", "spread", "expiration", "credit",
                   "collateral", "ROC", "cushion", "POP", "status"], out)


_EVENT_LABEL = {
    "account_opened": ("account opened", "dim"),
    "position_opened": ("opened", "open"),
    "position_closed": ("closed", "close"),
    "auto_exit": ("agent exit", "exit"),
    "marked": ("marked", "dim"),
}


def _event_line(e: dict) -> str:
    """One row of the append-only log, rendered without interpreting it.

    The log is the audit trail, so this deliberately shows whatever fields the
    event carries rather than a curated subset -- an event type added later
    still renders.
    """
    kind = e.get("kind", "?")
    label, cls = _EVENT_LABEL.get(kind, (kind.replace("_", " "), "dim"))
    skip = {"at", "kind"}
    detail = " &middot; ".join(
        f"{_e(k.replace('_', ' '))} <b>{_e(v)}</b>"
        for k, v in e.items() if k not in skip and v not in ("", None))
    when = str(e.get("at", "")).replace("T", " ")
    detail = detail or '<span class="dim">-</span>'
    return (f'<div class="evt"><time>{_e(when)}</time>'
            f'<span class="what {cls}">{_e(label)}</span>'
            f"<span>{detail}</span></div>")


def _history_panel(led: Ledger) -> str:
    """Closed positions plus the raw event log, newest first."""
    events = list(reversed(led.events))
    log = ("".join(_event_line(e) for e in events) if events
           else '<div class="empty">No events yet.</div>')
    closed = led.closed_positions
    wins = [p for p in closed if p.realized_pl > 0]
    summary = ""
    if closed:
        gross_win = sum(p.realized_pl for p in wins)
        gross_loss = sum(p.realized_pl for p in closed if p.realized_pl <= 0)
        avg = led.realized_pl / len(closed)
        summary = (
            f'<div class="cards" style="margin-bottom:8px">'
            f'<div class="card"><div class="k">closed trades</div>'
            f'<div class="v">{len(closed)}</div></div>'
            f'<div class="card"><div class="k">win rate</div>'
            f'<div class="v">{len(wins) / len(closed):.0%}</div></div>'
            f'<div class="card"><div class="k">realized P&amp;L</div>'
            f'<div class="v">{_sign(led.realized_pl, ",.2f")}</div></div>'
            f'<div class="card"><div class="k">average per trade</div>'
            f'<div class="v">{_sign(avg, ",.2f")}</div></div>'
            f'<div class="card"><div class="k">won / lost</div>'
            f'<div class="v">{_sign(gross_win, ",.0f")} / {_sign(gross_loss, ",.0f")}</div></div>'
            f"</div>")
    return (f"{summary}<h2>Closed positions</h2>{_closed_table(closed)}"
            f"<h2>Event log &mdash; every action, newest first</h2>"
            f'<div class="rules">{log}</div>')


def _ready_pill(led: Ledger, settings: Settings) -> str:
    r = assess(led, settings)
    return f"{r.pct:.0%}"


def _readiness_panel(led: Ledger, settings: Settings) -> str:
    """The go-live score, rendered honestly -- a low bar is the useful output."""
    r = assess(led, settings)
    pct = r.pct
    cls = "high" if pct >= 0.85 else ("mid" if pct >= 0.5 else "low")
    rows = "".join(
        f'<div class="chk"><div class="m {"y" if c.ok else ("n" if c.blocking else "w")}">'
        f'{"&#10003;" if c.ok else ("&#10007;" if c.blocking else "!")}</div>'
        f"<div><div>{_e(c.label)}"
        + ("" if c.blocking else ' <span class="tag">advisory</span>')
        + f'</div><div class="d">{_e(c.detail)}</div></div></div>'
        for c in r.criteria)
    return (
        f'<div class="meter"><div class="top">'
        f'<div><div class="score">{r.met} of {r.total} checks pass</div>'
        f'<div class="dim" style="font-size:13px;margin-top:3px">{_e(r.verdict)}</div></div>'
        f'<div class="score" style="color:var(--{"pos" if cls == "high" else "warn" if cls == "mid" else "neg"})">'
        f"{pct:.0%}</div></div>"
        f'<div class="bar"><span class="{cls}" style="width:{max(pct * 100, 2):.0f}%"></span></div>'
        f'<div class="checks">{rows}</div>'
        f'<div class="dim" style="font-size:12px;margin-top:14px;line-height:1.5">'
        f"This is a readiness signal, not a permission. Even at 100% the agent has no "
        f"path to placing a live order &mdash; it refuses a non-paper ledger in code, and "
        f"a human places every real trade.</div></div>")


def _deviations_panel(settings: Settings) -> str:
    """What this account has changed, stated as a change rather than as a rule.

    A dashboard that renders the loosened number as if it were the mandate is
    how a limit quietly stops being a limit.
    """
    dev = settings.deviations()
    if not dev:
        return ('<div class="rules"><ul><li>Running the strategy\'s numbers exactly '
                '&mdash; nothing has been overridden.</li></ul></div>')
    items = "".join(f"<li>{_e(d)}</li>" for d in dev)
    return (f'<div class="rules dev"><div class="devh">This account is not running '
            f'the standard rules</div><ul>{items}</ul>'
            f'<div class="devf">Changed with <code>./run.py config --set</code>. '
            f'Every proposal sized under these carries the same note.</div></div>')


def render(led: Ledger, props: list[Proposal], settings: Settings,
           sess: SessionState, path=DASHBOARD_HTML):
    used_pct = (led.collateral_held / settings.max_total_collateral
                if settings.max_total_collateral else 0)
    eff = settings.strategy()
    n_dev = len(settings.deviations())
    max_credit_txt = (f", credit &le; ${eff.max_credit_per_trade:,.0f}"
                      if eff.max_credit_per_trade else ", no upper cap on credit")
    dev_flag = (f' &middot; <a href="#" data-goto="rules" class="devlink">'
                f'{n_dev} rule{"s" if n_dev != 1 else ""} overridden</a>' if n_dev else
                " (the strategy's own numbers)")
    if settings.require_approval():
        approval_txt = ("every position that adds risk needs explicit per-trade human "
                        "sign-off. This agent proposes; it never places an opening order.")
        pending_txt = "awaiting human approval"
    else:
        approval_txt = (f"per-trade human sign-off is <b>OFF</b> for this paper account. "
                        f"The agent opens clear proposals itself and records the approver "
                        f"as <code>{_e(settings.auto_approver())}</code>. This is paper-only "
                        f"&mdash; a live ledger always requires a human, and the agent has "
                        f"no path to placing a live order at all.")
        pending_txt = "opened automatically on the next run"
    sectors = ", ".join(f"{k} x{v}" for k, v in sorted(led.sector_counts().items())) or "none"
    cards = [
        ("net liquidation", f"${led.net_liq:,.2f}"),
        ("cash", f"${led.cash:,.2f}"),
        ("collateral at risk", f"${led.collateral_held:,.0f}"),
        ("available balance", f"${led.buying_power:,.2f}"),
        ("realized P&amp;L", _sign(led.realized_pl, ",.2f")),
        ("unrealized P&amp;L", _sign(led.unrealized_pl, ",.2f")),
        ("total return", _sign(led.total_return * 100, ".2f", money=False) + "%"),
        ("open positions", f"{len(led.open_positions)} / {settings.max_open_positions}"),
        ("next expiry", _next_expiry(led.open_positions)),
    ]
    card_html = "".join(f'<div class="card"><div class="k">{k}</div>'
                        f'<div class="v">{v}</div></div>' for k, v in cards)

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Put Credit Spread Agent - {_e(settings.account_label)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>S&amp;P 500 Beaten-Down Put Credit Spread Agent</h1>
<div class="sub">{_e(settings.account_label)} &middot; mode <b>{_e(led.mode)}</b> &middot;
opened {_e(led.created_at[:10])} &middot; rebuilt {dt.datetime.now():%Y-%m-%d %H:%M}</div>
<div class="banner">{_e(sess.banner)}</div>

<div class="tabs" role="tablist">
<button role="tab" aria-selected="true" data-t="now">Positions</button>
<button role="tab" aria-selected="false" data-t="history">History<span class="pill"
>{len(led.closed_positions)}</span></button>
<button role="tab" aria-selected="false" data-t="ready">Go-live<span class="pill"
>{_ready_pill(led, settings)}</span></button>
<button role="tab" aria-selected="false" data-t="rules">Rules</button>
</div>

<section class="panel" id="p-now">
<div class="cards">{card_html}</div>

<h2>Portfolio limits</h2>
<div class="rules"><ul>
<li>Collateral deployed: <b>${led.collateral_held:,.0f}</b> of
${settings.max_total_collateral:,.0f} cap ({used_pct:.0%} used)</li>
<li>Open positions: <b>{len(led.open_positions)}</b> of {settings.max_open_positions}
&middot; max {settings.max_positions_per_sector} per sector &middot; currently: {_e(sectors)}</li>
<li>Available balance: <b>${led.buying_power:,.2f}</b> (cash ${led.cash:,.2f} less
${led.capital_at_risk:,.0f} of capital at risk). Nothing opens that needs more free
balance than this &mdash; the account can always pay its own max loss.</li>
<li>Per trade: collateral &le; ${eff.max_collateral_per_trade:,.0f}, credit &ge;
${eff.min_credit_per_trade:,.0f}{max_credit_txt}{dev_flag}</li>
</ul></div>

<h2>Open positions</h2>
{_positions_table(led.open_positions, settings)}

<h2>Pending proposals &mdash; {pending_txt}</h2>
{_proposals_table(props)}
</section>

<section class="panel" id="p-history" hidden>
{_history_panel(led)}
</section>

<section class="panel" id="p-ready" hidden>
<h2>Readiness to trade this live</h2>
{_readiness_panel(led, settings)}
</section>

<section class="panel" id="p-rules" hidden>
<h2>Configuration</h2>
{_deviations_panel(settings)}

<h2>Rules this agent runs under</h2>
<div class="rules"><ul>
<li><b>Screen:</b> &ge;{STRATEGY.min_pct_off_52w_high:.0%} off the 52-week high
<i>and</i> {abs(STRATEGY.primary_band[1]):.0%}&ndash;{abs(STRATEGY.primary_band[0]):.0%}
below the 50-day average. Above the 50dma, or more than
{abs(STRATEGY.broken_below):.0%} below it, is a different thesis and is excluded.</li>
<li><b>Structure:</b> bull put credit spread only. No naked puts, no debit spreads.</li>
<li><b>Sizing:</b> narrowest width that clears ${eff.min_credit_per_trade:,.0f} credit
at &ge;{eff.min_otm_cushion:.0%} OTM, collateral &le;
${eff.max_collateral_per_trade:,.0f}. Skip the name rather than moving the
strike to the money.</li>
<li><b>Expiration:</b> ~{STRATEGY.target_dte} DTE on a real listed Friday, confirmed
against the live chain. No earnings inside the window.</li>
<li><b>Opening range:</b> no position is opened in the first
{settings.opening_settle_minutes} minutes after the bell &mdash; paper included.
The opening book is the widest and thinnest of the day.</li>
<li><b>Exit:</b> take profit at {eff.take_profit_pct:.0%} of max credit
(band {eff.take_profit_band[0]:.0%}&ndash;{eff.take_profit_band[1]:.0%}). If the short strike is tested,
roll down-and-out or accept the defined loss &mdash; never remove the long leg.</li>
<li><b>Approval:</b> {approval_txt}</li>
<li><b>Exits are the exception:</b> profit is booked and stops are cut by the agent
itself, because a target only works taken mechanically and a stop must fire while
nobody is watching. Closing only, paper only, never on a stale mark.</li>
</ul></div>
</section>

<script>
(function(){{
  var tabs=[].slice.call(document.querySelectorAll('.tabs button'));
  function show(name){{
    tabs.forEach(function(b){{ b.setAttribute('aria-selected', b.dataset.t===name); }});
    ['now','history','ready','rules'].forEach(function(n){{
      var el=document.getElementById('p-'+n);
      if(el) el.hidden = (n!==name);
    }});
    try{{ localStorage.setItem('pcs-tab', name); }}catch(e){{}}
  }}
  tabs.forEach(function(b){{ b.addEventListener('click', function(){{ show(b.dataset.t); }}); }});
  [].slice.call(document.querySelectorAll('[data-goto]')).forEach(function(a){{
    a.addEventListener('click', function(e){{ e.preventDefault(); show(a.dataset.goto); }});
  }});
  var saved;
  try{{ saved = localStorage.getItem('pcs-tab'); }}catch(e){{}}
  if(saved && document.getElementById('p-'+saved)) show(saved);
}})();
</script>
</div></body></html>"""
    path.write_text(doc)
    return path
