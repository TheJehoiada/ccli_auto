from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, JavascriptException
from selenium.webdriver.chrome.service import Service
import ctypes
import json
import variables
import requests
import time
import random
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

# Add your login credentials here
email = variables.ccli_userame
password = variables.ccli_password

# Configuration flags
manual_mode = getattr(variables, "manual_mode", False)
use_remote_debugger = getattr(variables, "use_remote_debugger", False)
remote_debugger_address = getattr(
    variables, "remote_debugger_address", "127.0.0.1:9222"
)
browser_executable_path = getattr(
    variables,
    "browser_executable_path",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
)

_brave_process = None  # Tracks the self-launched Brave process for cleanup in gui_login()

# ChromeDriver cache lives in AppData\Local to avoid OneDrive sync interference.
# Do NOT put this inside an OneDrive folder — sync locks can prevent chromedriver.exe from running.
_DRIVER_CACHE_DIR = Path.home() / "AppData" / "Local" / "brave_ccli_drivers"


def _get_brave_chromium_version(executable_path):
    """
    Read the exact Chromium version embedded in the Brave binary via the Windows
    file version API (ctypes). This returns the real Chromium build number, e.g.
    '150.0.7871.63', NOT Brave's own directory name '150.1.92.134'.
    Falls back to directory-name detection if the version API fails.
    """
    path = str(executable_path)
    try:
        ver_size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if ver_size:
            buf = ctypes.create_string_buffer(ver_size)
            if ctypes.windll.version.GetFileVersionInfoW(path, 0, ver_size, buf):
                lp_buf = ctypes.c_void_p()
                n_len = ctypes.c_uint()
                if ctypes.windll.version.VerQueryValueW(
                    buf, "\\", ctypes.byref(lp_buf), ctypes.byref(n_len)
                ):
                    # VS_FIXEDFILEINFO layout: dwFileVersionMS at index 2, dwFileVersionLS at 3
                    dwords = ctypes.cast(lp_buf, ctypes.POINTER(ctypes.c_uint32))
                    ms, ls = dwords[2], dwords[3]
                    major = (ms >> 16) & 0xFFFF
                    minor = ms & 0xFFFF
                    build = (ls >> 16) & 0xFFFF
                    patch = ls & 0xFFFF
                    return f"{major}.{minor}.{build}.{patch}"
    except Exception:
        pass

    # Fallback: read the numbered subdirectory name next to brave.exe.
    # Note: Brave names this folder with its own version scheme (e.g. 150.1.92.134),
    # so the major version is correct but the rest may not match Chromium's build number.
    app_dir = Path(executable_path).parent
    version_pattern = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
    try:
        versions = [
            d.name for d in app_dir.iterdir()
            if d.is_dir() and version_pattern.match(d.name)
        ]
        if versions:
            return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    except Exception:
        pass

    raise RuntimeError(
        f"Could not determine Brave/Chromium version from {executable_path}"
    )


def _version_tuple(v):
    """Convert '150.0.7871.63' to (150, 0, 7871, 63) for correct numeric comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _get_chromedriver_path(executable_path):
    """
    Return the path to a ChromeDriver executable that matches the installed
    Brave/Chromium version. Downloads and caches it automatically when needed.

    Cache location: AppData/Local/brave_ccli_drivers/<major>/chromedriver.exe
    A new driver is only downloaded when Brave updates to a new major version.
    """
    version = _get_brave_chromium_version(executable_path)
    major = version.split(".")[0]

    cached_exe = _DRIVER_CACHE_DIR / major / "chromedriver.exe"
    if cached_exe.exists():
        print(f"[chromedriver] Using cached driver for Chromium {major} ({cached_exe})")
        return str(cached_exe)

    print(f"[chromedriver] Detected Brave/Chromium version: {version}. Searching for matching ChromeDriver...")

    # Query the official Chrome for Testing endpoint
    cft_url = (
        "https://googlechromelabs.github.io/chrome-for-testing/"
        "known-good-versions-with-downloads.json"
    )
    try:
        with urllib.request.urlopen(cft_url, timeout=30) as resp:
            cft_data = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch Chrome for Testing index: {exc}") from exc

    # Prefer an exact version match; otherwise take the highest available in this major.
    # Use numeric tuple comparison (not string) to correctly order e.g. .68 > .5.
    exact_url = None
    best_version = None
    download_url = None
    for entry in cft_data.get("versions", []):
        v = entry.get("version", "")
        if not v.startswith(f"{major}."):
            continue
        for dl in entry.get("downloads", {}).get("chromedriver", []):
            if dl.get("platform") == "win64":
                if v == version:
                    exact_url = dl.get("url")
                if best_version is None or _version_tuple(v) > _version_tuple(best_version):
                    best_version = v
                    download_url = dl.get("url")

    if exact_url:
        download_url = exact_url
        best_version = version
        print(f"[chromedriver] Found exact version match: {best_version}")
    elif download_url:
        print(f"[chromedriver] No exact match for {version}; closest available: {best_version}")
    else:
        raise RuntimeError(
            f"No win64 ChromeDriver found for Chromium major version {major}. "
            "Check https://googlechromelabs.github.io/chrome-for-testing/"
        )

    print(f"[chromedriver] Downloading ChromeDriver {best_version} from Chrome for Testing...")

    zip_path = _DRIVER_CACHE_DIR / f"chromedriver_{major}.zip"
    _DRIVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(download_url, zip_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to download ChromeDriver: {exc}") from exc

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            exe_data = None
            for name in z.namelist():
                if name.endswith("chromedriver.exe"):
                    exe_data = z.read(name)
                    break
            if exe_data is None:
                raise RuntimeError("chromedriver.exe not found inside the downloaded zip.")
        cached_exe.parent.mkdir(parents=True, exist_ok=True)
        cached_exe.write_bytes(exe_data)
    finally:
        try:
            zip_path.unlink()
        except Exception:
            pass

    print(f"[chromedriver] Saved to {cached_exe}")
    return str(cached_exe)


def _find_free_port():
    """Find an available TCP port for Brave's remote debugging endpoint."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_brave_with_debugging(port):
    """
    Launch Brave independently with --remote-debugging-port, bypassing ChromeDriver's
    browser launcher.

    When ChromeDriver launches Brave it adds --enable-automation, --test-type=webdriver,
    --disable-sync, and other flags. In Brave 150 (Chromium 150.0.7871.63), these flags
    combined with --remote-debugging-port cause an immediate startup crash. Pipe mode
    avoids the crash but the renderer then times out after 60 seconds.

    By launching Brave ourselves with only the minimal flags, neither crash occurs.
    ChromeDriver then connects to the already-running instance via debugger_address
    instead of launching its own copy, so it never adds those problematic flags.
    """
    global _brave_process

    profile_dir = str(Path.home() / "AppData" / "Local" / "brave_ccli_profile")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        browser_executable_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--allow-insecure-localhost",
        "--disable-brave-extension",
        "--disable-brave-rewards-extension",
        "--disable-brave-news-extension",
        "--disable-infobars",
        "--start-maximized",
    ]

    print(f"[brave] Launching Brave with remote debugging on port {port}...")
    _brave_process = subprocess.Popen(cmd)

    # Poll until Brave's DevTools HTTP endpoint responds
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[brave] Brave is ready on port {port}")
                    return
        except Exception:
            pass
        time.sleep(0.5)

    raise RuntimeError(
        f"Brave did not become ready for debugging on port {port} within 30 seconds. "
        "Ensure Brave can be launched normally from the Start menu."
    )


