import builtins
import io
import json
import os
import sys
from pathlib import Path
import re
import shutil
import datetime

# Ensure the working directory is always this script's folder so that
# Cookie.txt, RequestVerificationToken.txt, and variables.py are found correctly.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import requests

from cookie_extractor import gui_login
from get_cookies_and_token import get_cookie_and_token
import variables


# ---------------------- Debug utilities (file-based) ----------------------
def debug_log(message):
    try:
        with open("debug.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(str(message) + "\n")
    except Exception:
        # Never raise from debug logging
        pass


def encoding_info():
    import locale

    try:
        stdout_enc = getattr(sys.stdout, "encoding", None)
    except Exception:
        stdout_enc = None
    try:
        stderr_enc = getattr(sys.stderr, "encoding", None)
    except Exception:
        stderr_enc = None

    parts = [
        f"sys.stdout.encoding={stdout_enc} has_buffer={hasattr(sys.stdout, 'buffer')}",
        f"sys.stderr.encoding={stderr_enc} has_buffer={hasattr(sys.stderr, 'buffer')}",
        f"locale.preferred={locale.getpreferredencoding(False)}",
        f"sys.defaultencoding={sys.getdefaultencoding()}",
        f"sys.filesystemencoding={sys.getfilesystemencoding()}",
        f"PYTHONIOENCODING={os.environ.get('PYTHONIOENCODING')}",
    ]
    return "\n".join(parts)


def preview_codepoints(label, s, limit=40):
    try:
        snippet = s[:limit]
    except Exception:
        snippet = s
    cps = []
    try:
        for ch in snippet:
            cps.append(f"U+{ord(ch):04X}")
    except Exception:
        cps = ["<unprintable>"]
    return f"{label}: len={len(s) if hasattr(s,'__len__') else 'n/a'} snippet={repr(snippet)} codepoints={[', '.join(cps)]}"


def sanitize_header_value(name, value):
    """Ensure HTTP header value contains only latin-1 encodable characters.

    - Strip surrounding whitespace and internal CR/LF
    - Remove control characters
    - Drop any non-latin-1 characters (encoding errors ignored)
    Logs before/after if modifications occur.
    """
    if value is None:
        return value
    try:
        s = str(value)
    except Exception:
        s = repr(value)

    original = s
    # Normalize newlines and remove control chars except HT (\t) and SP
    s = s.replace("\r", " ").replace("\n", " ")
    s = "".join(
        ch
        for ch in s
        if (ch == "\t") or (32 <= ord(ch) <= 126) or (160 <= ord(ch) <= 255)
    )
    # Enforce latin-1 encodability by dropping unsupported chars
    s_bytes = s.encode("latin-1", "ignore")
    s_clean = s_bytes.decode("latin-1", "ignore").strip()

    if s_clean != original:
        debug_log("sanitize_header_value: modified " + name)
        debug_log(preview_codepoints(name + " BEFORE", original))
        debug_log(preview_codepoints(name + " AFTER ", s_clean))

    return s_clean


def configure_stream(stream):
    """Best-effort: ensure a text stream writes UTF-8 with replacement."""
    try:
        debug_log(
            f"configure_stream: trying reconfigure on {stream is sys.stdout and 'stdout' or stream is sys.stderr and 'stderr' or 'stream'}"
        )
        stream.reconfigure(encoding="utf-8", errors="replace")
        debug_log(
            f"configure_stream: reconfigure OK -> encoding={getattr(stream,'encoding',None)}"
        )
        return
    except Exception:
        debug_log("configure_stream: reconfigure not supported")

    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        debug_log("configure_stream: no .buffer; cannot wrap")
        return

    try:
        wrapped = io.TextIOWrapper(
            buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        if stream is sys.stdout:
            sys.stdout = wrapped
        elif stream is sys.stderr:
            sys.stderr = wrapped
        debug_log("configure_stream: wrapped stream with TextIOWrapper UTF-8")
    except Exception:
        # If wrapping fails, silently continue; we'll rely on safe_print
        debug_log("configure_stream: wrapping failed; relying on safe_print")


def safe_print(*args, sep=" ", end="\n", file=None, flush=False):
    """A print replacement that bypasses text encodings by writing UTF-8 bytes.

    Always encodes to UTF-8 with replacement and writes to the underlying
    buffer whenever available. This avoids UnicodeEncodeError regardless of
    the console's configured encoding.
    """
    if file is None:
        file = sys.stdout

    try:
        text = sep.join(str(a) for a in args) + end
    except Exception:
        text = sep.join(repr(a) for a in args) + end

    data = text.encode("utf-8", "replace")

    buf = getattr(file, "buffer", None)
    if buf is not None:
        try:
            buf.write(data)
        except Exception:
            # As a last resort, try the original stdout buffer
            try:
                sys.__stdout__.buffer.write(data)
            except Exception:
                # Fallback to text write; may still fail but we've tried bytes
                try:
                    file.write(text)
                except Exception:
                    pass
    else:
        # No buffer (e.g., StringIO); fall back to text write
        try:
            file.write(text)
        except Exception:
            pass

    if flush:
        try:
            # Flush both text and buffer if possible
            if buf is not None:
                try:
                    buf.flush()
                except Exception:
                    pass
            file.flush()
        except Exception:
            pass


os.environ.setdefault("PYTHONIOENCODING", "utf-8")
configure_stream(sys.stdout)
configure_stream(sys.stderr)
# Override built-in print with safe_print to avoid console encoding crashes
builtins.print = safe_print
debug_log("startup: overridden builtins.print with safe_print")
debug_log("startup encodings:\n" + encoding_info())


CACHE_FILE = Path("song_cache.json")
REPORTS_DIR = Path(getattr(variables, "freeshow_usage_dir", r"C:\Users\Media\AppData\Roaming\freeshow\usage.json"))
DONE_DIR = Path(__file__).parent / "Reported"


def load_song_cache():
    if CACHE_FILE.exists():
        try:
            with CACHE_FILE.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
                if isinstance(data, dict):
                    return {
                        str(key): value
                        for key, value in data.items()
                        if isinstance(value, dict)
                        and "song_id" in value
                        and "title" in value
                    }
        except Exception as exc:
            print(f"Warning: Unable to load cache '{CACHE_FILE}': {exc}")
    return {}


def save_song_cache(cache):
    try:
        with CACHE_FILE.open("w", encoding="utf-8") as cache_file:
            json.dump(cache, cache_file, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"Warning: Unable to write cache '{CACHE_FILE}': {exc}")


class Song:
    def __init__(self, ccli_number, song_id, title):
        self.ccli_number = ccli_number
        self.song_id = song_id
        self.title = title

    def __repr__(self):
        return f"{self.ccli_number} - {self.title} - {self.song_id}"


def search(song_ccli, Cookie):

    song_ccli_str = str(song_ccli).strip()
    if not song_ccli_str:
        debug_log("search: Skipping empty CCLI number from request list.")
        return None

    url_search = "https://reporting.ccli.com/api/search"

    params = {
        "searchTerm": song_ccli_str,
        "searchCategory": "all",
        "searchFilters": "[]",
    }

    cookie_header = sanitize_header_value("Cookie", Cookie.strip().rstrip(";"))

    headers_search = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Client-Locale": "en-GB",
        "Content-Type": "application/json;charset=utf-8",
        "Referer": "https://reporting.ccli.com/search?s="
        + song_ccli_str
        + "&page=1&category=all",
        "Cookie": cookie_header,
    }
    debug_log(
        preview_codepoints("Header Cookie (search)", headers_search.get("Cookie", ""))
    )

    debug_log(f"search: querying for CCLI={song_ccli_str}")
    response_search = requests.get(url_search, params=params, headers=headers_search)

    debug_log(
        f"search: status={response_search.status_code} content-type={response_search.headers.get('Content-Type')} content-encoding={response_search.headers.get('Content-Encoding')}"
    )

    if response_search.status_code == 200:
        try:
            data = response_search.json()
        except ValueError:
            debug_log(
                "search: non-JSON response received; snippet="
                + repr(response_search.text[:200])
            )
            return None

        results = data.get("results", {})
        songs = results.get("songs", [])
        for song_data in songs:
            ccli_number = str(song_data.get("ccliSongNo", "")).strip()
            if not ccli_number:
                continue
            if ccli_number != song_ccli_str:
                continue
            song_id = song_data.get("id")
            title = song_data.get("title", "").strip()
            if not song_id or not title:
                continue

            return Song(ccli_number, song_id, title)

        debug_log(f"search: no exact match for CCLI={song_ccli_str}")
        return None
    elif response_search.status_code == 401:
        debug_log(
            "search: 401 unauthorized; likely bad Cookie. Deleting token/cookies."
        )

        import os

        try:
            os.remove("RequestVerificationToken.txt")
            os.remove("Cookie.txt")
        except:
            pass

    else:
        debug_log(f"search: HTTP error status={response_search.status_code}")
    return None


def report(songs_dict, Cookie, RequestVerificationToken):

    # Always fetch a fresh antiforgery token before submitting — the token saved
    # during login is tied to the browser session and becomes invalid once it closes.
    from get_cookies_and_token import _try_fetch_token_from_server
    fresh_token = _try_fetch_token_from_server(Cookie)
    if fresh_token:
        debug_log("report: refreshed RequestVerificationToken from server")
        RequestVerificationToken = fresh_token
    else:
        debug_log("report: could not refresh token from server; using saved token")

    data = {
        "songs": [],
        "lyrics": {"uses": 1, "digital": 0, "print": 0, "record": 0, "translate": 0},
        "sheetMusic": [],
        "rehearsals": [],
        "mrsls": [],
    }

    for song in songs_dict.values():
        # Create a dictionary for each song
        song_entry = {
            "id": song.song_id,
            "title": song.title,
            "ccliSongNo": song.ccli_number,
        }
        # Append the song to the "songs" list in the data
        data["songs"].append(song_entry)

    totalNumberOfSongs = len(data["songs"])

    first_song = next(iter(songs_dict.values()))

    url_report = "https://reporting.ccli.com/api/report"
    cookie_header = sanitize_header_value("Cookie", Cookie.strip().rstrip(";"))

    headers_post = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json",
        "RequestVerificationToken": sanitize_header_value(
            "RequestVerificationToken", RequestVerificationToken
        ),
        "Client-Locale": "en-GB",
        "Origin": "https://reporting.ccli.com",
        "Referer": "https://reporting.ccli.com/search?s="
        + first_song.ccli_number
        + "&page=1&category=all",
        "Cookie": cookie_header,
    }
    debug_log(
        preview_codepoints("Header Cookie (report)", headers_post.get("Cookie", ""))
    )
    debug_log(
        preview_codepoints(
            "Header RequestVerificationToken",
            headers_post.get("RequestVerificationToken", ""),
        )
    )

    debug_log(
        f"report: submitting {len(data['songs'])} songs; referer ccli={first_song.ccli_number}"
    )
    response_post = requests.post(url_report, json=data, headers=headers_post)
    debug_log(
        f"report: status={response_post.status_code} snippet={repr(response_post.text[:200])}"
    )

    if response_post.status_code == 200:
        print("\n" + str(totalNumberOfSongs) + " songs reported successfully.")
        return True
    elif response_post.status_code == 409:
        debug_log(
            "report: 409 conflict; likely bad RequestVerificationToken. Deleting token/cookies."
        )
        print("Report failed: 409 Conflict — RequestVerificationToken was rejected. Saved credentials cleared; will re-login next run.")
        import os
        try:
            os.remove("RequestVerificationToken.txt")
            os.remove("Cookie.txt")
        except:
            pass
        return False
    elif response_post.status_code == 401:
        debug_log(
            "report: 401 unauthorized; likely bad Cookie. Deleting token/cookies."
        )
        print("Report failed: 401 Unauthorized — session cookie was rejected. Saved credentials cleared; will re-login next run.")
        import os
        try:
            os.remove("RequestVerificationToken.txt")
            os.remove("Cookie.txt")
        except:
            pass
        return False
    else:
        debug_log(
            f"report: error status={response_post.status_code} body={repr(response_post.text[:300])}"
        )
        print(f"Report failed: {response_post.status_code} — {response_post.text[:200]}")
        return False


