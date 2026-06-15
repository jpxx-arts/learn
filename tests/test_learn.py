#!/usr/bin/env python3
"""Testes unitários do CLI `learn`.

Carrega bin/learn como módulo (arquivo sem extensão .py) e exercita cada handler
contra um diretório de estado temporário. Cobre o caminho feliz e os enforcements
(gates de mastery, cognitive load, honestidade do nível, espaçamento).

Rodar:  python3 tests/test_learn.py        (ou: python3 -m unittest -v)
"""

import argparse
import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from importlib.machinery import SourceFileLoader
from pathlib import Path

LEARN_PATH = Path(__file__).resolve().parent.parent / "bin" / "learn"
learn = SourceFileLoader("learncli", str(LEARN_PATH)).load_module()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = learn.Store(self.tmp / "learn")
        self.call(learn.cmd_init)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        learn._TODAY_OVERRIDE = None  # evita vazar override de data entre testes

    def call(self, fn, **kw):
        """Invoca um handler, captura stdout, devolve o texto impresso."""
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            fn(self.store, argparse.Namespace(**kw))
        return buf.getvalue()

    def call_fail(self, fn, **kw):
        """Invoca um handler que deve abortar via die(); devolve o exit code."""
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fn(self.store, argparse.Namespace(**kw))
        return cm.exception.code

    def call_stdin(self, fn, stdin_text, expect_fail=False, **kw):
        """Invoca um handler que lê de stdin, injetando `stdin_text`."""
        import sys
        old, sys.stdin = sys.stdin, io.StringIO(stdin_text)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                if expect_fail:
                    with self.assertRaises(SystemExit) as cm:
                        fn(self.store, argparse.Namespace(**kw))
                    return cm.exception.code
                fn(self.store, argparse.Namespace(**kw))
        finally:
            sys.stdin = old

    def xp(self):
        return self.store.load("progress")["xp"]["total"]

    def touch(self, name, track="systems", depends=None, unlocks=None):
        return self.call(learn.cmd_topic_touch, name=name, track=track,
                         depends=depends, unlocks=unlocks)

    def add_weakness(self, name="confunde X com Y", severity=2, related=None,
                     track="systems", notes=""):
        return self.call(learn.cmd_weakness_add, name=name, severity=severity,
                         related=related, track=track, notes=notes)

    def add_task(self, title="t", type="build", topic="paging", desc=""):
        return self.call(learn.cmd_task_add, title=title, type=type,
                         topic=topic, desc=desc)


class TestInit(Base):
    def test_creates_state_files(self):
        for name in learn.FILES:
            self.assertTrue((self.store.state / f"{name}.json").exists())
        self.assertTrue((self.store.state / "sessions.jsonl").exists())
        self.assertTrue((self.store.root / "notes").is_dir())

    def test_sets_start_date(self):
        self.assertEqual(self.store.load("profile")["start_date"], date.today().isoformat())

    def test_idempotent(self):
        out = self.call(learn.cmd_init)
        self.assertIn("já existe", out)

    def test_starts_with_zero_xp(self):
        self.assertEqual(self.xp(), 0)


class TestTopics(Base):
    def test_touch_creates_node_and_awards_xp(self):
        self.touch("paging")
        t = self.store.load("curriculum")["topics"]["paging"]
        self.assertEqual(t["status"], "touched")
        self.assertEqual(t["lifecycle"], "active")
        self.assertEqual(t["track"], "systems")
        self.assertEqual(self.xp(), 10)

    def test_touch_records_dependencies(self):
        self.touch("vm", depends=["paging"], unlocks=["swap"])
        t = self.store.load("curriculum")["topics"]["vm"]
        self.assertEqual(t["depends_on"], ["paging"])
        self.assertEqual(t["unlocks"], ["swap"])

    def test_touch_existing_is_revisit_without_xp(self):
        self.touch("paging")
        out = self.touch("paging")
        self.assertIn("já existe", out)
        self.assertEqual(self.xp(), 10)  # não somou de novo

    def test_cognitive_load_blocks_fourth_active(self):
        self.touch("a"); self.touch("b"); self.touch("c")
        code = self.call_fail(learn.cmd_topic_touch, name="d", track="systems",
                              depends=None, unlocks=None)
        self.assertEqual(code, 1)
        self.assertNotIn("d", self.store.load("curriculum")["topics"])

    def test_park_then_touch_succeeds(self):
        self.touch("a"); self.touch("b"); self.touch("c")
        self.call(learn.cmd_topic_park, name="a")
        self.touch("d")
        self.assertIn("d", self.store.load("curriculum")["topics"])

    def test_activate_enforces_max(self):
        self.touch("a"); self.touch("b"); self.touch("c")
        self.call(learn.cmd_topic_park, name="a")
        self.touch("d")  # a parqueado, agora ativos: b,c,d
        code = self.call_fail(learn.cmd_topic_activate, name="a")
        self.assertEqual(code, 1)

    def test_status_validation(self):
        self.touch("paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_status, name="paging", value="bogus"), 1)
        self.call(learn.cmd_topic_status, name="paging", value="practiced")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["status"], "practiced")

    def test_rate_validation(self):
        self.touch("paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_rate, name="paging", value="purple"), 1)
        self.call(learn.cmd_topic_rate, name="paging", value="green")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["tutor_rating"], "green")

    def test_teachback_awards_xp(self):
        self.touch("paging")
        self.call(learn.cmd_topic_teachback, name="paging")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["status"], "teachback_ok")
        self.assertEqual(self.xp(), 30)  # 10 touch + 20 teachback

    def test_build_done_awards_xp(self):
        self.touch("paging")
        self.call(learn.cmd_build_done, name="paging")
        self.assertEqual(self.xp(), 60)  # 10 + 50

    def test_unknown_topic_dies(self):
        self.assertEqual(self.call_fail(learn.cmd_topic_status, name="ghost", value="practiced"), 1)