def create_chrome_driver():
    # NOTE: options.binary_location is NOT set here.
    # When using debugger_address, ChromeDriver connects to an already-running browser
    # rather than launching it, so the binary path is not needed.
    options = webdriver.ChromeOptions()

    if use_remote_debugger:
        # Attach to a user-managed Brave instance at the configured address
        options.debugger_address = remote_debugger_address
    else:
        # Launch Brave ourselves with only the flags we need, then connect ChromeDriver to it.
        # This avoids the crash that ChromeDriver's own launcher causes in Brave 150
        # (see _launch_brave_with_debugging for the full explanation).
        port = _find_free_port()
        _launch_brave_with_debugging(port)
        options.debugger_address = f"127.0.0.1:{port}"

    driver_path = _get_chromedriver_path(browser_executable_path)
    _log_path = str(Path(__file__).parent / "chromedriver.log")
    service = Service(driver_path, log_output=_log_path, service_args=["--verbose"])
    driver_instance = webdriver.Chrome(service=service, options=options)

    # Hide all common automation fingerprints so the CCLI login page does not
    # detect ChromeDriver. Patches both the Navigator prototype (which ChromeDriver
    # may set directly) and the instance-level proxy.
    driver_instance.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            // Patch at the prototype level — this is where ChromeDriver sets webdriver=true
            // when connecting via debugger_address, bypassing instance-level overrides.
            try {
                Object.defineProperty(Navigator.prototype, 'webdriver', {
                    get: () => undefined,
                    set: undefined,
                    configurable: true,
                    enumerable: true
                });
            } catch(e) {}

            // Also patch via a Proxy on the navigator instance for belt-and-braces coverage
            try {
                Object.defineProperty(window, 'navigator', {
                    value: new Proxy(navigator, {
                        has: (target, key) => (key === 'webdriver' ? false : key in target),
                        get: (target, key) => (key === 'webdriver' ? undefined : typeof target[key] === 'function' ? target[key].bind(target) : target[key])
                    }),
                    configurable: true,
                    writable: false,
                    enumerable: true
                });
            } catch(e) {}

            // Remove ChromeDriver's runtime injection markers if present
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e) {}
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e) {}
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(e) {}
            """,
        },
    )

    return driver_instance


# Variables to store the token and cookie
request_verification_token = None
required_cookies_dict = {}

required_cookies = [
    "ARRAffinity",
    "ARRAffinitySameSite",
    "CCLI_NET_AUTH",
    "CCLI_JWT_AUTH",
    ".AspNetCore.Session",
]
antiforgery_cookie_prefix = ".AspNetCore.Antiforgery"


def report_first_song():
    try:
        # Wait for the "Report Song" button to become clickable
        report_song_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(span/text(),'Report Song')]")
            )
        )
        report_song_button.click()

    except Exception as e:
        print(
            "Unable to automatically report the first song.\n Please try clicking the 'Report Song' button manually, to report any song."
        )


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
    cookie_names = [cookie["name"] for cookie in cookies]
    for required_cookie in required_cookies:
        if required_cookie not in cookie_names:
            return False
    if not any(
        cookie["name"].startswith(antiforgery_cookie_prefix) for cookie in cookies
    ):
        return False
    return True


def get_all_cookies():
    """Fetch cookies from ALL domains using CDP, not just the current page domain.

    driver.get_cookies() only returns cookies for the current domain, which misses
    cookies set on ccli.com or profile.ccli.com during the login redirect chain.
    """
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        return result.get("cookies", [])
    except Exception:
        # Fallback to standard get_cookies if CDP fails
        return driver.get_cookies()


def extract_required_cookies(cookies):
    cookies_dict = {}
    for cookie in cookies:
        cookie_name = cookie["name"]
        cookie_value = cookie["value"]
        # Check if the cookie name matches the required cookies
        if cookie_name in required_cookies:
            cookies_dict[cookie_name] = cookie_value
        # Handle antiforgery cookies
        if cookie_name.startswith(antiforgery_cookie_prefix):
            cookies_dict[cookie_name] = cookie_value
    return cookies_dict


def handle_cookie_popup():
    try:
        # Check if the cookie popup is displayed
        cookie_popup = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
        )
        if cookie_popup:
            # Click the "Allow all" button
            allow_all_button = driver.find_element(
                By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
            )
            allow_all_button.click()
            # print("Cookie popup closed.")
            # wait 2 seconds for the popup to close
            WebDriverWait(driver, 2).until_not(
                EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
            )
    except Exception as e:
        print("Cookie popup not found or already handled.")
        pass


def missing_cookies_report(cookies):
    """Return a list of human-readable strings describing which cookies are still missing."""
    cookie_names = [cookie["name"] for cookie in cookies]
    missing = []
    for required_cookie in required_cookies:
        if required_cookie not in cookie_names:
            missing.append(required_cookie)
    if not any(cookie["name"].startswith(antiforgery_cookie_prefix) for cookie in cookies):
        missing.append(f"{antiforgery_cookie_prefix}.*  (antiforgery)")
    return missing


def _wait_for_stop_or_timeout(seconds, missing_desc):
    """Display a countdown and return True if the user typed 'stop', False if time ran out."""
    import msvcrt
    print(f"\nStill missing: {missing_desc}")
    print("Type 'stop' and press Enter to give up, or do nothing to keep waiting automatically.")

    buffer = ""
    deadline = time.time() + seconds

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        print(f"\r  Auto-continuing in {remaining}s... (type 'stop' + Enter to quit)  ", end="", flush=True)

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

    print("\r  No input received — continuing to wait...                              ")
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
                    print(
                        "Manual mode waiting for login. Please finish signing in, then remain on reporting.ccli.com."
                    )
                    notice_shown = True
                else:
                    location = driver.current_url
                    if "reporting.ccli.com" not in location:
                        print(
                            "Still waiting for manual login to complete... navigate to https://reporting.ccli.com/search once signed in."
                        )
                    else:
                        print(f"Still waiting for cookies... ({elapsed}s elapsed, {remaining}s remaining)")
            else:
                print(f"Still waiting for cookies... ({elapsed}s elapsed, {remaining}s remaining)")

            print(f"  Missing ({len(missing)}): {', '.join(missing)}")

            time.sleep(poll_interval)

        # Timeout reached — give user 30 seconds to type 'stop', otherwise keep going
        cookies = get_all_cookies()
        missing = missing_cookies_report(cookies)
        total_waited = round_num * timeout
        print(f"\nStill waiting after {total_waited}s.")
        stopped = _wait_for_stop_or_timeout(30, ', '.join(missing))
        if stopped:
            print("Stopping cookie wait. Continuing with whatever was captured.")
            return cookies
        round_num += 1


def pause_for_cloudflare_challenge(timeout=240):
    # Allow the user to clear any Cloudflare challenge manually.
    start_time = time.time()
    notified = False

    while time.time() - start_time < timeout:
        current_url = driver.current_url
        if "challenges.cloudflare.com" in current_url:
            if not notified:
                print(
                    "Cloudflare challenge detected. Please complete the verification in the browser window."
                )
                notified = True
            time.sleep(2)
        else:
            if notified:
                print("Cloudflare challenge cleared. Continuing automation.")
            return

    if notified:
        print("Cloudflare challenge still active after waiting. Continuing anyway.")


def wait_for_sign_in_spinner():
    # Wait until the sign-in spinner is no longer visible before clicking.
    # Never gives up — if the spinner is still visible, we keep waiting.
    # This handles any hiding technique the page uses: display, visibility,
    # opacity, the HTML hidden attribute, and layout presence.
    print(
        "Waiting for the sign-in spinner to disappear. Please complete any prompts in the browser window."
    )

    def spinner_gone(d):
        try:
            return d.execute_script("""
                var el = document.getElementById('sign-in-spinner');
                if (!el) return true;
                var s = window.getComputedStyle(el);
                return s.display === 'none'
                    || s.visibility === 'hidden'
                    || parseFloat(s.opacity) === 0
                    || el.hidden
                    || el.offsetParent === null;
            """)
        except Exception:
            return True

    # Poll in 30-second windows and print a progress message each time.
    # The loop never exits until the spinner is actually gone.
    while True:
        try:
            WebDriverWait(driver, 30).until(spinner_gone)
            print("Sign-in spinner has disappeared.")
            return
        except TimeoutException:
            print("Still waiting for sign-in spinner to disappear...")


def getVerificationToken(cookies):

    print("Attempting to get verification token...")

    # Define the URL
    url = "https://reporting.ccli.com/api/antiForgery"

    # Define the headers from the raw capture
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

    # Define the cookies from the raw capture
    cookies = cookies

    # Send the GET request
    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=20)
    except Exception as e:
        print(f"Error: {e}")
        return None

    if response.status_code == 200:
        # Prefer header if present
        header_token = response.headers.get("RequestVerificationToken")
        if isinstance(header_token, str):
            try:
                header_token.encode("latin-1", "strict")
                return header_token.strip()
            except Exception:
                pass

        # Try JSON body
        try:
            data = response.json()
            # Some implementations return a raw JSON string: "token"
            if isinstance(data, str):
                try:
                    data.encode("latin-1", "strict")
                    return data.strip()
                except Exception:
                    pass
            # Others return an object with a token-like key
            if isinstance(data, dict):
                for key in (
                    "requestVerificationToken",
                    "token",
                    "RequestVerificationToken",
                ):
                    if key in data and isinstance(data[key], str):
                        cand = data[key].strip()
                        try:
                            cand.encode("latin-1", "strict")
                            return cand
                        except Exception:
                            continue
        except Exception:
            # ignore json parsing errors and fall through
            pass

        # As a very last resort, try to strip quotes from text if it's a simple JSON string
        txt = (response.text or "").strip()
        if txt.startswith('"') and txt.endswith('"') and len(txt) > 2:
            cand = txt[1:-1]
            try:
                cand.encode("latin-1", "strict")
                return cand
            except Exception:
                pass

        print(
            "Warning: Unable to extract a valid RequestVerificationToken from antiForgery API."
        )
        return None
    else:
        print(
            f"Error getting verification token. Status: {response.status_code}, Body: {response.text[:200]}"
        )
        return None


def gui_login():
    global driver  # Declare driver as global
    if manual_mode and use_remote_debugger:
        print(
            "Manual mode with remote debugger enabled. Ensure Chrome is already running with '--remote-debugging-port' set to"
            f" {remote_debugger_address.split(':')[-1]} before continuing."
        )

    driver = create_chrome_driver()
    driver.get("https://reporting.ccli.com/search")

    filtered_cookies = {}
    request_verification_token = None

    try:
        if manual_mode:
            print(
                "Manual mode enabled. Please complete the entire login flow in the opened browser window."
            )
            print(
                "Accept cookies, solve any challenges, and click Sign In yourself. The script will capture cookies once you're logged in."
            )
            cookies = collect_cookies(timeout=600, poll_interval=5, manual=True)
        else:
            pause_for_cloudflare_challenge()
            handle_cookie_popup()

            # Wait for redirect to login page
            WebDriverWait(driver, 20).until(
                EC.url_contains("profile.ccli.com/account/signin")
            )

            # Automatically fill in email and password
            email_field = driver.find_element(By.ID, "EmailAddress")
            password_field = driver.find_element(By.ID, "Password")

            email_field.send_keys(email)
            # pause 2 seconds
            time.sleep(2)

            # type the password key-by-key to try to trick the bot detection
            for letter in password:
                password_field.send_keys(letter)
                # wait random time between 0.1 and 0.3 seconds
                time.sleep(random.uniform(0.1, 0.3))

            # Click the login button

            wait_for_sign_in_spinner()
            # Re-find the button after the wait so the reference is never stale,
            # then force-enable it in case the page left it disabled.
            login_button = driver.find_element(By.ID, "sign-in")
            try:
                driver.execute_script(
                    "document.getElementById('sign-in').removeAttribute('disabled');"
                )
            except Exception:
                pass

            login_button.click()

            pause_for_cloudflare_challenge()

            # Wait until redirected back to the desired page
            WebDriverWait(driver, 20).until(
                EC.url_contains("reporting.ccli.com/search")
            )

            cookies = collect_cookies(timeout=300, poll_interval=5, manual=False)

        # Filter and print only the required cookies
        filtered_cookies = extract_required_cookies(cookies)
        if not filtered_cookies:
            raise RuntimeError(
                "Unable to find the required cookies. Ensure you're on https://reporting.ccli.com/search after logging in."
            )
        # for cookie_name, cookie_value in filtered_cookies.items():
        #     print(f"Cookie Name: {cookie_name}, Value: {cookie_value}")

        # Get the verification token
        request_verification_token = getVerificationToken(filtered_cookies)
        if not request_verification_token:
            raise RuntimeError(
                "Failed to obtain RequestVerificationToken. Verify you completed login and were redirected to reporting.ccli.com."
            )

    except Exception as e:
        print(f"\nLogin error: {e}")
        print("The script was unable to complete login.")
        input("Press Enter to close the browser and exit...")
        raise

    finally:
        driver.quit()

    cookie_string = "; ".join(
        [f"{name}={value}" for name, value in filtered_cookies.items()]
    )

    result = (request_verification_token, cookie_string)
    return result


if __name__ == "__main__":
    gui_login()from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, JavascriptException
from selenium.webdriver.chrome.service import Service
import ctypes
import json
import variables
import requests
import time
import random
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

# Add your login credentials here
email = variables.ccli_userame
password = variables.ccli_password

# Configuration flags
manual_mode = getattr(variables, "manual_mode", False)
use_remote_debugger = getattr(variables, "use_remote_debugger", False)
remote_debugger_address = getattr(
    variables, "remote_debugger_address", "127.0.0.1:9222"
)
browser_executable_path = getattr(
    variables,
    "browser_executable_path",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
)

_brave_process = None  # Tracks the self-launched Brave process for cleanup in gui_login()

# ChromeDriver cache lives in AppData\Local to avoid OneDrive sync interference.
# Do NOT put this inside an OneDrive folder — sync locks can prevent chromedriver.exe from running.
_DRIVER_CACHE_DIR = Path.home() / "AppData" / "Local" / "brave_ccli_drivers"


def _get_brave_chromium_version(executable_path):
    """
    Read the exact Chromium version embedded in the Brave binary via the Windows
    file version API (ctypes). This returns the real Chromium build number, e.g.
    '150.0.7871.63', NOT Brave's own directory name '150.1.92.134'.
    Falls back to directory-name detection if the version API fails.
    """
    path = str(executable_path)
    try:
        ver_size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if ver_size:
            buf = ctypes.create_string_buffer(ver_size)
            if ctypes.windll.version.GetFileVersionInfoW(path, 0, ver_size, buf):
                lp_buf = ctypes.c_void_p()
                n_len = ctypes.c_uint()
                if ctypes.windll.version.VerQueryValueW(
                    buf, "\\", ctypes.byref(lp_buf), ctypes.byref(n_len)
                ):
                    # VS_FIXEDFILEINFO layout: dwFileVersionMS at index 2, dwFileVersionLS at 3
                    dwords = ctypes.cast(lp_buf, ctypes.POINTER(ctypes.c_uint32))
                    ms, ls = dwords[2], dwords[3]
                    major = (ms >> 16) & 0xFFFF
                    minor = ms & 0xFFFF
                    build = (ls >> 16) & 0xFFFF
                    patch = ls & 0xFFFF
                    return f"{major}.{minor}.{build}.{patch}"
    except Exception:
        pass

    # Fallback: read the numbered subdirectory name next to brave.exe.
    # Note: Brave names this folder with its own version scheme (e.g. 150.1.92.134),
    # so the major version is correct but the rest may not match Chromium's build number.
    app_dir = Path(executable_path).parent
    version_pattern = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
    try:
        versions = [
            d.name for d in app_dir.iterdir()
            if d.is_dir() and version_pattern.match(d.name)
        ]
        if versions:
            return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    except Exception:
        pass

    raise RuntimeError(
        f"Could not determine Brave/Chromium version from {executable_path}"
    )


def _version_tuple(v):
    """Convert '150.0.7871.63' to (150, 0, 7871, 63) for correct numeric comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _get_chromedriver_path(executable_path):
    """
    Return the path to a ChromeDriver executable that matches the installed
    Brave/Chromium version. Downloads and caches it automatically when needed.

    Cache location: AppData/Local/brave_ccli_drivers/<major>/chromedriver.exe
    A new driver is only downloaded when Brave updates to a new major version.
    """
    version = _get_brave_chromium_version(executable_path)
    major = version.split(".")[0]

    cached_exe = _DRIVER_CACHE_DIR / major / "chromedriver.exe"
    if cached_exe.exists():
        print(f"[chromedriver] Using cached driver for Chromium {major} ({cached_exe})")
        return str(cached_exe)

    print(f"[chromedriver] Detected Brave/Chromium version: {version}. Searching for matching ChromeDriver...")

    # Query the official Chrome for Testing endpoint
    cft_url = (
        "https://googlechromelabs.github.io/chrome-for-testing/"
        "known-good-versions-with-downloads.json"
    )
    try:
        with urllib.request.urlopen(cft_url, timeout=30) as resp:
            cft_data = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch Chrome for Testing index: {exc}") from exc

    # Prefer an exact version match; otherwise take the highest available in this major.
    # Use numeric tuple comparison (not string) to correctly order e.g. .68 > .5.
    exact_url = None
    best_version = None
    download_url = None
    for entry in cft_data.get("versions", []):
        v = entry.get("version", "")
        if not v.startswith(f"{major}."):
            continue
        for dl in entry.get("downloads", {}).get("chromedriver", []):
            if dl.get("platform") == "win64":
                if v == version:
                    exact_url = dl.get("url")
                if best_version is None or _version_tuple(v) > _version_tuple(best_version):
                    best_version = v
                    download_url = dl.get("url")

    if exact_url:
        download_url = exact_url
        best_version = version
        print(f"[chromedriver] Found exact version match: {best_version}")
    elif download_url:
        print(f"[chromedriver] No exact match for {version}; closest available: {best_version}")
    else:
        raise RuntimeError(
            f"No win64 ChromeDriver found for Chromium major version {major}. "
            "Check https://googlechromelabs.github.io/chrome-for-testing/"
        )

    print(f"[chromedriver] Downloading ChromeDriver {best_version} from Chrome for Testing...")

    zip_path = _DRIVER_CACHE_DIR / f"chromedriver_{major}.zip"
    _DRIVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(download_url, zip_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to download ChromeDriver: {exc}") from exc

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            exe_data = None
            for name in z.namelist():
                if name.endswith("chromedriver.exe"):
                    exe_data = z.read(name)
                    break
            if exe_data is None:
                raise RuntimeError("chromedriver.exe not found inside the downloaded zip.")
        cached_exe.parent.mkdir(parents=True, exist_ok=True)
        cached_exe.write_bytes(exe_data)
    finally:
        try:
            zip_path.unlink()
        except Exception:
            pass

    print(f"[chromedriver] Saved to {cached_exe}")
    return str(cached_exe)


def _find_free_port():
    """Find an available TCP port for Brave's remote debugging endpoint."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_brave_with_debugging(port):
    """
    Launch Brave independently with --remote-debugging-port, bypassing ChromeDriver's
    browser launcher.

    When ChromeDriver launches Brave it adds --enable-automation, --test-type=webdriver,
    --disable-sync, and other flags. In Brave 150 (Chromium 150.0.7871.63), these flags
    combined with --remote-debugging-port cause an immediate startup crash. Pipe mode
    avoids the crash but the renderer then times out after 60 seconds.

    By launching Brave ourselves with only the minimal flags, neither crash occurs.
    ChromeDriver then connects to the already-running instance via debugger_address
    instead of launching its own copy, so it never adds those problematic flags.
    """
    global _brave_process

    profile_dir = str(Path.home() / "AppData" / "Local" / "brave_ccli_profile")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        browser_executable_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--allow-insecure-localhost",
        "--disable-brave-extension",
        "--disable-brave-rewards-extension",
        "--disable-brave-news-extension",
        "--disable-infobars",
        "--start-maximized",
    ]

    print(f"[brave] Launching Brave with remote debugging on port {port}...")
    _brave_process = subprocess.Popen(cmd)

    # Poll until Brave's DevTools HTTP endpoint responds
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[brave] Brave is ready on port {port}")
                    return
        except Exception:
            pass
        time.sleep(0.5)

    raise RuntimeError(
        f"Brave did not become ready for debugging on port {port} within 30 seconds. "
        "Ensure Brave can be launched normally from the Start menu."
    )


def create_chrome_driver():
    # NOTE: options.binary_location is NOT set here.
    # When using debugger_address, ChromeDriver connects to an already-running browser
    # rather than launching it, so the binary path is not needed.
    options = webdriver.ChromeOptions()

    if use_remote_debugger:
        # Attach to a user-managed Brave instance at the configured address
        options.debugger_address = remote_debugger_address
    else:
        # Launch Brave ourselves with only the flags we need, then connect ChromeDriver to it.
        # This avoids the crash that ChromeDriver's own launcher causes in Brave 150
        # (see _launch_brave_with_debugging for the full explanation).
        port = _find_free_port()
        _launch_brave_with_debugging(port)
        options.debugger_address = f"127.0.0.1:{port}"

    driver_path = _get_chromedriver_path(browser_executable_path)
    _log_path = str(Path(__file__).parent / "chromedriver.log")
    service = Service(driver_path, log_output=_log_path, service_args=["--verbose"])
    driver_instance = webdriver.Chrome(service=service, options=options)

    # Hide all common automation fingerprints so the CCLI login page does not
    # detect ChromeDriver. Patches both the Navigator prototype (which ChromeDriver
    # may set directly) and the instance-level proxy.
    driver_instance.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            // Patch at the prototype level — this is where ChromeDriver sets webdriver=true
            // when connecting via debugger_address, bypassing instance-level overrides.
            try {
                Object.defineProperty(Navigator.prototype, 'webdriver', {
                    get: () => undefined,
                    set: undefined,
                    configurable: true,
                    enumerable: true
                });
            } catch(e) {}

            // Also patch via a Proxy on the navigator instance for belt-and-braces coverage
            try {
                Object.defineProperty(window, 'navigator', {
                    value: new Proxy(navigator, {
                        has: (target, key) => (key === 'webdriver' ? false : key in target),
                        get: (target, key) => (key === 'webdriver' ? undefined : typeof target[key] === 'function' ? target[key].bind(target) : target[key])
                    }),
                    configurable: true,
                    writable: false,
                    enumerable: true
                });
            } catch(e) {}

            // Remove ChromeDriver's runtime injection markers if present
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e) {}
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e) {}
            try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(e) {}
            """,
        },
    )

    return driver_instance


