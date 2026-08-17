from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, JavascriptException
from selenium_stealth import stealth
from pathlib import Path
import io
import json
import os
import re
import re as stdlib_re
import socket
import subprocess
import urllib.request
import zipfile
import variables
import requests
import time
import random

email = variables.ccli_userame
password = variables.ccli_password

manual_mode = getattr(variables, "manual_mode", False)
use_remote_debugger = getattr(variables, "use_remote_debugger", False)
remote_debugger_address = getattr(variables, "remote_debugger_address", "127.0.0.1:9222")
browser_executable_path = getattr(
    variables,
    "browser_executable_path",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
)

_NO_WINDOW = 0x08000000

_BRAVE_USER_DATA = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "BraveSoftware" / "Brave-Browser" / "User Data"
)

_brave_proc = None


def _debug_log(message):
    try:
        with open("debug.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[cookie_extractor] {message}\n")
    except Exception:
        pass


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _get_chromium_version_from_devtools(port):
    """Query the running Brave DevTools endpoint for its full Chromium version.

    This avoids running brave.exe --version separately, which opens a second
    browser window when Brave is already running.
    Returns a string like "151.0.7922.137", or None on failure.
    """
    try:
        url = f"http://127.0.0.1:{port}/json/version"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        browser = data.get("Browser", "")
        _debug_log(f"DevTools /json/version Browser field: {browser}")
        # Matches "Chrome/151.0.7922.137" or "Chromium/151.0.7922.137"
        m = re.search(r"(?:Chrome|Chromium)/([\d]+\.[\d]+\.[\d]+\.[\d]+)", browser)
        if m:
            return m.group(1)
    except Exception as e:
        _debug_log(f"DevTools version query failed: {e}")
    return None


def _download_chromedriver_cft(major_version):
    """Download the matching ChromeDriver from Chrome for Testing, with local cache.

    Falls back gracefully if the download fails.
    Returns the path to chromedriver.exe, or None on failure.
    """
    try:
        # Resolve the latest patch release for this major version
        rel_url = f"https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_{major_version}"
        with urllib.request.urlopen(rel_url, timeout=10) as r:
            full_version = r.read().decode().strip()
        _debug_log(f"Latest CfT release for {major_version}: {full_version}")

        cache_dir = Path.home() / ".cache" / "chromedriver_cft" / full_version
        driver_path = cache_dir / "chromedriver.exe"

        if driver_path.exists():
            _debug_log(f"Using cached ChromeDriver: {driver_path}")
            print(f"Using cached ChromeDriver {full_version}")
            return str(driver_path)

        zip_url = (
            f"https://storage.googleapis.com/chrome-for-testing-public"
            f"/{full_version}/win64/chromedriver-win64.zip"
        )
        _debug_log(f"Downloading ChromeDriver from: {zip_url}")
        print(f"Downloading ChromeDriver {full_version}...")

        with urllib.request.urlopen(zip_url, timeout=60) as r:
            zip_data = r.read()

        cache_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            for name in z.namelist():
                if name.endswith("chromedriver.exe"):
                    driver_path.write_bytes(z.read(name))
                    break

        _debug_log(f"ChromeDriver saved to: {driver_path}")
        print(f"ChromeDriver {full_version} ready.")
        return str(driver_path)

    except Exception as e:
        _debug_log(f"CfT download failed: {e}")
        print(f"ChromeDriver download failed: {e}")
        return None


def _get_driver(options, brave_version):
    """Get a webdriver.Chrome instance that matches the running Brave version."""
    major = brave_version.split(".")[0] if brave_version else None

    # 1. Try direct CfT download with exact major version
    if major:
        driver_path = _download_chromedriver_cft(major)
        if driver_path:
            try:
                _debug_log(f"Trying CfT ChromeDriver: {driver_path}")
                return webdriver.Chrome(service=Service(driver_path), options=options)
            except Exception as e:
                _debug_log(f"CfT ChromeDriver failed: {e}")

    # 2. Try webdriver-manager as fallback
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        kwargs = {"driver_version": brave_version} if brave_version else {}
        _debug_log(f"Trying webdriver-manager driver_version={brave_version}")
        service = Service(ChromeDriverManager(**kwargs).install())
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        _debug_log(f"webdriver-manager failed: {e}")

    # 3. Last resort: Selenium Manager (may be wrong version)
    _debug_log("Falling back to Selenium Manager (version may be mismatched)")
    print("Warning: falling back to Selenium Manager — version may not match.")
    return webdriver.Chrome(options=options)


