"""Append a human marker line to an observation JSONL file."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark an observation window")
    parser.add_argument("--out", default="artifacts/observation.jsonl")
    parser.add_argument("--mark", required=True)
    parser.add_argument("--detail", default="")
    args = parser.parse_args()

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts_ms": int(time.time() * 1000), "marker": args.mark, "detail": args.detail}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
