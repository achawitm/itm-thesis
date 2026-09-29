# Tommy_BRAIN operating rules for AI sessions

- Start in READ mode. Read `00_START_HERE/PROJECT_STATE.md` and `00_START_HERE/STATE_DIGEST.md` (regenerate with `python3 00_START_HERE/tools/brain_digest.py`). Never infer state from logs, drafts or handoffs.
- Never hand-edit a `*_REGISTRY.csv` state column or `STATE_TRANSITIONS.csv`. Change state only with `00_START_HERE/tools/brain_transition.py`. Pass `--approved-by PI` only after the Human PI has explicitly said so in this session.
- Never edit `PROJECT_STATE.md` (PI only) or a file whose registry state is LOCKED / chapter phase is TH_LOCKED or later. Use TH_REVISION.
- Every claim needs a label: VERIFIED (with locator `p:`, `sec:`, `tbl:`, `fig:`, `para:`), REPORTED, or NOT_VERIFIED.
- Run `python3 00_START_HERE/tools/brain_check.py` before finishing; a commit that adds transition rows needs a `Transition: <TR-id>` trailer.
- One-time per clone: `git config core.hooksPath .githooks`.
- Writes are machine-enforced by the `brain_guard.py` PreToolUse hook. You may write on your own in `01_SOURCES/`, `02_EVIDENCE/` (not `SYNTHESIS.md`), `03_WRITING/TH/`, `04_TEMPLATES/` and `00_START_HERE/handoffs/`, and append **proposed** rows (work items `PROPOSED`, artifacts `DRAFT`, sources `REGISTERED`, evidence) to the registries with the Write/Edit tool; existing rows are read-only. Anywhere else, or if a call is blocked, stop and ask the Human PI for a grant (`brain_grant.py grant --scope ...`); never look for a workaround. `tools/`, `schemas/`, `.github/`, `PROJECT_STATE.md` (ethics), locked files, participant data and PI-only transitions are never yours. Use one plain command per Bash call (no `$(...)`, heredocs, newlines or `cd`).
