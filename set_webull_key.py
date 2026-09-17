#!/usr/bin/env python3
"""Safely store your Webull APP KEY and APP SECRET as raw bytes.

Writes each verbatim to a sidecar file next to this script
(.webull_app_key / .webull_app_secret) — NO .env, NO KEY=value parsing,
NO quoting — so any character survives untouched. It also removes any
WEBULL_APP_KEY / WEBULL_APP_SECRET lines from .env so a stale or truncated
value there can't win. auto_trader.py reads the sidecar files automatically.

Run:  python3 set_webull_key.py
Then: python3 auto_trader.py --webull-test
"""
import os

here = os.path.dirname(os.path.abspath(__file__))


def write_sidecar(name, value):
    with open(os.path.join(here, "." + name.lower()), "w") as f:
        f.write(value)


key = input("Paste your Webull APP KEY, then press Enter:\n").strip().strip("'\"")
secret = input("Paste your Webull APP SECRET, then press Enter:\n").strip().strip("'\"")
if not key or not secret:
    raise SystemExit("Both the APP KEY and APP SECRET are required — nothing changed.")

write_sidecar("WEBULL_APP_KEY", key)
write_sidecar("WEBULL_APP_SECRET", secret)

# Drop any stale/truncated key/secret lines from .env so the sidecar wins.
env = os.path.join(here, ".env")
if os.path.exists(env):
    kept = [ln for ln in open(env).read().splitlines()
            if not ln.startswith("WEBULL_APP_KEY=")
            and not ln.startswith("WEBULL_APP_SECRET=")]
    with open(env, "w") as f:
        f.write("\n".join(kept) + ("\n" if kept else ""))

print(f"\nSaved APP KEY ({len(key)} chars) and APP SECRET ({len(secret)} chars) to sidecar files.")
print("Cleared any WEBULL_APP_KEY/WEBULL_APP_SECRET lines from .env.")
print("Now run:  python3 auto_trader.py --webull-test")
