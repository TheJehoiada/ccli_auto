from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, JavascriptException
import json
import variables
import requests
import time
import random

# Add your login credentials here
email = variables.ccli_userame
password = variables.ccli_password

# Configuration flags
manual_mode = getattr(variables, "manual_mode", False)
use_remote_debugger = getattr(variables, "use_remote_debugger", False)
remote_debugger_address = getattr(
    variables, "remote_debugger_address", "127.0.0.1:9222"
)


def create_chrome_driver():
    options = webdriver.ChromeOptions()
    # Start Chrome with logging preferences for performance (network events only)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    # Reduce Selenium fingerprints
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    if use_remote_debugger:
        options.debugger_address = remote_debugger_address

    driver_instance = webdriver.Chrome(options=options)

    if not use_remote_debugger:
        # Remove navigator.webdriver flag for this session when we fully manage Chrome
        driver_instance.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(window, 'navigator', {
                    value: new Proxy(navigator, {
                        has: (target, key) => (key === 'webdriver' ? false : key in target),
                        get: (target, key) => (key === 'webdriver' ? undefined : target[key])
                    })
                });
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
    "CCLI_AUTH",
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
                cookies = driver.get_cookies()
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


def collect_cookies(timeout=300, poll_interval=5, manual=False):
    start_time = time.time()
    cookies = []
    notice_shown = False

    while time.time() - start_time < timeout:
        cookies = driver.get_cookies()
        if are_cookies_captured(cookies):
            print("All required cookies captured!")
            return cookies

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
                    print("Still waiting for manual login to complete...")
        else:
            print("Still waiting for all cookies...")

        time.sleep(poll_interval)

    print("Timed out waiting for all cookies. Continuing with whatever was captured.")
    return cookies


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


def wait_for_sign_in_spinner(timeout=120):
    # Wait until the sign-in spinner stops blocking the button so the user can finish any challenges.
    spinner_js = "return document.getElementById('sign-in-spinner');"

    def spinner_hidden(driver):
        try:
            element = driver.execute_script(spinner_js)
            if element is None:
                return True
            return (
                driver.execute_script(
                    "return window.getComputedStyle(document.getElementById('sign-in-spinner')).getPropertyValue('display');"
                )
                == "none"
            )
        except JavascriptException:
            return True

    try:
        print(
            "Waiting for the sign-in spinner to disappear. Please complete any prompts in the browser window."
        )
        WebDriverWait(driver, timeout).until(spinner_hidden)
    except TimeoutException:
        print("Spinner still visible after waiting. Continuing anyway.")


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

            login_button = driver.find_element(By.ID, "sign-in")
            wait_for_sign_in_spinner()
            # change the html to enable the login button
            try:
                driver.execute_script(
                    "document.getElementById('sign-in').removeAttribute('disabled');"
                )
            except:
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
        print(f"Error: {e}")
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
