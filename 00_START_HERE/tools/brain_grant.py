#!/usr/bin/env python3
"""Human-PI-only: open or close the AI's WRITE window.

  brain_grant.py grant --scope '03_WRITING/TH/**' --minutes 120 [--transitions]
  brain_grant.py revoke
  brain_grant.py status

The grant lives in .brain/grant.json (untracked). The guard blocks the AI from touching it
or running this tool, so run it yourself (in Claude Code: `! python3 ... brain_grant.py ...`).
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_guard  # noqa: E402
import brain_lib as lib  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(lib.DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant")
    g.add_argument("--scope", action="append", required=True, help="repo-relative glob; repeatable")
    g.add_argument("--minutes", type=int, default=120)
    g.add_argument("--transitions", action="store_true", help="also allow brain_transition.py (incl. --approved-by PI)")
    g.add_argument("--note", default="")
    sub.add_parser("revoke")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    root = Path(args.root)
    path = root / brain_guard.GRANT_PATH
    cfg = lib.load_json(root, "guard.json")
    now = datetime.now(timezone.utc)

    if args.cmd == "grant":
        if not 1 <= args.minutes <= cfg["max_grant_minutes"]:
            print(f"--minutes must be 1..{cfg['max_grant_minutes']}", file=sys.stderr)
            return 1
        for s in args.scope:
            if s.startswith("/") or ".." in Path(s).parts:
                print(f"scope {s!r} must be a repo-relative glob without '..'", file=sys.stderr)
                return 1
        grant = {"granted_by": "PI", "granted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "expires": (now + timedelta(minutes=args.minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "scopes": args.scope, "transitions": args.transitions, "note": args.note}
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(grant, indent=2) + "\n", encoding="utf-8")
        with open(path.parent / "grants.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(grant) + "\n")
        print(f"WRITE granted until {grant['expires']} for {args.scope}"
              + (" (+transitions)" if args.transitions else ""))
        print("Paths in guard.json deny_write and LOCKED files stay protected regardless.")
    elif args.cmd == "revoke":
        path.unlink(missing_ok=True)
        print("READ mode restored.")
    else:
        g = brain_guard.load_grant(root, now)
        print("READ mode (no valid grant)" if g is None else
              f"WRITE until {g['expires']} scopes={g['scopes']} transitions={g['transitions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