class TestMasteryGate(Base):
    def _ready(self):
        self.touch("paging")
        self.add_task(topic="paging")
        self.call(learn.cmd_task_submit, id=1)
        self.call(learn.cmd_task_accept, id=1, feedback="")
        self.call(learn.cmd_topic_teachback, name="paging")

    def test_blocks_without_teachback(self):
        self.touch("paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_mastered, name="paging", force=False), 1)

    def test_blocks_without_accepted_task(self):
        self.touch("paging")
        self.call(learn.cmd_topic_teachback, name="paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_mastered, name="paging", force=False), 1)

    def test_passes_when_requirements_met(self):
        self._ready()
        self.call(learn.cmd_topic_mastered, name="paging", force=False)
        t = self.store.load("curriculum")["topics"]["paging"]
        self.assertEqual(t["status"], "mastered")
        self.assertEqual(t["lifecycle"], "mastered")

    def test_force_bypasses_gate(self):
        self.touch("paging")
        self.call(learn.cmd_topic_mastered, name="paging", force=True)
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["status"], "mastered")

    def test_idempotent(self):
        self._ready()
        self.call(learn.cmd_topic_mastered, name="paging", force=False)
        out = self.call(learn.cmd_topic_mastered, name="paging", force=False)
        self.assertIn("já está mastered", out)


class TestSpacing(Base):
    def test_open_weakness_counter_increments_on_new_topic(self):
        self.add_weakness(related=["tlb"])
        self.touch("paging")
        self.assertEqual(self.store.load("weaknesses")["weaknesses"][0]["concepts_since_last_touch"], 1)

    def test_resolved_weakness_not_incremented(self):
        self.add_weakness()
        self.call(learn.cmd_weakness_resolve, id=1)
        self.touch("paging")
        self.assertEqual(self.store.load("weaknesses")["weaknesses"][0]["concepts_since_last_touch"], 0)

    def test_affinity_surfaced_by_structural_relation(self):
        self.add_weakness(related=["paging"])
        out = self.touch("paging")
        self.assertIn("AFINIDADE", out)

    def test_same_track_but_unrelated_is_not_affinity(self):
        # mesma trilha, mas sem relação estrutural -> NÃO deve aparecer como afinidade
        self.add_weakness(track="systems", related=["scheduling"])
        out = self.touch("paging", track="systems")
        self.assertNotIn("AFINIDADE", out)

    def test_due_surfaced_after_threshold(self):
        self.add_weakness(track="networks", related=["unrelated"])
        for i in range(5):
            self.touch(f"t{i}", track="systems")
            if i < 2:  # mantém ≤3 ativos parqueando
                self.call(learn.cmd_topic_park, name=f"t{i}")
            else:
                self.call(learn.cmd_topic_park, name=f"t{i}")
        out = self.touch("final", track="systems")
        self.assertIn("VENCIDA", out)


class TestWeaknesses(Base):
    def test_add_assigns_incrementing_ids(self):
        self.add_weakness(name="w1"); self.add_weakness(name="w2")
        ws = self.store.load("weaknesses")
        self.assertEqual([w["id"] for w in ws["weaknesses"]], [1, 2])
        self.assertEqual(ws["next_id"], 3)

    def test_resolve_sets_state_and_xp(self):
        self.add_weakness()
        self.call(learn.cmd_weakness_resolve, id=1)
        w = self.store.load("weaknesses")["weaknesses"][0]
        self.assertEqual(w["status"], "resolved")
        self.assertEqual(w["last_revisited"], date.today().isoformat())
        self.assertEqual(self.xp(), 25)

    def test_touch_resets_counter_without_resolving(self):
        self.add_weakness()
        self.touch("paging")  # counter -> 1
        self.call(learn.cmd_weakness_touch, id=1)
        w = self.store.load("weaknesses")["weaknesses"][0]
        self.assertEqual(w["concepts_since_last_touch"], 0)
        self.assertEqual(w["status"], "open")
        self.assertEqual(self.xp(), 10)  # touch não dá XP de weakness

    def test_resolve_unknown_dies(self):
        self.assertEqual(self.call_fail(learn.cmd_weakness_resolve, id=99), 1)


class TestTasks(Base):
    def test_lifecycle_add_submit_accept(self):
        self.add_task(title="walker")
        self.call(learn.cmd_task_submit, id=1)
        self.assertEqual(self.store.load("tasks")["tasks"][0]["status"], "submitted")
        self.call(learn.cmd_task_accept, id=1, feedback="bom")
        t = self.store.load("tasks")["tasks"][0]
        self.assertEqual(t["status"], "accepted")
        self.assertEqual(t["feedback"], "bom")
        self.assertEqual(self.xp(), 15)

    def test_reject_returns_to_pending_with_feedback(self):
        self.add_task()
        self.call(learn.cmd_task_submit, id=1)
        self.call(learn.cmd_task_reject, id=1, feedback="faltou edge case")
        t = self.store.load("tasks")["tasks"][0]
        self.assertEqual(t["status"], "pending")
        self.assertEqual(t["feedback"], "faltou edge case")

    def test_edit_rescopes_metadata_only(self):
        self.add_task(title="boot", type="read", topic="boot-sequence", desc="old")
        self.call(learn.cmd_task_edit, id=1, title=None, type="exercise",
                  topic=None, desc="map x86 stages -> riscv")
        t = self.store.load("tasks")["tasks"][0]
        self.assertEqual(t["title"], "boot")            # unchanged
        self.assertEqual(t["type"], "exercise")          # changed
        self.assertEqual(t["topic"], "boot-sequence")    # unchanged
        self.assertEqual(t["description"], "map x86 stages -> riscv")
        self.assertEqual(t["status"], "pending")         # lifecycle untouched
        self.assertEqual(self.xp(), 0)                   # no XP for an edit

    def test_edit_no_fields_dies(self):
        self.add_task()
        self.assertEqual(
            self.call_fail(learn.cmd_task_edit, id=1,
                           title=None, type=None, topic=None, desc=None), 1)

    def test_edit_unknown_dies(self):
        self.assertEqual(
            self.call_fail(learn.cmd_task_edit, id=99,
                           title="x", type=None, topic=None, desc=None), 1)
        self.assertEqual(self.xp(), 0)

    def test_accept_unknown_dies(self):
        self.assertEqual(self.call_fail(learn.cmd_task_accept, id=42, feedback=""), 1)


class TestProgress(Base):
    def test_level_set_records_name_and_evidence(self):
        self.call(learn.cmd_level_set, track="systems", n=3,
                  because="lê mm.rs com fricção razoável")
        lv = self.store.load("progress")["levels"]["systems"]
        self.assertEqual(lv["n"], 3)
        self.assertEqual(lv["name"], "Iniciante Avançado")
        self.assertIn("mm.rs", lv["because"])

    def test_level_out_of_range_dies(self):
        self.assertEqual(self.call_fail(learn.cmd_level_set, track="systems", n=11,
                                        because="x"), 1)

    def test_level_does_not_change_xp(self):
        self.call(learn.cmd_level_set, track="systems", n=2, because="x")
        self.assertEqual(self.xp(), 0)  # nível é cosmético-desacoplado de XP

    def test_badge_appends_and_awards_xp(self):
        self.call(learn.cmd_badge_add, name="Primeiro allocator", xp=80)
        prog = self.store.load("progress")
        self.assertEqual(len(prog["badges"]), 1)
        self.assertEqual(prog["badges"][0]["name"], "Primeiro allocator")
        self.assertEqual(self.xp(), 80)

    def test_recap_appends_session_line(self):
        self.call(learn.cmd_recap, clear="paging", foggy="tlb shootdown", surprise="overcommit")
        lines = (self.store.state / "sessions.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("tlb shootdown", lines[0])


class TestProfile(Base):
    FULL = ('{"handle": "jpxx", '
            '"human": {"deep_why": "provar a mim mesmo", "wounds": "ponteiros"}, '
            '"technical": {"primary_track": "systems", "languages": "C, Rust"}}')

    def test_set_writes_fields(self):
        self.call_stdin(learn.cmd_profile_set, self.FULL)
        p = self.store.load("profile")
        self.assertEqual(p["handle"], "jpxx")
        self.assertEqual(p["human"]["deep_why"], "provar a mim mesmo")
        self.assertEqual(p["technical"]["primary_track"], "systems")

    def test_set_preserves_start_date(self):
        original = self.store.load("profile")["start_date"]
        self.call_stdin(learn.cmd_profile_set, '{"handle": "jpxx"}')
        self.assertEqual(self.store.load("profile")["start_date"], original)

    def test_partial_update_deep_merges(self):
        self.call_stdin(learn.cmd_profile_set, self.FULL)
        # update parcial: acrescenta um campo aninhado sem apagar os irmãos
        self.call_stdin(learn.cmd_profile_set,
                        '{"technical": {"references_studied": "OSTEP"}}')
        tech = self.store.load("profile")["technical"]
        self.assertEqual(tech["references_studied"], "OSTEP")
        self.assertEqual(tech["languages"], "C, Rust")  # preservado
        self.assertEqual(tech["primary_track"], "systems")  # preservado

    def test_invalid_json_dies(self):
        self.assertEqual(self.call_stdin(learn.cmd_profile_set, "{nope", expect_fail=True), 1)

    def test_non_object_dies(self):
        self.assertEqual(self.call_stdin(learn.cmd_profile_set, "[1, 2, 3]", expect_fail=True), 1)


class TestXPIntegration(Base):
    def test_full_session_totals_correctly(self):
        self.touch("paging")                                    # +10
        self.add_task(topic="paging")
        self.call(learn.cmd_task_submit, id=1)
        self.call(learn.cmd_task_accept, id=1, feedback="")     # +15
        self.call(learn.cmd_topic_teachback, name="paging")     # +20
        self.call(learn.cmd_topic_mastered, name="paging", force=False)  # +30
        self.add_weakness()
        self.call(learn.cmd_weakness_resolve, id=1)             # +25
        self.call(learn.cmd_badge_add, name="b", xp=50)         # +50
        self.assertEqual(self.xp(), 150)


class TestPersistence(Base):
    def test_no_tmp_files_left_behind(self):
        self.touch("paging")
        leftovers = list(self.store.state.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_state_is_valid_json(self):
        import json
        self.touch("paging")
        for name in learn.FILES:
            json.loads((self.store.state / f"{name}.json").read_text())


class TestNewVerbs(Base):
    def test_bloom_sets_and_validates(self):
        self.touch("paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_bloom, name="paging", level="bogus"), 1)
        self.call(learn.cmd_topic_bloom, name="paging", level="apply")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["bloom"], "apply")

    def test_dreyfus_sets_and_validates(self):
        self.touch("paging")
        self.assertEqual(self.call_fail(learn.cmd_topic_dreyfus, name="paging", stage="guru"), 1)
        self.call(learn.cmd_topic_dreyfus, name="paging", stage="competent")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["dreyfus"], "competent")

    def test_trap_appends(self):
        self.touch("paging")
        self.call(learn.cmd_topic_trap, name="paging", text="acha que malloc dá RAM física")
        self.call(learn.cmd_topic_trap, name="paging", text="ignora a TLB")
        self.assertEqual(self.store.load("curriculum")["topics"]["paging"]["traps"],
                         ["acha que malloc dá RAM física", "ignora a TLB"])


class TestDate(Base):
    def test_date_override_stamps_history(self):
        learn._TODAY_OVERRIDE = "2026-05-16"
        self.touch("paging")
        t = self.store.load("curriculum")["topics"]["paging"]
        self.assertEqual(t["first_touched"], "2026-05-16")
        self.assertEqual(t["last_touched"], "2026-05-16")


class TestGit(Base):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git não disponível")
        super().setUp()
        self.root = str(self.store.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "t@test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Test"], check=True)

    def last_msg(self):
        return subprocess.run(["git", "-C", self.root, "log", "-1", "--pretty=%s"],
                              capture_output=True, text=True).stdout.strip()

    def n_commits(self):
        r = subprocess.run(["git", "-C", self.root, "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True)
        return int(r.stdout.strip()) if r.returncode == 0 else 0

    def test_milestone_commits_conventional_message(self):
        self.add_weakness(name="w", related=["paging"])
        self.assertEqual(self.last_msg(), "docs(paging): weakness #1 opened")

    def test_badge_commit_scope_learn(self):
        self.call(learn.cmd_badge_add, name="Primeiro allocator", xp=80)
        self.assertEqual(self.last_msg(), "docs(learn): badge: Primeiro allocator")

    def test_msg_and_scope_override(self):
        self.store.msg_override = "T2 accepted"
        self.store.scope_override = "demand-paging"
        self.add_task(topic="paging")
        self.call(learn.cmd_task_submit, id=1)
        self.call(learn.cmd_task_accept, id=1, feedback="")
        self.assertEqual(self.last_msg(), "docs(demand-paging): T2 accepted")

    def test_non_milestone_does_not_commit(self):
        before = self.n_commits()
        self.touch("paging")          # touch não é marco
        self.call(learn.cmd_topic_rate, name="paging", value="green")  # rate idem
        self.assertEqual(self.n_commits(), before)

    def test_no_commit_flag(self):
        self.store.no_commit = True
        before = self.n_commits()
        self.add_weakness()
        self.assertEqual(self.n_commits(), before)

    def test_only_state_is_staged(self):
        # um arquivo fora de state/ não deve entrar no commit do CLI
        (self.store.root / "roadmap.md").write_text("norte editável")
        self.call(learn.cmd_badge_add, name="b", xp=10)
        tracked = subprocess.run(["git", "-C", self.root, "ls-files"],
                                 capture_output=True, text=True).stdout
        self.assertIn("state/progress.json", tracked)
        self.assertNotIn("roadmap.md", tracked)


class TestParserEnforcement(unittest.TestCase):
    """Enforcements feitos no nível do argparse (antes de qualquer handler)."""

    def _fails(self, argv):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                learn.build_parser().parse_args(argv)
        return cm.exception.code

    def test_level_set_requires_because(self):
        self.assertEqual(self._fails(["level", "set", "systems", "3"]), 2)

    def test_weakness_severity_choices(self):
        self.assertEqual(self._fails(["weakness", "add", "--name", "w", "--severity", "9"]), 2)

    def test_task_type_choices(self):
        self.assertEqual(self._fails(
            ["task", "add", "--title", "t", "--type", "nonsense", "--topic", "x"]), 2)

    def test_task_reject_requires_feedback(self):
        self.assertEqual(self._fails(["task", "reject", "1"]), 2)

    def test_subcommand_required(self):
        self.assertEqual(self._fails([]), 2)

    def test_board_parser_accepts_plain(self):
        ns = learn.build_parser().parse_args(["board", "--plain"])
        self.assertTrue(ns.plain)


class Board(Base):
    def board(self):
        return self.call(learn.cmd_board, plain=True)

    def test_renders_fresh_state(self):
        out = self.board()
        for header in ["LEARN", "TÓPICOS ATIVOS", "TAREFAS", "PONTOS FRACOS"]:
            self.assertIn(header, out)
        self.assertIn("sem nível avaliado", out)

    def test_no_ansi_when_plain(self):
        self.touch("x")
        self.assertNotIn("\033[", self.board())

    def test_shows_topics_and_active_count(self):
        self.touch("virtual-memory")
        out = self.board()
        self.assertIn("virtual-memory", out)
        self.assertIn("(1/3)", out)

    def test_shows_task_with_submitted_note(self):
        self.call(learn.cmd_task_add, title="Ler TLPI", type="read", topic="vm", desc="")
        self.call(learn.cmd_task_submit, id=1)
        out = self.board()
        self.assertIn("Ler TLPI", out)
        self.assertIn("aguardando correção", out)

    def test_flags_overdue_weakness(self):
        self.add_weakness(name="ponteiros", severity=3)
        for i in range(5):  # 5 tópicos novos => contador de espaçamento chega a 5
            self.touch(f"t{i}")
            self.call(learn.cmd_topic_park, name=f"t{i}")  # parqueia p/ não estourar cognitive load
        out = self.board()
        self.assertIn("ponteiros", out)
        self.assertIn("vencida", out)

    def test_shows_badge(self):
        self.call(learn.cmd_badge_add, name="Primeira RFC lida", xp=50)
        out = self.board()
        self.assertIn("MARCOS", out)
        self.assertIn("Primeira RFC lida", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