def _bring_window_to_front():
    """Bring Brave to the OS foreground so document.hasFocus() returns True.

    When launched by a CMD script, Brave opens in the background while the
    CMD window keeps OS focus. CCLI's login spinner waits for hasFocus=True
    before enabling the form — so the spinner never resolves until Brave is
    the foreground window. This is the root cause of the spinner staying.
    """
    # Primary: WScript.Shell AppActivate — explicitly designed for background
    # processes to activate windows, unlike SetForegroundWindow which Windows
    # blocks from background processes.
    try:
        if _brave_proc:
            subprocess.run(
                [
                    "powershell", "-WindowStyle", "Hidden", "-Command",
                    f"(New-Object -ComObject WScript.Shell).AppActivate({_brave_proc.pid})",
                ],
                capture_output=True, timeout=5, creationflags=_NO_WINDOW,
            )
            _debug_log("AppActivate called via PowerShell")
            return
    except Exception as e:
        _debug_log(f"AppActivate failed: {e}")

    # Fallback: ctypes with thread input attachment (needed to steal focus)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if _brave_proc:
            pid = _brave_proc.pid
            found = [0]
            Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

            def cb(hwnd, _):
                lp = ctypes.c_ulong(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lp))
                if lp.value == pid and user32.IsWindowVisible(hwnd):
                    found[0] = hwnd
                    return False
                return True

            user32.EnumWindows(Proc(cb), 0)
            if found[0]:
                fg = user32.GetForegroundWindow()
                fg_tid = user32.GetWindowThreadProcessId(fg, None)
                our_tid = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(fg_tid, our_tid, True)
                user32.BringWindowToTop(found[0])
                user32.ShowWindow(found[0], 9)  # SW_RESTORE
                user32.SetForegroundWindow(found[0])
                user32.AttachThreadInput(fg_tid, our_tid, False)
                _debug_log(f"SetForegroundWindow hwnd={found[0]}")
    except Exception as e:
        _debug_log(f"SetForegroundWindow failed: {e}")


def _close_existing_brave():
    """Kill zombie chromedriver and any running Brave, then clean stale locks."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "chromedriver.exe"],
            capture_output=True, creationflags=_NO_WINDOW,
        )
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq brave.exe"],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        )
        if "brave.exe" in result.stdout.lower():
            _debug_log("Brave is running, killing it")
            print("Closing existing Brave browser...")
            subprocess.run(
                ["taskkill", "/F", "/IM", "brave.exe"],
                capture_output=True, creationflags=_NO_WINDOW,
            )
            time.sleep(4)
            print("Brave closed.")
        else:
            _debug_log("Brave was not running")
    except Exception as e:
        _debug_log(f"brave kill warning: {e}")

    try:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock = _BRAVE_USER_DATA / name
            if lock.exists():
                lock.unlink()
                _debug_log(f"Removed stale lock: {name}")
                print(f"Removed stale Brave lock file: {name}")
    except Exception as e:
        _debug_log(f"lock cleanup warning: {e}")


def create_chrome_driver():
    """Launch Brave as a subprocess then attach ChromeDriver to it.

    This avoids the ChromeDriver-launches-Brave crash. Brave is started
    normally (via subprocess.Popen), then ChromeDriver connects to the
    already-running instance via debugger_address. Brave's Chromium version
    is read from the DevTools /json/version endpoint so we never need to run
    brave.exe --version (which opens a second browser when Brave is running).
    """
    global _brave_proc

    _close_existing_brave()

    port = _find_free_port()
    _debug_log(f"Launching Brave subprocess on port {port}")
    print(f"Launching Brave on debugging port {port}...")

    _brave_proc = subprocess.Popen(
        [
            browser_executable_path,
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--disable-brave-extension",
            "--disable-brave-rewards-extension",
            "--disable-brave-news-extension",
            "--disable-infobars",
            "about:blank",
        ]
    )
    _debug_log(f"Brave PID={_brave_proc.pid}")

    if not _wait_for_port(port, timeout=30):
        _debug_log("Brave debugging port never became available")
        raise RuntimeError("Brave failed to open its remote debugging port within 30 seconds.")

    _debug_log(f"Port {port} ready")

    # Get version from the running browser — no second brave.exe launch needed
    brave_version = _get_chromium_version_from_devtools(port)
    _debug_log(f"Brave Chromium version: {brave_version}")
    print(f"Brave Chromium version: {brave_version}")

    print("Connecting ChromeDriver...")
    options = webdriver.ChromeOptions()
    options.debugger_address = f"127.0.0.1:{port}"

    driver_instance = _get_driver(options, brave_version)

    _debug_log("ChromeDriver attached successfully")
    print("ChromeDriver connected.")

    # stealth() uses Page.addScriptToEvaluateOnNewDocument — applies to all
    # future page loads even when attaching to an already-running browser.
    stealth(
        driver_instance,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )

    # Register additional overrides for every future page load.
    # Critically: document.hasFocus() returning true is required for CCLI's
    # login spinner to resolve. OS-level window focus is unreliable when the
    # script runs in the background, so we override the JS function instead.
    try:
        driver_instance.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(Document.prototype, 'hasFocus', {
                        value: function() { return true; },
                        writable: true, configurable: true
                    });
                    Object.defineProperty(document, 'hidden', {
                        get: () => false, configurable: true
                    });
                    Object.defineProperty(document, 'visibilityState', {
                        get: () => 'visible', configurable: true
                    });
                """
            },
        )
    except Exception as e:
        _debug_log(f"addScriptToEvaluateOnNewDocument (focus) warning: {e}")

    return driver_instance


