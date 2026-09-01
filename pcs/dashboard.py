"""Step 9 -- the dashboard.

A single self-contained HTML file, rewritten on every run, showing the paper
account, open positions with their management state, the current proposal
queue, and the rules the agent is operating under. No external assets, so it
opens straight off disk.
"""

from __future__ import annotations

import datetime as dt
import html
import sys

from . import learning, watchlist
from .config import DASHBOARD_HTML, STRATEGY, WEB_INDEX, Settings
from .exits import decide
from .ledger import Ledger, Position
from .proposer import Proposal
from .readiness import assess
from .session import SessionState

# The strategy in a mark: a price path falling, and the floor the long leg puts
# under it. Inline SVG rather than a file -- the dashboard is one self-contained
# page that gets copied to a web root, and an asset it could arrive without is a
# broken image on the only view of the account.
# One mark, defined once. The header logo and the tab icon drew the same path
# from two separate string literals, so an edit to one silently drifted from
# the other. The line ends up and to the right, with a pullback in the middle:
# a beaten-down name bouncing off support is the whole thesis, and an icon that
# ended lower than it started said the opposite.
_MARK_LINE = "M5.5 22 L12.5 15.5 L17 19 L25.5 8.5"
_MARK_ARROW = "M20.4 8.5 H25.5 V13.6"
_MARK_BASE = "M5.5 26 H26.5"

LOGO_SVG = f"""<svg class="logo" viewBox="0 0 32 32" fill="none" aria-hidden="true">
<rect width="32" height="32" rx="8.5" fill="url(#lg)"/>
<rect width="32" height="32" rx="8.5" fill="url(#lv)"/>
<path d="{_MARK_LINE}" stroke="#fff" stroke-width="2.3"
 stroke-linecap="round" stroke-linejoin="round"/>
<path d="{_MARK_ARROW}" stroke="#fff" stroke-width="2.3"
 stroke-linecap="round" stroke-linejoin="round"/>
<path d="{_MARK_BASE}" stroke="#fff" stroke-opacity=".62" stroke-width="2.6"
 stroke-linecap="round"/>
<defs>
<linearGradient id="lg" x1="0" y1="0" x2="30" y2="32" gradientUnits="userSpaceOnUse">
<stop stop-color="#4b9bf5"/><stop offset="1" stop-color="#2ea55c"/></linearGradient>
<linearGradient id="lv" x1="16" y1="0" x2="16" y2="32" gradientUnits="userSpaceOnUse">
<stop stop-color="#fff" stop-opacity=".18"/><stop offset="1" stop-opacity=".12"/>
</linearGradient>
</defs></svg>"""


def _favicon() -> str:
    """Same mark, inlined as a data URI so the tab icon needs no second request."""
    import urllib.parse
    ico = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           '<rect width="32" height="32" rx="8.5" fill="#2f7fe0"/>'
           f'<path d="{_MARK_LINE}" stroke="#fff" stroke-width="2.6" fill="none" '
           'stroke-linecap="round" stroke-linejoin="round"/>'
           f'<path d="{_MARK_ARROW}" stroke="#fff" stroke-width="2.6" fill="none" '
           'stroke-linecap="round" stroke-linejoin="round"/>'
           f'<path d="{_MARK_BASE}" stroke="#fff" stroke-opacity=".65" '
           'stroke-width="2.8" stroke-linecap="round"/></svg>')
    return "data:image/svg+xml," + urllib.parse.quote(ico)


