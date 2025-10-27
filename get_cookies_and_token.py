import os
import requests
from cookie_extractor import gui_login


def _is_valid_header_value(name: str, value: str) -> bool:
    if not value:
        return False
    try:
        # RFC 7230: header values must be latin-1 encodable and not contain CR/LF
        if "\r" in value or "\n" in value:
            return False
        value.encode("latin-1", "strict")
        # Avoid U+FFFD (replacement char) which indicates prior decode corruption
        if "\ufffd" in value:
            return False
        return True
    except Exception:
        return False


def _try_fetch_token_from_server(cookie_value: str) -> str | None:
    """Attempt to fetch a fresh anti-forgery token using the existing Cookie.

    Returns the token string or None on failure.
    """
    if not cookie_value:
        return None
    url = "https://reporting.ccli.com/api/antiForgery"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://reporting.ccli.com/",
        "Cookie": cookie_value.strip().rstrip(";"),
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
    except Exception:
        return None

    # Some implementations send the token in a header
    token = resp.headers.get("RequestVerificationToken")
    if token and _is_valid_header_value("RequestVerificationToken", token):
        return token.strip()

    # Others return JSON with a field name containing token
    try:
        data = resp.json()
        # Common keys seen in similar apps
        for key in ("requestVerificationToken", "token", "RequestVerificationToken"):
            if key in data and isinstance(data[key], str):
                cand = data[key].strip()
                if _is_valid_header_value("RequestVerificationToken", cand):
                    return cand
    except Exception:
        # Last resort: inspect text for a plausible token pattern (avoid using it if it contains replacement chars)
        txt = resp.text or ""
        if "\ufffd" not in txt:
            # Heuristic: pick first long-ish token-looking substring
            # This is intentionally conservative
            parts = [p for p in txt.replace("\n", " ").split() if len(p) >= 24]
            if parts:
                cand = parts[0].strip()
                if _is_valid_header_value("RequestVerificationToken", cand):
                    return cand

    return None


def get_cookie_and_token():

    try:
        # Read RequestVerificationToken from a file
        print("Attempting to get RequestVerificationToken and Cookie from file.")

        # check if file ReqyestVerificationToken.txt exists
        if not os.path.exists("RequestVerificationToken.txt") or not os.path.exists(
            "Cookie.txt"
        ):
            raise Exception(
                "File RequestVerificationToken.txt or Cookie.txt not found."
            )

        with open("RequestVerificationToken.txt", "r", encoding="utf-8") as f:
            RequestVerificationToken = f.read().strip()

        # Read Cookie from a file
        with open("Cookie.txt", "r", encoding="utf-8") as f:
            Cookie = f.read().strip()

        print("RequestVerificationToken and Cookie read from file.")

        # Validate values; if invalid, try server refresh before falling back to GUI login
        if not _is_valid_header_value("Cookie", Cookie):
            print("Cookie from file appears invalid. Will attempt refresh via server.")
            Cookie = Cookie.strip().replace("\r", " ").replace("\n", " ")
        if not _is_valid_header_value(
            "RequestVerificationToken", RequestVerificationToken
        ):
            print(
                "RequestVerificationToken from file appears invalid. Attempting to fetch a fresh token from server."
            )
            fresh = _try_fetch_token_from_server(Cookie)
            if fresh:
                RequestVerificationToken = fresh
                # Persist corrected token
                try:
                    with open(
                        "RequestVerificationToken.txt", "w", encoding="utf-8"
                    ) as f:
                        f.write(RequestVerificationToken)
                except Exception:
                    pass
            else:
                # Force fallback to GUI login by raising
                raise Exception("Unable to refresh token from server")

    except Exception:
        print(
            "Unable to get RequestVerificationToken and Cookie from file. Will try to login manually."
        )
        RequestVerificationToken, Cookie = gui_login()

        if RequestVerificationToken == None or Cookie == None:
            print("Unable to login. Exiting.")
            exit()

        else:
            print(
                "RequestVerificationToken and Cookie obtained successfully. Saving them to file for quicker future access."
            )
            # Validate the values before writing
            if not _is_valid_header_value(
                "RequestVerificationToken", RequestVerificationToken
            ):
                print(
                    "Warning: Obtained RequestVerificationToken contains invalid characters. Trying to fetch from server using new cookie."
                )
                fresh = _try_fetch_token_from_server(Cookie)
                if fresh:
                    RequestVerificationToken = fresh
                else:
                    print(
                        "Warning: Could not correct token from server; proceeding but requests may fail."
                    )

            with open("RequestVerificationToken.txt", "w", encoding="utf-8") as f:
                f.write(RequestVerificationToken.strip())

            with open("Cookie.txt", "w", encoding="utf-8") as f:
                f.write(Cookie.strip())

    return RequestVerificationToken, Cookie
