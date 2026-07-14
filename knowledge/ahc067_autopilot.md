# ahc067 autopilot insights

Auto-generated per generation.

## 2026-07-06 23:15 g1 CutBuilder -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_1_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-06 23:15 g1 PerformanceBuilder -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_2_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-06 23:49 g1 CutBuilder -> error
- verdict: adapter failed with exit code 124; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_3_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-06 23:49 g1 AnnealingBuilder -> accepted
- mean effective delta 461497.1666666667, confidence 1.0, wins/ties/losses 30/0/0
- verdict: mean effective delta 461497.1666666667 is positive with 30 win(s), 0 loss(es)
- agent summary tail: hard TL **Risk:** Low — the acceptance path is unchanged (`evaluate()` gates every trial), so validity/reachability/output-format guarantees carry over from the parent. The only behavioral risk is timing margin, which was explicitly tested: max observed elapsed 1.888s leaves a 0.11s buffer before the 2.0s limit, consistent with the existing `Timer(1.90)` safety margin used throughout the solver.

## 2026-07-06 23:57 g2 CutBuilder -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_5_repair1.stderr.txt
- repair attempts: 2 (error -> error)
- agent summary tail: You've hit your session limit · resets 3am (UTC)

## 2026-07-06 23:57 g2 ParameterTuner -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_6_repair1.stderr.txt
- repair attempts: 2 (error -> error)
- agent summary tail: You've hit your session limit · resets 3am (UTC)

## 2026-07-07 03:17 g1 CutBuilder -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_7_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-07 03:17 g1 PerformanceBuilder -> error
- verdict: adapter failed with exit code 1; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_8_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-07 03:49 g1 CutBuilder -> error
- verdict: adapter failed with exit code 124; see /home/user/ahc_autopilot/experiments/adapter_logs/trial_9_repair1.stderr.txt
- repair attempts: 2 (error -> error)

## 2026-07-07 03:49 g1 AnnealingBuilder -> rejected
- mean effective delta -1534.2333333333333, confidence 0.27, wins/ties/losses 1/28/1
- verdict: 1 worsening seed(s) exceed the budget of 0; worst per-seed worsening 73249.0 exceeds the budget of 0.0
- repair attempts: 2 (error -> rejected)
- agent summary tail: * I also recorded in `knowledge/ahc067.md` that this solver's wall-clock-bounded anneal loops introduce real run-to-run score noise (observed swings up to ~1.3%), so small A/B deltas need repeated trials to trust, and that the 2/8/11/16 tie looks like it may be a structural ceiling rather than a reachable-but-stuck local optimum — worth investigating before further local-search variants target it.

## 2026-07-07 14:44 g1 CutBuilder -> rejected
- mean effective delta -15566.6, confidence 0.0, wins/ties/losses 0/3/2
- verdict: candidate fails or misses 25 seed(s) the baseline solves; 2 worsening seed(s) exceed the budget of 1
- repair attempts: 2 (rejected -> rejected)
- pruned early: 2 worsening seed(s) exceed the budget of 1
- agent summary tail: ce gate's single-shot per-seed comparison carries real inherent noise (tens of thousands of points) independent of code correctness — that variance may still occasionally register as a "worsening seed" even for a logically-safe change. The fix minimizes structural risk to what's achievable within CutBuilder's scope; it does not address the underlying timing sensitivity, which is out of scope here.

## 2026-07-07 14:44 g1 GateReuseBuilder -> rejected
- mean effective delta -11837.6, confidence 0.0, wins/ties/losses 0/3/2
- verdict: candidate fails or misses 25 seed(s) the baseline solves; 2 worsening seed(s) exceed the budget of 1
- repair attempts: 2 (duplicate -> rejected)
- pruned early: 2 worsening seed(s) exceed the budget of 1
- agent summary tail: Not essential — I'll just wait for the background task notification.

