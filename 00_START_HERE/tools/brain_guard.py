#!/usr/bin/env python3
"""Claude Code PreToolUse hook: three-tier write policy for AI sessions.

1. Autonomous areas (guard.json `autonomous_write`): the AI may write without a grant.
   Registries listed in `append_only` accept new proposal rows only (existing rows are read-only).
2. Everything else in the repo needs a PI grant (.brain/grant.json, made with brain_grant.py).
3. `deny_write` paths, LOCKED files and blocked participant data are never writable by the AI.

Reads of participant data are also refused while the ethics guard is blocking.
Reads the hook event (JSON) on stdin. Exit 0 allows; exit 2 blocks and tells the model why.
Any internal error blocks too (fail closed). See 00_START_HERE/README.md.
"""
import csv
import io
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_lib as lib  # noqa: E402

GRANT_PATH = ".brain/grant.json"
HOW = ("Ask the Human PI to run, in their own terminal (in Claude Code, prefix with `!`): "
       "python3 00_START_HERE/tools/brain_grant.py grant --scope '<glob>' --minutes 120")
SEPARATORS = set(";|&()")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def glob_re(pat):
    if pat.endswith("/**"):
        return re.compile("^" + glob_re(pat[:-3]).pattern[1:-1] + "(?:/.*)?$")
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(rel, patterns):
    return any(glob_re(p).match(rel) for p in patterns)