def refresh_cached_songs(songs_dict, Cookie, song_cache, song_sources):
    refreshed = False
    for ccli_number, source in list(song_sources.items()):
        if source != "cache":
            continue

        debug_log(f"refresh_cached_songs: refreshing CCLI={ccli_number}")
        refreshed_song = search(ccli_number, Cookie)
        if refreshed_song:
            songs_dict[ccli_number] = refreshed_song
            song_cache[ccli_number] = {
                "song_id": refreshed_song.song_id,
                "title": refreshed_song.title,
            }
            song_sources[ccli_number] = "fresh"
            refreshed = True
        else:
            debug_log(f"refresh_cached_songs: unable to refresh CCLI={ccli_number}")
            print(
                f"Unable to refresh song details for cached CCLI number {ccli_number}."
            )
    return refreshed



# ---------------------- Reports folder processing ----------------------
def find_report_files():
    files = []
    if REPORTS_DIR.exists() and REPORTS_DIR.is_dir():
        for p in REPORTS_DIR.iterdir():
            if p.is_dir():
                continue
            if p.suffix.lower() == ".json":
                files.append(p)
    debug_log(f"find_report_files: found {len(files)} files")
    return sorted(files)


def _collect_ccli_from_json(obj, out_set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if "ccli" in kl:
                if isinstance(v, (int, float)):
                    out_set.add(str(int(v)))
                elif isinstance(v, str):
                    nums = re.findall(r"\b\d{3,}\b", v)
                    for n in nums:
                        out_set.add(n)
            _collect_ccli_from_json(v, out_set)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ccli_from_json(item, out_set)


def parse_freeshow_json(file_path: Path):
    cclis = []
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        found = set()
        _collect_ccli_from_json(data, found)
        cclis = [s for s in sorted(found) if s.strip()]
    except Exception as e:
        debug_log(f"parse_freeshow_json: error reading {file_path} -> {e}")
    debug_log(f"parse_freeshow_json: {file_path.name} -> {len(cclis)} CCLI items")
    return cclis


def extract_ccli_from_file(file_path: Path):
    if file_path.suffix.lower() == ".json":
        return parse_freeshow_json(file_path)
    return []


def move_to_done(file_path: Path):
    try:
        DONE_DIR.mkdir(parents=True, exist_ok=True)
        target = DONE_DIR / file_path.name
        if target.exists():
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            target = DONE_DIR / f"{file_path.stem}_{ts}{file_path.suffix}"
        shutil.move(str(file_path), str(target))
        debug_log(f"move_to_done: moved {file_path.name} -> {target.name}")
        print(f"Moved {file_path.name} to Reported/")
    except Exception as e:
        debug_log(f"move_to_done: error moving {file_path} -> {e}")
        print(f"Warning: could not move {file_path.name} to Reported: {e}")


def process_report_file(file_path: Path, RequestVerificationToken, Cookie, song_cache):
    debug_log(f"process_report_file: start {file_path}")
    ccli_list = extract_ccli_from_file(file_path)
    if not ccli_list:
        print(f"No CCLI entries found in {file_path.name}; moving to Done.")
        move_to_done(file_path)
        return

    songs_dict = {}
    song_sources = {}
    processed_cclis = set()

    print(f"Processing {file_path.name} ({len(ccli_list)} items)...")

    for raw in ccli_list:
        ccli_number = str(raw).strip()
        if not ccli_number or ccli_number in processed_cclis:
            continue
        processed_cclis.add(ccli_number)

        cached_entry = song_cache.get(ccli_number)
        if cached_entry and cached_entry.get("song_id") and cached_entry.get("title"):
            songs_dict[ccli_number] = Song(
                ccli_number, cached_entry["song_id"], cached_entry["title"]
            )
            song_sources[ccli_number] = "cache"
            print(
                f"[cache] {ccli_number} - {cached_entry['title']} - {cached_entry['song_id']}"
            )
            continue

        print(f"[search] Fetching details for CCLI {ccli_number}...")
        song_details = search(ccli_number, Cookie)
        if song_details:
            songs_dict[ccli_number] = song_details
            song_cache[ccli_number] = {
                "song_id": song_details.song_id,
                "title": song_details.title,
            }
            song_sources[ccli_number] = "fresh"
            print(
                f"[found] {song_details.ccli_number} - {song_details.title} - {song_details.song_id}"
            )
        else:
            song_sources[ccli_number] = "missing"
            print(f"[missing] Could not resolve CCLI {ccli_number}")

    missing_cclis = [k for k, src in song_sources.items() if src == "missing"]
    if missing_cclis:
        print(
            "Warning: Unable to find matching songs for the following CCLI numbers: "
            + ", ".join(missing_cclis)
        )

    if not songs_dict:
        print(f"No songs could be resolved for {file_path.name}. File will not be moved — check your CCLI login and try again.")
        return

    # Persist cache updates before report
    save_song_cache(song_cache)

    # Attempt report
    # Show a quick summary list before reporting
    print("\nReporting the following songs:")
    for s in songs_dict.values():
        print(f"{s.ccli_number} - {s.title} - {s.song_id}")

    success = report(songs_dict, Cookie, RequestVerificationToken)
    if not success and any(src == "cache" for src in song_sources.values()):
        print(
            f"Report failed for {file_path.name}. Attempting to refresh cached details and retry."
        )
        refreshed = refresh_cached_songs(songs_dict, Cookie, song_cache, song_sources)
        if refreshed:
            save_song_cache(song_cache)
            success = report(songs_dict, Cookie, RequestVerificationToken)

    if success:
        move_to_done(file_path)
    else:
        print(f"Report did not complete successfully for {file_path.name}.")



def main():
    report_files = find_report_files()

    RequestVerificationToken, Cookie = get_cookie_and_token()
    song_cache = load_song_cache()

    if report_files:
        for f in report_files:
            process_report_file(f, RequestVerificationToken, Cookie, song_cache)
        return

    print("No report files found in the exports folder. Nothing to report.")


if __name__ == "__main__":
    # Install a UTF-8 friendly excepthook to avoid encoding crashes when printing tracebacks
    def utf8_excepthook(exc_type, exc_value, tb):
        import traceback

        try:
            trace = "".join(traceback.format_exception(exc_type, exc_value, tb))
        except Exception:
            trace = f"{exc_type.__name__}: {exc_value}"

        try:
            with open("debug.log", "a", encoding="utf-8", errors="replace") as f:
                f.write("=== Unhandled exception ===\n")
                f.write(trace)
                f.write("\n=== Encoding info ===\n")
                f.write(encoding_info() + "\n")
        except Exception:
            pass

        # Keep console message minimal and safe
        safe_print("Unhandled exception. See debug.log for details.")

    sys.excepthook = utf8_excepthook
    main()
