#!/usr/bin/env python
"""Download sigrok-cli Windows installer with size verification."""
import os, sys, urllib.request

URL = "https://sigrok.org/download/binary/sigrok-cli/sigrok-cli-0.7.2-x86_64-installer.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sigrok-cli-0.7.2-x86_64-installer.exe")

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=60)
total = int(resp.headers.get("Content-Length", 0))
done = 0
with open(OUT, "wb") as f:
    while True:
        chunk = resp.read(1 << 16)
        if not chunk:
            break
        f.write(chunk)
        done += len(chunk)
        if done % (1 << 20) == 0:
            print(f"  {done/1e6:.1f}/{total/1e6:.1f} MB", flush=True)

ok = total and done == total
print(f"OK={ok} got={done} expected={total}")
sys.exit(0 if ok else 1)