CSS = """
/* Light by default. The dark palette is a deliberate opt-in via the header
   toggle, not the system preference -- this page is read on a phone in a
   bright room as often as at a desk at night, and guessing wrong on the one
   view of a live account is worse than making it one click. */
:root{
--bg:#f4f7fc; --bg2:#eef3fb;
--panel:#ffffff; --panel2:#fafcff; --head:#f3f6fc;
--line:#e2e8f2; --line2:#eef2f8;
--ink:#101828; --ink2:#344054; --dim:#68758a;
--pos:#0b8f4e; --posbg:#e8f7ef; --posln:#b6e5cb;
--neg:#d0342c; --negbg:#fdedec; --negln:#f6c9c6;
--warn:#a35c05; --warnbg:#fff5e6; --warnln:#f2d9a8;
--accent:#2f6bed; --accbg:#eaf0fe; --accln:#c3d5fb;
--info:#7a3fd6; --infobg:#f3ecfe; --infoln:#ddc9f8;
--pink:#c0316f; --pinkbg:#fdecf3; --pinkln:#f6c6da;
--chip:#f0f4fa;
--sh:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.05);
--sh2:0 4px 14px rgba(16,24,40,.07),0 1px 3px rgba(16,24,40,.05);
--glow1:rgba(47,107,237,.10); --glow2:rgba(11,143,78,.09); --glow3:rgba(122,63,214,.07);
}
:root[data-theme="dark"]{
--bg:#0b0e14; --bg2:#0e131b;
--panel:#151a23; --panel2:#1a212c; --head:#121821;
--line:#242c39; --line2:#1c2330;
--ink:#e9edf4; --ink2:#c3cddb; --dim:#8794a7;
--pos:#4ac26b; --posbg:rgba(74,194,107,.10); --posln:#215a35;
--neg:#f4726a; --negbg:rgba(244,114,106,.10); --negln:#5e2a28;
--warn:#e0a63c; --warnbg:rgba(224,166,60,.10); --warnln:#5d451a;
--accent:#5c9bff; --accbg:rgba(92,155,255,.11); --accln:#27456f;
--info:#b189f5; --infobg:rgba(177,137,245,.11); --infoln:#432f66;
--pink:#ef82ab; --pinkbg:rgba(239,130,171,.11); --pinkln:#632c44;
--chip:#1e2532;
--sh:0 1px 2px rgba(0,0,0,.4); --sh2:0 6px 20px rgba(0,0,0,.42);
--glow1:rgba(92,155,255,.10); --glow2:rgba(74,194,107,.06); --glow3:rgba(177,137,245,.06);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;
font:14px/1.55 ui-sans-serif,-apple-system,"SF Pro Text",Segoe UI,Inter,sans-serif;
background-image:
radial-gradient(820px 420px at 8% -10%,var(--glow1),transparent 70%),
radial-gradient(700px 380px at 96% -6%,var(--glow2),transparent 70%),
radial-gradient(760px 460px at 55% 110%,var(--glow3),transparent 70%);
background-attachment:fixed}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 72px}

/* -- header ------------------------------------------------------------- */
.top{display:flex;align-items:center;gap:14px;margin-bottom:7px}
.brand{display:flex;align-items:center;gap:13px;min-width:0}
.logo{width:42px;height:42px;flex:none;border-radius:11px;
box-shadow:0 4px 14px rgba(47,107,237,.28),0 0 0 1px rgba(255,255,255,.14) inset}
.brandtext{min-width:0}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.025em;line-height:1.2;font-weight:680;
background:linear-gradient(96deg,var(--ink),var(--accent) 165%);
-webkit-background-clip:text;background-clip:text;color:transparent}
.brandtag{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
font-weight:700}
.themer{margin-left:auto;flex:none;appearance:none;cursor:pointer;width:34px;height:34px;
border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--dim);
box-shadow:var(--sh);font-size:15px;line-height:1;display:grid;place-items:center;
transition:color .15s,border-color .15s,transform .15s}
.themer:hover{color:var(--accent);border-color:var(--accln);transform:translateY(-1px)}
h2{font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
margin:30px 0 12px;font-weight:700}
.sub{color:var(--dim);font-size:13px;margin-bottom:16px}
.sub b{color:var(--ink2);font-weight:650}
.banner{background:linear-gradient(92deg,var(--warnbg),transparent 78%);
border:1px solid var(--warnln);border-left:3px solid var(--warn);
padding:11px 15px;border-radius:4px 10px 10px 4px;margin:16px 0;color:var(--ink2);
font-size:13px}

/* -- stat cards --------------------------------------------------------- */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px}
.card{position:relative;background:linear-gradient(180deg,var(--panel),var(--panel2));
border:1px solid var(--line);border-radius:13px;padding:15px 16px;box-shadow:var(--sh);
overflow:hidden;transition:box-shadow .16s,transform .16s,border-color .16s}
.card::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;
background:linear-gradient(90deg,var(--accent),transparent 88%);opacity:.85}
.card.c-pos::before{background:linear-gradient(90deg,var(--pos),transparent 88%)}
.card.c-neg::before{background:linear-gradient(90deg,var(--neg),transparent 88%)}
.card.c-warn::before{background:linear-gradient(90deg,var(--warn),transparent 88%)}
.card.c-info::before{background:linear-gradient(90deg,var(--info),transparent 88%)}
.card:hover{box-shadow:var(--sh2);transform:translateY(-2px);border-color:var(--accln)}
.card .k{color:var(--dim);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
font-weight:700}
.card .v{font-size:23px;margin-top:6px;font-variant-numeric:tabular-nums;
letter-spacing:-.025em;font-weight:620;color:var(--ink)}

/* -- tables ------------------------------------------------------------- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:13px}
table{width:100%;min-width:760px;border-collapse:separate;border-spacing:0;
background:var(--panel);border:1px solid var(--line);border-radius:13px;overflow:hidden;
box-shadow:var(--sh)}
th{text-align:left;font-size:10px;letter-spacing:.08em;text-transform:uppercase;
color:var(--dim);padding:11px 13px;background:var(--head);
border-bottom:1px solid var(--line);font-weight:700;white-space:nowrap}
td{padding:11px 13px;border-bottom:1px solid var(--line2);
font-variant-numeric:tabular-nums;vertical-align:top;color:var(--ink2)}
tbody tr:last-child td{border-bottom:none}
tbody tr{transition:background .12s} tbody tr:hover{background:var(--panel2)}
.pos{color:var(--pos);font-weight:600} .neg{color:var(--neg);font-weight:600}
.dim{color:var(--dim)}

/* -- chips -------------------------------------------------------------- */
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
background:var(--chip);color:var(--dim);border:1px solid var(--line);white-space:nowrap;
font-weight:600}
.tag.ok{color:var(--pos);border-color:var(--posln);background:var(--posbg)}
.tag.blocked{color:var(--neg);border-color:var(--negln);background:var(--negbg)}
.tag.act{color:var(--warn);border-color:var(--warnln);background:var(--warnbg)}
.note{font-size:12px;color:var(--warn);padding:3px 0;line-height:1.45}
.dte{display:inline-block;padding:1.5px 8px;border-radius:999px;font-size:11px;
font-weight:650;background:var(--chip);border:1px solid var(--line);color:var(--dim);
white-space:nowrap}
.dte.soon{color:var(--warn);border-color:var(--warnln);background:var(--warnbg)}
.dte.now{color:var(--neg);border-color:var(--negln);background:var(--negbg)}
.exp{font-variant-numeric:tabular-nums;white-space:nowrap}

/* -- panels ------------------------------------------------------------- */
.rules{background:var(--panel);border:1px solid var(--line);border-radius:13px;
padding:16px 20px;box-shadow:var(--sh)}
.rules li{margin:6px 0;color:var(--ink2);font-size:13px}
.rules li b{color:var(--ink)}
.rules.dev{border-color:var(--warnln);
background:linear-gradient(180deg,var(--warnbg),var(--panel) 62%)}
.devh{font-weight:700;color:var(--warn);font-size:13px;margin-bottom:6px;
letter-spacing:.01em}
.devf{color:var(--dim);font-size:12px;margin-top:8px}
.devlink{color:var(--warn);text-decoration:none;border-bottom:1px dotted var(--warnln);
font-weight:600}
.lede{color:var(--dim);font-size:13px;margin:-4px 0 13px;max-width:66ch;line-height:1.55}
.empty{color:var(--dim);padding:18px;background:var(--panel);
border:1px dashed var(--line);border-radius:13px;font-size:13px}
code{background:var(--chip);padding:1.5px 6px;border-radius:5px;font-size:12px;
border:1px solid var(--line2);color:var(--ink2);
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}

/* -- watchlist ---------------------------------------------------------- */
.wpills{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:12px}
.wpill{font-size:11px;letter-spacing:.04em;padding:4px 10px;border-radius:999px;
background:var(--panel);border:1px solid var(--line);color:var(--dim);
box-shadow:var(--sh);font-weight:600}
.wpill b{color:var(--ink);margin-left:3px}
.sig{font-size:10px;font-weight:700;letter-spacing:.06em;padding:3px 8px;
border-radius:6px;white-space:nowrap;cursor:help}
.s-ready{background:var(--posbg);color:var(--pos);border:1px solid var(--posln)}
.s-block{background:var(--warnbg);color:var(--warn);border:1px solid var(--warnln)}
.s-hold {background:var(--accbg);color:var(--accent);border:1px solid var(--accln)}
.s-earn {background:var(--pinkbg);color:var(--pink);border:1px solid var(--pinkln)}
.s-nofit{background:var(--chip);color:var(--dim);border:1px solid var(--line)}
.s-near {background:var(--chip);color:var(--dim);border:1px solid var(--line)}
.why{display:block;margin-top:5px;font-size:12px;color:var(--dim);
line-height:1.45;white-space:normal;max-width:34ch}
.wnote{font-size:12px;color:var(--warn);background:var(--warnbg);
border:1px solid var(--warnln);border-radius:10px;padding:9px 12px;margin-bottom:12px;
line-height:1.5}
.wnote.ok{color:var(--pos);background:var(--posbg);border-color:var(--posln)}

/* -- tabs --------------------------------------------------------------- */
.tabs{display:flex;gap:2px;margin:24px 0 18px;border-bottom:1px solid var(--line);
flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.tabs::-webkit-scrollbar{display:none}
.tabs button{appearance:none;background:none;border:none;
border-bottom:2px solid transparent;color:var(--dim);font:inherit;font-weight:650;
font-size:13px;padding:10px 15px;cursor:pointer;margin-bottom:-1px;
transition:color .12s,border-color .12s;white-space:nowrap;flex:none}
.tabs button:hover{color:var(--ink)}
.tabs button[aria-selected="true"]{color:var(--accent);border-bottom-color:var(--accent)}
.tabs .pill{display:inline-block;margin-left:7px;padding:1px 7px;border-radius:999px;
background:var(--chip);font-size:10.5px;color:var(--dim);font-weight:700;
border:1px solid var(--line)}
.tabs button[aria-selected="true"] .pill{background:var(--accbg);color:var(--accent);
border-color:var(--accln)}
.panel[hidden]{display:none}

/* -- readiness meter ---------------------------------------------------- */
.meter{background:var(--panel);border:1px solid var(--line);border-radius:13px;
padding:18px 20px;box-shadow:var(--sh)}
.meter .top{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
flex-wrap:wrap;margin-bottom:12px}
.meter .score{font-size:27px;font-weight:680;letter-spacing:-.025em;
font-variant-numeric:tabular-nums;color:var(--ink)}
.bar{height:9px;border-radius:999px;background:var(--chip);overflow:hidden;
border:1px solid var(--line)}
.bar span{display:block;height:100%;border-radius:999px;transition:width .4s ease}
.bar span.low{background:linear-gradient(90deg,#f0857c,var(--neg))}
.bar span.mid{background:linear-gradient(90deg,#e8b968,var(--warn))}
.bar span.high{background:linear-gradient(90deg,#5fd08f,var(--pos))}
.checks{margin-top:16px;display:grid;gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:11px;overflow:hidden}
.chk{display:grid;grid-template-columns:22px 1fr;gap:11px;padding:11px 14px;
background:var(--panel);align-items:start;font-size:13px;color:var(--ink2)}
.chk .m{font-weight:700;line-height:1.3}
.chk .m.y{color:var(--pos)} .chk .m.n{color:var(--neg)} .chk .m.w{color:var(--warn)}
.chk .d{color:var(--dim);font-size:12px;margin-top:2px;line-height:1.45}

/* -- event log ---------------------------------------------------------- */
.evt{display:grid;grid-template-columns:148px 118px 1fr;gap:12px;padding:9px 0;
border-bottom:1px solid var(--line2);font-size:13px;align-items:baseline;
color:var(--ink2)}
.evt:last-child{border-bottom:none}
.evt time{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px}
.evt .what{font-weight:700;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase}
.evt .what.open{color:var(--accent)} .evt .what.close{color:var(--warn)}
.evt .what.exit{color:var(--pos)} .evt .what.dim{color:var(--dim)}
.c{display:block}

/* -- learning ----------------------------------------------------------- */
.lesson{background:var(--panel);border:1px solid var(--line);border-left:3px solid
var(--accent);border-radius:4px 13px 13px 4px;padding:14px 17px;margin-bottom:10px;
box-shadow:var(--sh)}
.lesson.tentative{border-left-color:var(--warn)}
.lesson.insufficient{border-left-color:var(--dim)}
.lesson h3{margin:0 0 5px;font-size:14px;font-weight:660;color:var(--ink);
display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.lesson p{margin:0;color:var(--ink2);font-size:13px;line-height:1.55}
.lesson .fix{margin-top:9px;font-size:12.5px;color:var(--dim)}
.conf{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
padding:3px 8px;border-radius:6px;white-space:nowrap}
.conf.supported{background:var(--posbg);color:var(--pos);border:1px solid var(--posln)}
.conf.tentative{background:var(--warnbg);color:var(--warn);border:1px solid var(--warnln)}
.conf.insufficient{background:var(--chip);color:var(--dim);border:1px solid var(--line)}

@media (max-width:700px){
.wrap{padding:18px 13px 54px}
h1{font-size:16.5px} h2{margin:24px 0 10px}
.logo{width:34px;height:34px} .brand{gap:10px} .brandtag{font-size:9.5px}
.themer{width:31px;height:31px}
.cards{grid-template-columns:repeat(auto-fit,minmax(144px,1fr));gap:9px}
.card{padding:12px 13px;border-radius:11px} .card .v{font-size:19px}
.tabs{gap:0;margin:18px 0 14px} .tabs button{padding:10px 13px;font-size:12.5px}
.scroll{overflow-x:visible;border-radius:0}
table{min-width:0;border:none;background:none;overflow:visible;box-shadow:none}
thead{display:none}
tbody tr{display:block;background:linear-gradient(180deg,var(--panel),var(--panel2));
border:1px solid var(--line);border-radius:13px;padding:5px 14px;margin-bottom:10px;
box-shadow:var(--sh)}
tbody tr:hover{background:var(--panel)}
tbody td{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
padding:8px 0;text-align:right;border-bottom:1px solid var(--line2)}
tbody tr td:last-child{border-bottom:none}
tbody td::before{content:attr(data-l);color:var(--dim);font-size:10px;letter-spacing:.07em;
text-transform:uppercase;text-align:left;flex:0 0 auto;padding-top:3px;font-weight:700}
tbody td .c{text-align:right;min-width:0;overflow-wrap:anywhere}
.note{text-align:right}
.evt{grid-template-columns:1fr;gap:3px;padding:11px 0}
.meter{padding:15px 16px} .meter .score{font-size:22px}
.chk{grid-template-columns:20px 1fr;padding:10px 12px}
.rules{padding:14px 17px} .rules ul{padding-left:19px}
.lesson{padding:13px 15px}
}

@media (prefers-reduced-motion:reduce){
*{transition:none!important}
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


def _pl_class(v: float) -> str:
    """Accent stripe for a P&L card. Flat is neutral -- a book that has done
    nothing should not be tinted like a book that has lost something."""
    if v > 0:
        return "c-pos"
    if v < 0:
        return "c-neg"
    return ""


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


_SIG_CLASS = {"HOLDING": "s-hold", "READY": "s-ready", "BLOCKED": "s-block",
              "EARNINGS": "s-earn", "NO_FIT": "s-nofit", "NEAR": "s-near",
              "STRETCHED": "s-near"}


def _watch_pill() -> str:
    """Never a bare count.

    This used to render the READY count when it was non-zero and the total
    entry count otherwise -- so "1" meant either "one name is ready to trade"
    or "one name is tracked and none are ready", which are opposite readings of
    the same glyph. The word is what carries the meaning; keep it.
    """
    wl = watchlist.load()
    if wl is None:
        return "none"
    ready = wl.count("READY")
    return f"{ready} ready" if ready else f"{len(wl.entries)} watched"


def _watchlist_panel() -> str:
    """What the agent is watching. Read from disk so `watch` can refresh it on
    its own schedule -- around the clock -- without any other command running."""
    wl = watchlist.load()
    if wl is None:
        return ('<div class="rules"><ul><li>No watchlist yet. Build one with '
                '<code>./run.py watch</code> &mdash; it never opens anything, so it '
                'can run at any hour.</li></ul></div>')

    # A benched name still gets watched -- watching is free -- but READY means
    # "opens on the next run", and for a benched name that is false. Say it
    # here rather than letting two tabs of the same page disagree.
    benched = learning.blocked_symbols(learning.load())
    hit = sorted(benched & {e.symbol for e in wl.entries})
    bench_note = (f'<div class="wnote">Benched by self-repair and skipped by '
                  f'<code>propose</code> regardless of signal: '
                  f'<b>{_e(", ".join(hit))}</b>. See the Learning tab.</div>'
                  if hit else "")

    counts = [(sig, wl.count(sig)) for sig in
              ("READY", "BLOCKED", "EARNINGS", "NO_FIT", "NEAR", "STRETCHED")]
    pills = "".join(
        f'<span class="wpill {_SIG_CLASS.get(sig, "")}">{_e(sig.replace("_", " "))}'
        f' <b>{n}</b></span>' for sig, n in counts if n)

    # A stale premium is not a premium you can get. Say so above the numbers,
    # not in a footnote under them.
    if wl.tradeable:
        banner = ('<div class="wnote ok">Priced on a live market '
                  f'&mdash; refreshed {_e(wl.generated_at.replace("T", " "))}</div>')
    else:
        banner = (f'<div class="wnote">Priced on a {_e(wl.quote_quality.replace("_", " "))} '
                  f'({_e(wl.phase)}) &mdash; these show where each name stands, '
                  f'not what it would fill at. Refreshed '
                  f'{_e(wl.generated_at.replace("T", " "))}.</div>')

    rows = []
    for e in wl.entries:
        # `blockers` holds the real reasons from risk.check() and used to be
        # computed and thrown away, with `reason` reachable only by hovering.
        # A name could then sit BLOCKED for a week with no way to tell whether
        # closing one winner would unblock it or whether nothing would.
        why = "; ".join(e.blockers) if e.blockers else e.reason
        sig = (f'<span class="sig {_SIG_CLASS.get(e.signal, "")}">'
               f'{_e(e.signal.replace("_", " "))}</span>'
               + (f'<span class="why">{_e(why)}</span>' if why else ""))
        if e.has_spread:
            spread = f"{e.short_strike:g}/{e.long_strike:g}p"
            prem = (f'<b>${e.credit_dollars:,.0f}</b>'
                    f'<span class="sub">nat ${e.credit_nat_dollars:,.0f}</span>')
            rate = f'<b>{e.roc:.1%}</b><span class="sub">on ${e.collateral:,.0f}</span>'
            cush = f"{e.cushion:.1%}"
            exp = _expiry_cell(e.expiration, e.dte)
        else:
            spread = prem = rate = cush = exp = '<span class="sub">&mdash;</span>'
        rows.append([
            f'<b>{_e(e.symbol)}</b><span class="sub">{_e(e.sector)}</span>',
            sig, spread, exp, prem, rate, cush,
            f'{e.pct_off_high:.0%}<span class="sub">{e.pct_from_dma50:+.1%} vs 50dma</span>',
        ])
    table = _table(["ticker", "signal", "strikes", "expiry", "premium", "rate",
                    "cushion", "off high"], rows)
    return f'<div class="wpills">{pills}</div>{bench_note}{banner}{table}'


def _learn_pill(settings: Settings) -> str:
    j = learning.load()
    s = learning.summary(j, settings)
    if s["quarantined"]:
        return f'{len(s["quarantined"])} benched'
    return f'{s["closed"]}/{s["needed"]}'


def _learning_panel(settings: Settings) -> str:
    """What the record supports, and what the agent repaired by itself.

    The two halves are rendered apart on purpose. Mixing "the agent benched a
    ticker" in with "wider cushions did better on 11 trades" invites reading
    both as things that happened, when only the first one did.
    """
    j = learning.load()
    s = learning.summary(j, settings)

    cards = [
        ("closed trades", f'{s["closed"]}', ""),
        ("wins / losses", f'{s["wins"]} / {s["losses"]}',
         "c-pos" if s["wins"] > s["losses"] else ("c-neg" if s["losses"] else "")),
        ("learning threshold", f'{s["needed"]}', "c-info"),
        ("faults logged", f'{s["faults"]}', "c-warn" if s["faults"] else ""),
        ("benched now", f'{len(s["quarantined"])}',
         "c-warn" if s["quarantined"] else ""),
        ("self-repairs", f'{s["repairs"]}', "c-info"),
    ]
    card_html = "".join(f'<div class="card {c}"><div class="k">{k}</div>'
                        f'<div class="v">{v}</div></div>' for k, v, c in cards)

    body = [f'<div class="cards">{card_html}</div>']

    # -- the half the agent acts on itself ---------------------------------
    body.append("<h2>Repairs the agent made itself</h2>")
    body.append('<p class="lede">Bounded on purpose. A quarantine can only take a '
                'name <i>out</i> of the proposal list, never put one in, and it '
                'expires on its own &mdash; so an outage at the data provider cannot '
                'quietly shrink the universe for good.</p>')
    if j.quarantines:
        rows = [[_e(q.symbol), _e(q.since), _e(q.until), str(q.faults), _e(q.reason)]
                for q in sorted(j.quarantines, key=lambda x: x.until)]
        body.append(_table(["symbol", "benched", "until", "failures", "why"], rows))
    else:
        body.append('<div class="empty">Nothing is benched. Every screened name is '
                    'eligible to be proposed.</div>')
    if j.repairs:
        log = "".join(
            f'<div class="evt"><time>{_e(r.get("at", "").replace("T", " "))}</time>'
            f'<span class="what exit">repair</span><span>{_e(r.get("action", ""))}</span>'
            f"</div>" for r in reversed(j.repairs[-25:]))
        body.append(f'<h2>Repair log</h2><div class="rules">{log}</div>')

    # -- the half that is only ever a suggestion ---------------------------
    body.append("<h2>What the closed record supports</h2>")
    body.append('<p class="lede">Suggestions, and nothing else. The agent does not '
                'apply any of these &mdash; a pattern drawn from a dozen fills is a '
                'hypothesis, and moving a risk rule to fit it is how a strategy gets '
                'worse while its numbers look better. Skill-fixed rules are never '
                'suggested for change at all.</p>')
    for les in learning.lessons(j, settings):
        fix = (f'<div class="fix">Suggested, <b>not applied</b>: '
               f"<code>{_e(les.suggestion)}</code></div>" if les.suggestion else "")
        body.append(
            f'<div class="lesson {_e(les.confidence)}"><h3>{_e(les.title)}'
            f'<span class="conf {_e(les.confidence)}">{_e(les.confidence)}</span>'
            f'<span class="tag">n={les.sample}</span></h3>'
            f"<p>{_e(les.finding)}</p>{fix}</div>")

    gaps = "".join(f"<li>{_e(g)}</li>" for g in learning.feature_gaps())
    body.append(
        f"<h2>Not learnable yet</h2>"
        f'<div class="rules"><div class="devf" style="margin:0 0 8px">These were '
        f"never written down at open, so their absence from the findings above "
        f'means "unmeasured", not "no effect".</div><ul>{gaps}</ul></div>')
    return "".join(body)


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


def _publish(doc: str) -> None:
    """Copy the rendered page into the web root a server actually serves.

    Written atomically: a reader mid-request must never get half a page. A
    failure is reported rather than swallowed -- a dashboard that quietly stops
    updating is worse than no dashboard, because it presents a stale book as
    the current one, and that is the exact failure this function exists to fix.
    """
    if WEB_INDEX is None:
        return
    tmp = WEB_INDEX.with_name(WEB_INDEX.name + ".tmp")
    try:
        tmp.write_text(doc, encoding="utf-8")
        tmp.replace(WEB_INDEX)
    except OSError as exc:
        print(f"warning: rendered {DASHBOARD_HTML} but could not publish to "
              f"{WEB_INDEX} ({exc}) -- the served page is now STALE.",
              file=sys.stderr)


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
        ("net liquidation", f"${led.net_liq:,.2f}", ""),
        ("cash", f"${led.cash:,.2f}", ""),
        ("collateral at risk", f"${led.collateral_held:,.0f}", "c-warn"),
        ("available balance", f"${led.buying_power:,.2f}", "c-info"),
        ("realized P&amp;L", _sign(led.realized_pl, ",.2f"), _pl_class(led.realized_pl)),
        ("unrealized P&amp;L", _sign(led.unrealized_pl, ",.2f"), _pl_class(led.unrealized_pl)),
        ("total return", _sign(led.total_return * 100, ".2f", money=False) + "%",
         _pl_class(led.total_return)),
        ("open positions", f"{len(led.open_positions)} / {settings.max_open_positions}", ""),
        ("next expiry", _next_expiry(led.open_positions), "c-info"),
    ]
    card_html = "".join(f'<div class="card {c}"><div class="k">{k}</div>'
                        f'<div class="v">{v}</div></div>' for k, v, c in cards)

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Put Credit Spread Agent - {_e(settings.account_label)}</title>
<link rel="icon" href="{_favicon()}">
<style>{CSS}</style>
<script>/* Applied before first paint: a saved dark theme must not flash light. */
try{{var t=localStorage.getItem('pcs-theme');
if(t)document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}</script>
</head><body><div class="wrap">
<div class="top"><div class="brand">{LOGO_SVG}<div class="brandtext">
<h1>Put Credit Spread Agent</h1>
<div class="brandtag">S&amp;P 500 &middot; beaten-down &middot; defined risk</div>
</div></div>
<button class="themer" id="themer" type="button" aria-label="Switch between the light
and dark palette" title="Light / dark">&#9681;</button></div>
<div class="sub">{_e(settings.account_label)} &middot; mode <b>{_e(led.mode)}</b> &middot;
opened {_e(led.created_at[:10])} &middot; rebuilt {dt.datetime.now():%Y-%m-%d %H:%M}</div>
<div class="banner">{_e(sess.banner)}</div>

<div class="tabs" role="tablist">
<button role="tab" aria-selected="true" data-t="now">Positions</button>
<button role="tab" aria-selected="false" data-t="history">History<span class="pill"
>{len(led.closed_positions)}</span></button>
<button role="tab" aria-selected="false" data-t="ready">Go-live<span class="pill"
>{_ready_pill(led, settings)}</span></button>
<button role="tab" aria-selected="false" data-t="watch">Watchlist<span class="pill"
>{_watch_pill()}</span></button>
<button role="tab" aria-selected="false" data-t="learn">Learning<span class="pill"
>{_learn_pill(settings)}</span></button>
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

<section class="panel" id="p-watch" hidden>
<h2>Watchlist</h2>
<p class="lede">Names the agent is tracking, with the spread it would take and
why it has not. Refreshed on its own schedule &mdash; watching is not trading,
so this runs around the clock.</p>
{_watchlist_panel()}
</section>

<section class="panel" id="p-learn" hidden>
<h2>Self-learning</h2>
<p class="lede">The agent keeps a journal of every closed trade and of its own
operational failures, and treats them differently. Failures it repairs itself.
Trades it can only report on &mdash; and only once there are enough of them.</p>
{_learning_panel(settings)}
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
    ['now','history','watch','ready','learn','rules'].forEach(function(n){{
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

  var root=document.documentElement, btn=document.getElementById('themer');
  if(btn) btn.addEventListener('click', function(){{
    var dark = root.getAttribute('data-theme') !== 'dark';
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    try{{ localStorage.setItem('pcs-theme', dark ? 'dark' : 'light'); }}catch(e){{}}
  }});
}})();
</script>
</div></body></html>"""
    path.write_text(doc)
    _publish(doc)
    return path
