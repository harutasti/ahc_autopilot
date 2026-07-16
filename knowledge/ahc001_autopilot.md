# ahc001 autopilot insights

Auto-generated per generation.

## 2026-07-16 15:08 g1 AnnealingBuilder -> accepted
- mean effective delta 974851526.45, confidence 1.0, wins/ties/losses 20/0/0
- verdict: mean effective delta 974851526.45 is positive with 20 win(s), 0 loss(es)
- agent summary tail: nverging well in practice per above). Fixed seed means behavior is deterministic per input but not adaptively tuned per-case; T0/T1/step-size constants are reasonable defaults, not exhaustively tuned — a future tuning pass could still find headroom. Time margin (4.8s vs 5s hard limit) should be safe across judge hardware but is worth double-checking against a slower grading machine if TLEs appear.

## 2026-07-16 15:08 g1 StateDesignBuilder -> accepted
- mean effective delta 981058644.45, confidence 1.0, wins/ties/losses 20/0/0
- verdict: mean effective delta 981058644.45 is positive with 20 win(s), 0 loss(es)
- agent summary tail: l risk is timing margin on a slower grading machine, mitigated by using only 4.5 of the 5 available seconds. This move set (single-edge resampling) doesn't include swap/translate moves, so there's headroom left for other roles (e.g. a move-set/schedule specialist) to push scores further — intentionally left out to stay scoped to state representation and incremental scoring, per the assigned focus.

## 2026-07-16 15:45 g2 AnnealingBuilder -> rejected
- mean effective delta -2024257.0, confidence 0.263, wins/ties/losses 1/0/4
- verdict: candidate fails or misses 15 seed(s) the baseline solves; 4 worsening seed(s) exceed the budget of 0; worst per-seed worsening 7908468.0 exceeds the budget of 0.0; median effective delta is negative
- repair attempts: 2 (rejected -> rejected)
- pruned early: 4 worsening seed(s) exceed the budget of 0
- agent summary tail: at exceeds the gate's zero-tolerance-per-seed budget regardless of code quality; this is a structural property of the time-based cutoff (out of scope for me to fix, since time allocation isn't part of the AnnealingBuilder role), not something this repair can eliminate. I picked the 5%/5% share config since it was empirically the least-bad among what I tested and matches the original design intent.

## 2026-07-16 15:45 g2 StateDesignBuilder -> duplicate
- verdict: candidate source is identical to already-evaluated run 120
- repair attempts: 2 (rejected -> duplicate)
- agent summary tail: ). No score improvement over baseline either, since the intended "restore" feature never engaged in practice. **Risk:** None beyond what the already-accepted baseline carries. I also added `knowledge/ahc001.md` documenting why hot-loop touches are unusually risky here, so future StateDesignBuilder/AnnealingBuilder attempts instrument trigger rates before trusting a "should be neutral" hypothesis.