required_cookies = [
    "ARRAffinity",
    "ARRAffinitySameSite",
    "CCLI_NET_AUTH",
    "CCLI_JWT_AUTH",
    ".AspNetCore.Session",
]
antiforgery_cookie_prefix = ".AspNetCore.Antiforgery"

request_verification_token = None
required_cookies_dict = {}


def capture_post_requests(logs):
    global request_verification_token
    for entry in logs:
        log = json.loads(entry["message"])["message"]
        if (
            log["method"] == "Network.requestWillBeSent"
            and log["params"]["request"]["method"] == "POST"
        ):
            headers = log["params"]["request"]["headers"]
            if "RequestVerificationToken" in headers:
                request_verification_token = headers["RequestVerificationToken"]
                cookies = get_all_cookies()
                if are_cookies_captured(cookies):
                    print("Cookies Captured")
                    required_cookies_dict.update(extract_required_cookies(cookies))
                return True
    return False


def are_cookies_captured(cookies):
    names = [c["name"] for c in cookies]
    for rc in required_cookies:
        if rc not in names:
            return False
    if not any(c["name"].startswith(antiforgery_cookie_prefix) for c in cookies):
        return False
    return True


def get_all_cookies():
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        return result.get("cookies", [])
    except Exception:
        return driver.get_cookies()


def extract_required_cookies(cookies):
    out = {}
    for c in cookies:
        n, v = c["name"], c["value"]
        if n in required_cookies or n.startswith(antiforgery_cookie_prefix):
            out[n] = v
    return out


def handle_cookie_popup():
    try:
        popup = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
        )
        if popup:
            driver.find_element(
                By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
            ).click()
            WebDriverWait(driver, 2).until_not(
                EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
            )
    except Exception:
        print("Cookie popup not found or already handled.")


def missing_cookies_report(cookies):
    names = [c["name"] for c in cookies]
    missing = [rc for rc in required_cookies if rc not in names]
    if not any(c["name"].startswith(antiforgery_cookie_prefix) for c in cookies):
        missing.append(f"{antiforgery_cookie_prefix}.*")
    return missing


def _wait_for_stop_or_timeout(seconds, missing_desc):
    import msvcrt
    print(f"\nStill missing: {missing_desc}")
    print("Type stop + Enter to quit, or wait to keep going.")
    buffer = ""
    deadline = time.time() + seconds
    while time.time() < deadline:
        remaining = int(deadline - time.time())
        print(f"\r  Auto-continuing in {remaining}s...  ", end="", flush=True)
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                if buffer.strip().lower() == "stop":
                    return True
                buffer = ""
            elif ch == "\b":
                buffer = buffer[:-1]
            else:
                buffer += ch
        time.sleep(0.2)
    print("\r  No input — continuing...                    ")
    return False


