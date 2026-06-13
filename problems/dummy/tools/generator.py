#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    print(rng.randint(0, 1_000_000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

