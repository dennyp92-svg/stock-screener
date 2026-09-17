#!/usr/bin/env python3
"""Safely store your Webull APP KEY as raw bytes in a sidecar file.

Writes the key verbatim to .webull_app_key next to this script — NO .env,
NO KEY=value parsing, NO quoting — so any character in the key (a leading
'#'/'$' or an embedded quote) survives untouched. auto_trader.py reads this
file automatically when the WEBULL_APP_KEY env var isn't set.

Run:  python3 set_webull_key.py
Then: python3 auto_trader.py --webull-test
"""
import os

here = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(here, ".webull_app_key")

key = input("Paste your Webull APP KEY, then press Enter:\n").strip().strip("'\"")
if not key:
    raise SystemExit("No key entered — nothing changed.")

with open(path, "w") as f:
    f.write(key)

print(f"\nWrote {len(key)} characters to {path} (raw, no parsing).")
print("Now run:  python3 auto_trader.py --webull-test")
