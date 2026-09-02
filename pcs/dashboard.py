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

from . import brand, health, learning, watchlist
from .config import (
    DASHBOARD_HTML,
    DASHBOARD_SETTABLE,
    STRATEGY,
    WEB_INDEX,
    Settings,
    load_overrides,
)
from .exits import decide
from .ledger import Ledger, Position
from .proposer import Proposal
from .readiness import assess
from .session import SessionState

# The mark lives in pcs/brand.py so the login page can draw the same one
# without importing this module -- authd must not pull pandas and yfinance into
# a process that only serves a form. Inline SVG rather than a file: the
# dashboard is one self-contained page copied to a web root, and an asset it
# could arrive without is a broken image on the only view of the account.
LOGO_SVG = brand.LOGO_SVG
_favicon = brand.favicon


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
/* The second line inside a table cell. This used to borrow `.sub`, which is
   the PAGE subtitle: a span with no display rule and a 16px bottom margin.
   Inline, it ran every pair together -- "METACommunication Services",
   "$322nat", "3.2%66% est. win" -- and the margin did nothing. One class, one
   meaning. */
.csub{display:block;margin-top:3px;font-size:11.5px;line-height:1.35;
color:var(--dim);font-weight:500;letter-spacing:.01em}
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
/* --- the one editable setting, and signing out --------------------- */
.setf{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:9px 0 2px}
.setf label{font-size:12px;color:var(--dim);letter-spacing:.02em}
.setf input[type=number]{width:78px;padding:5px 8px;border-radius:8px;
border:1px solid var(--line);background:var(--panel);color:var(--ink);
font:inherit;font-variant-numeric:tabular-nums;-moz-appearance:textfield}
.setf input[type=number]:focus{outline:2px solid var(--accln);outline-offset:1px;
border-color:var(--accent)}
.setf button{appearance:none;cursor:pointer;padding:5px 13px;border-radius:8px;
border:1px solid var(--accln);background:var(--accbg);color:var(--accent);
font:inherit;font-weight:650}
.setf button:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.setnote{font-size:11.5px;color:var(--dim)}
.sortbar{display:flex;align-items:center;gap:8px;margin:0 0 11px}
.sortbar label{font-size:11.5px;color:var(--dim);letter-spacing:.02em}
.sortsel{appearance:none;cursor:pointer;padding:5px 26px 5px 9px;border-radius:8px;
border:1px solid var(--line);background:var(--panel);color:var(--ink);font:inherit;
font-size:12.5px;background-image:linear-gradient(45deg,transparent 50%,var(--dim) 50%),
linear-gradient(135deg,var(--dim) 50%,transparent 50%);
background-position:calc(100% - 14px) 52%,calc(100% - 9px) 52%;
background-size:5px 5px,5px 5px;background-repeat:no-repeat}
.sortsel:focus{outline:2px solid var(--accln);outline-offset:1px}
.sortdir{appearance:none;cursor:pointer;width:28px;height:28px;border-radius:8px;
border:1px solid var(--line);background:var(--panel);color:var(--ink2);font:inherit;
line-height:1}
.sortdir:hover:not(:disabled){color:var(--accent);border-color:var(--accln)}
.sortdir:disabled{opacity:.4;cursor:default}
th.s{cursor:pointer;user-select:none;white-space:nowrap}
th.s:hover,th.s:focus-visible{color:var(--accent)}
th.s::after{content:'\2195';margin-left:5px;font-size:9px;opacity:.3}
th.s[aria-sort=ascending]::after{content:'\2191';opacity:1;color:var(--accent)}
th.s[aria-sort=descending]::after{content:'\2193';opacity:1;color:var(--accent)}
.gearwrap{position:relative;flex:none}
.gearpop{position:absolute;right:0;top:42px;z-index:40;width:272px;padding:14px;
border-radius:14px;border:1px solid var(--line);background:var(--panel);
box-shadow:0 18px 44px rgba(15,23,42,.20)}
.gearttl{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
color:var(--dim);margin-bottom:10px}
.gearpop .setf{flex-direction:column;align-items:stretch;gap:7px;margin:0}
.gearpop .setf input[type=number]{width:100%}
.gearpop .setf button{width:100%;padding:7px 13px}
.gearfoot{margin-top:12px;padding-top:10px;border-top:1px solid var(--line);
font-size:11px;line-height:1.5;color:var(--dim)}
.logoutf{flex:none;margin-left:8px}
.logoutf button{appearance:none;cursor:pointer;height:34px;padding:0 13px;
border-radius:10px;border:1px solid var(--line);background:var(--panel);
color:var(--dim);font:inherit;font-size:12.5px;font-weight:600;
transition:color .15s,border-color .15s,transform .15s}
.logoutf button:hover{color:var(--neg);border-color:var(--negln);
transform:translateY(-1px)}
.saved{background:var(--posbg);border:1px solid var(--posln);color:var(--pos);
border-radius:11px;padding:10px 14px;margin-bottom:14px;font-size:13px}
/* --- liveness and alerting ------------------------------------------- */
/* The page is read by someone who assumes silence means calm. These are the
   two places that assumption is checked: the heartbeat says the agent ran,
   the alert block says what it found. */