## 2026-07-08 05:32 g1 ParameterTuner -> rejected
- mean effective delta -2060.342857142857, confidence 0.105, wins/ties/losses 1/31/3
- verdict: candidate fails or misses 25 seed(s) the baseline solves; 3 worsening seed(s) exceed the budget of 1
- repair attempts: 3 (rejected -> duplicate -> rejected)
- pruned early: 3 worsening seed(s) exceed the budget of 1
- agent summary tail: ously-tried source in `experiments/sources/` that this candidate is not a duplicate. **Risk:** Low — every phase result is still gated by exact BFS `evaluate()`/`best_t` tracking, so output is monotonically non-decreasing regardless of schedule; only the search trajectory changes. Timing margin is preserved (max observed 1.893s, well under the 2.0s hard limit, same 1.88 final cutoff as baseline).

## 2026-07-13 04:39 g1 StateDesignBuilder -> accepted
- mean effective delta 2714691.8666666667, confidence 1.0, wins/ties/losses 22/7/1
- verdict: mean effective delta 2714691.8666666667 is positive with 22 win(s), 1 loss(es)

## 2026-07-13 05:04 g2 StateDesignBuilder -> accepted
- mean effective delta 995046.6333333333, confidence 1.0, wins/ties/losses 13/17/0
- verdict: mean effective delta 995046.6333333333 is positive with 13 win(s), 0 loss(es)
- repair attempts: 2 (rejected -> accepted)
- agent summary tail: e exactly (T=13662 and T=14492, matching baseline precisely) - Seeds 1, 3, 4 (previous wins) are unchanged - New wins recovered on seeds 5, 6, 15, 21, 24 (each +2.6M to +4.8M) that were pruned/unseen in the failed gate run - Max elapsed time 1.889s, matching baseline, well under the 2.0s budget - Clean build with `-Wall -Wextra`, no warnings No regressions remain across any locally testable seed.

## 2026-07-13 09:06 g1 StateDesignBuilder -> accepted
- mean effective delta 856352.6, confidence 1.0, wins/ties/losses 26/4/0
- verdict: mean effective delta 856352.6 is positive with 26 win(s), 0 loss(es)
- repair attempts: 2 (rejected -> accepted)
- agent summary tail: 4 ties, 0 losses**, worst-case delta 0 (well under the 150,000 budget and 1-seed limit). - Max elapsed time across all seeds: 1.900s, safely under the 2.0s gate limit (unchanged from before — the extra variants are cheap deterministic BFS/DFS, not RNG-timer-bound). The candidate is now a strict improvement over the accepted mainline with zero worsening seeds. File changed: `solver/main.cpp` only.

## 2026-07-13 09:31 g2 StateDesignBuilder -> accepted
- mean effective delta 391476.73333333334, confidence 1.0, wins/ties/losses 17/13/0
- verdict: mean effective delta 391476.73333333334 is positive with 17 win(s), 0 loss(es)
- agent summary tail: ovably optimal given its pocket pool); closing that gap further would need a different pocket-composition strategy (e.g., directional shell growth away from start rather than whole-graph-distance balls, or multiple independent chains) — noted in knowledge for the next iteration rather than attempted here, since further cut_cap/seed_cap sweeps showed no more low-risk gains available in this design.

## 2026-07-14 00:38 g1 StateDesignBuilder -> accepted
- mean effective delta 369678.0333333333, confidence 1.0, wins/ties/losses 26/4/0
- verdict: mean effective delta 369678.0333333333 is positive with 26 win(s), 0 loss(es)
- agent summary tail: I already have a background wait job (bkehp2esg) that will send a notification once the 100-seed comparison completes. I'll pause here until that notification arrives.

## 2026-07-14 01:14 g2 StateDesignBuilder -> error
- verdict: unrecoverable adapter failure (rate_limit): You've hit your session limit · resets 3am (UTC)
- repair attempts: 3 (rejected -> rejected -> error)
- agent summary tail: You've hit your session limit · resets 3am (UTC)