# Variables to store the token and cookie
request_verification_token = None
required_cookies_dict = {}

required_cookies = [
    "ARRAffinity",
    "ARRAffinitySameSite",
    "CCLI_NET_AUTH",
    "CCLI_JWT_AUTH",
    ".AspNetCore.Session",
]
antiforgery_cookie_prefix = ".AspNetCore.Antiforgery"


def report_first_song():
    try:
        # Wait for the "Report Song" button to become clickable
        report_song_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(span/text(),'Report Song')]")
            )
        )
        report_song_button.click()

    except Exception as e:
        print(
            "Unable to automatically report the first song.\n Please try clicking the 'Report Song' button manually, to report any song."
        )


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
    cookie_names = [cookie["name"] for cookie in cookies]
    for required_cookie in required_cookies:
        if required_cookie not in cookie_names:
            return False
    if not any(
        cookie["name"].startswith(antiforgery_cookie_prefix) for cookie in cookies
    ):
        return False
    return True


def get_all_cookies():
    """Fetch cookies from ALL domains using CDP, not just the current page domain.

    driver.get_cookies() only returns cookies for the current domain, which misses
    cookies set on ccli.com or profile.ccli.com during the login redirect chain.
    """
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        return result.get("cookies", [])
    except Exception:
        # Fallback to standard get_cookies if CDP fails
        return driver.get_cookies()