def load_grant(root, now):
    p = Path(root) / GRANT_PATH
    if not p.is_file():
        return None
    try:
        g = json.loads(p.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(g["expires"].replace("Z", "+00:00"))
        if g.get("granted_by") != "PI" or exp <= now or not g.get("scopes"):
            return None
        return g
    except (ValueError, KeyError, TypeError):
        return None


def locked_paths(root):
    schemas, workflow = lib.load_schemas(root)
    locked = set()
    for name in ("chapters", "artifacts"):
        header, rows, problems = lib.read_csv(Path(root) / schemas[name]["path"])
        if header != schemas[name]["columns"] or problems:
            raise ValueError(f"{schemas[name]['path']} is missing or malformed")
        for r in rows:
            if name == "chapters" and r["phase"] in workflow["translation_gate"]["locked_phases"]:
                locked.add(r["th_path"])
            if name == "artifacts" and r["state"] == "LOCKED":
                locked.add(r["path"])
    return locked, workflow


def ethics_reasons(ctx):
    return lib.ethics_block_reasons(lib.parse_project_state(ctx["root"]), ctx["now"].date())


def participant_globs(ctx):
    _, workflow = lib.load_schemas(ctx["root"])
    return [d + "/**" for d in workflow["ethics"]["participant_data_dirs"]]


def rel_to_root(path_str, cwd, root):
    try:
        return (Path(cwd) / path_str).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def parse_csv(text):
    return [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]


def append_check(rel, old_text, new_text, ctx):
    """New rows only, in their initial/proposed state; existing rows untouched."""
    spec = ctx["cfg"]["append_only"][rel]
    schemas, _ = lib.load_schemas(ctx["root"])
    cols = schemas[spec["registry"]]["columns"]
    if new_text.startswith("﻿"):
        return False, f"{rel}: save as UTF-8 without BOM"
    new, old = parse_csv(new_text), parse_csv(old_text)
    if not new or new[0] != cols:
        return False, f"{rel}: header must stay {cols}"
    old_rows = old[1:]
    if new[1:len(old_rows) + 1] != old_rows:
        return False, (f"{rel} is append-only for the AI: existing rows must not change or move "
                       f"(state changes go through brain_transition.py; other edits need a PI grant)")
    blocked = ethics_reasons(ctx)
    for row in new[len(old_rows) + 1:]:
        if len(row) != len(cols):
            return False, f"{rel}: new row has {len(row)} columns, expected {len(cols)}"
        d = dict(zip(cols, row))
        for key, want in spec["require"].items():
            if d[key] != want:
                return False, f"{rel}: new rows must be proposals with {key}={want!r}, not {d[key]!r}"
        if blocked and (d.get("kind") == "PARTICIPANT_DATA" or (d.get("path") and matches(d["path"], participant_globs(ctx)))):
            return False, f"{rel}: participant-data entries are blocked ({'; '.join(blocked)})"
    return True, ""


def proposed_text(tool, ti, current):
    if tool == "Write":
        return ti.get("content", "")
    edits = [ti] if tool == "Edit" else ti.get("edits", [])
    text = current
    for e in edits:
        old, new = e["old_string"], e["new_string"]
        if old not in text:
            raise ValueError("edit does not apply to the current file")
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def write_ok(path_str, cwd, ctx, tool=None, ti=None):
    """(allowed, reason) for writing one path. tool/ti enable append-only verification."""
    root, cfg, grant = ctx["root"], ctx["cfg"], ctx["grant"]
    rel = rel_to_root(path_str, cwd, root)
    if rel is None:
        return True, ""  # outside the repository (scratchpad, /dev/null, /tmp)
    if matches(rel, cfg["deny_write"]):
        return False, f"{rel} is PI-only governance state (guard.json deny_write); registry state changes go through brain_transition.py"
    try:
        locked, workflow = locked_paths(root)
    except Exception as exc:  # unreadable registries: fail closed
        return False, f"cannot read registries to check locks ({exc}); the Human PI must repair them"
    if rel in locked:
        return False, f"{rel} is LOCKED; change it via a TH_REVISION/unlock transition, not an edit"
    reasons = ethics_reasons(ctx)
    if reasons and matches(rel, participant_globs(ctx)):
        return False, f"{rel} is participant data and the ethics guard is blocking ({'; '.join(reasons)})"
    if grant is not None and matches(rel, grant["scopes"]):
        return True, ""
    if matches(rel, cfg["autonomous_write"]) and not matches(rel, cfg["autonomous_exclude"]):
        if rel not in cfg["append_only"]:
            return True, ""
        if tool not in ("Write", "Edit", "MultiEdit"):
            return False, f"{rel} is append-only for the AI: use the Write/Edit tool so the appended rows can be verified"
        p = root / rel
        try:
            current = p.read_text(encoding="utf-8") if p.exists() else ""
            new_text = proposed_text(tool, ti, current)
        except (ValueError, KeyError, OSError) as exc:
            return False, f"{rel}: cannot verify the edit ({exc})"
        return append_check(rel, current, new_text, ctx)
    return False, (f"{rel} is outside the autonomous write areas {cfg['autonomous_write']} "
                   f"and the current grant. {HOW}")


def read_ok(path_str, cwd, ctx):
    if not path_str:
        return True, ""
    rel = rel_to_root(path_str, cwd, ctx["root"])
    if rel is None:
        return True, ""
    reasons = ethics_reasons(ctx)
    if reasons and matches(rel, participant_globs(ctx)):
        return False, f"participant data is locked ({'; '.join(reasons)}); the PI must change PROJECT_STATE.md first"
    return True, ""


def is_op(tok):
    return tok and all(c in ";|&()<>" for c in tok)


def split_bash(cmd):
    """Return (segments, redirect_targets) or raise ValueError."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    toks = list(lex)
    segs, cur, targets, i = [], [], [], 0
    while i < len(toks):
        t = toks[i]
        if is_op(t) and ("<" in t or ">" in t):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if ">" in t and not (nxt == "/dev/null" or (t.endswith("&") and nxt.isdigit())):
                targets.append(nxt)
            i += 2
            continue
        if is_op(t) and set(t) <= SEPARATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
        i += 1
    if cur:
        segs.append(cur)
    return segs, targets


def bash_ok(cmd, cwd, ctx):
    cfg, grant = ctx["cfg"], ctx["grant"]
    if re.search(r"\.brain\b|brain_grant|brain_guard|guard\.json|\.claude\b|core\.hooksPath|--no-verify", cmd):
        return False, "commands touching the grant, the guard, Claude settings or hook bypasses are never allowed"
    if any(x in cmd for x in ("$(", "`", "<(", ">(", "<<", "\n")):
        return False, "command substitution, heredocs and multi-line commands cannot be inspected; use one plain command"
    try:
        segs, targets = split_bash(cmd)
    except ValueError as exc:
        return False, f"cannot parse command ({exc})"
    for word in [w for s in segs for w in s[1:] if not w.startswith("-")] + targets:
        ok, why = read_ok(word, cwd, ctx)
        if not ok:
            return False, why
    for t in targets:
        ok, why = write_ok(t, cwd, ctx)
        if not ok:
            return False, f"redirect to {t}: {why}"
    for words in segs:
        name, args = words[0], words[1:]
        nonflag = [a for a in args if not a.startswith("-")]
        if re.match(r"^\w+=", name):
            return False, "environment-variable prefixes are not allowed"
        if name in cfg["bash_readonly"]:
            if name == "find" and any(a in cfg["find_forbidden_flags"] for a in args):
                return False, "find with -delete/-exec/-fprint is not read-only"
        elif name in cfg["bash_write_cmds"]:
            if name == "dd":
                paths = [a.split("=", 1)[1] for a in args if a.startswith("of=")]
            else:
                paths = nonflag[-1:] if name in cfg["bash_last_arg_only"] else nonflag
            for a in paths:
                ok, why = write_ok(a, cwd, ctx)
                if not ok:
                    return False, f"{name} {a}: {why}"
        elif name == "git":
            ok, why = git_ok(args, cwd, ctx)
            if not ok:
                return False, why
        elif name in ("python3", "python"):
            ok, why = python_ok(args, cwd, ctx)
            if not ok:
                return False, why
        elif name in cfg["bash_extra_allow"] and grant is not None:
            continue
        else:
            return False, f"`{name}` is not in the guard allowlist (guard.json bash_readonly / bash_write_cmds); the PI can extend it"
    return True, ""


def git_ok(args, cwd, ctx):
    cfg, grant = ctx["cfg"], ctx["grant"]
    if not args or args[0].startswith("-"):
        return False, "git global options (-C, -c, ...) are not allowed"
    sub, rest = args[0], args[1:]
    if sub in cfg["git_forbidden"] or any(a in cfg["git_forbidden_flags"] for a in rest):
        return False, f"git {sub} with those flags can destroy work or bypass the hooks"
    if sub == "checkout" and "--" in rest:
        return False, "git checkout -- discards changes"
    if sub in cfg["git_read"] or sub in cfg["git_autonomous"]:
        return True, ""
    if sub in cfg["git_write"]:
        if grant is None:
            return False, f"git {sub} can rewrite the working tree or history and needs a PI grant. {HOW}"
        if sub in cfg["git_path_args"]:
            for a in [a for a in rest if not a.startswith("-")]:
                ok, why = write_ok(a, cwd, ctx)
                if not ok:
                    return False, f"git {sub} {a}: {why}"
        return True, ""
    return False, f"git {sub} is not in the guard allowlist"


def python_ok(args, cwd, ctx):
    cfg, grant, root = ctx["cfg"], ctx["grant"], ctx["root"]
    if args[:2] == ["-m", "unittest"]:
        rest = args[2:]
        if rest[:1] == ["discover"] and "-s" in rest and "-t" not in rest and rest.index("-s") + 1 < len(rest):
            d = (Path(cwd) / rest[rest.index("-s") + 1]).resolve()
            tests = (root / "00_START_HERE/tests").resolve()
            if d == tests or tests in d.parents:
                return True, ""
        return False, "only `python3 -m unittest discover -s 00_START_HERE/tests` is allowed (unittest can run arbitrary code)"
    if not args or args[0].startswith("-"):
        return False, "python -c / inline code cannot be inspected"
    script = (Path(cwd) / args[0]).resolve()
    if script.parent != (root / "00_START_HERE/tools").resolve():
        return False, f"only the brain_* tools under 00_START_HERE/tools may be run, not {args[0]}"
    if script.name in cfg["python_readonly_tools"]:
        return True, ""
    if script.name == cfg["python_transition_tool"]:
        rest = args[1:]
        for i, a in enumerate(rest):
            val = a.split("=", 1)[1] if a.startswith("--actor=") else (rest[i + 1] if a == "--actor" and i + 1 < len(rest) else None)
            if val is not None and not val.startswith("AI:"):
                return False, "the AI must record itself as --actor AI:<session>, never PI"
        claims_approval = any(a == "--approved-by" or a.startswith("--approved-by=") for a in rest)
        if claims_approval and (grant is None or not grant.get("transitions")):
            return False, ("--approved-by asserts the PI's approval and needs a PI grant made with --transitions. "
                           "Without it, brain_transition.py still runs but refuses PI-only transitions (locks, gates). " + HOW)
        return True, ""
    return False, f"{script.name} is not an allowed tool"


def decide(event, root, now=None):
    root = Path(root).resolve()
    now = now or datetime.now(timezone.utc)
    ctx = {"root": root, "now": now, "cfg": lib.load_json(root, "guard.json"), "grant": load_grant(root, now)}
    tool, ti = event.get("tool_name"), event.get("tool_input") or {}
    cwd = event.get("cwd") or str(root)
    if tool in WRITE_TOOLS:
        target = ti.get("file_path") or ti.get("notebook_path")
        if not target:
            return False, "write tool call without a target path"
        return write_ok(target, cwd, ctx, tool, ti)
    if tool == "Read":
        return read_ok(ti.get("file_path"), cwd, ctx)
    if tool in ("Grep", "Glob"):
        return read_ok(ti.get("path"), cwd, ctx)
    if tool == "Bash":
        return bash_ok(ti.get("command", ""), cwd, ctx)
    return True, ""


def main():
    try:
        event = json.load(sys.stdin)
        ok, reason = decide(event, lib.DEFAULT_ROOT)
    except Exception as exc:  # exit 1 would not block, so fail closed with exit 2
        print(f"brain_guard: internal error, blocking: {exc}", file=sys.stderr)
        return 2
    if ok:
        return 0
    print(f"brain_guard: BLOCKED. {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
