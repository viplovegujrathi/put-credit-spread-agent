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
from .ledger import Ledger, Position
from .paper_broker import management_note
from .proposer import Proposal
from .session import SessionState

CSS = """
:root{--bg:#0f1216;--panel:#171b21;--line:#252b33;--ink:#e6e9ee;--dim:#8b95a3;
--pos:#3fb950;--neg:#f85149;--warn:#d29922;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-sans-serif,-apple-system,"SF Pro Text",Segoe UI,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:14px;letter-spacing:.08em;
text-transform:uppercase;color:var(--dim);margin:32px 0 12px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.banner{background:#1d2027;border-left:3px solid var(--warn);padding:10px 14px;
border-radius:0 6px 6px 0;margin:14px 0;color:#d7dce3;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.card .k{color:var(--dim);font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.card .v{font-size:21px;margin-top:5px;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--dim);padding:10px 12px;border-bottom:1px solid var(--line);font-weight:600}
td{padding:10px 12px;border-bottom:1px solid #1e232a;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.pos{color:var(--pos)} .neg{color:var(--neg)} .dim{color:var(--dim)}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
background:#22272e;color:var(--dim);border:1px solid var(--line)}
.tag.ok{color:var(--pos);border-color:#20402a} .tag.blocked{color:var(--neg);border-color:#4a2224}
.tag.act{color:var(--warn);border-color:#463a17}
.note{font-size:12px;color:var(--warn);padding:2px 0}
.rules{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 18px}
.rules li{margin:4px 0;color:#c3cad3;font-size:13px}
.empty{color:var(--dim);padding:16px;background:var(--panel);
border:1px solid var(--line);border-radius:8px;font-size:13px}
code{background:#22272e;padding:1px 6px;border-radius:4px;font-size:12px}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _sign(v: float, fmt: str = ",.0f", money: bool = True) -> str:
    cls = "pos" if v > 0 else ("neg" if v < 0 else "dim")
    txt = f"{'$' if money else ''}{v:{fmt}}"
    if v > 0:
        txt = "+" + txt
    return f'<span class="{cls}">{txt}</span>'


def _positions_table(rows: list[Position]) -> str:
    if not rows:
        return '<div class="empty">No open positions.</div>'
    body = []
    for p in rows:
        note = management_note(p)
        tag = f'<span class="tag act">{_e(note.split(":")[0])}</span>' if note else ""
        body.append(
            f"<tr><td><code>{_e(p.id)}</code></td><td><b>{_e(p.symbol)}</b>"
            f"<div class='dim' style='font-size:11px'>{_e(p.sector)}</div></td>"
            f"<td>{p.short_strike:g}/{p.long_strike:g}p</td>"
            f"<td>{_e(p.expiration)}<div class='dim' style='font-size:11px'>{p.dte} DTE</div></td>"
            f"<td>{p.contracts}</td><td>${p.credit_dollars:,.0f}</td>"
            f"<td>${p.collateral:,.0f}</td>"
            f"<td>${p.mark_cost_to_close * 100 * p.contracts:,.0f}</td>"
            f"<td>{_sign(p.open_pl)}</td><td>{p.pct_of_max_credit:.0%} {tag}"
            + (f"<div class='note'>{_e(note)}</div>" if note else "")
            + "</td></tr>")
    return (
        "<table><tr><th>id</th><th>ticker</th><th>spread</th><th>expiration</th>"
        "<th>qty</th><th>credit</th><th>collateral</th><th>cost to close</th>"
        "<th>P&amp;L</th><th>% of max credit</th></tr>" + "".join(body) + "</table>")


def _closed_table(rows: list[Position]) -> str:
    if not rows:
        return '<div class="empty">No closed positions yet.</div>'
    body = "".join(
        f"<tr><td><code>{_e(p.id)}</code></td><td><b>{_e(p.symbol)}</b></td>"
        f"<td>{p.short_strike:g}/{p.long_strike:g}p</td><td>{_e(p.expiration)}</td>"
        f"<td>${p.credit_dollars:,.0f}</td><td>${p.close_debit * 100 * p.contracts:,.0f}</td>"
        f"<td>{_sign(p.realized_pl)}</td><td class='dim'>{_e(p.close_reason)}</td></tr>"
        for p in sorted(rows, key=lambda x: x.closed_at, reverse=True))
    return ("<table><tr><th>id</th><th>ticker</th><th>spread</th><th>expiration</th>"
            "<th>credit</th><th>debit</th><th>realized</th><th>outcome</th></tr>"
            + body + "</table>")


def _proposals_table(props: list[Proposal]) -> str:
    pending = [p for p in props if p.status == "pending"]
    if not pending:
        return '<div class="empty">No pending proposals. Run <code>./run.py propose</code>.</div>'
    body = []
    for p in pending:
        s = p.spread
        tag = ('<span class="tag ok">clear</span>' if p.risk_ok
               else '<span class="tag blocked">blocked</span>')
        notes = "".join(f"<div class='note'>{_e(w)}</div>"
                        for w in (p.risk_warnings if p.risk_ok else p.risk_reasons))
        body.append(
            f"<tr><td><code>{_e(p.id)}</code></td><td><b>{_e(p.symbol)}</b>"
            f"<div class='dim' style='font-size:11px'>{_e(p.sector)}</div></td>"
            f"<td>{s['short_strike']:g}/{s['long_strike']:g}p<div class='dim' "
            f"style='font-size:11px'>${s['width']:g} wide</div></td>"
            f"<td>{_e(s['expiration'])}<div class='dim' style='font-size:11px'>"
            f"{s['dte']} DTE</div></td><td>${s['credit_dollars']:.0f}"
            f"<div class='dim' style='font-size:11px'>nat ${s['credit_nat_dollars']:.0f}</div></td>"
            f"<td>${s['collateral']:.0f}</td><td>{s['roc']:.0%}</td>"
            f"<td>{s['cushion']:.1%}</td>"
            f"<td>{'-' if s['pop_est'] is None else format(s['pop_est'], '.0%')}</td>"
            f"<td>{tag}{notes}</td></tr>")
    return ("<table><tr><th>proposal</th><th>ticker</th><th>spread</th><th>expiration</th>"
            "<th>credit</th><th>collateral</th><th>ROC</th><th>cushion</th><th>POP</th>"
            "<th>status</th></tr>" + "".join(body) + "</table>")


def render(led: Ledger, props: list[Proposal], settings: Settings,
           sess: SessionState, path=DASHBOARD_HTML):
    used_pct = (led.collateral_held / settings.max_total_collateral
                if settings.max_total_collateral else 0)
    sectors = ", ".join(f"{k} x{v}" for k, v in sorted(led.sector_counts().items())) or "none"
    cards = [
        ("net liquidation", f"${led.net_liq:,.2f}"),
        ("cash", f"${led.cash:,.2f}"),
        ("collateral at risk", f"${led.collateral_held:,.0f}"),
        ("buying power", f"${led.buying_power:,.2f}"),
        ("realized P&amp;L", _sign(led.realized_pl, ",.2f")),
        ("unrealized P&amp;L", _sign(led.unrealized_pl, ",.2f")),
        ("total return", _sign(led.total_return * 100, ".2f", money=False) + "%"),
        ("open positions", f"{len(led.open_positions)} / {settings.max_open_positions}"),
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

<div class="cards">{card_html}</div>

<h2>Portfolio limits</h2>
<div class="rules"><ul>
<li>Collateral deployed: <b>${led.collateral_held:,.0f}</b> of
${settings.max_total_collateral:,.0f} cap ({used_pct:.0%} used)</li>
<li>Open positions: <b>{len(led.open_positions)}</b> of {settings.max_open_positions}
&middot; max {settings.max_positions_per_sector} per sector &middot; currently: {_e(sectors)}</li>
<li>Per trade (fixed by the strategy): collateral &le;
${STRATEGY.max_collateral_per_trade:,.0f}, credit &ge;
${STRATEGY.min_credit_per_trade:,.0f}, no upper cap on credit</li>
</ul></div>

<h2>Open positions</h2>
{_positions_table(led.open_positions)}

<h2>Pending proposals &mdash; awaiting human approval</h2>
{_proposals_table(props)}

<h2>Closed positions</h2>
{_closed_table(led.closed_positions)}

<h2>Rules this agent runs under</h2>
<div class="rules"><ul>
<li><b>Screen:</b> &ge;{STRATEGY.min_pct_off_52w_high:.0%} off the 52-week high
<i>and</i> {abs(STRATEGY.primary_band[1]):.0%}&ndash;{abs(STRATEGY.primary_band[0]):.0%}
below the 50-day average. Above the 50dma, or more than
{abs(STRATEGY.broken_below):.0%} below it, is a different thesis and is excluded.</li>
<li><b>Structure:</b> bull put credit spread only. No naked puts, no debit spreads.</li>
<li><b>Sizing:</b> narrowest width that clears ${STRATEGY.min_credit_per_trade:,.0f} credit
at &ge;{STRATEGY.min_otm_cushion:.0%} OTM, collateral &le;
${STRATEGY.max_collateral_per_trade:,.0f}. Skip the name rather than moving the
strike to the money.</li>
<li><b>Expiration:</b> ~{STRATEGY.target_dte} DTE on a real listed Friday, confirmed
against the live chain. No earnings inside the window.</li>
<li><b>Exit:</b> take profit at {STRATEGY.take_profit_band[0]:.0%}&ndash;
{STRATEGY.take_profit_band[1]:.0%} of max credit. If the short strike is tested,
roll down-and-out or accept the defined loss &mdash; never remove the long leg.</li>
<li><b>Approval:</b> every trade needs explicit per-trade human sign-off. This agent
proposes; it never places.</li>
</ul></div>
</div></body></html>"""
    path.write_text(doc)
    return path
