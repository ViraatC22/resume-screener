"""Handle candidates' replies to their interview invites.

Run it after `crewai run` has sent the invites:

    uv run check_replies.py                           # check Gmail once
    uv run check_replies.py --watch                   # keep checking every minute
    uv run check_replies.py --dry-run sample_replies  # test with saved .eml files; sends and saves nothing

A candidate who asks for another time gets an updated invite when a matching
slot is free, or three alternative times when it isn't (or when they just say
the time doesn't work). After three rounds of alternatives, or for replies that
aren't about scheduling, the candidate is flagged for a person to follow up.
"""

import argparse
import email
import time
from email import policy
from pathlib import Path

from dotenv import load_dotenv

from tools.replies import fetch_messages, process_replies

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Handle replies to interview invites.")
    parser.add_argument("--watch", action="store_true", help="keep checking every minute")
    parser.add_argument("--dry-run", metavar="FOLDER", help="read .eml files from FOLDER instead of Gmail; send and save nothing")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)

    while True:
        if args.dry_run:
            messages = [
                email.message_from_bytes(path.read_bytes(), policy=policy.default)
                for path in sorted(Path(args.dry_run).glob("*.eml"))
            ]
        else:
            messages = fetch_messages()
        process_replies(messages, dry_run=bool(args.dry_run))
        if not args.watch or args.dry_run:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