.hb{display:flex;flex-wrap:wrap;gap:6px 18px;margin:-8px 0 16px;font-size:12px;
color:var(--dim)}
.hb-item b{color:var(--ink2);font-weight:650;margin-right:5px}
.hb-ok{color:var(--ink2);font-variant-numeric:tabular-nums}
.hb-bad{color:var(--neg);font-weight:650;font-variant-numeric:tabular-nums}
.alerts{margin-bottom:16px;border:1px solid var(--negln);border-radius:13px;
background:var(--panel);overflow:hidden;box-shadow:var(--sh)}
.ahead{background:var(--negbg);color:var(--neg);font-weight:700;font-size:11px;
letter-spacing:.07em;text-transform:uppercase;padding:9px 15px;
border-bottom:1px solid var(--negln)}
.alert{padding:11px 15px;border-bottom:1px solid var(--line2);
border-left:3px solid transparent}
.alert:last-child{border-bottom:none}
.alert.critical{border-left-color:var(--neg)}
.alert.warning{border-left-color:var(--warn)}
.alert.info{border-left-color:var(--accent)}
.atitle{font-weight:650;color:var(--ink);font-size:13px}
.alert.critical .atitle{color:var(--neg)}
.alert.warning .atitle{color:var(--warn)}
.adetail{color:var(--dim);font-size:12px;margin-top:3px;line-height:1.5}
/* Mark age. Every P&L figure on a row is only as true as this. */
.agemark{font-size:11px;margin-top:3px;font-variant-numeric:tabular-nums}
.agemark.fresh{color:var(--dim)}
.agemark.aging{color:var(--warn)}
.agemark.stale,.agemark.never{color:var(--neg);font-weight:650}
/* Distance from spot to the short strike. */
.cush{font-weight:650;font-variant-numeric:tabular-nums}
.cush.ok{color:var(--pos)}
.cush.near{color:var(--warn)}
.cush.tight{color:var(--warn)}
.cush.breach{color:var(--neg)}
.ksub{font-size:11px;color:var(--dim);font-weight:500;margin-top:3px;
letter-spacing:0;text-transform:none}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
background:var(--chip);color:var(--dim);border:1px solid var(--line);white-space:nowrap;
font-weight:600}
.tag.ok{color:var(--pos);border-color:var(--posln);background:var(--posbg)}
.tag.blocked{color:var(--neg);border-color:var(--negln);background:var(--negbg)}
.tag.act{color:var(--warn);border-color:var(--warnln);background:var(--warnbg)}
.tag.stale{color:var(--neg);border-color:var(--negln);background:var(--negbg);margin-left:4px}
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
.tabs{gap:0;margin:18px 0 14px}
/* nowrap + overflow-x on .tabs means these must not shrink, or six tabs
   squeeze into six unreadable slivers instead of scrolling. */
.tabs button{padding:10px 13px;font-size:12.5px;flex:none}
.sortbar{flex-wrap:wrap;margin-bottom:9px}
.sortsel{font-size:13px;padding:7px 26px 7px 10px}
.sortdir{width:32px;height:32px}
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

/* Phone. The masthead is the only thing that cannot just reflow: three words
   of title and a 3-word tagline in caps eat five lines before any data shows.
   The tagline is decoration -- it goes. */
@media (max-width:520px){
.top{gap:9px;margin-bottom:5px}
.brand{gap:10px}
.brandtag{display:none}
h1{font-size:16px;margin:0}
.logo{width:36px;height:36px}
.sub{font-size:12px;margin-bottom:13px}
.themer{width:36px;height:36px}                 /* thumb, not cursor */
.logoutf{margin-left:5px}
.logoutf button{height:36px;padding:0 11px;font-size:12.5px}
.gearpop{width:min(272px,calc(100vw - 26px));top:44px}
.cards{grid-template-columns:repeat(auto-fit,minmax(132px,1fr))}
.card .v{font-size:18px}
.hb{gap:5px 13px}
}

