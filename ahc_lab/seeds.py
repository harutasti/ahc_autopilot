from __future__ import annotations


def parse_seed_spec(spec: str | None) -> list[int]:
    """Parse seed specs like `0-9`, `1,3,5`, or `0-99:5`."""
    if spec is None or spec.strip() == "":
        return []
    seeds: list[int] = []
    seen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if ":" in part:
            part, step_s = part.split(":", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("seed range step must be positive")
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if end < start:
                raise ValueError("seed range end must be >= start")
            values = range(start, end + 1, step)
        else:
            values = [int(part)]
        for value in values:
            if value not in seen:
                seeds.append(value)
                seen.add(value)
    return seeds


def format_seed_list(seeds: list[int]) -> str:
    return ",".join(str(seed) for seed in seeds)

