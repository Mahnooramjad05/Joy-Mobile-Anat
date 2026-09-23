"""Small jobs the batch runner cannot do cleanly on its own.

    python helper.py wait-online [seconds]   wait for the internet, exit 0 or 1
    python helper.py prune-logs [days]       delete run logs older than N days

Kept separate from the sync so the sync itself is unchanged by packaging.
"""

import os
import socket
import sys
import time

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Reaching any one of these means the connection is really up, not just that an
# adapter has an address. Two providers, so one being down is not a false alarm.
PROBES = [("www.google.com", 443), ("8.8.8.8", 53), ("1.1.1.1", 53)]


def wait_online(limit_seconds=120):
    """Block until the internet answers, or the limit runs out.

    A laptop waking at 06:00 has a working task scheduler before it has working
    Wi-Fi. Without this the sync would fail on a connection that was seconds
    away from being ready.
    """
    deadline = time.time() + limit_seconds
    attempt = 0

    while True:
        for host, port in PROBES:
            try:
                with socket.create_connection((host, port), timeout=4):
                    if attempt:
                        print(f"Internet is up (after {attempt * 5} seconds).")
                    return True
            except OSError:
                continue

        if time.time() >= deadline:
            print(f"Still no internet after {limit_seconds} seconds.")
            return False

        attempt += 1
        if attempt == 1:
            print("Waiting for the internet connection...")
        time.sleep(5)


def prune_logs(days=60):
    """Keep the last N days of run logs, quietly."""
    if not os.path.isdir(LOG_DIR):
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(LOG_DIR):
        if not name.lower().endswith(".log"):
            continue
        path = os.path.join(LOG_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass          # a locked or vanished file is not worth failing over
    return removed


def main(argv):
    if not argv:
        print(__doc__)
        return 2

    command = argv[0]
    if command == "wait-online":
        limit = int(argv[1]) if len(argv) > 1 else 120
        return 0 if wait_online(limit) else 1
    if command == "prune-logs":
        days = int(argv[1]) if len(argv) > 1 else 60
        prune_logs(days)
        return 0

    print(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
