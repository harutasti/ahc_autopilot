#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    tools_dir = Path(__file__).resolve().parent / "tools 2"
    cmd = [
        "cargo",
        "run",
        "--release",
        "--bin",
        "vis",
        str(args.input.resolve()),
        str(args.output.resolve()),
    ]
    proc = subprocess.run(
        cmd,
        cwd=tools_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    if proc.returncode != 0:
        return proc.returncode
    match = re.search(r"([-+0-9.eE]+)", proc.stdout)
    if not match:
        print(f"could not parse score from official visualizer output: {proc.stdout!r}", file=sys.stderr)
        return 1
    print(f"SCORE: {match.group(1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

