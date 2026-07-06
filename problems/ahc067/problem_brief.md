# ahc067 Brief

- State: `(position, switch parity mask)` where `K=10`, so at most `20*20*2^10` BFS states.
- Objective: maximize the hero's shortest action count `T` from `(0,0)` to `(19,19)`.
- Score: `round(1e6 * log2(T / N))` when reachable; unreachable gives only `1`.
- Output: first doors `D`, then `D` lines `d i j g`; then switches `S`, then `S` lines `p q s`.
- Valid output constraints: `D<=50`, no duplicate door edge, at most one switch per cell, door type `0..19`, switch type `0..9`.
- Door behavior: type `2k` starts open and type `2k+1` starts closed; switch `k` toggles both.
- Baseline idea: no doors and no switches, which is always valid because empty cells are connected.
- Likely strategies: force detours with alternating door pairs, place switches on mandatory corridors, and avoid making the goal unreachable.
- Known risks: invalid duplicate edges, closed-door layouts that make the goal unreachable, and expensive scorer BFS inside tuning loops.
