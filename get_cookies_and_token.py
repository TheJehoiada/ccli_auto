import os
import requests
from cookie_extractor import gui_login


def _debug(msg):
    """Append a diagnostic line to debug.log."""
    try:
        with open("debug.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[get_cookies_and_token] {msg}\n")
    except Exception:
        pass


def _is_valid_header_value(name: str, value: str) -> bool:
    if not value:
        return False
    try:
        if "\r" in value or "\n" in value:
            return False
        value.encode("latin-1", "strict")
        if "\ufffd" in value:
            return False
        return True
    except Exception:
        return False


def _cookie_string_to_dict(cookie_string: str) -> dict:
    """Convert a Cookie header string to a dict for requests' cookies= parameter."""
    out = {}
    for part in cookie_string.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            out[name.strip()] = value.strip()
    return out


def _try_fetch_token_from_server(cookie_value: str) -> str | None:
    """Fetch a fresh anti-forgery token from the server.

    Returns the token string or None on failure.

    Key fixes vs original:
    1. Passes cookies as a dict (requests cookies= param) instead of a raw
       Cookie header — this matches how getVerificationToken() works and avoids
       any redirect/rejection the raw-header approach caused.
    2. Uses allow_redirects=False so a login-page redirect is detected and
       logged rather than silently returning HTML that can't be parsed.
    3. Handles plain-string JSON responses (the endpoint returns just the token
       as a quoted JSON string; the original code's for-loop over dict keys
       silently missed this and returned None every time).
    4. Full diagnostics written to debug.log so failures are visible.
    """
    if not cookie_value:
        _debug("_try_fetch_token_from_server: empty cookie_value")
        return None

    url = "https://reporting.ccli.com/api/antiForgery"
    cookie_dict = _cookie_string_to_dict(cookie_value)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://reporting.ccli.com/",
        "Content-Type": "application/json;charset=utf-8",
    }

    try:
        resp = requests.get(
            url,
            headers=headers,
            cookies=cookie_dict,
            timeout=20,
            allow_redirects=False,  # detect redirects explicitly
        )
    except Exception as e:
        _debug(f"_try_fetch_token_from_server: request exception: {e}")
        return None

    _debug(
        f"_try_fetch_token_from_server: status={resp.status_code} "
        f"content-type={resp.headers.get('Content-Type')} "
        f"body_snippet={resp.text[:120]!r}"
    )

    # Redirect = session expired / not authenticated for this endpoint
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")
        _debug(f"_try_fetch_token_from_server: redirected to {location}")
        return None

    if resp.status_code != 200:
        _debug(f"_try_fetch_token_from_server: non-200 status {resp.status_code}")
        return None

    # 1. Token in response header
    token = resp.headers.get("RequestVerificationToken")
    if token and _is_valid_header_value("RequestVerificationToken", token):
        _debug(f"_try_fetch_token_from_server: token from header (len={len(token)})")
        return token.strip()

    # 2. Parse response body
    try:
        data = resp.json()

        # Most common: endpoint returns just the token as a plain JSON string
        if isinstance(data, str):
            cand = data.strip()
            if _is_valid_header_value("RequestVerificationToken", cand):
                _debug(f"_try_fetch_token_from_server: token from json string (len={len(cand)})")
                return cand

        # Less common: object with a named field
        if isinstance(data, dict):
            for key in (
                "requestVerificationToken",
                "token",
                "RequestVerificationToken",
            ):
                if key in data and isinstance(data[key], str):
                    cand = data[key].strip()
                    if _is_valid_header_value("RequestVerificationToken", cand):
                        _debug(f"_try_fetch_token_from_server: token from json dict key={key} (len={len(cand)})")
                        return cand

    except Exception as e:
        _debug(f"_try_fetch_token_from_server: json parse failed: {e}")

    # 3. Last-resort text scan
    txt = (resp.text or "").strip()
    if txt.startswith('"') and txt.endswith('"') and len(txt) > 2:
        cand = txt[1:-1]
        if _is_valid_header_value("RequestVerificationToken", cand):
            _debug(f"_try_fetch_token_from_server: token from quoted text (len={len(cand)})")
            return cand

    if "\ufffd" not in txt:
        parts = [p.strip('"') for p in txt.replace("\n", " ").split() if len(p) >= 24]
        if parts:
            cand = parts[0]
            if _is_valid_header_value("RequestVerificationToken", cand):
                _debug(f"_try_fetch_token_from_server: token from text scan (len={len(cand)})")
                return cand

    _debug(f"_try_fetch_token_from_server: could not extract token from response")
    return None


def get_cookie_and_token():

    try:
        print("Attempting to get RequestVerificationToken and Cookie from file.")

        if not os.path.exists("RequestVerificationToken.txt") or not os.path.exists(
            "Cookie.txt"
        ):
            raise Exception(
                "File RequestVerificationToken.txt or Cookie.txt not found."
            )

        with open("RequestVerificationToken.txt", "r", encoding="utf-8") as f:
            RequestVerificationToken = f.read().strip()

        with open("Cookie.txt", "r", encoding="utf-8") as f:
            Cookie = f.read().strip()

        print("RequestVerificationToken and Cookie read from file.")

        if not _is_valid_header_value("Cookie", Cookie):
            print("Cookie from file appears invalid. Will attempt refresh via server.")
            Cookie = Cookie.strip().replace("\r", " ").replace("\n", " ")

        # Always try to get a fresh token — the file token may be stale
        fresh = _try_fetch_token_from_server(Cookie)
        if fresh:
            RequestVerificationToken = fresh
            try:
                with open("RequestVerificationToken.txt", "w", encoding="utf-8") as f:
                    f.write(RequestVerificationToken)
            except Exception:
                pass
        elif not _is_valid_header_value(
            "RequestVerificationToken", RequestVerificationToken
        ):
            print("Token from file is invalid and server refresh failed.")
            raise Exception("Unable to refresh token from server")

    except Exception:
        print(
            "Unable to get RequestVerificationToken and Cookie from file. Will try to login manually."
        )
        RequestVerificationToken, Cookie = gui_login()

        if RequestVerificationToken is None or Cookie is None:
            print("Unable to login. Exiting.")
            exit()

        else:
            print(
                "RequestVerificationToken and Cookie obtained successfully. Saving to file."
            )
            if not _is_valid_header_value(
                "RequestVerificationToken", RequestVerificationToken
            ):
                print("Warning: token contains invalid characters. Trying server refresh.")
                fresh = _try_fetch_token_from_server(Cookie)
                if fresh:
                    RequestVerificationToken = fresh
                else:
                    print("Warning: Could not refresh token; proceeding but requests may fail.")

            with open("RequestVerificationToken.txt", "w", encoding="utf-8") as f:
                f.write(RequestVerificationToken.strip())

            with open("Cookie.txt", "w", encoding="utf-8") as f:
                f.write(Cookie.strip())

    return RequestVerificationToken, Cookie
