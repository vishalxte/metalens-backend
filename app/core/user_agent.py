"""
Minimal User-Agent parsing for the session list.

WHY THIS IS ~80 LINES AND NOT A DEPENDENCY
──────────────────────────────────────────
The only consumer is the "active sessions" screen, which needs to render
"Chrome on Windows" instead of a 200-character UA string. Pulling in
`ua-parser` / `user-agents` would add a production dependency (and its
transitive regex database, refreshed on its own release cadence) to
render three short labels.

WHY THE VALUES ARE STORED RATHER THAN PARSED ON READ
────────────────────────────────────────────────────
Parsing happens exactly ONCE per session, at login. Parsing on read
would repeat the work for every row of every page of the session list.
The raw `user_agent` is still stored alongside, so nothing is lost and
the derived columns can be recomputed at any time — this is
denormalization for query/display convenience, not a second source of
truth.

ACCURACY EXPECTATIONS — read this before trusting the output
────────────────────────────────────────────────────────────
UA strings are deliberately deceptive for historical
compatibility reasons: Chrome claims to be Safari, Edge claims to be
Chrome, and every browser claims to be Mozilla. Order of checks below
therefore matters and is NOT arbitrary — the most-specific token has to
win. This is good enough to label a session in an admin console. It is
NOT good enough to make a security decision on, and nothing does.
"""
from typing import Optional, Tuple


# Order is significant: each entry's token must be checked before any
# browser whose UA string also contains it. Edge contains "Chrome" and
# "Safari"; Chrome contains "Safari"; so Edge -> Chrome -> Safari.
_BROWSERS = (
    ("Edg/", "Edge"),
    ("EdgA/", "Edge"),
    ("OPR/", "Opera"),
    ("Opera", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("FxiOS", "Firefox"),
    ("CriOS", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
    ("MSIE", "Internet Explorer"),
    ("Trident/", "Internet Explorer"),
)

# Non-browser clients. Checked FIRST — an integration or a scripted call
# must never be mislabelled as somebody's browser in the session list.
_CLIENTS = (
    ("python-requests", "Python requests"),
    ("python-httpx", "Python httpx"),
    ("httpx", "Python httpx"),
    ("aiohttp", "Python aiohttp"),
    ("curl/", "curl"),
    ("Wget/", "Wget"),
    ("PostmanRuntime", "Postman"),
    ("insomnia", "Insomnia"),
    ("axios/", "axios"),
    ("node-fetch", "node-fetch"),
    ("okhttp", "OkHttp"),
    ("Go-http-client", "Go HTTP client"),
    ("Java/", "Java HTTP client"),
)

# Checked before the desktop table: an Android UA also contains "Linux",
# and an iOS UA also contains "like Mac OS X".
_MOBILE_PLATFORMS = (
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("iPod", "iOS"),
)

_DESKTOP_PLATFORMS = (
    ("Windows NT 10.0", "Windows 10/11"),
    ("Windows NT 6.3", "Windows 8.1"),
    ("Windows NT 6.1", "Windows 7"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Ubuntu", "Ubuntu"),
    ("Linux", "Linux"),
)


def _first_match(user_agent: str, table) -> Optional[str]:
    for token, label in table:
        if token in user_agent:
            return label
    return None


def parse_user_agent(
    user_agent: Optional[str]
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (browser, platform, device_name).

    `device_name` is a human-readable summary for the session list —
    "Chrome on Windows 10/11", "Safari on iOS", "curl". It is derived,
    NOT a hardware identifier: a UA string cannot identify a specific
    device, and pretending otherwise in a column called device_name
    would be misleading in an audit context.

    Everything is None for a missing UA, which is normal for a
    non-browser caller that sends no header at all.
    """
    if not user_agent:
        return None, None, None

    # Non-browser clients first — see the comment on _CLIENTS.
    client = _first_match(user_agent, _CLIENTS)

    if client:
        return client, "API Client", client

    browser = _first_match(user_agent, _BROWSERS)

    platform = (
        _first_match(user_agent, _MOBILE_PLATFORMS)
        or _first_match(user_agent, _DESKTOP_PLATFORMS)
    )

    if browser and platform:
        device_name = f"{browser} on {platform}"
    elif browser:
        device_name = browser
    elif platform:
        device_name = platform
    else:
        # Unrecognised UA. Keep a short prefix rather than returning
        # None, so the session list shows *something* identifiable and
        # an unknown client is visible rather than blank.
        device_name = user_agent[:60]

    return browser, platform, device_name