def collect_cookies(timeout=300, poll_interval=5, manual=False):
    notice_shown = False
    round_num = 1
    while True:
        start_time = time.time()
        while time.time() - start_time < timeout:
            cookies = get_all_cookies()
            if are_cookies_captured(cookies):
                print("All required cookies captured!")
                return cookies
            missing = missing_cookies_report(cookies)
            elapsed = int(time.time() - start_time)
            remaining = int(timeout - elapsed)
            if manual:
                if not notice_shown:
                    print("Manual mode: finish signing in, stay on reporting.ccli.com.")
                    notice_shown = True
                else:
                    loc = driver.current_url
                    if "reporting.ccli.com" not in loc:
                        print("Navigate to https://reporting.ccli.com/search once signed in.")
                    else:
                        print(f"Waiting... ({elapsed}s elapsed, {remaining}s remaining)")
            else:
                print(f"Waiting for cookies... ({elapsed}s elapsed, {remaining}s remaining)")
            print(f"  Missing ({len(missing)}): {', '.join(missing)}")
            time.sleep(poll_interval)

        cookies = get_all_cookies()
        missing = missing_cookies_report(cookies)
        print(f"\nStill waiting after {round_num * timeout}s.")
        if _wait_for_stop_or_timeout(30, ", ".join(missing)):
            return cookies
        round_num += 1


def pause_for_cloudflare_challenge(timeout=240):
    start_time = time.time()
    notified = False
    while time.time() - start_time < timeout:
        try:
            url = driver.current_url
        except Exception:
            return
        if "challenges.cloudflare.com" in url:
            if not notified:
                print("Cloudflare challenge — please complete it in the browser.")
                notified = True
            time.sleep(2)
        else:
            if notified:
                print("Cloudflare challenge cleared.")
            return


def wait_for_sign_in_spinner(timeout=300):
    """Wait up to 5 minutes for the sign-in spinner to disappear.

    If the spinner does not resolve within the timeout, raises TimeoutException.
    The caller must NOT force-click; that triggers bot detection.
    """
    def spinner_hidden(d):
        try:
            el = d.execute_script("return document.getElementById('sign-in-spinner');")
            if el is None:
                return True
            return d.execute_script(
                "return window.getComputedStyle(document.getElementById('sign-in-spinner')).getPropertyValue('display');"
            ) == "none"
        except JavascriptException:
            return True

    print("Waiting for the sign-in spinner to disappear (up to 5 minutes)...")
    WebDriverWait(driver, timeout).until(spinner_hidden)
    print("Spinner resolved.")


def getVerificationToken(cookies):
    print("Attempting to get verification token...")
    url = "https://reporting.ccli.com/api/antiForgery"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://reporting.ccli.com/",
        "Content-Type": "application/json;charset=utf-8",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Te": "trailers",
    }
    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=20)
    except Exception as e:
        print(f"Error: {e}")
        return None

    if resp.status_code == 200:
        ht = resp.headers.get("RequestVerificationToken")
        if isinstance(ht, str):
            try:
                ht.encode("latin-1", "strict")
                return ht.strip()
            except Exception:
                pass
        try:
            data = resp.json()
            if isinstance(data, str):
                try:
                    data.encode("latin-1", "strict")
                    return data.strip()
                except Exception:
                    pass
            if isinstance(data, dict):
                for key in ("requestVerificationToken", "token", "RequestVerificationToken"):
                    if key in data and isinstance(data[key], str):
                        cand = data[key].strip()
                        try:
                            cand.encode("latin-1", "strict")
                            return cand
                        except Exception:
                            continue
        except Exception:
            pass
        txt = (resp.text or "").strip()
        if txt.startswith('"') and txt.endswith('"') and len(txt) > 2:
            cand = txt[1:-1]
            try:
                cand.encode("latin-1", "strict")
                return cand
            except Exception:
                pass
        print("Warning: could not extract RequestVerificationToken.")
        return None
    else:
        print(f"Token request failed. Status: {resp.status_code}")
        return None



