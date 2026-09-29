#!/usr/bin/env python3
"""Validate Tommy_BRAIN registries, gates and the transition audit log.

Exit code 0 = clean, 1 = errors found. Warnings never fail the run.
Usage: brain_check.py [--root PATH]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brain_lib as lib  # noqa: E402


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, rule, where, msg):
        self.errors.append(f"[{rule}] {where}: {msg}")

    def warn(self, rule, where, msg):
        self.warnings.append(f"[{rule}] {where}: {msg}")


def load_registries(root, schemas, rep):
    """Read every registry and check structure. Returns (data, ok)."""
    data, ok = {}, True
    for name, spec in schemas.items():
        header, rows, problems = lib.read_csv(Path(root) / spec["path"])
        for p in problems:
            rep.error("SCHEMA", spec["path"], p)
            ok = False
        if header is not None and header != spec["columns"]:
            rep.error("SCHEMA", spec["path"], f"header must be {spec['columns']}, found {header}")
            ok = False
        data[name] = rows
        seen = set()
        for row in rows:
            where = f"{spec['path']}:{row['_line']}"
            rid = row.get(spec["id"], "")
            if not re.match(spec["id_pattern"], rid):
                rep.error("SCHEMA", where, f"{spec['id']} {rid!r} does not match {spec['id_pattern']}")
            if rid in seen:
                rep.error("SCHEMA", where, f"duplicate {spec['id']} {rid}")
            seen.add(rid)
            for col in spec["required"]:
                if not row.get(col, "").strip():
                    rep.error("SCHEMA", where, f"{col} is required")
            for col, allowed in spec["enums"].items():
                if row.get(col, "") not in allowed:
                    rep.error("SCHEMA", where, f"{col} {row.get(col)!r} not in {allowed}")
    return data, ok and not rep.errors


def check_evidence(root, data, workflow, rep):
    sources = {s["source_id"]: s for s in data["sources"]}
    chapters = {c["chapter_id"] for c in data["chapters"]}
    locator = re.compile(workflow["locator_pattern"], re.I)
    ev_by_id = {e["evidence_id"]: e for e in data["evidence"]}
    seen_keys = set()
    for s in data["sources"]:
        where = f"SOURCE_REGISTRY:{s['_line']}"
        if s["citation_key"] in seen_keys:
            rep.error("SOURCE", where, f"duplicate citation_key {s['citation_key']}")
        seen_keys.add(s["citation_key"])
        if s["year"] and not re.match(r"^\d{4}$", s["year"]):
            rep.error("SOURCE", where, f"year {s['year']!r} is not 4 digits")
    for e in data["evidence"]:
        where = f"EVIDENCE_TABLE:{e['_line']}"
        src = sources.get(e["source_id"])
        if src is None:
            rep.error("FK", where, f"source_id {e['source_id']} not in SOURCE_REGISTRY")
        if e["chapter_id"] and e["chapter_id"] not in chapters:
            rep.error("FK", where, f"chapter_id {e['chapter_id']} not in CHAPTER_REGISTRY")
        if e["label"] == "VERIFIED":
            if not locator.match(e["locator"]):
                rep.error("EVIDENCE", where, "VERIFIED requires a locator like 'p:12', 'sec:3.2', 'tbl:4', 'fig:2', 'para:5'")
            if src and src["status"] in ("SUPERSEDED", "RETRACTED"):
                rep.error("EVIDENCE", where, f"VERIFIED claim rests on {src['status']} source {src['source_id']}")
        elif e["label"] == "REPORTED" and not e["reported_by"].strip():
            rep.warn("EVIDENCE", where, "REPORTED claim has no reported_by")
    synth = Path(root) / "02_EVIDENCE/SYNTHESIS.md"
    if synth.exists():
        for n, line in enumerate(synth.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.lstrip().startswith(("- ", "* ")):
                continue
            ids = re.findall(r"EV-\d{5}", line)
            where = f"SYNTHESIS.md:{n}"
            if not ids:
                rep.error("SYNTHESIS", where, "claim bullet cites no evidence ID")
            for i in ids:
                if i not in ev_by_id:
                    rep.error("SYNTHESIS", where, f"{i} not in EVIDENCE_TABLE")
                elif ev_by_id[i]["label"] == "NOT_VERIFIED":
                    rep.warn("SYNTHESIS", where, f"{i} is NOT_VERIFIED")


def check_references(root, data, rep):
    chapters = {c["chapter_id"] for c in data["chapters"]}
    for w in data["work_items"]:
        if w["chapter_id"] and w["chapter_id"] not in chapters:
            rep.error("FK", f"WORK_ITEM_REGISTRY:{w['_line']}", f"chapter_id {w['chapter_id']} not in CHAPTER_REGISTRY")
    registered = set()
    for c in data["chapters"]:
        registered.update(p for p in (c["th_path"], c["en_path"]) if p)
    for a in data["artifacts"]:
        where = f"ARTIFACT_REGISTRY:{a['_line']}"
        registered.add(a["path"])
        if a["chapter_id"] and a["chapter_id"] not in chapters:
            rep.error("FK", where, f"chapter_id {a['chapter_id']} not in CHAPTER_REGISTRY")
        p = Path(root) / a["path"]
        if not p.is_file():
            rep.error("ARTIFACT", where, f"path {a['path']} does not exist")
        elif a["state"] == "LOCKED":
            if not a["sha256"]:
                rep.error("ARTIFACT", where, "LOCKED artifact has no sha256")
            elif lib.sha256_file(p) != a["sha256"]:
                rep.error("LOCK", where, f"{a['path']} changed after locking (sha256 mismatch)")
    for sub in ("TH", "EN"):
        for f in lib.real_files(Path(root) / "03_WRITING" / sub):
            rel = f.relative_to(root).as_posix()
            if rel.startswith("03_WRITING/EN/_prep/"):
                continue
            if rel not in registered:
                rep.error("ORPHAN", rel, "file is not registered in CHAPTER_REGISTRY or ARTIFACT_REGISTRY")


def check_chapters(root, data, workflow, rep):
    gate = workflow["translation_gate"]
    for c in data["chapters"]:
        if c["phase"] in gate["locked_phases"]:
            where = f"CHAPTER_REGISTRY:{c['_line']}"
            p = Path(root) / c["th_path"]
            if not c["locked_revision"] or not c["locked_sha256"]:
                rep.error("LOCK", where, f"{c['chapter_id']} is {c['phase']} but has no locked revision/sha256")
            elif not p.is_file():
                rep.error("LOCK", where, f"{c['th_path']} does not exist")
            elif lib.sha256_file(p) != c["locked_sha256"]:
                rep.error("LOCK", where, f"{c['th_path']} changed after locking (sha256 mismatch); use TH_REVISION")
    ok, reasons = lib.gate_status(data["chapters"], workflow)
    en_rows = [c for c in data["chapters"] if c["phase"] in gate["en_phases"]]
    en_files = [f for f in lib.real_files(Path(root) / gate["en_dir"])
                if not f.relative_to(root).as_posix().startswith(gate["en_exempt_dir"] + "/")]
    if (en_rows or en_files) and not ok:
        what = [c["chapter_id"] for c in en_rows] + [f.relative_to(root).as_posix() for f in en_files]
        rep.error("GATE", "translation", f"English work present ({', '.join(what)}) but gate is closed: {'; '.join(reasons)}")


def check_ethics(root, data, workflow, rep):
    reasons = lib.ethics_block_reasons(lib.parse_project_state(root))
    if not reasons:
        return
    why = "; ".join(reasons)
    for w in data["work_items"]:
        if w["type"] == "PARTICIPANT_DATA" and w["state"] == "ACTIVE":
            rep.error("ETHICS", f"WORK_ITEM_REGISTRY:{w['_line']}", f"participant-data work item is ACTIVE while blocked ({why})")
    for a in data["artifacts"]:
        if a["kind"] == "PARTICIPANT_DATA":
            rep.error("ETHICS", f"ARTIFACT_REGISTRY:{a['_line']}", f"participant-data artifact registered while blocked ({why})")
    for d in workflow["ethics"]["participant_data_dirs"]:
        for f in lib.real_files(Path(root) / d):
            rep.error("ETHICS", f.relative_to(root).as_posix(), f"participant-data file present while blocked ({why})")


def check_transitions(data, schemas, workflow, rep):
    entities = workflow["entities"]
    actor_re = re.compile(workflow["actor_pattern"])
    registries = {t: {r[schemas[e["registry"]]["id"]]: r for r in data[e["registry"]]} for t, e in entities.items()}
    last, prev = {}, lib.GENESIS
    for row in data["transitions"]:
        where = f"STATE_TRANSITIONS:{row['_line']}"
        if row["prev_hash"] != prev:
            rep.error("AUDIT", where, "prev_hash does not match previous row (log edited or reordered)")
        if row["row_hash"] != lib.transition_hash(row):
            rep.error("AUDIT", where, "row_hash does not match row contents (row edited)")
        prev = row["row_hash"]
        if not actor_re.match(row["actor"]):
            rep.error("AUDIT", where, f"actor {row['actor']!r} must be PI or AI:<session>")
        spec = entities.get(row["entity_type"])
        if spec is None:
            continue
        key = (row["entity_type"], row["entity_id"])
        if row["entity_id"] not in registries[row["entity_type"]]:
            rep.error("AUDIT", where, f"{row['entity_type']} {row['entity_id']} is not in its registry")
        cur = last.get(key, spec["initial"])
        if row["from_state"] != cur:
            rep.error("AUDIT", where, f"from_state {row['from_state']} but {row['entity_id']} was {cur}")
        if row["to_state"] not in spec["transitions"].get(cur, []):
            rep.error("AUDIT", where, f"illegal transition {cur} -> {row['to_state']} for {row['entity_type']}")
        if lib.transition_is_pi_required(spec["pi_required"], cur, row["to_state"]) and "PI" not in (row["approved_by"], row["actor"]):
            rep.error("AUDIT", where, f"{cur} -> {row['to_state']} requires approved_by=PI")
        last[key] = row["to_state"]
    for etype, spec in entities.items():
        for eid, r in registries[etype].items():
            expected = last.get((etype, eid), spec["initial"])
            if r[spec["state_column"]] != expected:
                rep.error("STATE", f"{etype} {eid}", f"registry says {r[spec['state_column']]} but transition log says {expected}")


def run_checks(root):
    root = Path(root)
    schemas, workflow = lib.load_schemas(root)
    rep = Report()
    data, ok = load_registries(root, schemas, rep)
    if not ok:
        return rep
    check_evidence(root, data, workflow, rep)
    check_references(root, data, rep)
    check_chapters(root, data, workflow, rep)
    check_ethics(root, data, workflow, rep)
    check_transitions(data, schemas, workflow, rep)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(lib.DEFAULT_ROOT))
    args = ap.parse_args(argv)
    rep = run_checks(args.root)
    for w in rep.warnings:
        print("WARN ", w)
    for e in rep.errors:
        print("ERROR", e)
    print(f"brain_check: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
