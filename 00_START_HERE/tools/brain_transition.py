#!/usr/bin/env python3
"""The one sanctioned way to change state.

Validates the transition, updates the registry row, appends a hash-chained row to
STATE_TRANSITIONS.csv, re-runs brain_check, and rolls everything back if that fails.

Usage:
  brain_transition.py CHAPTER CH-01 TH_REVIEW --actor AI:session1
  brain_transition.py CHAPTER CH-01 TH_LOCKED --actor AI:session1 --approved-by PI
"""
import argparse
import re
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_check  # noqa: E402
import brain_lib as lib  # noqa: E402


def fail(msg):
    print(f"brain_transition: refused: {msg}", file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entity_type", choices=["CHAPTER", "WORK_ITEM", "ARTIFACT"])
    ap.add_argument("entity_id")
    ap.add_argument("to_state")
    ap.add_argument("--actor", required=True, help="PI or AI:<session>")
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--root", default=str(lib.DEFAULT_ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root)

    schemas, workflow = lib.load_schemas(root)
    spec = workflow["entities"][args.entity_type]
    reg = schemas[spec["registry"]]
    tr = schemas["transitions"]
    if not re.match(workflow["actor_pattern"], args.actor):
        return fail(f"actor {args.actor!r} must be PI or AI:<session>")

    pre = brain_check.run_checks(root)
    if pre.errors:
        print("\n".join(pre.errors), file=sys.stderr)
        return fail("repository is not clean; fix brain_check errors before transitioning")

    _, rows, _ = lib.read_csv(root / reg["path"])
    _, trs, _ = lib.read_csv(root / tr["path"])
    row = next((r for r in rows if r[reg["id"]] == args.entity_id), None)
    if row is None:
        return fail(f"{args.entity_type} {args.entity_id} not found")
    col = spec["state_column"]
    cur = row[col]
    if args.to_state not in spec["transitions"].get(cur, []):
        return fail(f"illegal transition {cur} -> {args.to_state}; allowed: {spec['transitions'].get(cur, [])}")
    approved = args.approved_by or ("PI" if args.actor == "PI" else "")
    if lib.transition_is_pi_required(spec["pi_required"], cur, args.to_state) and approved != "PI":
        return fail(f"{cur} -> {args.to_state} requires PI approval (pass --approved-by PI once the PI has said so)")

    gate = workflow["translation_gate"]
    if args.entity_type == "CHAPTER":
        _, chapters, _ = lib.read_csv(root / schemas["chapters"]["path"])
        if args.to_state == "EN_TRANSLATE":
            ok, reasons = lib.gate_status(chapters, workflow)
            if not ok:
                return fail("translation gate closed: " + "; ".join(reasons))
        if (cur == "TH_LOCKED" and args.to_state == "TH_REVISION" and args.entity_id in gate["chapters"]
                and any(c["phase"] in gate["en_phases"] for c in chapters)):
            return fail("unlocking a gate chapter while English work exists; return EN chapters to TH_LOCKED first")
        if args.to_state == "TH_LOCKED" and not (root / row["th_path"]).is_file():
            return fail(f"{row['th_path']} does not exist")
    if args.entity_type == "ARTIFACT" and args.to_state == "LOCKED" and not (root / row["path"]).is_file():
        return fail(f"{row['path']} does not exist")
    if args.entity_type == "WORK_ITEM" and row["type"] == "PARTICIPANT_DATA" and args.to_state == "ACTIVE":
        reasons = lib.ethics_block_reasons(lib.parse_project_state(root))
        if reasons:
            return fail("participant-data work blocked: " + "; ".join(reasons))

    paths = [root / reg["path"], root / tr["path"]]
    backup = {p: p.read_bytes() for p in paths}

    row[col] = args.to_state
    row["updated_at"] = date.today().isoformat()
    if args.entity_type == "CHAPTER" and args.to_state == "TH_LOCKED":
        n = int(row["locked_revision"][1:]) + 1 if re.match(r"^r\d+$", row["locked_revision"]) else 1
        row["locked_revision"] = f"r{n}"
        row["locked_sha256"] = lib.sha256_file(root / row["th_path"])
    if args.entity_type == "ARTIFACT" and args.to_state == "LOCKED":
        row["sha256"] = lib.sha256_file(root / row["path"])

    now = datetime.now(timezone.utc)
    new = {
        "transition_id": f"TR-{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}",
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entity_type": args.entity_type,
        "entity_id": args.entity_id,
        "from_state": cur,
        "to_state": args.to_state,
        "actor": args.actor,
        "approved_by": approved,
        "note": args.note,
        "prev_hash": trs[-1]["row_hash"] if trs else lib.GENESIS,
    }
    new["row_hash"] = lib.transition_hash(new)
    trs.append(new)
    for r in rows:
        r.pop("_line", None)
    lib.write_csv(root / reg["path"], reg["columns"], rows)
    lib.write_csv(root / tr["path"], tr["columns"], trs)

    post = brain_check.run_checks(root)
    if post.errors:
        for p, b in backup.items():
            p.write_bytes(b)
        print("\n".join(post.errors), file=sys.stderr)
        return fail("post-transition check failed; changes rolled back")
    print(f"{new['transition_id']}: {args.entity_type} {args.entity_id} {cur} -> {args.to_state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
