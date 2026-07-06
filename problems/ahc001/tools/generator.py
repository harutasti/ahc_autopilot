#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    tools_dir = Path(__file__).resolve().parent / "tools 2"
    source = tools_dir / "in" / f"{args.seed:04d}.txt"
    if not source.exists():
        print(
            f"seed {args.seed} is not available; use 0-49 or generate more official inputs",
            file=sys.stderr,
        )
        return 1
    with source.open("rb") as src:
        shutil.copyfileobj(src, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

