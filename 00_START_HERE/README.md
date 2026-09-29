# Tommy_BRAIN v3.0: control layer

Python 3.9+, standard library only.

## Tools (`tools/`)
| Tool | Purpose |
|---|---|
| `brain_check.py` | Validates schemas, foreign keys, evidence labels, lock hashes, translation gate, ethics guard, and the hash-chained audit log. Exit 1 on errors. |
| `brain_transition.py` | The only sanctioned way to change state. Validates, updates the registry, appends to `STATE_TRANSITIONS.csv`, re-checks, rolls back on failure. |
| `brain_digest.py` | Writes `STATE_DIGEST.md`, a compact read-only view for session start. |
| `brain_guard.py` | Claude Code `PreToolUse` hook (configured in `.claude/settings.json`). Enforces READ mode and PI-granted WRITE. |
| `brain_grant.py` | Human-PI-only: `grant`, `revoke`, `status` for the AI's WRITE window. |

Tests: `python3 -m unittest discover -s 00_START_HERE/tests`

## Enable hooks (once per clone)
`git config core.hooksPath .githooks`. `pre-commit` runs `brain_check.py`; `commit-msg` requires a `Transition:` trailer when log rows are added. Hooks can be bypassed, so CI (`.github/workflows/brain-check.yml`) is the real gate: mark it a required check on `main`.

## READ/WRITE enforcement (`brain_guard.py`)
- **READ is the default.** With no valid `.brain/grant.json`, Write/Edit/NotebookEdit inside the repo and every Bash command that modifies files or the repository are blocked. Reads, `brain_check.py`, `brain_digest.py` and `unittest` still work. Paths outside the repo (scratchpad, `/tmp`) are unaffected.
- **The PI opens WRITE** in their own terminal (in Claude Code, prefix with `!` so it bypasses the hook):
  `python3 00_START_HERE/tools/brain_grant.py grant --scope '03_WRITING/TH/**' --minutes 120 [--transitions]`.
  Grants are scoped to globs, expire (max 480 min), and are logged to `.brain/grants.log`. `--transitions` additionally allows `brain_transition.py`, including `--approved-by PI`. `revoke` and `status` also exist.
- **Never writable by the AI, even with a grant** (`guard.json` `deny_write`): `.brain/`, `.claude/`, `.githooks/`, `.github/`, `PROJECT_STATE.md`, `tools/`, `schemas/`, `STATE_TRANSITIONS.csv`, `CHAPTER_REGISTRY.csv`. Also never writable: files whose registry state is LOCKED (or a chapter at TH_LOCKED or later), and participant-data folders while the ethics guard blocks.
- **Bash is allowlist-based and fails closed.** Command substitution, heredocs, multi-line commands, `python -c`, `bash -c`, unknown commands, `git config`, `--no-verify`, force-push and `reset --hard` are refused; every segment of a `&&`/`|` chain and every redirect target is checked. `--actor PI` is refused, so the AI must record itself as `AI:<session>`. Extend the allowlist in `guard.json` (`bash_extra_allow`).
- Any internal hook error blocks the call (exit 2).
- **Limits:** this is a guardrail against mistakes and prompt-level drift, not a sandbox. A Bash allowlist can't prove arbitrary programs harmless (that's why unknown programs are refused), and anything the PI runs themselves is trusted. `WORK_ITEM_REGISTRY.csv` and `ARTIFACT_REGISTRY.csv` are writable with a scope so the AI can add PROPOSED rows; hand-edited states are caught by `brain_check` (pre-commit and CI).

## Defaults (edit in `schemas/`)
- IDs: `CH-01`, `WI-0001`, `ART-0001`, `SRC-0001`, `EV-00001`, `TR-<UTC timestamp>-<4 hex>`.
- Chapter phases: `TH_DRAFT → TH_REVIEW → TH_LOCKED → EN_TRANSLATE → EN_REVIEW → EN_LOCKED`, plus `TH_LOCKED → TH_REVISION → TH_REVIEW` (unlock) and `EN_TRANSLATE → TH_LOCKED` (abort English).
- Work items: `PROPOSED → ACTIVE ⇄ BLOCKED → DONE | CANCELLED`. Artifacts: `DRAFT → APPROVED → LOCKED → SUPERSEDED`.
- PI approval required for: locking, unlocking, entering EN_TRANSLATE/EN_LOCKED, activating or completing work items, approving/locking/superseding artifacts.
- Actors: `PI` or `AI:<session>`. Approval is recorded in `approved_by`.
- Locators for VERIFIED: `p:12`, `pp:12-14`, `sec:3.2`, `tbl:4`, `fig:2`, `para:5`.
- Translation gate: CH-01..03 must each be at TH_LOCKED or later with a locked revision. English chapter files or EN_* phases while it is closed are errors; `03_WRITING/EN/_prep/` (glossary, terminology) is exempt. Unlocking a gate chapter while English work exists is refused.
- Ethics: blocked unless `ethics_status: VERIFIED`, not expired, and `participant_data_lock: UNLOCKED`. Blocks ACTIVE participant-data work items, PARTICIPANT_DATA artifacts, and any file under `03_WRITING/instruments/participant_data/`.
- Synthesis: every bullet in `SYNTHESIS.md` must cite an existing `EV-xxxxx`.
- CSV: UTF-8 without BOM, `\n` line endings, ISO dates; do not edit in Excel.

## Known limits
- Hooks and the hash chain make tampering detectable, not impossible; branch protection and CODEOWNERS (`.github/CODEOWNERS`, set the PI's handle) are what stop it.
- The AI's READ/WRITE mode is not machine-enforced yet; see the optional Claude Code `PreToolUse` hook idea in the review.
