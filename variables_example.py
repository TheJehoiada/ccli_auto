"""
Variables template

Copy this file to variables.py and fill in your credentials. The default
workflow is to drop your report files into the Reports folder and run
auto_ccli.py. A browser-based login may be needed the first time to capture
your authentication cookies/token.
"""

ccli_userame = "abc@gmail.com"
ccli_password = "mysecurepassword"

# Optional: control how the first-time login is performed
manual_mode = True  # If True, you complete any challenges manually
use_remote_debugger = False  # Advanced: attach to an existing Chrome
remote_debugger_address = "127.0.0.1:9222"

# Note: The legacy fallback using OpenSong or a hardcoded song_list is no longer
# required for the normal workflow and has been removed from this template. If
# you prefer the legacy flow, you can create these variables in your variables.py:
#   getFromOpenSong = True
#   opensongFolder = r"C:\\Program Files\\OpenSong"
#   song_list = ["12345", "67890"]
