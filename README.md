# ccli_auto

Automate reporting of song usage to CCLI. Drop your report files into the `Reports` folder and run the script—each song is resolved and submitted, with clear, per-song output.

## Quick start

1) Create your config
- Copy `variables_example.py` to `variables.py`
- Fill in `ccli_userame` and `ccli_password`

2) Install dependencies
```
pip install requests selenium
```

3) Prepare your input
- Preferred: Export a FreeShow JSON report and put it in `Reports/`.
- Alternative: Place an OpenSong `ActivityLog.xml` in `Reports/` (or keep your OpenSong folder configured for the legacy fallback).

4) Run
```
python auto_ccli.py
```

On first run, a browser window may open so you can complete the login. After that, the script reuses the saved cookies and anti-forgery token.

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
Moved 2025-10-26_11-58.json to Done/
```

## Cookies and token

CCLI does not provide a public reporting API. The script authenticates via a normal browser login and captures the required cookies and anti-forgery token. These are saved for reuse:
- `Cookie.txt`
- `RequestVerificationToken.txt`

The script validates these values and can refresh the token from the server if needed. You can also obtain them manually from your browser dev tools and paste them into the files above.

## Input options

Preferred: place `.json` or `.xml` files in `Reports/` and run the script.

Legacy fallback (optional): you can still report from a list without files.
- In `variables.py`, define:
  - `getFromOpenSong = True` and `opensongFolder = r"C:\\Program Files\\OpenSong"`, or
  - `song_list = ["12345", "67890"]`
If these variables are absent, the script skips the legacy flow.

## Housekeeping

This repo ignores sensitive and local-only artifacts (see `.gitignore`):
- `variables.py`, cookies/token files, cache (`song_cache.json`)
- `Reports/` and processed `Settings/Reported/`
- logs (`debug.log`, `*.log`), `__pycache__/`, `.vscode/`, virtual envs

## Troubleshooting

- If a console encoding error appears, the script auto-configures robust UTF‑8 printing and also logs diagnostics to `debug.log`.
- If requests fail with 401/409, the script can refresh tokens and will prompt a new login if necessary.

## Testing/cleanup

For test runs, you can delete reports from the last 3 months:
```
python delete_all.py
```

