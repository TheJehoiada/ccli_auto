# ccli_auto
Automate reporting of FreeShow song usage to CCLI. Each song is resolved and submitted, with clear, per-song output.

## Files
| File | Purpose |
|------|---------|
| `variables.py` | Your credentials and file paths |
| `auto_ccli.py` | Resolves CCLI numbers and submits the report |
| `cookie_extractor.py` | Handles browser login and cookie capture |
| `get_cookies_and_token.py` | Manages saved credentials and token refresh |
| `check_usage.py` | Checks if FreeShow's usage file has any CCLI entries |
| `ccli_weekly_report.cmd` | Scheduled task script that runs the full workflow |
| `delete_all.py` | Utility to delete past reports from CCLI by date range |

## Quick start
1. **Create your config**
   - Copy `variables_example.py` to `variables.py`
   - Fill in `ccli_userame` and `ccli_password`
   - Fill in the correct folder paths
   - Update `browser_executable_path` if not using Brave

2. **Install dependencies**
   ```
   pip install requests selenium selenium-stealth webdriver-manager
   ```

3. **Manual mode**
   - FreeShow exports usage automatically to `freeshow_usage_dir`
   - Run `py auto_ccli.py`

4. **Automatic (recommended)**
   - Open Windows Task Scheduler and create a new task named `CCLI_Auto_Report`
   - Set the trigger to a weekly time
   - Set the action to start `ccli_weekly_report.cmd`
   - Set "Start in" to the scripts folder

   When it runs the script will:
   - Check if FreeShow is running and close it gracefully, waiting for it to fully shut down
   - Check if the FreeShow usage file has any CCLI entries
   - If usage is found, move the file to `freeshow_usage_dir` with a datestamp
   - Always scan `freeshow_usage_dir` for any pending export files and report them to CCLI
   - Move successfully reported files to the `Reported/` folder

On the first run a browser window will open to complete the login. After that the script reuses saved cookies and refreshes the anti-forgery token automatically before each report submission.

## What the script does
- Reads CCLI numbers from FreeShow JSON export files in `freeshow_usage_dir`
- For each unique CCLI number:
  - Uses a local cache (`song_cache.json`) when possible
  - Otherwise searches CCLI for the song ID and official title
  - Prints a verbose line per song showing cache/search status, title, and song ID
- Fetches a fresh anti-forgery token before each submission using a persistent HTTP session, ensuring the token and its paired cookie are always submitted together
- Submits the report to CCLI
- On a 401 or 409 response, automatically re-logs in and retries
- Moves successfully reported files to `Reported/`
- Files with no CCLI entries are also moved to `Reported/`
- Files where the report failed are left in place for the next run

Example console output:
```
Attempting direct HTTP login (no browser)...
Direct login failed, will try browser login...
Launching Brave on debugging port 49766...
Brave Chromium version: 151.0.7922.137
Using cached ChromeDriver 151.0.7922.138
ChromeDriver connected.
Checking for existing CCLI session...
Waiting for the sign-in spinner to disappear (up to 5 minutes)...
Spinner resolved.
All required cookies captured!
Processing 2026-04-19.json (3 items)...
[cache] 6016351 - 10,000 Reasons (Bless The Lord) - 1839350d-9dd7-44b3-8ea4-69643e28a1a9
[search] Fetching details for CCLI 1406918...
[found] 1406918 - Shout To The Lord - 8d86f402-22c2-4e7f-866b-92584f1944d7
[missing] Could not resolve CCLI 9999999
Reporting the following songs:
6016351 - 10,000 Reasons (Bless The Lord) - 1839350d-9dd7-44b3-8ea4-69643e28a1a9
1406918 - Shout To The Lord - 8d86f402-22c2-4e7f-866b-92584f1944d7

2 songs reported successfully.
Moved 2026-04-19.json to Reported/
```

## Cookies and token
CCLI does not provide a public reporting API. The script authenticates via a normal browser login and captures the required cookies and anti-forgery token. These are saved for reuse:
- `Cookie.txt`
- `RequestVerificationToken.txt`

Before each report submission the script opens a `requests.Session`, fetches a fresh anti-forgery token through that session, and immediately submits the report through the same session. This is important because CCLI's server rotates the anti-forgery cookie on each token request; using the same session ensures the updated cookie and the new token are always submitted together.

If the session has expired the script will open the browser, log in, and retry automatically without any manual steps.

## How browser login works
CCLI's login page uses bot detection that prevents automated browsers from enabling the login form. The script works around this without any manual intervention:

1. **Direct HTTP login is tried first** — no browser is needed at all if the existing session is still valid.
2. **If that fails, Brave is launched as a normal subprocess** (not through ChromeDriver). ChromeDriver then *attaches* to the already-running browser rather than launching it, which avoids a crash that occurs when ChromeDriver tries to start Brave itself.
3. **CCLI is opened in a new browser tab via CDP** (`Target.createTarget`). Because ChromeDriver's automation infrastructure is not active on the new tab when the page loads, CCLI's bot detection sees a clean browser and enables the login form normally.
4. **ChromeDriver switches to the new tab** (after a brief wait for the page to finish its checks), fills in credentials, and completes login.
5. **ChromeDriver version is detected automatically** from the running Brave instance and the matching ChromeDriver is downloaded from Google's Chrome for Testing service and cached in `~/.cache/chromedriver_cft/`.

## variables.py reference
| Variable | Description |
|----------|-------------|
| `ccli_userame` | Your CCLI login email |
| `ccli_password` | Your CCLI password |
| `browser_executable_path` | Full path to Brave or Chrome executable |
| `freeshow_usage_dir` | Folder where FreeShow exports are stored and read from |
| `freeshow_usage_source` | Full path to FreeShow's live `usage.json` |
| `manual_mode` | If `True`, you complete the browser login yourself |
| `use_remote_debugger` | Advanced: attach to an already-running browser |
| `remote_debugger_address` | Address for remote debugger (default `127.0.0.1:9222`) |

## Troubleshooting
- If a console encoding error appears, the script auto-configures UTF-8 printing and logs diagnostics to `debug.log`
- If a report fails with 401 or 409, the script automatically clears saved credentials, re-logs in via the browser, and retries in the same run
- If the browser login takes longer than expected, the console will show which cookies are still missing every 5 seconds. After 5 minutes the script will ask whether to keep waiting — press Enter to continue or type `stop` and Enter to give up
- The `py` command is used instead of `python` in the CMD script to bypass the Windows Store Python alias
- ChromeDriver is downloaded automatically to `~/.cache/chromedriver_cft/` and matched to the installed Brave version. If Brave auto-updates and the version no longer matches, delete that folder and the script will download the correct version on the next run

## Deleting test reports
To delete past reports from CCLI by a specific date range:
```
py delete_all.py
```
The script will ask whether to delete by months or days, confirm the range before doing anything, then list everything it deleted (or skipped as outside the range) in a summary at the end.