def extract_required_cookies(cookies):
    cookies_dict = {}
    for cookie in cookies:
        cookie_name = cookie["name"]
        cookie_value = cookie["value"]
        # Check if the cookie name matches the required cookies
        if cookie_name in required_cookies:
            cookies_dict[cookie_name] = cookie_value
        # Handle antiforgery cookies
        if cookie_name.startswith(antiforgery_cookie_prefix):
            cookies_dict[cookie_name] = cookie_value
    return cookies_dict


def handle_cookie_popup():
    try:
        # Check if the cookie popup is displayed
        cookie_popup = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
        )
        if cookie_popup:
            # Click the "Allow all" button
            allow_all_button = driver.find_element(
                By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
            )
            allow_all_button.click()
            # print("Cookie popup closed.")
            # wait 2 seconds for the popup to close
            WebDriverWait(driver, 2).until_not(
                EC.visibility_of_element_located((By.ID, "CybotCookiebotDialog"))
            )
    except Exception as e:
        print("Cookie popup not found or already handled.")
        pass


def missing_cookies_report(cookies):
    """Return a list of human-readable strings describing which cookies are still missing."""
    cookie_names = [cookie["name"] for cookie in cookies]
    missing = []
    for required_cookie in required_cookies:
        if required_cookie not in cookie_names:
            missing.append(required_cookie)
    if not any(cookie["name"].startswith(antiforgery_cookie_prefix) for cookie in cookies):
        missing.append(f"{antiforgery_cookie_prefix}.*  (antiforgery)")
    return missing


