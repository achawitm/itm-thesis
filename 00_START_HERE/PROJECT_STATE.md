# PROJECT_STATE

Active directives. Parsed by `tools/brain_check.py` (lines of the form `key: value` inside the block below).
Only the Human PI edits this file.

```yaml
mode_default: READ
ethics_status: NOT_VERIFIED        # VERIFIED | NOT_VERIFIED
ethics_valid_until:                # ISO date; blank = no expiry recorded
protocol_version:
participant_data_lock: LOCKED      # LOCKED | UNLOCKED
```

Participant-data work is blocked unless `ethics_status: VERIFIED`, `ethics_valid_until` has not passed,
and `participant_data_lock: UNLOCKED`. Either lock blocking is enough to block.
