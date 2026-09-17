#!/usr/bin/env python3
"""Safely write your Webull APP KEY into the local .env file.

The key is stored single-quoted so a leading '#' or '$' (which otherwise
makes python-dotenv and the shell read the value as empty) is preserved.

Run:  python3 set_webull_key.py
Then: python3 auto_trader.py --webull-test
"""
import os

ENV = ".env"

key = input("Paste your Webull APP KEY, then press Enter:\n").strip().strip("'\"")
if not key:
    raise SystemExit("No key entered — nothing changed.")

lines = []
if os.path.exists(ENV):
    with open(ENV) as f:
        lines = f.read().splitlines()

out, found = [], False
for ln in lines:
    if ln.startswith("WEBULL_APP_KEY="):
        out.append("WEBULL_APP_KEY='" + key + "'")
        found = True
    else:
        out.append(ln)
if not found:
    out.append("WEBULL_APP_KEY='" + key + "'")

with open(ENV, "w") as f:
    f.write("\n".join(out) + "\n")

print(f"\nWrote WEBULL_APP_KEY ({len(key)} characters) to {ENV}, single-quoted.")
print("Now run:  python3 auto_trader.py --webull-test")