def _try_direct_login():
    """Attempt login via direct HTTP requests — no browser, no CDP, no spinner.

    Returns (token, cookie_string) on success, or None if a JS challenge blocks it.
    """
    print("[direct login] Starting direct HTTP login attempt...")
    _debug_log("_try_direct_login called")

    session = requests.Session()
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/151.0.0.0 Safari/537.36")
    base_headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
    }

    try:
        resp = session.get(
            "https://reporting.ccli.com/search",
            headers=base_headers, timeout=30, allow_redirects=True,
        )
        _debug_log(f"Initial GET: status={resp.status_code} url={resp.url}")
        print(f"[direct login] Initial GET status={resp.status_code} url={resp.url}")

        if "profile.ccli.com" not in resp.url:
            _debug_log("No redirect to login page")
            print("[direct login] No redirect to login page")
            return None

        login_url = resp.url
        pat1 = 'name="__RequestVerificationToken"'
        pat2 = 'value="'

        # Extract antiforgery token from form HTML
        token_val = None
        for tag in stdlib_re.findall(r'<input[^>]+>', resp.text):
            if '__RequestVerificationToken' in tag:
                m = stdlib_re.search(r'value="([^"]*)"', tag)
                if m:
                    token_val = m.group(1)
                    break

        if not token_val:
            _debug_log("__RequestVerificationToken not found in page")
            print("[direct login] Token not found — page may have a JS challenge")
            return None

        _debug_log(f"Form token found (length={len(token_val)})")
        print(f"[direct login] Form token found (length={len(token_val)})")

        resp = session.post(
            login_url,
            data={
                "__RequestVerificationToken": token_val,
                "EmailAddress": email,
                "Password": password,
                "RememberMe": "false",
            },
            headers={
                **base_headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_url,
                "Origin": "https://profile.ccli.com",
            },
            timeout=30, allow_redirects=True,
        )
        _debug_log(f"POST: status={resp.status_code} url={resp.url}")
        print(f"[direct login] POST status={resp.status_code} url={resp.url}")

        jar = {c.name: c.value for c in session.cookies}
        _debug_log(f"Cookies after POST: {list(jar.keys())}")
        print(f"[direct login] Cookies: {list(jar.keys())}")

        if "CCLI_NET_AUTH" not in jar and "CCLI_JWT_AUTH" not in jar:
            _debug_log("Auth cookies missing after POST")
            print("[direct login] Auth cookies missing — credentials wrong or JS challenge blocked POST")
            return None

        resp = session.get(
            "https://reporting.ccli.com/search",
            headers=base_headers, timeout=30, allow_redirects=True,
        )
        jar = {c.name: c.value for c in session.cookies}

        filtered = {
            n: v for n, v in jar.items()
            if n in required_cookies or n.startswith(antiforgery_cookie_prefix)
        }
        missing = [rc for rc in required_cookies if rc not in filtered]
        if missing:
            _debug_log(f"Missing required cookies: {missing}")
            print(f"[direct login] Missing cookies: {missing}")
            return None

        token = getVerificationToken(filtered)
        if not token:
            _debug_log("Could not fetch antiforgery token")
            print("[direct login] Could not fetch antiforgery token")
            return None

        cookie_string = "; ".join(f"{n}={v}" for n, v in filtered.items())
        print("[direct login] SUCCESS")
        _debug_log("Direct login succeeded")
        return (token, cookie_string)

    except Exception as e:
        _debug_log(f"Direct login exception: {e}")
        print(f"[direct login] Exception: {e}")
        return None