def _wait_for_stop_or_timeout(seconds, missing_desc):
    """Display a countdown and return True if the user typed 'stop', False if time ran out."""
    import msvcrt
    print(f"\nStill missing: {missing_desc}")
    print("Type 'stop' and press Enter to give up, or do nothing to keep waiting automatically.")

    buffer = ""
    deadline = time.time() + seconds

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        print(f"\r  Auto-continuing in {remaining}s... (type 'stop' + Enter to quit)  ", end="", flush=True)

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

    print("\r  No input received — continuing to wait...                              ")
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
                    print(
                        "Manual mode waiting for login. Please finish signing in, then remain on reporting.ccli.com."
                    )
                    notice_shown = True
                else:
                    location = driver.current_url
                    if "reporting.ccli.com" not in location:
                        print(
                            "Still waiting for manual login to complete... navigate to https://reporting.ccli.com/search once signed in."
                        )
                    else:
                        print(f"Still waiting for cookies... ({elapsed}s elapsed, {remaining}s remaining)")
            else:
                print(f"Still waiting for cookies... ({elapsed}s elapsed, {remaining}s remaining)")

            print(f"  Missing ({len(missing)}): {', '.join(missing)}")

            time.sleep(poll_interval)

        # Timeout reached — give user 30 seconds to type 'stop', otherwise keep going
        cookies = get_all_cookies()
        missing = missing_cookies_report(cookies)
        total_waited = round_num * timeout
        print(f"\nStill waiting after {total_waited}s.")
        stopped = _wait_for_stop_or_timeout(30, ', '.join(missing))
        if stopped:
            print("Stopping cookie wait. Continuing with whatever was captured.")
            return cookies
        round_num += 1


