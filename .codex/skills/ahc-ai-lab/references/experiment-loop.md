# Experiment Loop

Use three evaluation levels:

- `smoke`: 3-10 seeds for validity and crashes;
- `small`: 30-100 seeds for quick signal;
- `large`: 300+ seeds for adoption confidence.

For every trial, save:

- baseline run id;
- candidate run id;
- source hashes (recorded automatically on each run; restore with `python tools/ahc.py source --run ID --checkout`);
- agent and hypothesis;
- changed files;
- mean and median effective delta;
- wins, losses, and worst worsening seeds;
- elapsed time changes;
- final decision.

Stop or change strategy when:

- several trials improve smoke but fail large;
- all changes are performance-neutral but search quality is weak;
- bad seeds share a visible structure;
- a specialist has repeated rejected candidates.

