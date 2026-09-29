# Tommy_BRAIN operating rules for AI sessions

- Start in READ mode. Read `00_START_HERE/PROJECT_STATE.md` and `00_START_HERE/STATE_DIGEST.md` (regenerate with `python3 00_START_HERE/tools/brain_digest.py`). Never infer state from logs, drafts or handoffs.
- Never hand-edit a `*_REGISTRY.csv` state column or `STATE_TRANSITIONS.csv`. Change state only with `00_START_HERE/tools/brain_transition.py`. Pass `--approved-by PI` only after the Human PI has explicitly said so in this session.
- Never edit `PROJECT_STATE.md` (PI only) or a file whose registry state is LOCKED / chapter phase is TH_LOCKED or later. Use TH_REVISION.
- Every claim needs a label: VERIFIED (with locator `p:`, `sec:`, `tbl:`, `fig:`, `para:`), REPORTED, or NOT_VERIFIED.
- Run `python3 00_START_HERE/tools/brain_check.py` before finishing; a commit that adds transition rows needs a `Transition: <TR-id>` trailer.
- One-time per clone: `git config core.hooksPath .githooks`.
