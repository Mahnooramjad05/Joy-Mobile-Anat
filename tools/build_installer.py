"""Build KSP-Price-Update.zip: a self-contained Windows installer for the sync.

    python tools/build_installer.py

Produces dist/KSP-Price-Update.zip, which contains a private Python, the sync,
and every setting pre-filled. The person receiving it extracts the zip and
double-clicks Install.bat. Nothing else.

**The zip contains real secrets** -- the Google service account key and the
Gmail app password -- so dist/ is gitignored and the zip must never be
committed. This script reads those values straight from .env and the key file
and writes them into the package; they are not printed anywhere.

Why the python.org embeddable build rather than PyInstaller: python.exe from
python.org is signed by the Python Software Foundation and Defender leaves it
alone. A PyInstaller one-file exe is unsigned, unpacks itself to a temp folder
at startup, and is flagged often enough that handing one to a non-technical
person in another country is asking for a bad afternoon.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER_DIR = os.path.join(ROOT, "installer")
BUILD_DIR = os.path.join(ROOT, "build", "installer")
DIST_DIR = os.path.join(ROOT, "dist")
ZIP_NAME = "KSP-Price-Update.zip"

PYTHON_VERSION = "3.12.10"
EMBED_URL = (f"https://www.python.org/ftp/python/{PYTHON_VERSION}"
             f"/python-{PYTHON_VERSION}-embed-amd64.zip")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# What the sync itself needs. Deliberately not the API, not the tools, not the
# tests, and not the 270 KB of captured fixtures.
SCRAPER_MODULES = [
    "config.py", "credentials.py", "fetch.py",
    "normalize.py", "notify.py", "sheets.py", "sources.py", "sync.py",
]

DEPENDENCIES = [
    "requests>=2.31",
    "gspread>=6.0",
    "google-auth>=2.28",
    "tzdata",          # Windows has no system zone database; emails say Israel time
    # sources.py imports BeautifulSoup at module level for the PelePhone page
    # adapter. KSP never touches it, but the import runs either way, so it has
    # to be here -- the alternative is editing the sync, and packaging should
    # not do that.
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
]

# Written into settings.env. Left is what the sync reads, right is where the
# value comes from in .env -- the API and the sync use different names for the
# spreadsheet, which is the one mapping that matters.
SETTINGS = [
    ("TRADEIN_SPREADSHEET_ID", ("TRADEIN_SPREADSHEET_ID", "SPREADSHEET_ID")),
    ("SMTP_HOST", ("SMTP_HOST",)),
    ("SMTP_PORT", ("SMTP_PORT",)),
    ("SMTP_USER", ("SMTP_USER",)),
    ("SMTP_PASSWORD", ("SMTP_PASSWORD", "SMTP_PASS")),
    ("NOTIFY_FROM", ("NOTIFY_FROM", "EMAIL_FROM")),
    ("NOTIFY_TO", ("NOTIFY_TO", "EMAIL_TO")),
    ("NOTIFY_FAILURE_TO", ("NOTIFY_FAILURE_TO", "EMAIL_FAILURE_TO")),
]


def read_env(path):
    """Parse a .env file into a dict. Values are never printed."""
    values = {}
    if not os.path.exists(path):
        return values
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def download(url, destination):
    if os.path.exists(destination):
        print(f"  cached  {os.path.basename(destination)}")
        return destination
    print(f"  fetching {url}")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read()
    with open(destination, "wb") as fh:
        fh.write(data)
    digest = hashlib.sha256(data).hexdigest()[:16]
    print(f"  saved   {os.path.basename(destination)} "
          f"({len(data) / 1_048_576:.1f} MB, sha256 {digest}...)")
    return destination


def build_python(app_dir, cache_dir):
    """Unpack the embeddable Python and install the dependencies into it."""
    python_dir = os.path.join(app_dir, "python")
    os.makedirs(python_dir, exist_ok=True)

    embed_zip = download(EMBED_URL, os.path.join(cache_dir, os.path.basename(EMBED_URL)))
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(python_dir)

    # The embeddable build takes full control of sys.path through its ._pth
    # file: site imports are off (so no pip and no site-packages) and, crucially,
    # neither the working directory nor PYTHONPATH is consulted. Without the
    # ".." entry below, `python -m scraper.sync` cannot find the sync at all --
    # the app folder is the parent of this python folder.
    for name in os.listdir(python_dir):
        if not name.endswith("._pth"):
            continue
        path = os.path.join(python_dir, name)
        with io.open(path, encoding="utf-8") as fh:
            content = fh.read()

        content = content.replace("#import site", "import site")
        existing = [line.strip() for line in content.splitlines()]
        for entry in ("Lib\\site-packages", ".."):
            if entry not in existing:
                content = content.rstrip() + "\n" + entry + "\n"

        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        print(f"  {name}: site-packages enabled, app folder (..) on sys.path")

    python_exe = os.path.join(python_dir, "python.exe")

    get_pip = download(GET_PIP_URL, os.path.join(cache_dir, "get-pip.py"))
    print("  installing pip")
    run([python_exe, get_pip, "--no-warn-script-location", "-q"])

    print(f"  installing {len(DEPENDENCIES)} dependencies")
    run([python_exe, "-m", "pip", "install", "--no-warn-script-location", "-q",
         *DEPENDENCIES])

    # pip and its caches are only needed to build the package, not to run it.
    for junk in ("Scripts", "Lib/site-packages/pip", "Lib/site-packages/setuptools",
                 "Lib/site-packages/wheel", "Lib/site-packages/pkg_resources"):
        shutil.rmtree(os.path.join(python_dir, *junk.split("/")), ignore_errors=True)

    return python_exe


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise SystemExit(f"command failed: {' '.join(str(c) for c in command[:3])} ...")


def copy_sync(app_dir):
    """The sync, and nothing else from the repository."""
    target = os.path.join(app_dir, "scraper")
    os.makedirs(target, exist_ok=True)
    for name in SCRAPER_MODULES:
        shutil.copy2(os.path.join(ROOT, "scraper", name), os.path.join(target, name))
    print(f"  copied {len(SCRAPER_MODULES)} sync modules (no tests, no fixtures, no API)")


def write_settings(app_dir, env, failure_to=None):
    """Pre-fill every setting. Returns the list of names that could not be filled."""
    lines = [
        "# Settings for the daily KSP price update.",
        "# Written by tools/build_installer.py. Do not edit by hand.",
        "#",
        "# This file contains a password. Keep the folder to yourself.",
        "",
    ]
    missing = []

    for name, sources in SETTINGS:
        value = ""
        if name == "NOTIFY_FAILURE_TO" and failure_to:
            value = failure_to
        else:
            for source in sources:
                if env.get(source):
                    value = env[source]
                    break
        if not value:
            missing.append(name)
            continue
        lines.append(f"{name}={value}")

    # The key travels as a file beside this one, so the multi-line JSON never
    # has to survive being squeezed into a single environment variable.
    lines.append("TRADEIN_CREDENTIALS=google-credentials.json")
    lines.append("")

    path = os.path.join(app_dir, "settings.env")
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines))
    return missing


def copy_static(app_dir):
    for name in ("run-sync.bat", "setup.ps1", "task.xml.template", "helper.py"):
        shutil.copy2(os.path.join(INSTALLER_DIR, name), os.path.join(app_dir, name))
    os.makedirs(os.path.join(app_dir, "logs"), exist_ok=True)
    with io.open(os.path.join(app_dir, "logs", "README.txt"), "w",
                 encoding="utf-8", newline="\r\n") as fh:
        fh.write("A log of every run is written here.\r\n"
                 "Logs older than 60 days are deleted automatically.\r\n")


def verify(app_dir, python_exe):
    """Import everything the sync needs, using the bundled Python.

    This exists because the first build shipped without bs4 and the failure only
    appeared on the installed machine. A build that cannot import its own code
    should never become a zip.
    """
    # Imported the way the scheduled task imports it: `scraper.sync` puts its
    # own folder on sys.path and then pulls in config, notify, sheets and the
    # rest, so this one import exercises the whole chain.
    checks = (
        "import scraper.sync",
        "import requests, gspread, google.oauth2.service_account, bs4, lxml",
        "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Jerusalem')",
    )
    result = subprocess.run([python_exe, "-c", "; ".join(checks) + "; print('ok')"],
                            capture_output=True, text=True, cwd=app_dir)
    if result.returncode != 0 or "ok" not in result.stdout:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("the packaged Python cannot import the sync -- not shipping it")
    print("  every module imports, Asia/Jerusalem resolves")

    result = subprocess.run([python_exe, "-m", "scraper.sync", "--help"],
                            capture_output=True, text=True, cwd=app_dir)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit("`python -m scraper.sync` does not start -- not shipping it")
    print("  `python -m scraper.sync` starts")


def make_zip(staging_dir, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for folder, _dirs, files in os.walk(staging_dir):
            for name in files:
                full = os.path.join(folder, name)
                archive.write(full, os.path.relpath(full, staging_dir))
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python tools/build_installer.py")
    parser.add_argument("--failure-to", help="NOTIFY_FAILURE_TO, if not in .env")
    parser.add_argument("--keep-build", action="store_true",
                        help="leave build/installer in place for inspection")
    args = parser.parse_args(argv)

    print("Building KSP-Price-Update.zip")
    print()

    env = read_env(os.path.join(ROOT, ".env"))
    key_path = os.path.join(ROOT, "google-credentials.json")
    if not os.path.exists(key_path):
        raise SystemExit("google-credentials.json is missing from the repository root.")

    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    app_dir = os.path.join(BUILD_DIR, "app")
    os.makedirs(app_dir, exist_ok=True)

    print("Python runtime")
    python_exe = build_python(app_dir, os.path.join(ROOT, "build", "cache"))

    print("\nProgram files")
    copy_sync(app_dir)
    copy_static(app_dir)
    shutil.copy2(key_path, os.path.join(app_dir, "google-credentials.json"))
    print("  copied the Google key")

    missing = write_settings(app_dir, env, failure_to=args.failure_to)
    print(f"  wrote settings.env ({len(SETTINGS) - len(missing)} of "
          f"{len(SETTINGS)} settings filled)")
    if missing:
        print(f"\n  MISSING: {', '.join(missing)}")
        print("  The package will not work until these are set. Add them to .env")
        print("  (or pass --failure-to) and build again.")

    for name in ("Install.bat", "Uninstall.bat"):
        shutil.copy2(os.path.join(INSTALLER_DIR, name), os.path.join(BUILD_DIR, name))

    print("\nChecking the package")
    verify(app_dir, python_exe)

    print("\nPackaging")
    out = make_zip(BUILD_DIR, os.path.join(DIST_DIR, ZIP_NAME))
    size = os.path.getsize(out) / 1_048_576

    if not args.keep_build:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    print(f"  {out}")
    print(f"  {size:.1f} MB")
    print()
    print("This zip contains the Google key and the email password.")
    print("Never commit it, and send it to her over something private.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