def pause_for_cloudflare_challenge(timeout=240):
    # Allow the user to clear any Cloudflare challenge manually.
    start_time = time.time()
    notified = False

    while time.time() - start_time < timeout:
        current_url = driver.current_url
        if "challenges.cloudflare.com" in current_url:
            if not notified:
                print(
                    "Cloudflare challenge detected. Please complete the verification in the browser window."
                )
                notified = True
            time.sleep(2)
        else:
            if notified:
                print("Cloudflare challenge cleared. Continuing automation.")
            return

    if notified:
        print("Cloudflare challenge still active after waiting. Continuing anyway.")


def wait_for_sign_in_spinner():
    # Wait until the sign-in spinner is no longer visible before clicking.
    # Never gives up — if the spinner is still visible, we keep waiting.
    # This handles any hiding technique the page uses: display, visibility,
    # opacity, the HTML hidden attribute, and layout presence.
    print(
        "Waiting for the sign-in spinner to disappear. Please complete any prompts in the browser window."
    )

    def spinner_gone(d):
        try:
            return d.execute_script("""
                var el = document.getElementById('sign-in-spinner');
                if (!el) return true;
                var s = window.getComputedStyle(el);
                return s.display === 'none'
                    || s.visibility === 'hidden'
                    || parseFloat(s.opacity) === 0
                    || el.hidden
                    || el.offsetParent === null;
            """)
        except Exception:
            return True

    # Poll in 30-second windows and print a progress message each time.
    # The loop never exits until the spinner is actually gone.
    while True:
        try:
            WebDriverWait(driver, 30).until(spinner_gone)
            print("Sign-in spinner has disappeared.")
            return
        except TimeoutException:
            print("Still waiting for sign-in spinner to disappear...")


