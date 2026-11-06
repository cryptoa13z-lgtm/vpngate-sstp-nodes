#!/usr/bin/env python3
"""Fail if public output contains obvious private credential material."""
from pathlib import Path
import re

root = Path(__file__).parent
# Scan generated/public artifacts only. Documentation and source intentionally
# mention forbidden names to explain the boundary and are not published data.
files = list((root / "public").glob("*"))
patterns = [
    re.compile(r"vless://", re.I),
    re.compile(r"\b(?:ADMIN|KEY|TOKEN|UUID)\s*[:=]", re.I),
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I),
]
errors = []
for path in files:
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            errors.append(f"{path}: matched {pattern.pattern}")
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print(f"OK: scanned {len(files)} files; no private credential or VLESS material found in public outputs")
