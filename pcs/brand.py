"""The mark, in one place.

The header logo, the tab icon and the login page all draw the same chart line.
They used to be separate string literals, so editing one drifted from the
others -- which is how the icon ended up pointing down while nobody noticed.

Deliberately dependency-free: the auth service imports this and must not pull
pandas and yfinance into a process that only serves a login form.
"""

from __future__ import annotations

import urllib.parse

# Up and to the right, with a pullback in the middle. A beaten-down name
# bouncing off support is the whole thesis; a line that ended lower than it
# started said the opposite.
MARK_LINE = "M5.5 22 L12.5 15.5 L17 19 L25.5 8.5"
MARK_ARROW = "M20.4 8.5 H25.5 V13.6"
MARK_BASE = "M5.5 26 H26.5"

LOGO_SVG = f"""<svg class="logo" viewBox="0 0 32 32" fill="none" aria-hidden="true">
<rect width="32" height="32" rx="8.5" fill="url(#lg)"/>
<rect width="32" height="32" rx="8.5" fill="url(#lv)"/>
<path d="{MARK_LINE}" stroke="#fff" stroke-width="2.3"
 stroke-linecap="round" stroke-linejoin="round"/>
<path d="{MARK_ARROW}" stroke="#fff" stroke-width="2.3"
 stroke-linecap="round" stroke-linejoin="round"/>
<path d="{MARK_BASE}" stroke="#fff" stroke-opacity=".62" stroke-width="2.6"
 stroke-linecap="round"/>
<defs>
<linearGradient id="lg" x1="0" y1="0" x2="30" y2="32" gradientUnits="userSpaceOnUse">
<stop stop-color="#4b9bf5"/><stop offset="1" stop-color="#2ea55c"/></linearGradient>
<linearGradient id="lv" x1="16" y1="0" x2="16" y2="32" gradientUnits="userSpaceOnUse">
<stop stop-color="#fff" stop-opacity=".18"/><stop offset="1" stop-opacity=".12"/>
</linearGradient>
</defs></svg>"""


def favicon() -> str:
    """The same mark as a data URI, so the tab icon needs no second request."""
    ico = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           '<rect width="32" height="32" rx="8.5" fill="#2f7fe0"/>'
           f'<path d="{MARK_LINE}" stroke="#fff" stroke-width="2.6" fill="none" '
           'stroke-linecap="round" stroke-linejoin="round"/>'
           f'<path d="{MARK_ARROW}" stroke="#fff" stroke-width="2.6" fill="none" '
           'stroke-linecap="round" stroke-linejoin="round"/>'
           f'<path d="{MARK_BASE}" stroke="#fff" stroke-opacity=".65" '
           'stroke-width="2.8" stroke-linecap="round"/></svg>')
    return "data:image/svg+xml," + urllib.parse.quote(ico)
