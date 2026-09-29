"""Shared helpers for the Tommy_BRAIN tools. Standard library only."""
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = "00_START_HERE/schemas"
PROJECT_STATE = "00_START_HERE/PROJECT_STATE.md"
HASH_EXCLUDE = ("prev_hash", "row_hash")
GENESIS = "GENESIS"


def load_json(root, name):
    return json.loads((Path(root) / SCHEMA_DIR / name).read_text(encoding="utf-8"))


def load_schemas(root):
    return load_json(root, "registries.json"), load_json(root, "workflow.json")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    """Return (header, rows, problems). Each row is a dict with an extra `_line`."""
    path = Path(path)
    if not path.exists():
        return None, [], ["file is missing"]
    raw = path.read_bytes()
    problems = []
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("file has a UTF-8 BOM; save as UTF-8 without BOM")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, [], problems + ["file is not valid UTF-8"]
    reader = csv.reader(text.splitlines())
    lines = list(reader)
    if not lines:
        return [], [], problems + ["file is empty (header row missing)"]
    header = lines[0]
    rows = []
    for i, cells in enumerate(lines[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(header):
            problems.append(f"line {i}: expected {len(header)} columns, found {len(cells)}")
            continue
        row = dict(zip(header, cells))
        row["_line"] = i
        rows.append(row)
    return header, rows, problems


def write_csv(path, columns, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])


def parse_project_state(root):
    state = {}
    p = Path(root) / PROJECT_STATE
    if not p.exists():
        return state
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([a-z_]+):\s*([^#]*?)\s*(#.*)?$", line)
        if m:
            state[m.group(1)] = m.group(2).strip()
    return state


def ethics_block_reasons(state, today=None):
    """Reasons participant-data work is blocked; an empty list means it is allowed."""
    today = today or date.today()
    reasons = []
    if state.get("ethics_status") != "VERIFIED":
        reasons.append("ethics_status is not VERIFIED")
    valid_until = state.get("ethics_valid_until", "")
    if valid_until:
        try:
            if date.fromisoformat(valid_until) < today:
                reasons.append(f"ethics approval expired on {valid_until}")
        except ValueError:
            reasons.append(f"ethics_valid_until is not an ISO date: {valid_until!r}")
    if state.get("participant_data_lock", "LOCKED") != "UNLOCKED":
        reasons.append("participant_data_lock is not UNLOCKED")
    return reasons


def transition_hash(row):
    parts = [row["prev_hash"]] + [row[c] for c in row if c not in HASH_EXCLUDE and not c.startswith("_")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def transition_is_pi_required(rules, from_state, to_state):
    return f"{from_state}>{to_state}" in rules or f"*>{to_state}" in rules


def gate_status(chapters, workflow):
    """(ok, reasons) for the global translation gate given the chapter rows."""
    gate = workflow["translation_gate"]
    by_id = {c["chapter_id"]: c for c in chapters}
    reasons = []
    for cid in gate["chapters"]:
        row = by_id.get(cid)
        if row is None:
            reasons.append(f"{cid} is not in CHAPTER_REGISTRY")
        elif row["phase"] not in gate["satisfied_phases"]:
            reasons.append(f"{cid} is {row['phase']}, needs TH_LOCKED")
        elif not row["locked_revision"] or not row["locked_sha256"]:
            reasons.append(f"{cid} has no approved locked revision")
    return not reasons, reasons


def real_files(directory):
    """Files under a directory, ignoring .gitkeep."""
    d = Path(directory)
    if not d.exists():
        return []
    return sorted(p for p in d.rglob("*") if p.is_file() and p.name != ".gitkeep")