def getVerificationToken(cookies):

    print("Attempting to get verification token...")

    # Define the URL
    url = "https://reporting.ccli.com/api/antiForgery"

    # Define the headers from the raw capture
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

    # Define the cookies from the raw capture
    cookies = cookies

    # Send the GET request
    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=20)
    except Exception as e:
        print(f"Error: {e}")
        return None

    if response.status_code == 200:
        # Prefer header if present
        header_token = response.headers.get("RequestVerificationToken")
        if isinstance(header_token, str):
            try:
                header_token.encode("latin-1", "strict")
                return header_token.strip()
            except Exception:
                pass

        # Try JSON body
        try:
            data = response.json()
            # Some implementations return a raw JSON string: "token"
            if isinstance(data, str):
                try:
                    data.encode("latin-1", "strict")
                    return data.strip()
                except Exception:
                    pass
            # Others return an object with a token-like key
            if isinstance(data, dict):
                for key in (
                    "requestVerificationToken",
                    "token",
                    "RequestVerificationToken",
                ):
                    if key in data and isinstance(data[key], str):
                        cand = data[key].strip()
                        try:
                            cand.encode("latin-1", "strict")
                            return cand
                        except Exception:
                            continue
        except Exception:
            # ignore json parsing errors and fall through
            pass

        # As a very last resort, try to strip quotes from text if it's a simple JSON string
        txt = (response.text or "").strip()
        if txt.startswith('"') and txt.endswith('"') and len(txt) > 2:
            cand = txt[1:-1]
            try:
                cand.encode("latin-1", "strict")
                return cand
            except Exception:
                pass

        print(
            "Warning: Unable to extract a valid RequestVerificationToken from antiForgery API."
        )
        return None
    else:
        print(
            f"Error getting verification token. Status: {response.status_code}, Body: {response.text[:200]}"
        )
        return None


