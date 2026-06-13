#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    target = int(args.input.read_text(encoding="utf-8").strip())
    try:
        answer = int(args.output.read_text(encoding="utf-8").strip())
    except Exception:
        print("SCORE: 0")
        return 0
    score = max(0, 1_000_000 - abs(target - answer))
    print(f"SCORE: {score}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

