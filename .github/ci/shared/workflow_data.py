#!/usr/bin/env python3
"""Read JSON fields for shell orchestration and GitHub Actions outputs."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("field", "outputs"))
    parser.add_argument("path", type=Path)
    parser.add_argument("fields", nargs="+")
    args = parser.parse_args()
    if args.command == "field" and len(args.fields) != 1:
        parser.error("field requires exactly one JSON field")
    record = json.loads(args.path.read_text())
    for field in args.fields:
        value = record[field]
        if args.command == "field":
            print(value)
        else:
            print(f"{field}={value if isinstance(value, str) else json.dumps(value)}")


if __name__ == "__main__":
    main()