@media (prefers-reduced-motion:reduce){
*{transition:none!important}
}
"""

def _e(x) -> str:
    return html.escape(str(x))


def _sign(v: float, fmt: str = ",.0f", money: bool = True) -> str:
    # The sign goes outside the currency symbol: "-$239.35", not "$-239.35".
    # The second one reads as a typo on the number that matters most.
    cls = "pos" if v > 0 else ("neg" if v < 0 else "dim")
    mark = "+" if v > 0 else ("-" if v < 0 else "")
    txt = f"{mark}{'$' if money else ''}{abs(v):{fmt}}"
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


def _table(headers: list[str], rows: list[list[str]],
           sorts: list[list] | None = None, name: str = "") -> str:
    """One table builder for all three tables.

    Every cell carries its own column label as `data-l`. On a phone the table
    collapses to a stack of cards and that attribute becomes the row label, so
    headers and labels cannot drift apart -- they are the same list.

    `sorts` makes the table sortable: a grid parallel to `rows` giving one sort
    key per cell. Numbers sort numerically, strings as text, and `None` always
    sinks to the bottom -- a row with no spread has no premium to rank, and
    burying it at either end of every ordering is the only honest place for it.

    The KEY is emitted, not parsed back out of the rendered cell. "$849" and
    "17.8%" and "2026-10-02" are three different parses and one of them is
    wrong; the value that produced the text is already here.

    Two controls, because `thead` is `display:none` at phone widths and a
    header that is not on screen cannot be clicked: the header row sorts on
    desktop, and a select above the table does the same job at every width.
    """
    if sorts is None:
        head = "".join(f"<th>{_e(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f'<td data-l="{_e(h)}"><span class="c">{c}</span></td>'
                             for h, c in zip(headers, r, strict=True)) + "</tr>"
            for r in rows)
        return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>")

    # Column type from the first key that exists, so the caller states it by
    # passing a number or a string rather than by passing a flag as well.
    types, dirs = [], []
    for i in range(len(headers)):
        first = next((g[i] for g in sorts if g[i] is not None), None)
        num = isinstance(first, (int, float)) and not isinstance(first, bool)
        types.append("n" if num else "s")
        # First click shows the most useful end first: biggest premium, best
        # win estimate, widest cushion. Text and rank columns read forwards.
        dirs.append(-1 if num and headers[i] in _DESC_FIRST else 1)

    head = "".join(
        f'<th class="s" data-i="{i}" data-t="{t}" data-d="{d}" '
        f'role="button" tabindex="0" aria-sort="none" '
        f'title="Sort by {_e(h)}">{_e(h)}</th>'
        for i, (h, t, d) in enumerate(zip(headers, types, dirs, strict=True)))
    body = "".join(
        "<tr>" + "".join(
            f'<td data-l="{_e(h)}"'
            + ("" if k is None else f' data-s="{_e(str(k))}"')
            + f'><span class="c">{c}</span></td>'
            for h, c, k in zip(headers, r, g, strict=True)) + "</tr>"
        for r, g in zip(rows, sorts, strict=True))
    opts = "".join(f'<option value="{i}">{_e(h)}</option>'
                   for i, h in enumerate(headers))
    tid = _e(name or "t")
    return (
        f'<div class="sortbar"><label for="sort-{tid}">Sort by</label>'
        f'<select id="sort-{tid}" class="sortsel"><option value="">'
        f"default order</option>{opts}</select>"
        f'<button type="button" class="sortdir" aria-label="Reverse the order" '
        f'title="Reverse the order" disabled>&#8645;</button></div>'
        f'<div class="scroll"><table class="sortable" data-name="{tid}">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


# Columns where the interesting end is the top one. Everything else reads
# forwards: A before Z, READY before BLOCKED, soonest earnings first.
_DESC_FIRST = frozenset({"premium", "return", "cushion", "off high", "credit",
                         "p&l"})


def _mark_state(p: Position, sess: SessionState | None) -> tuple[str, str]:
    """How much the price behind this row can be trusted, and how to say it.

    Every headline number on the page -- net liq, unrealised P&L, cost to close,
    % of max credit -- is a function of one mark per position. A mark from
    ninety seconds ago and one from three days ago used to render in the same
    typeface. This is the difference.
    """
    age = p.mark_age_minutes
    if age is None:
        return "never", "never marked \u00b7 showing the fill"
    if age < 90:
        txt = f"marked {age:.0f}m ago"
    elif age < 60 * 36:
        txt = f"marked {age / 60:.0f}h ago"
    else:
        txt = f"marked {age / 1440:.0f}d ago"
    trading = bool(sess and sess.is_open)
    if trading and age > health.MARK_MISSING_AFTER_MIN:
        return "stale", txt
    if age > 60 * 24:
        return "stale", txt
    if trading and age > health.MARK_INTERVAL_MIN * 1.5:
        return "aging", txt
    return "fresh", txt


def _cushion_cell(p: Position) -> str:
    """Distance from spot to the short strike -- the number that says whether
    this position is comfortable or already being tested.

    An unknown cushion must not render like a wide one, so a position with no
    spot gets an explicit dash rather than a zero.
    """
    c = p.cushion
    if c is None:
        return '<span class="dim">no spot</span>'
    if c < 0:
        cls, note = "cush breach", "through it"
    elif c < 0.02:
        cls, note = "cush tight", "under 2%"
    elif c < 0.05:
        cls, note = "cush near", "narrowing"
    else:
        cls, note = "cush ok", ""
    return (f'<span class="{cls}">{c:+.1%}</span>'
            + (f'<div class="dim" style="font-size:11px">{_e(note)}</div>' if note else ""))


def _exit_pill(p: Position, d, mark: str, sess: SessionState | None,
               settings: Settings) -> str:
    """What is actually going to happen to this position, not what would.

    The page used to render one amber pill for every decision that said "act",
    which is the same pill whether the agent closed it, held it because the
    market is shut, or never decided at all because the mark was stale. Those
    are three different amounts of trouble.
    """
    if mark in ("stale", "never"):
        return ('<span class="tag stale">NOT DECIDED</span>'
                '<div class="note">the mark behind this row is stale, so no exit '
                'rule was evaluated on it. Nothing will fire until it re-prices.</div>')
    if not d.act:
        return f'<span class="tag">{_e(d.headline)}</span>' if d.reason else ""
    if settings.mode != "paper" or not settings.auto_exit:
        why = ("live mode -- a close is an order for you to place"
               if settings.mode != "paper" else "auto-exit is off")
        return (f'<span class="tag act">{_e(d.headline)}</span>'
                f'<span class="tag stale">NEEDS YOU</span>'
                f'<div class="note">{_e(why)} &mdash; the agent will not close this.</div>')
    if sess is not None and not sess.is_open:
        return (f'<span class="tag act">{_e(d.headline)}</span>'
                f'<span class="tag stale">HELD</span>'
                f'<div class="note">due, but the market is '
                f'{_e(sess.phase)} &mdash; it fires on the next mark after the open. '
                f'Still open, still moving.</div>')
    return (f'<span class="tag act">{_e(d.headline)}</span>'
            '<div class="note">the agent closes this on the next mark.</div>')


def _max_positions_control(settings: Settings) -> str:
    """The one setting editable from the page.

    Posts to the login service, which is the only thing here with a writable
    path and a session to check it against. The agent picks the new value up on
    its next run because `Settings.load()` reads the override file every time --
    there is no restart and no redeploy in the loop.

    The page itself is a static file, so it keeps showing the OLD number until
    something re-renders it. Saying so is better than a number that appears to
    have ignored you; the mark timer rewrites this page every 15 minutes during
    market hours.
    """
    lo, hi = DASHBOARD_SETTABLE["max_open_positions"]
    src = ("set from this page" if "max_open_positions" in load_overrides()
           else "from data/settings.json")
    return (
        f'<form class="setf" method="post" action="/settings">'
        f'<input type="hidden" name="key" value="max_open_positions">'
        f'<input type="hidden" name="next" value="/?saved=max_open_positions">'
        f'<label for="maxpos">Max open positions</label>'
        f'<input id="maxpos" name="value" type="number" inputmode="numeric" '
        f'min="{lo}" max="{hi}" step="1" value="{settings.max_open_positions}" '
        f'aria-describedby="maxpos-note">'
        f'<button type="submit">Save</button>'
        f'<span class="setnote" id="maxpos-note">{lo}&ndash;{hi} '
        f'&middot; {_e(src)} &middot; the agent applies it on its next run</span>'
        f"</form>")


def _settings_menu(settings: Settings) -> str:
    """The one setting editable from the page, behind the gear in the header.

    It used to sit inside the Portfolio limits list -- a write control dropped
    into the middle of a paragraph of read-only prose, where nobody looks for
    one. Settings go where settings go.
    """
    return ('<div class="gearwrap">'
            '<button class="themer gearer" id="gearer" type="button" '
            'aria-haspopup="dialog" aria-expanded="false" aria-controls="gearpop" '
            'aria-label="Settings" title="Settings">&#9881;</button>'
            '<div class="gearpop" id="gearpop" hidden role="dialog" '
            'aria-label="Dashboard settings">'
            '<div class="gearttl">Settings</div>'
            f'{_max_positions_control(settings)}'
            '<div class="gearfoot">Every login sees this page and can change '
            'this. Nothing reachable here can arm trading or waive the '
            'per-trade approval gate.</div>'
            "</div></div>")


def _logout_button() -> str:
    """POST, not a link. A GET logout is triggerable from any page that can
    embed an image, and browsers prefetch links."""
    return ('<form class="logoutf" method="post" action="/logout">'
            '<button type="submit" title="Sign out of the dashboard">Sign out</button>'
            "</form>")


def _alerts_panel(alerts: list) -> str:
    """The push list, rendered where a pull-only reader will see it first.

    These are the states the operator asked to be TOLD about. Until there is a
    delivery channel this is the honest half: they are at least impossible to
    miss on the page instead of buried in a column.
    """
    if not alerts:
        return ""
    rows = "".join(
        f'<div class="alert {a.severity}"><div class="atitle">{_e(a.title)}</div>'
        f'<div class="adetail">{_e(a.detail)}</div></div>' for a in alerts)
    n_crit = sum(1 for a in alerts if a.severity == health.CRITICAL)
    head = (f"{n_crit} thing(s) need you" if n_crit
            else f"{len(alerts)} thing(s) worth knowing")
    return f'<div class="alerts"><div class="ahead">{_e(head)}</div>{rows}</div>'


def _heartbeat(led: Ledger, hb: health.Health, sess: SessionState) -> str:
    """Is the agent running? A dead scheduler and a quiet market render the
    same flat book, so the page has to say which one it is looking at."""
    bits = []
    for kind, label in (("mark", "marks"), ("propose", "propose"), ("watch", "watch")):
        run = hb.last(kind)
        n = hb.runs_today(kind)
        from_ledger = False
        if run is None:
            # The record starts empty on the day this shipped. The ledger is
            # older than the record and already knows: saying "never run" over
            # four positions that only propose could have created, each marked
            # four hours ago, makes the whole line unreadable.
            seen = health.ledger_evidence(led, kind)
            if not seen:
                bits.append(f'<span class="hb-item"><b>{label}</b> '
                            f'<span class="hb-bad">never run</span></span>')
                continue
            run, from_ledger = health.Run(kind, seen), True
        age = run.when and (dt.datetime.now() - run.when).total_seconds() / 60
        if age is None:
            when = run.at
        elif age < 90:
            when = f"{age:.0f}m ago"
        elif age < 60 * 36:
            when = f"{age / 60:.0f}h ago"
        else:
            when = f"{age / 1440:.0f}d ago"
        bad = (kind == "mark" and sess.is_open and led.open_positions
               and age is not None and age > health.MARK_MISSING_AFTER_MIN)
        cls = "hb-bad" if bad else "hb-ok"
        tail = ("from the ledger" if from_ledger else f"{n} today")
        bits.append(f'<span class="hb-item"><b>{label}</b> '
                    f'<span class="{cls}">{_e(when)}</span>'
                    f'<span class="dim"> &middot; {tail}</span></span>')
    return f'<div class="hb">{"".join(bits)}</div>'


def _positions_table(rows: list[Position], settings: Settings,
                     sess: SessionState | None = None) -> str:
    if not rows:
        return '<div class="empty">No open positions.</div>'
    out = []
    for p in rows:
        d = decide(p, settings)
        mark, age_txt = _mark_state(p, sess)
        pill = _exit_pill(p, d, mark, sess, settings)
        note = d.reason if (d.reason and mark not in ("stale", "never")) else ""
        spot = (f"${p.mark_spot:,.2f}" if p.mark_spot
                else '<span class="dim">&mdash;</span>')
        be_gap = ((p.mark_spot - p.breakeven) / p.mark_spot) if p.mark_spot else None
        out.append([
            f"<b>{_e(p.symbol)}</b>"
            f"<div class='dim' style='font-size:11px'>{_e(p.sector)}</div>",
            f"{p.short_strike:g}/{p.long_strike:g}p",
            _expiry_cell(p.expiration, p.dte),
            f"{p.contracts}",
            f'{spot}<div class="agemark {mark}">{_e(age_txt)}</div>',
            _cushion_cell(p),
            f"${p.breakeven:,.2f}"
            + (f'<div class="dim" style="font-size:11px">{be_gap:+.1%} away</div>'
               if be_gap is not None else ""),
            f"${p.credit_dollars:,.0f}",
            f"${p.collateral:,.0f}",
            f"${p.mark_cost_to_close * 100 * p.contracts:,.0f}"
            '<div class="dim" style="font-size:11px">modelled mid</div>',
            _sign(p.open_pl),
            f"{p.pct_of_max_credit:.0%} {pill}"
            + (f"<div class='note'>{_e(note)}</div>" if note else ""),
        ])
    return _table(["ticker", "spread", "expiration", "qty", "spot", "to short",
                   "breakeven", "credit", "collateral", "cost to close", "P&L",
                   "status"], out)


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


# `marked` is written on every mark run -- about 26 a day -- and the log is
# rendered newest-first and unpaginated. Left in, the two rows anyone actually
# opens this tab for are off the bottom of the screen inside a week. They stay
# in the ledger, which is the audit trail; they are just not the headline.
_NOISY_EVENTS = {"marked"}
_LOG_LIMIT = 250


def _history_panel(led: Ledger) -> str:
    """Closed positions plus the event log, newest first."""
    events = [e for e in reversed(led.events)
              if e.get("kind") not in _NOISY_EVENTS]
    hidden = len(led.events) - len(events)
    shown, clipped = events[:_LOG_LIMIT], max(0, len(events) - _LOG_LIMIT)
    log = ("".join(_event_line(e) for e in shown) if shown
           else '<div class="empty">No events yet.</div>')
    foot = []
    if hidden:
        foot.append(f"{hidden:,} routine <code>marked</code> event(s) hidden")
    if clipped:
        foot.append(f"{clipped:,} older event(s) not shown")
    footer = (f'<div class="devf">{" &middot; ".join(foot)} &mdash; the full record '
              f'is in <code>data/ledger.json</code>.</div>' if foot else "")

    closed = led.closed_positions
    wins = [p for p in closed if p.realized_pl > 0]
    summary = ""
    if closed:
        gross_win = sum(p.realized_pl for p in wins)
        gross_loss = sum(p.realized_pl for p in closed if p.realized_pl <= 0)
        avg = led.realized_pl / len(closed)
        # An 80% win rate can be eight trades booked at 20% of max credit and
        # two stopped at 2x -- a net loser presented as healthy. Capture is the
        # number that tells those apart, so it sits next to the win rate.
        cap = [p.realized_pl / p.credit_dollars for p in closed if p.credit_dollars]
        avg_cap = sum(cap) / len(cap) if cap else 0.0
        reasons: dict[str, int] = {}
        for p in closed:
            reasons[p.close_reason or "unknown"] = reasons.get(p.close_reason or "unknown", 0) + 1
        reason_txt = ", ".join(f"{k.replace('_', ' ')} x{v}"
                               for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
        summary = (
            f'<div class="cards" style="margin-bottom:8px">'
            f'<div class="card"><div class="k">closed trades</div>'
            f'<div class="v">{len(closed)}</div></div>'
            f'<div class="card"><div class="k">win rate</div>'
            f'<div class="v">{len(wins) / len(closed):.0%}</div></div>'
            f'<div class="card {_pl_class(avg_cap)}"><div class="k">avg credit capture</div>'
            f'<div class="v">{_sign(avg_cap * 100, ".0f", money=False)}%</div></div>'
            f'<div class="card"><div class="k">realized P&amp;L</div>'
            f'<div class="v">{_sign(led.realized_pl, ",.2f")}</div></div>'
            f'<div class="card"><div class="k">average per trade</div>'
            f'<div class="v">{_sign(avg, ",.2f")}</div></div>'
            f'<div class="card"><div class="k">won / lost</div>'
            f'<div class="v">{_sign(gross_win, ",.0f")} / {_sign(gross_loss, ",.0f")}</div></div>'
            f"</div>"
            f'<div class="rules" style="margin-bottom:14px"><ul><li><b>How they '
            f'ended:</b> {_e(reason_txt)}. Capture is realised P&amp;L over credit '
            f'taken in &mdash; a high win rate at low capture and a few full-size '
            f'stops is a losing book that reads as a winning one.</li></ul></div>')
    return (f"{summary}<h2>Closed positions</h2>{_closed_table(closed)}"
            f"<h2>Event log &mdash; every action, newest first</h2>"
            f'<div class="rules">{log}</div>{footer}')


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

    # READY first, then the ones a decision could still be made about, then
    # the ones that are out for a reason. Sorting the SIGNAL column should walk
    # that ladder, not the alphabet.
    order = {"READY": 0, "NEAR": 1, "BLOCKED": 2, "EARNINGS": 3,
             "NO_FIT": 4, "STRETCHED": 5}
    rows, keys = [], []
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
            # Collateral is what the account would actually have to set aside
            # to take this, and it was the one number a reader had to reverse
            # out of the rate column. It is the cost of the trade; it gets a
            # column of its own.
            coll = (f'<b>${e.collateral:,.0f}</b>'
                    f'<span class="csub">max loss</span>')
            spread = (f'<b>{e.short_strike:g}/{e.long_strike:g}p</b>'
                      f'<span class="csub">{_e(e.expiration)} &middot; {e.dte}d</span>')
            prem = (f'<b>${e.credit_dollars:,.0f}</b>'
                    f'<span class="csub">nat ${e.credit_nat_dollars:,.0f}</span>')
            rate = (f'<b>{e.roc:.1%}</b>'
                    f'<span class="csub">on collateral</span>')
            # Cushion is how far out of the money it is; pop_est is the model's
            # odds of keeping the whole credit. Both were computed; only one was
            # shown, and the one shown says nothing about likelihood.
            cush = (f'<b>{e.cushion:.1%}</b>'
                    + (f'<span class="csub">{e.pop_est:.0%} est. win</span>'
                       if e.pop_est is not None else
                       '<span class="csub">no POP estimate</span>'))
        else:
            coll = spread = prem = rate = cush = '<span class="csub">&mdash;</span>'
        # "EARNINGS" alone is unanswerable: tomorrow and in three weeks are the
        # difference between waiting and dropping the name for this cycle.
        earn = (f'<span class="exp">{_e(e.earnings_date)}</span>' if e.earnings_date
                else '<span class="csub">unknown</span>')
        rows.append([
            f'<b>{_e(e.symbol)}</b><span class="csub">{_e(e.sector)}</span>',
            sig, spread, coll, prem, rate, cush, earn,
            f'<b>{e.pct_off_high:.0%}</b>'
            f'<span class="csub">{e.pct_from_dma50:+.1%} vs 50dma</span>',
        ])
        # None for anything a name without a spread does not have: it cannot be
        # ranked on a premium it was never quoted.
        keys.append([
            e.symbol, order.get(e.signal, 9),
            e.dte if e.has_spread else None,
            e.collateral if e.has_spread else None,
            e.credit_dollars if e.has_spread else None,
            e.roc if e.has_spread else None,
            e.cushion if e.has_spread else None,
            e.earnings_date or None,            # ISO dates sort as text
            e.pct_off_high,
        ])
    table = _table(["ticker", "signal", "spread", "collateral", "premium",
                    "return", "cushion", "earnings", "off high"],
                   rows, keys, name="watch")
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
    hb = health.load()
    alerts = health.alerts(led, settings, hb, sess)

    # Worst case as a fraction of the account, not just as a dollar amount.
    # "$1,953 at risk" and "65% of everything you have" are the same number and
    # only one of them is read as a warning.
    risk_pct = (led.collateral_held / led.net_liq) if led.net_liq else 0.0
    # The cards read top-to-bottom as one arithmetic: start + premium = cash,
    # cash carries collateral, what is left is free. Every card names its own
    # basis, because the two risk numbers here are NOT the same number --
    # collateral is net of the credit, the buying-power hold is the gross
    # width -- and a reader who subtracts one from cash and gets the other
    # card's value wrong has been misled by the layout, not by the maths.
    n_open = len(led.open_positions)
    fees = led.fees_on_open
    fee_txt = f" &middot; after ${fees:,.2f} fees" if fees else ""
    total_pl = round(led.realized_pl + led.unrealized_pl, 2)
    cards = [
        ("net liquidation", f"${led.net_liq:,.2f}"
         '<div class="ksub">cash less the cost to close every spread</div>', ""),
        ("premium collected", f"${led.premium_collected:,.2f}"
         f'<div class="ksub">banked on {n_open} open spread(s){fee_txt}</div>', "c-info"),
        ("cash", f"${led.cash:,.2f}"
         f'<div class="ksub">${led.starting_cash:,.0f} start + premium</div>', ""),
        ("collateral held", f"${led.collateral_held:,.2f}"
         f'<div class="ksub">most this book can lose &middot; '
         f'{risk_pct:.0%} of net liq</div>',
         "c-neg" if risk_pct > 0.5 else "c-warn"),
        ("free to open", f"${led.buying_power:,.2f}"
         f'<div class="ksub">cash less the full ${led.capital_at_risk:,.0f} '
         f'width &mdash; the conservative hold</div>', "c-info"),
        # The open/booked split is only worth the width once BOTH halves exist.
        # With nothing closed, "open +$140" restates a headline of +$139.52 and
        # rounds it differently on the way -- two numbers for one fact, and the
        # cheaper one wrong.
        ("total P&amp;L", _sign(total_pl, ",.2f")
         + f'<div class="ksub">'
           f'{_sign(led.total_return * 100, ".2f", money=False)}% on starting cash'
           + (f' &middot; open {_sign(led.unrealized_pl, ",.2f")}'
              f' &middot; booked {_sign(led.realized_pl, ",.2f")}'
              if led.closed_positions else "")
           + "</div>",
         _pl_class(total_pl)),
        ("open positions", f"{n_open} / {settings.max_open_positions}"
         f'<div class="ksub">max {settings.max_positions_per_sector} per sector '
         f'&middot; change it under the gear</div>', ""),
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
and dark palette" title="Light / dark">&#9681;</button>
{_settings_menu(settings)}{_logout_button()}</div>
<div class="sub">{_e(settings.account_label)} &middot; mode <b>{_e(led.mode)}</b> &middot;
opened {_e(led.created_at[:10])} &middot; rebuilt {dt.datetime.now():%Y-%m-%d %H:%M}</div>
<div class="banner">{_e(sess.banner)}</div>
{_heartbeat(led, hb, sess)}

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
<div class="saved" id="saved" hidden></div>
{_alerts_panel(alerts)}
<div class="cards">{card_html}</div>

<h2>Open positions</h2>
{_positions_table(led.open_positions, settings, sess)}

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
<h2>Portfolio limits</h2>
<div class="rules"><ul>
<li>Collateral deployed: <b>${led.collateral_held:,.0f}</b> of
${settings.max_total_collateral:,.0f} cap ({used_pct:.0%} used)</li>
<li>Open positions: <b>{len(led.open_positions)}</b> of {settings.max_open_positions}
&middot; max {settings.max_positions_per_sector} per sector &middot; currently: {_e(sectors)}
&middot; the cap is editable under the gear in the header</li>
<li>Worst case: if every open spread went to max loss the account would lose
<b>${led.collateral_held:,.0f}</b>, which is <b>{risk_pct:.0%}</b> of a
${led.net_liq:,.0f} net liq, across {len(led.open_positions)} position(s) in
{len(led.sector_counts())} GICS sector(s). The sector cap counts labels, not
correlation &mdash; four large-cap tech names in two sectors pass it and still
move together on one bad index day.</li>
<li>Available balance: <b>${led.buying_power:,.2f}</b> (cash ${led.cash:,.2f} less
${led.capital_at_risk:,.0f} of capital at risk). Nothing opens that needs more free
balance than this &mdash; the account can always pay its own max loss.</li>
<li>Per trade: collateral &le; ${eff.max_collateral_per_trade:,.0f}, credit &ge;
${eff.min_credit_per_trade:,.0f}{max_credit_txt}{dev_flag}</li>
</ul></div>

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

  // A save redirects back here with ?saved=<key>. This page is a static file
  // rewritten by the agent, so the number above is still the old one until the
  // next run re-renders it -- which is exactly what this says.
  var m=/[?&]saved=([a-z_]+)/.exec(location.search), box=document.getElementById('saved');
  if(m && box){{
    box.textContent='Saved '+m[1].replace(/_/g,' ')+
      '. The agent uses it on its next run. This page still shows the old '+
      'number until it is rebuilt (the mark timer does that every 15 minutes '+
      'while the market is open).';
    box.hidden=false;
    history.replaceState(null,'',location.pathname);
  }}

  // Client-side sort. The page is a static file behind nginx -- there is no
  // server to ask for a different ordering, and no need for one: the keys are
  // already in the markup as data-s.
  [].forEach.call(document.querySelectorAll('table.sortable'), function(tb){{
    var body=tb.tBodies[0], ths=[].slice.call(tb.tHead.rows[0].cells);
    var orig=[].slice.call(body.rows), key='', dir=1;
    var bar=tb.closest('.scroll').previousElementSibling;
    var sel=bar&&bar.querySelector('.sortsel'), flip=bar&&bar.querySelector('.sortdir');
    var store='pcs-sort-'+(tb.dataset.name||'t');
    function draw(){{
      var i=key===''?-1:+key;
      if(i<0){{ orig.forEach(function(r){{ body.appendChild(r); }}); }}
      else {{
        var t=ths[i].dataset.t||'s';
        orig.slice().sort(function(a,b){{
          var x=a.cells[i].dataset.s, y=b.cells[i].dataset.s;
          // A row with nothing in this column sinks either way round, so
          // reversing never buries the rows you were reading.
          if(x===undefined&&y===undefined) return 0;
          if(x===undefined) return 1;
          if(y===undefined) return -1;
          if(t==='n'){{ return (parseFloat(x)-parseFloat(y))*dir; }}
          return x.localeCompare(y)*dir;
        }}).forEach(function(r){{ body.appendChild(r); }});
      }}
      ths.forEach(function(h,j){{
        h.setAttribute('aria-sort', j===i ? (dir>0?'ascending':'descending') : 'none');
      }});
      if(sel) sel.value=key;
      if(flip){{ flip.disabled=(i<0); flip.textContent=(i<0||dir>0)?'\u2193':'\u2191'; }}
      try{{ localStorage.setItem(store, key===''?'':key+':'+dir); }}catch(e){{}}
    }}
    function pick(i){{
      if(String(i)===key){{ dir=-dir; }}
      else {{ key=String(i); dir=+(ths[i].dataset.d||1); }}
      draw();
    }}
    ths.forEach(function(h,j){{
      h.addEventListener('click', function(){{ pick(j); }});
      h.addEventListener('keydown', function(e){{
        if(e.key==='Enter'||e.key===' '){{ e.preventDefault(); pick(j); }} }});
    }});
    if(sel) sel.addEventListener('change', function(){{
      key=sel.value; if(key!=='') dir=+(ths[+key].dataset.d||1); draw(); }});
    if(flip) flip.addEventListener('click', function(){{
      if(key!==''){{ dir=-dir; draw(); }} }});
    try{{
      var was=localStorage.getItem(store);
      if(was){{ var q=was.split(':'); key=q[0]; dir=+q[1]||1; }}
    }}catch(e){{}}
    draw();
  }});

  // Settings popover. Click-outside and Escape close it; without those a
  // panel that overlays the alert row is worse than no panel.
  var gb=document.getElementById('gearer'), gp=document.getElementById('gearpop');
  if(gb&&gp){{
    var setg=function(open){{
      gp.hidden=!open; gb.setAttribute('aria-expanded', open?'true':'false');
      if(open){{ var f=gp.querySelector('input[type=number]'); if(f) f.focus(); }}
    }};
    gb.addEventListener('click', function(e){{ e.stopPropagation(); setg(gp.hidden); }});
    gp.addEventListener('click', function(e){{ e.stopPropagation(); }});
    document.addEventListener('click', function(){{ if(!gp.hidden) setg(false); }});
    document.addEventListener('keydown', function(e){{
      if(e.key==='Escape'&&!gp.hidden){{ setg(false); gb.focus(); }} }});
  }}
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
