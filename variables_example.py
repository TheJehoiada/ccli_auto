"""
Variables template

Copy this file to variables.py and fill in your credentials. 
Fill in the correct folder paths.

The default workflow will use the auto_ccli.py script to report files that have been manually exported from FreeShow.
If FreeShow has logged the song usage but not exported the usage, then the ccli_weekly_report.cmd script will safely and automatically export it.
The ccli_weekly_report can be scheduled in Windows Task Scheduler to run automatically every week.

A browser-based login may be needed the first time to capture your authentication cookies/token.
"""

ccli_userame = "abc@gmail.com"
ccli_password = "mysecurepassword"

# --- Browser ---
# Path to the browser executable (Brave shown below; swap for Chrome if needed)
browser_executable_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

# --- File paths ---
# Folder where FreeShow usage files are exported to AND where auto_ccli.py reads from
freeshow_usage_dir = r"C:\Users\Media\OneDrive - Church\FreeShowSettings&Shows\Exports\Usage"

# Full path to FreeShow's live usage.json (the file that gets copied out and reset)
freeshow_usage_source = r"C:\Users\Media\AppData\Roaming\freeshow\usage.json"

# Optional: control how the first-time login is performed
manual_mode = False  # If True, you complete any challenges manually
use_remote_debugger = False  # Advanced: attach to an existing Chrome
remote_debugger_address = "127.0.0.1:9222"