def gui_login():
    global driver, _brave_proc

    # Try direct HTTP login first — no browser, no CDP, no bot detection risk.
    # Falls back to browser login if a JS challenge blocks the HTTP approach.
    result = _try_direct_login()
    if result:
        return result

    print("Falling back to browser-based login...")
    _debug_log("Direct login failed — starting browser login")

    driver = create_chrome_driver()

    # Open CCLI in a NEW tab using CDP's Target.createTarget.
    # This bypasses Brave's popup blocker (which blocks window.open from
    # about:blank) and creates a real browser tab navigated to CCLI with
    # NO CDP domains enabled on the new target. CCLI loads there, its bot
    # check runs, finds nothing, and clears the spinner — the same as when
    # you manually open a new tab. We wait for the check to complete, THEN
    # switch ChromeDriver to the tab (which by then has already passed).
    new_target = driver.execute_cdp_cmd(
        "Target.createTarget",
        {"url": "https://reporting.ccli.com/search"},
    )
    _debug_log(f"Created new target via CDP: {new_target}")
    time.sleep(8)  # Let the new tab load and CCLI's spinner check complete

    # Switch ChromeDriver to the new tab — the spinner check is already done
    handles = driver.window_handles
    driver.switch_to.window(handles[-1])
    _debug_log(f"Switched to new tab, current url: {driver.current_url}")

    filtered_cookies = {}
    local_token = None

    try:
        if manual_mode:
            print("Manual mode: complete login in the browser, stay on reporting.ccli.com.")
            cookies = collect_cookies(timeout=600, poll_interval=5, manual=True)
        else:
            print("Checking for existing CCLI session...")
            existing = get_all_cookies()
            if are_cookies_captured(existing):
                print("Existing session found. Skipping login.")
                cookies = existing
            else:
                pause_for_cloudflare_challenge()
                handle_cookie_popup()

                WebDriverWait(driver, 20).until(
                    EC.url_contains("profile.ccli.com/account/signin")
                )

                # Bring Brave to OS foreground so document.hasFocus()=True.
                # CCLI's spinner waits for hasFocus before enabling the form.
                _bring_window_to_front()
                time.sleep(2)

                has_focus = driver.execute_script("return document.hasFocus();")
                _debug_log(f"document.hasFocus() after foreground: {has_focus}")
                print(f"document.hasFocus() = {has_focus}")

                # Give the page a moment to fully initialize
                time.sleep(1)

                # Log spinner state before any interaction
                try:
                    spinner_info = driver.execute_script(
                        "var el=document.getElementById('sign-in-spinner');"
                        "if(!el) return 'not-found';"
                        "var s=window.getComputedStyle(el);"
                        "return 'display:'+s.display+' vis:'+s.visibility;"
                    )
                    _debug_log(f"Spinner state before interaction: {spinner_info}")
                    print(f"Spinner state: {spinner_info}")
                except Exception as e:
                    _debug_log(f"Spinner check error: {e}")

                # Simulate human mouse movement — behavioral bot detection
                # requires evidence of interaction before enabling the login form
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    body = driver.find_element(By.TAG_NAME, "body")
                    actions = ActionChains(driver)
                    actions.move_to_element(body).perform()
                    time.sleep(0.5)
                    actions.move_by_offset(40, 60).perform()
                    time.sleep(0.4)
                    actions.move_by_offset(-20, 30).perform()
                    time.sleep(0.3)
                    actions.move_by_offset(10, -15).perform()
                    _debug_log("Mouse movement simulated")
                except Exception as e:
                    _debug_log(f"Mouse movement failed: {e}")

                # Inject overrides into the current page context.
                # Page.addScriptToEvaluateOnNewDocument covers future loads;
                # execute_script covers the already-loaded login page.
                try:
                    driver.execute_script("""
                        // navigator.webdriver
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                            configurable: true
                        });

                        // document.hasFocus() — CCLI's spinner polls this to
                        // decide whether to enable the login form. Overriding
                        // it to always return true fixes the spinner without
                        // needing OS-level window focus.
                        Object.defineProperty(Document.prototype, 'hasFocus', {
                            value: function() { return true; },
                            writable: true,
                            configurable: true
                        });

                        // document.hidden / visibilityState
                        Object.defineProperty(document, 'hidden', {
                            get: () => false, configurable: true
                        });
                        Object.defineProperty(document, 'visibilityState', {
                            get: () => 'visible', configurable: true
                        });

                        // Dispatch focus events to trigger any event listeners
                        window.dispatchEvent(new FocusEvent('focus'));
                        document.dispatchEvent(new FocusEvent('focus'));

                        // Remove ChromeDriver globals
                        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                    """)
                    wd_val = driver.execute_script("return navigator.webdriver;")
                    hf_val = driver.execute_script("return document.hasFocus();")
                    _debug_log(f"navigator.webdriver={wd_val} document.hasFocus()={hf_val}")
                    print(f"navigator.webdriver={wd_val}  document.hasFocus()={hf_val}")
                except Exception as e:
                    _debug_log(f"injection warning: {e}")

                email_field = driver.find_element(By.ID, "EmailAddress")
                pw_field = driver.find_element(By.ID, "Password")

                email_field.send_keys(email)
                time.sleep(2)
                for letter in password:
                    pw_field.send_keys(letter)
                    time.sleep(random.uniform(0.1, 0.3))

                wait_for_sign_in_spinner()

                try:
                    driver.execute_script("document.getElementById('sign-in').click();")
                except Exception:
                    driver.find_element(By.ID, "sign-in").click()

                pause_for_cloudflare_challenge()
                WebDriverWait(driver, 60).until(EC.url_contains("reporting.ccli.com/search"))
                cookies = collect_cookies(timeout=300, poll_interval=5, manual=False)

        filtered_cookies = extract_required_cookies(cookies)
        if not filtered_cookies:
            raise RuntimeError("Unable to find required cookies.")

        local_token = getVerificationToken(filtered_cookies)
        if not local_token:
            raise RuntimeError("Failed to obtain RequestVerificationToken.")

    except Exception as e:
        _debug_log(f"gui_login error: {e}")
        print(f"\nLogin error: {e}")
        raise

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if _brave_proc and _brave_proc.poll() is None:
            try:
                _brave_proc.terminate()
            except Exception:
                pass
            _brave_proc = None

    cookie_string = "; ".join(f"{n}={v}" for n, v in filtered_cookies.items())
    return (local_token, cookie_string)


if __name__ == "__main__":
    gui_login()
