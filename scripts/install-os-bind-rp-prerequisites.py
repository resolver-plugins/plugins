#!/usr/bin/env python3
"""Read source-owned floors from an authenticated native plugin manifest."""
import json
from pathlib import Path
import re
import sys

record = json.loads(Path(sys.argv[1]).read_text())
formula = re.fullmatch(r"bind920 >= (9\.20\.[0-9]+(?:_[1-9][0-9]*)?)", record.get("dep_formula", ""))
deps = record.get("deps") or {}
core = deps.get("opnsense") or {}
runtime = deps.get("bind920") or {}
if (formula is None or set(deps) != {"opnsense", "bind920"}
        or core.get("origin") != "opnsense/opnsense"
        or runtime.get("origin") != "dns/bind920"
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", core.get("version", "")) is None):
    raise SystemExit("invalid plugin prerequisite manifest")
print(formula[1] + "|" + core["version"])
