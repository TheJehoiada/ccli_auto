import os
import sys
import math
import datetime
# Ensure the working directory is always this script's folder so that
# Cookie.txt, RequestVerificationToken.txt, and variables.py are found correctly.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import requests
from get_cookies_and_token import get_cookie_and_token
import variables
import json

type = "lyrics"  # lyrics or sheetmusic


def ask_time_range():
    print("\n--- CCLI Report Deletion ---")
    print("How would you like to specify the time range to delete?")
    print("  1. Months")
    print("  2. Days")

    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice in ("1", "2"):
            break
        print("Please enter 1 or 2.")

    if choice == "1":
        while True:
            val = input("How many months back should be deleted? ").strip()
            if val.isdigit() and int(val) > 0:
                months = int(val)
                break
            print("Please enter a positive whole number.")
        # No day filter needed — delete everything the API returns
        days_filter = None
        label = f"{months} month(s)"
        return months, days_filter, label
    else:
        while True:
            val = input("How many days back should be deleted? ").strip()
            if val.isdigit() and int(val) > 0:
                days = int(val)
                break
            print("Please enter a positive whole number.")
        # Round up to nearest whole month for the API, but filter precisely by days
        months = math.ceil(days / 30)
        days_filter = days
        label = f"{days} day(s)"
        return months, days_filter, label


def parse_report_date(date_str):
    """Parse a reportDate string into a date object. Returns None if unparseable."""
    if not date_str or date_str == "unknown date":
        return None
    # Try common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(date_str[:len(fmt)], fmt).date()
        except ValueError:
            continue
    # Last resort: strip everything after T and try date only
    try:
        return datetime.datetime.fromisoformat(date_str.split("T")[0]).date()
    except Exception:
        return None


def confirm(label):
    print(f"\nYou are about to delete all {type} reports from the past {label}.")
    print("This cannot be undone.")
    while True:
        answer = input("Are you sure you want to continue? (yes/no): ").strip().lower()
        if answer in ("yes", "no"):
            return answer == "yes"
        print("Please type 'yes' or 'no'.")


def get_history(Cookie, RequestVerificationToken, last_month_range):
    print("Attempting to get report history...")

    url = f"https://reporting.ccli.com/api/history/{type}?lastMonthRange={last_month_range}"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://reporting.ccli.com/",
        "Content-Type": "application/json;charset=utf-8",
        "RequestVerificationToken": RequestVerificationToken,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Te": "trailers",
        "Cookie": Cookie.strip().rstrip(";"),
    }

    try:
        response = requests.get(url, headers=headers)
    except Exception as e:
        print(f"Error: {e}")
        return None, Cookie, RequestVerificationToken

    if response.status_code == 200:
        return response.json(), Cookie, RequestVerificationToken
    else:
        print(f"Error getting history — status: {response.status_code}")
        print(f"Response: {response.text[:500]}")
        return None, Cookie, RequestVerificationToken


def delete_report(report_id, song_title, Cookie, RequestVerificationToken):
    url = f"https://reporting.ccli.com/api/report/{type}/{report_id}"

    cookie_header = Cookie.strip().rstrip(";")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://reporting.ccli.com/",
        "Content-Type": "application/json;charset=utf-8",
        "RequestVerificationToken": RequestVerificationToken,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Te": "trailers",
        "Cookie": cookie_header,
    }

    try:
        response = requests.delete(url, headers=headers)
        if response.status_code == 200:
            print(f"  Deleted: {song_title} (ID: {report_id})")
            return True
        else:
            print(f"  Failed:  {song_title} (ID: {report_id}) — status {response.status_code}")
            return False
    except Exception as e:
        print(f"  Error:   {song_title} (ID: {report_id}) — {e}")
        return False


def process_reports(json_data, Cookie, RequestVerificationToken, days_filter=None):
    deleted = []
    failed = []
    skipped = []

    cutoff = None
    if days_filter is not None:
        cutoff = datetime.date.today() - datetime.timedelta(days=days_filter)

    for song_data in json_data["data"]:
        # 'song' may be a plain string or a nested object with a title field
        song_field = song_data.get("song", "Unknown Title")
        if isinstance(song_field, dict):
            song_title = song_field.get("title") or song_field.get("name") or str(song_field)
        else:
            song_title = str(song_field) if song_field else "Unknown Title"

        for report in song_data["data"]:
            report_id = report["reportId"]
            report_date_str = report.get("date", "unknown date")
            entry = f"{song_title} — reported on {report_date_str} (ID: {report_id})"

            # Apply day filter if specified
            if cutoff is not None:
                report_date = parse_report_date(report_date_str)
                if report_date is None:
                    print(f"  Skipped: {song_title} — could not parse date '{report_date_str}'")
                    skipped.append(entry)
                    continue
                if report_date < cutoff:
                    print(f"  Skipped: {song_title} — {report_date_str} is outside the {days_filter}-day range")
                    skipped.append(entry)
                    continue

            success = delete_report(report_id, song_title, Cookie, RequestVerificationToken)
            if success:
                deleted.append(entry)
            else:
                failed.append(entry)

    return deleted, failed, skipped


# --- Main ---
last_month_range, days_filter, label = ask_time_range()

if not confirm(label):
    print("Deletion cancelled.")
else:
    RequestVerificationToken, Cookie = get_cookie_and_token()
    history, Cookie, RequestVerificationToken = get_history(Cookie, RequestVerificationToken, last_month_range)
    if history:
        print(f"\nDeleting reports...\n")
        deleted, failed, skipped = process_reports(history, Cookie, RequestVerificationToken, days_filter)

        print(f"\n--- Deletion Summary ---")
        print(f"Successfully deleted ({len(deleted)}):")
        if deleted:
            for entry in deleted:
                print(f"  {entry}")
        else:
            print("  None")

        if skipped:
            print(f"\nSkipped — outside date range ({len(skipped)}):")
            for entry in skipped:
                print(f"  {entry}")

        if failed:
            print(f"\nFailed to delete ({len(failed)}):")
            for entry in failed:
                print(f"  {entry}")
    else:
        print("Could not retrieve history. Nothing was deleted.")

input("\nPress Enter to close...")