def gui_login():
    global driver  # Declare driver as global
    if manual_mode and use_remote_debugger:
        print(
            "Manual mode with remote debugger enabled. Ensure Chrome is already running with '--remote-debugging-port' set to"
            f" {remote_debugger_address.split(':')[-1]} before continuing."
        )

    driver = create_chrome_driver()
    driver.get("https://reporting.ccli.com/search")

    filtered_cookies = {}
    request_verification_token = None

    try:
        if manual_mode:
            print(
                "Manual mode enabled. Please complete the entire login flow in the opened browser window."
            )
            print(
                "Accept cookies, solve any challenges, and click Sign In yourself. The script will capture cookies once you're logged in."
            )
            cookies = collect_cookies(timeout=600, poll_interval=5, manual=True)
        else:
            pause_for_cloudflare_challenge()
            handle_cookie_popup()

            # Wait for redirect to login page
            WebDriverWait(driver, 20).until(
                EC.url_contains("profile.ccli.com/account/signin")
            )

            # Automatically fill in email and password
            email_field = driver.find_element(By.ID, "EmailAddress")
            password_field = driver.find_element(By.ID, "Password")

            email_field.send_keys(email)
            # pause 2 seconds
            time.sleep(2)

            # type the password key-by-key to try to trick the bot detection
            for letter in password:
                password_field.send_keys(letter)
                # wait random time between 0.1 and 0.3 seconds
                time.sleep(random.uniform(0.1, 0.3))

            # Click the login button

            wait_for_sign_in_spinner()
            # Re-find the button after the wait so the reference is never stale,
            # then force-enable it in case the page left it disabled.
            login_button = driver.find_element(By.ID, "sign-in")
            try:
                driver.execute_script(
                    "document.getElementById('sign-in').removeAttribute('disabled');"
                )
            except Exception:
                pass

            login_button.click()

            pause_for_cloudflare_challenge()

            # Wait until redirected back to the desired page
            WebDriverWait(driver, 20).until(
                EC.url_contains("reporting.ccli.com/search")
            )

            cookies = collect_cookies(timeout=300, poll_interval=5, manual=False)

        # Filter and print only the required cookies
        filtered_cookies = extract_required_cookies(cookies)
        if not filtered_cookies:
            raise RuntimeError(
                "Unable to find the required cookies. Ensure you're on https://reporting.ccli.com/search after logging in."
            )
        # for cookie_name, cookie_value in filtered_cookies.items():
        #     print(f"Cookie Name: {cookie_name}, Value: {cookie_value}")

        # Get the verification token
        request_verification_token = getVerificationToken(filtered_cookies)
        if not request_verification_token:
            raise RuntimeError(
                "Failed to obtain RequestVerificationToken. Verify you completed login and were redirected to reporting.ccli.com."
            )

    except Exception as e:
        print(f"\nLogin error: {e}")
        print("The script was unable to complete login.")
        input("Press Enter to close the browser and exit...")
        raise

    finally:
        driver.quit()

    cookie_string = "; ".join(
        [f"{name}={value}" for name, value in filtered_cookies.items()]
    )

    result = (request_verification_token, cookie_string)
    return result


if __name__ == "__main__":
    gui_login()
