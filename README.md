# ccli_auto

Automate reporting of song usage to CCLI. Each song is resolved and submitted, with clear, per-song output.

## Quick start

1) Create your config
- Copy `variables_example.py` to `variables.py`
- Fill in `ccli_userame` and `ccli_password`
- Fill in the correct folder paths.
- Update browser location if not using Brave.

2) Install dependencies
```
pip install requests selenium
```

3) Manual Mode
- Export FreeShow usage.
- Run python auto_ccli.py

4) Automatic
```
Open Windows Task Scheduler and create a new task, and name it CCLI_Auto_Report
Set the trigger to a weekly time.
Set the action to start a program and link it to the ccli_weekly_report.cmd file.
Have it start in its own folder

When it runs, it will close FreeShow.
Then it will copy all of the currently logged song usage to the chosen usage folder.
Then it will run the auto_ccli.py script.
```

On the first run, a browser window may open to complete the login. After that, the script reuses the saved cookies and anti-forgery token.

## What the script does

- Collects CCLI numbers from your input files:
  - FreeShow JSON (.json)
  - OpenSong ActivityLog.xml (.xml)
- For each unique CCLI number:
  - Uses a local cache when possible
  - Otherwise searches CCLI for song ID and official title
  - Prints a verbose line per song (cache/search, title, song_id)
- Submits a report to CCLI
- Moves processed input to `Reports/Done/` (handles name collisions safely)

Example console output:
```
Attempting to get RequestVerificationToken and Cookie from file.
RequestVerificationToken and Cookie read from file.

Processing 2025-10-26_11-58.json (43 items)...
[cache] 798108 - Ancient Of Days - daeca40a-7f82-42c1-bb44-305a5c68a697
[search] Fetching details for CCLI 2447467...
[found] 2447467 - Some Title - 123e4567-e89b-12d3-a456-426614174000
[missing] Could not resolve CCLI 9999999

Reporting the following songs:
798108 - Ancient Of Days - daeca40a-7f82-42c1-bb44-305a5c68a697
...

43 songs reported successfully.
Moved 2025-10-26_11-58.json to Reported/
```

## Cookies and token

CCLI does not provide a public reporting API. The script authenticates via a normal browser login and captures the required cookies and anti-forgery token. These are saved for reuse:
- `Cookie.txt`
- `RequestVerificationToken.txt`

The script validates these values and can refresh the token from the server if needed. You can also obtain them manually from your browser dev tools and paste them into the files above.

## Troubleshooting

- If a console encoding error appears, the script auto-configures robust UTF‑8 printing and also logs diagnostics to `debug.log`.
- If requests fail with 401/409, the script can refresh tokens and will prompt a new login if necessary.

## Testing/cleanup

For test runs, you can delete reports for any length of time that you specify using:
```
python delete_all.py
```
