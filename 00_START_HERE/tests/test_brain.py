import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "00_START_HERE/tools"))
import brain_check  # noqa: E402
import brain_lib as lib  # noqa: E402
import brain_transition  # noqa: E402

PI_STATE = """```yaml
ethics_status: VERIFIED
ethics_valid_until: 2999-01-01
participant_data_lock: UNLOCKED
```
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("00_START_HERE", "01_SOURCES", "02_EVIDENCE", "03_WRITING"):
            shutil.copytree(REPO / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for ch in ("01", "02", "03"):
            (self.root / f"03_WRITING/TH/ch{ch}.md").write_text(f"chapter {ch}\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        return brain_check.run_checks(self.root).errors

    def has(self, rule):
        return any(e.startswith(f"[{rule}]") for e in self.errors())

    def append(self, rel, line):
        with open(self.root / rel, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def move(self, etype, eid, to, actor="AI:t", approved="PI"):
        args = [etype, eid, to, "--actor", actor, "--root", str(self.root)]
        if approved:
            args += ["--approved-by", approved]
        return brain_transition.main(args)

    def lock_all(self, ids=("CH-01", "CH-02", "CH-03")):
        for cid in ids:
            self.assertEqual(self.move("CHAPTER", cid, "TH_REVIEW", approved=""), 0)
            self.assertEqual(self.move("CHAPTER", cid, "TH_LOCKED"), 0)


class SchemaAndEvidence(Base):
    def test_clean_scaffold_passes(self):
        self.assertEqual(self.errors(), [])

    def test_bad_enum_and_id(self):
        self.append("00_START_HERE/WORK_ITEM_REGISTRY.csv", "WI-1,x,WRITING,,ACTIVE,")
        self.append("00_START_HERE/WORK_ITEM_REGISTRY.csv", "WI-0002,x,WRITING,,WEIRD,")
        errs = "\n".join(self.errors())
        self.assertIn("WI-1", errs)
        self.assertIn("WEIRD", errs)

    def test_verified_needs_locator(self):
        self.append("01_SOURCES/SOURCE_REGISTRY.csv", "SRC-0001,k2020,T,A,2020,,JOURNAL,REGISTERED,")
        self.append("02_EVIDENCE/EVIDENCE_TABLE.csv", "EV-00001,SRC-0001,claim,VERIFIED,,,")
        self.assertTrue(self.has("EVIDENCE"))
        (self.root / "02_EVIDENCE/EVIDENCE_TABLE.csv").write_text(
            "evidence_id,source_id,claim,label,locator,reported_by,chapter_id\n"
            "EV-00001,SRC-0001,claim,VERIFIED,p:12,,\n", encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_verified_on_retracted_source(self):
        self.append("01_SOURCES/SOURCE_REGISTRY.csv", "SRC-0001,k2020,T,A,2020,,JOURNAL,RETRACTED,")
        self.append("02_EVIDENCE/EVIDENCE_TABLE.csv", "EV-00001,SRC-0001,claim,VERIFIED,p:1,,")
        self.assertTrue(self.has("EVIDENCE"))

    def test_synthesis_bullet_needs_evidence(self):
        self.append("02_EVIDENCE/SYNTHESIS.md", "- an uncited claim")
        self.assertTrue(self.has("SYNTHESIS"))

    def test_orphan_chapter_file(self):
        (self.root / "03_WRITING/TH/stray.md").write_text("x", encoding="utf-8")
        self.assertTrue(self.has("ORPHAN"))


class Gates(Base):
    def test_english_file_without_gate(self):
        (self.root / "03_WRITING/EN/ch01.md").write_text("x", encoding="utf-8")
        self.assertTrue(self.has("GATE"))

    def test_prep_dir_is_exempt(self):
        (self.root / "03_WRITING/EN/_prep/glossary.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_transition_flow_and_gate(self):
        self.lock_all(("CH-01", "CH-02"))
        self.assertEqual(self.move("CHAPTER", "CH-01", "EN_TRANSLATE"), 1)  # CH-03 not locked
        self.lock_all(("CH-03",))
        self.assertEqual(self.move("CHAPTER", "CH-01", "EN_TRANSLATE"), 0)
        self.assertEqual(self.errors(), [])

    def test_pi_approval_required(self):
        self.move("CHAPTER", "CH-01", "TH_REVIEW", approved="")
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_LOCKED", approved=""), 1)

    def test_illegal_transition(self):
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_LOCKED"), 1)

    def test_locked_file_edit_is_caught(self):
        self.lock_all(("CH-01",))
        (self.root / "03_WRITING/TH/ch01.md").write_text("edited\n", encoding="utf-8")
        self.assertTrue(self.has("LOCK"))

    def test_unlock_blocked_while_english_exists(self):
        self.lock_all()
        self.move("CHAPTER", "CH-01", "EN_TRANSLATE")
        self.assertEqual(self.move("CHAPTER", "CH-03", "TH_REVISION"), 1)
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_LOCKED"), 0)  # abort EN
        self.assertEqual(self.move("CHAPTER", "CH-03", "TH_REVISION"), 0)
        self.assertEqual(self.errors(), [])

    def test_relock_bumps_revision(self):
        self.lock_all(("CH-01",))
        self.move("CHAPTER", "CH-01", "TH_REVISION")
        (self.root / "03_WRITING/TH/ch01.md").write_text("v2\n", encoding="utf-8")
        self.move("CHAPTER", "CH-01", "TH_REVIEW", approved="")
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_LOCKED"), 0)
        _, rows, _ = lib.read_csv(self.root / "00_START_HERE/CHAPTER_REGISTRY.csv")
        self.assertEqual(rows[0]["locked_revision"], "r2")


class Ethics(Base):
    def test_blocked_by_default(self):
        self.append("00_START_HERE/WORK_ITEM_REGISTRY.csv", "WI-0001,Interviews,PARTICIPANT_DATA,CH-03,PROPOSED,")
        self.assertEqual(self.move("WORK_ITEM", "WI-0001", "ACTIVE"), 1)

    def test_participant_file_blocked(self):
        (self.root / "03_WRITING/instruments/participant_data/p1.csv").write_text("x", encoding="utf-8")
        self.assertTrue(self.has("ETHICS"))

    def test_allowed_when_verified_and_unlocked(self):
        (self.root / "00_START_HERE/PROJECT_STATE.md").write_text(PI_STATE, encoding="utf-8")
        self.append("00_START_HERE/WORK_ITEM_REGISTRY.csv", "WI-0001,Interviews,PARTICIPANT_DATA,CH-03,PROPOSED,")
        self.assertEqual(self.move("WORK_ITEM", "WI-0001", "ACTIVE"), 0)
        (self.root / "03_WRITING/instruments/participant_data/p1.csv").write_text("x", encoding="utf-8")
        self.assertEqual(self.errors(), [])

    def test_expired_approval_blocks(self):
        (self.root / "00_START_HERE/PROJECT_STATE.md").write_text(
            PI_STATE.replace("2999-01-01", "2000-01-01"), encoding="utf-8")
        (self.root / "03_WRITING/instruments/participant_data/p1.csv").write_text("x", encoding="utf-8")
        self.assertTrue(self.has("ETHICS"))


class AuditLog(Base):
    def test_hand_edited_registry_is_caught(self):
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_REVIEW", approved=""), 0)
        p = self.root / "00_START_HERE/CHAPTER_REGISTRY.csv"
        p.write_text(p.read_text(encoding="utf-8").replace("CH-02,Literature Review,TH_DRAFT", "CH-02,Literature Review,TH_REVIEW"), encoding="utf-8")
        self.assertTrue(self.has("STATE"))

    def test_tampered_log_row_is_caught(self):
        self.move("CHAPTER", "CH-01", "TH_REVIEW", approved="")
        p = self.root / "00_START_HERE/STATE_TRANSITIONS.csv"
        p.write_text(p.read_text(encoding="utf-8").replace("AI:t", "PI"), encoding="utf-8")
        self.assertTrue(self.has("AUDIT"))

    def test_deleted_log_row_is_caught(self):
        self.move("CHAPTER", "CH-01", "TH_REVIEW", approved="")
        self.move("CHAPTER", "CH-02", "TH_REVIEW", approved="")
        p = self.root / "00_START_HERE/STATE_TRANSITIONS.csv"
        lines = p.read_text(encoding="utf-8").splitlines()
        del lines[1]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertTrue(self.has("AUDIT"))

    def test_dirty_repo_refuses_transition(self):
        (self.root / "03_WRITING/EN/ch01.md").write_text("x", encoding="utf-8")  # already violates gate
        before = (self.root / "00_START_HERE/STATE_TRANSITIONS.csv").read_bytes()
        self.assertEqual(self.move("CHAPTER", "CH-01", "TH_REVIEW", approved=""), 1)
        self.assertEqual((self.root / "00_START_HERE/STATE_TRANSITIONS.csv").read_bytes(), before)

    def test_failed_post_check_rolls_back(self):
        real = brain_check.run_checks
        calls = []

        def flaky(root):
            calls.append(1)
            rep = real(root)
            if len(calls) > 1:  # the post-transition check
                rep.error("TEST", "x", "forced failure")
            return rep

        files = [self.root / "00_START_HERE/STATE_TRANSITIONS.csv", self.root / "00_START_HERE/CHAPTER_REGISTRY.csv"]
        before = [f.read_bytes() for f in files]
        brain_check.run_checks = flaky
        try:
            self.assertEqual(self.move("CHAPTER", "CH-01", "TH_REVIEW", approved=""), 1)
        finally:
            brain_check.run_checks = real
        self.assertEqual([f.read_bytes() for f in files], before)


if __name__ == "__main__":
    unittest.main()
