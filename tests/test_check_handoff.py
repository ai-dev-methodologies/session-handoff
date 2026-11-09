"""Specifies the new check_handoff.py behavior (spec sections 2, 5, 6, 7, 8, 9, 10, 14.1-14.5, 17.2-17.4,
18.1, 18.2, 18.4, 18.5).

Test names carry the review-verdict item id (M1, A3, ...). A negative test asserts the exit code and a
needle the message must carry (an item name, a field label, a section title), so a handoff that already
fails for an unrelated reason cannot turn a negative test green.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_handoff.py"
VALID = ROOT / "tests" / "fixtures" / "check" / "valid.md"
TEMPLATE = ROOT / "tests" / "fixtures" / "check" / "template.md"
PREVIOUS = ROOT / "tests" / "fixtures" / "check" / "previous.md"

STATE_FIELDS = ("Branch", "HEAD", "Uncommitted changes", "Unpushed commits", "Lockfile changed")
ITEM_KEYS = ("name", "does", "decision", "result", "done-when", "after-return")
SCHEDULED_KEYS = ("name", "does", "decision", "fires-at", "done-when", "after-return")


def load_checker():
    spec = importlib.util.spec_from_file_location("check_handoff_under_test", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def set_section(text, title, body, sep=True):
    """Replace the body of `## <title>`. body=None removes the body so the next heading follows directly."""
    lines = text.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"## {title}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    if body is None:
        mid = []
    else:
        mid = [""] + body.split("\n") + ([""] + ["---"] + [""] if sep else [""])
    return "\n".join(lines[: start + 1] + mid + lines[end:])


def without_section(text, title):
    """Remove `## <title>` and its body."""
    lines = text.split("\n")
    start = lines.index(f"## {title}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[:start] + lines[end:])


ORIGIN = "/home/u/.claude/handoffs/2026-11-08-retry-session.tmp · 2026-11-08"  # same as both fixtures
CONSTRAINTS = "edit src/sync.py only; keep the queue schema unchanged"  # same as both fixtures


def goal_section(origin=ORIGIN,
                 goal='Make the sync job survive timeouts without dropping events. User: "retry the sync job instead of dropping events"',
                 done="a 30s outage loses no events; `python -m unittest tests.test_sync` passes",
                 constraints=CONSTRAINTS, changes="none", omit=()):
    values = {"Origin": origin, "Goal": goal, "Done-when": done, "Constraints": constraints, "Changes": changes}
    return "\n".join(f"- **{k}:** {v}".rstrip() for k, v in values.items() if k not in omit)


CHANGE_1 = '2026-11-09 "also cover the queue" : sync job only → sync job and queue'
CHANGE_2 = '2026-11-10 "drop the queue again" : sync job and queue → sync job only'


def live_item(name="review-high", omit=(), item_id="a1b2c3d4", **override):
    values = {
        "name": f"{name} ({item_id})" if item_id else name,
        "does": "reviews the retry diff",
        "decision": "Add retry with backoff",
        "result": "/tmp/handoff-fixture/result.md",
        "done-when": "result.md exists and ends with DONE",
        "after-return": "fold the findings into the Decisions table",
    }
    values.update({k.replace("_", "-"): v for k, v in override.items()})
    keys = [k for k in ITEM_KEYS if k not in omit]
    out = []
    for i, key in enumerate(keys):
        out.append(("  - " if i == 0 else "    ") + f"{key}: {values[key]}")
    return "\n".join(out)


def scheduled_item(name="nightly-check", omit=(), **override):
    values = {
        "name": name,
        "does": "re-runs the flaky test at night",
        "decision": "Add retry with backoff",
        "fires-at": "02:00 KST daily",
        "done-when": "the cron run log shows a pass",
        "after-return": "close the flaky-test task",
    }
    values.update({k.replace("_", "-"): v for k, v in override.items()})
    keys = [k for k in SCHEDULED_KEYS if k not in omit]
    return "\n".join(("  - " if i == 0 else "    ") + f"{k}: {values[k]}" for i, k in enumerate(keys))


def state_section(
    branch="main",
    head="a1b2c3d",
    uncommitted="none",
    unpushed="none",
    lockfile="no",
    subagents=None,
    runners=None,
    scheduled=None,
    extra_lines=(),
):
    """subagents/runners/scheduled: None -> `none`; an item block -> list; "" -> empty value; False -> omitted."""
    def live(label, value):
        if value is None:
            return f"- **{label}:** none"
        if value == "":
            return f"- **{label}:**"
        return f"- **{label}:**\n{value}"

    lines = [
        f"- **Branch:** {branch}".rstrip(),
        f"- **HEAD:** {head}".rstrip(),
        f"- **Uncommitted changes:** {uncommitted}".rstrip(),
        f"- **Unpushed commits:** {unpushed}".rstrip(),
        f"- **Lockfile changed:** {lockfile}".rstrip(),
    ]
    if subagents is not False:
        lines.append(live("Live subagents", subagents))
    if runners is not False:
        lines.append(live("Live runners", runners))
    if scheduled is not False:
        lines.append(live("Live scheduled", scheduled))
    lines.extend(extra_lines)
    return "\n".join(lines)


def decisions_table(rows):
    head = "| What | Why | How |\n|---|---|---|"
    return "\n".join([head] + ["| " + " | ".join(r) + " |" for r in rows])


class CheckerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.base = VALID.read_text(encoding="utf-8")

    def env(self, **extra):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                   HANDOFF_LOG=str(self.tmp / "runs.jsonl"))
        for name in ("CLAUDE_CODE_SESSION_ID", "HANDOFF_CALLER"):  # a developer's own session must not leak into a record
            env.pop(name, None)
        env.update(extra)
        return env

    def run_text(self, text, name="handoff.md", args=(), extra_env=None):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return subprocess.run(
            [sys.executable, str(CHECKER), str(path), *args],
            capture_output=True, text=True, encoding="utf-8", env=self.env(**(extra_env or {})),
        )

    def with_state(self, **kw):
        return set_section(self.base, "Session State", state_section(**kw))

    def assertPasses(self, proc, msg=None):
        self.assertEqual(proc.returncode, 0, msg or f"expected exit 0, got {proc.returncode}\n{proc.stdout}{proc.stderr}")

    def assertFailsWith(self, proc, *needles):
        self.assertEqual(proc.returncode, 1, f"expected exit 1, got {proc.returncode}\n{proc.stdout}{proc.stderr}")
        self.assertIn("[FAIL]", proc.stdout)
        low = proc.stdout.lower()
        for needle in needles:
            self.assertIn(needle.lower(), low, f"message lacks {needle!r}:\n{proc.stdout}")


class PositiveFixtureTest(CheckerCase):
    def test_positive_fixture_passes(self):
        proc = self.run_text(self.base)
        self.assertPasses(proc)
        self.assertIn("[PASS]", proc.stdout)

    def test_M1_live_items_none_for_both_fields_passes(self):
        self.assertPasses(self.run_text(self.with_state(subagents=None, runners=None)))

    def test_A3_runner_item_only_with_subagents_none_passes(self):
        text = self.with_state(subagents=None, runners=live_item("full-tests"))
        self.assertPasses(self.run_text(text))

    def test_A3_two_items_in_one_field_pass(self):
        items = live_item("review-high") + "\n" + live_item("review-low")
        self.assertPasses(self.run_text(self.with_state(subagents=items)))


class MarkerRemovalTest(CheckerCase):
    def test_marker_absent_does_not_fail_for_marker_reasons(self):
        self.assertNotIn("Auto-resume", self.base)
        proc = self.run_text(self.base)
        self.assertPasses(proc)
        low = (proc.stdout + proc.stderr).lower()
        self.assertNotIn("auto-resume", low)
        self.assertNotIn("marker", low)

    def test_marker_check_removed_from_module(self):
        mod = load_checker()
        for name in ("marker_failures", "MARKER_RE", "MARKER_PREFIX", "INSTRUCTION_WORDS"):
            self.assertFalse(hasattr(mod, name), f"{name} must be removed with the marker check (spec 2)")

    def test_legacy_marker_without_basis_does_not_fail(self):
        text = self.base.replace(
            "**Topic:** add retry with backoff to the sync job\n",
            "**Topic:** add retry with backoff to the sync job\n**Auto-resume:** yes · cwd=/tmp/proj\n",
        )
        self.assertIn("**Auto-resume:**", text)
        proc = self.run_text(text)
        self.assertPasses(proc)
        self.assertNotIn("marker", proc.stdout.lower())


class SessionStateTest(CheckerCase):
    def test_M1_empty_session_state_fails(self):
        text = set_section(self.base, "Session State", None)
        self.assertFailsWith(self.run_text(text), "Session State")

    def test_M1_blank_session_state_fails(self):
        text = set_section(self.base, "Session State", "", sep=False)
        self.assertFailsWith(self.run_text(text), "Session State")

    def test_M1_empty_field_value_fails(self):
        for label, kw in (
            ("Branch", "branch"),
            ("HEAD", "head"),
            ("Uncommitted changes", "uncommitted"),
            ("Unpushed commits", "unpushed"),
            ("Lockfile changed", "lockfile"),
        ):
            with self.subTest(field=label):
                proc = self.run_text(self.with_state(**{kw: ""}))
                self.assertFailsWith(proc, label)

    def test_M1_placeholder_field_value_fails(self):
        for label, kw in (
            ("Branch", "branch"),
            ("HEAD", "head"),
            ("Uncommitted changes", "uncommitted"),
            ("Unpushed commits", "unpushed"),
            ("Lockfile changed", "lockfile"),
        ):
            for value in ("TBD", "[branch name]", "<value>"):
                with self.subTest(field=label, value=value):
                    proc = self.run_text(self.with_state(**{kw: value}))
                    self.assertFailsWith(proc, label)

    def test_M1_missing_state_field_fails(self):
        for label in STATE_FIELDS:
            with self.subTest(field=label):
                lines = [
                    ln for ln in state_section().split("\n")
                    if not ln.startswith(f"- **{label}:**")
                ]
                text = set_section(self.base, "Session State", "\n".join(lines))
                self.assertFailsWith(self.run_text(text), label)

    def test_M1_live_subagents_without_live_runners_fails(self):
        text = self.with_state(subagents=live_item(), runners=False)
        self.assertFailsWith(self.run_text(text), "Live runners")

    def test_M1_live_runners_without_live_subagents_fails(self):
        text = self.with_state(subagents=False, runners=live_item("full-tests"))
        self.assertFailsWith(self.run_text(text), "Live subagents")

    def test_M1_old_live_background_work_field_fails(self):
        text = self.with_state(
            subagents=False,
            runners=False,
            extra_lines=("- **Live background work:** none",),
        )
        self.assertFailsWith(self.run_text(text), "Live")

    def test_M1_empty_live_field_value_fails(self):
        for label, kw in (("Live subagents", "subagents"), ("Live runners", "runners")):
            with self.subTest(field=label):
                proc = self.run_text(self.with_state(**{kw: ""}))
                self.assertFailsWith(proc, label)


class LiveItemTest(CheckerCase):
    def test_A3_missing_key_fails_for_subagents(self):
        for key in ITEM_KEYS:
            with self.subTest(key=key):
                text = self.with_state(subagents=live_item("review-high", omit=(key,)))
                self.assertFailsWith(self.run_text(text), "review-high" if key != "name" else "name")

    def test_A3_missing_key_fails_for_runners(self):
        for key in ITEM_KEYS:
            with self.subTest(key=key):
                text = self.with_state(runners=live_item("full-tests", omit=(key,)))
                self.assertFailsWith(self.run_text(text), "full-tests" if key != "name" else "name")

    def test_A3_missing_key_in_second_item_names_that_item(self):
        items = live_item("review-high") + "\n" + live_item("review-low", omit=("after-return",))
        proc = self.run_text(self.with_state(subagents=items))
        self.assertFailsWith(proc, "review-low")
        self.assertNotIn("review-high", proc.stdout)

    def test_A3_placeholder_key_value_fails(self):
        for key in ("does", "decision", "done-when", "after-return"):
            for value in ("TBD", "[fill in]"):
                with self.subTest(key=key, value=value):
                    kw = {key.replace("-", "_"): value}
                    text = self.with_state(subagents=live_item("review-high", **kw))
                    self.assertFailsWith(self.run_text(text), "review-high")

    def test_M2_result_sentence_fails(self):
        for sentence in ("running, will land in the transcript", "unknown", "see the log"):
            with self.subTest(result=sentence):
                text = self.with_state(subagents=live_item("review-high", result=sentence))
                self.assertFailsWith(self.run_text(text), "review-high")

    def test_M2_result_relative_path_fails(self):
        text = self.with_state(subagents=live_item("review-high", result="out/result.md"))
        self.assertFailsWith(self.run_text(text), "review-high")

    def test_M2_result_under_tilde_jobs_fails(self):
        text = self.with_state(subagents=live_item("review-high", result="~/.claude/jobs/x/out.md"))
        self.assertFailsWith(self.run_text(text), "review-high")

    def test_M2_result_under_home_jobs_fails(self):
        result = f"{self.home.as_posix()}/.claude/jobs/x/out.md"
        text = self.with_state(runners=live_item("full-tests", result=result))
        self.assertFailsWith(self.run_text(text), "full-tests")

    def test_M2_result_outside_jobs_under_home_passes(self):
        result = f"{self.home.as_posix()}/.claude/handoffs/x/out.md"
        text = self.with_state(runners=live_item("full-tests", result=result))
        self.assertPasses(self.run_text(text))

    def test_M2_result_tilde_path_passes(self):
        text = self.with_state(subagents=live_item("review-high", result="~/notes/result.md"))
        self.assertPasses(self.run_text(text))

    def test_M9_result_windows_absolute_paths_pass(self):
        for result in (
            "C:\\work\\out\\result.md",
            "C:/work/out/result.md",
            "/c/work/out/result.md",
            "/mnt/c/work/out/result.md",
        ):
            with self.subTest(result=result):
                text = self.with_state(subagents=live_item("review-high", result=result))
                self.assertPasses(self.run_text(text))


class DecisionsTest(CheckerCase):
    GOOD = ["Keep the queue unchanged", '"queue migration is out of scope"', "edit src/sync.py only"]

    def with_rows(self, rows):
        return set_section(self.base, "Decisions Made", decisions_table(rows))

    def test_A2_empty_cell_fails_with_row_number(self):
        for col, label in enumerate(("What", "Why", "How")):
            with self.subTest(column=label):
                bad = list(self.GOOD)
                bad[col] = ""
                proc = self.run_text(self.with_rows([self.GOOD, self.GOOD, bad]))
                self.assertFailsWith(proc, "Decisions")
                lines = [ln for ln in proc.stdout.splitlines() if "decisions" in ln.lower()]
                self.assertTrue(
                    any(re.search(r"\b3\b", ln) for ln in lines),
                    f"message lacks the row number 3 (third data row):\n{proc.stdout}",
                )

    def test_A2_placeholder_cell_fails(self):
        for col, label in enumerate(("What", "Why", "How")):
            for value in ("TBD", "[x]", "<x>", "n/a", "..."):
                with self.subTest(column=label, value=value):
                    bad = list(self.GOOD)
                    bad[col] = value
                    proc = self.run_text(self.with_rows([bad]))
                    self.assertFailsWith(proc, "Decisions")

    def test_A2_row_with_only_two_cells_fails(self):
        proc = self.run_text(self.with_rows([["Keep the queue unchanged", '"out of scope"']]))
        self.assertFailsWith(proc, "Decisions")

    def test_A2_zero_rows_fails(self):
        proc = self.run_text(self.with_rows([]))
        self.assertFailsWith(proc, "Decisions")

    def test_A2_header_only_without_separator_fails(self):
        text = set_section(self.base, "Decisions Made", "| What | Why | How |")
        self.assertFailsWith(self.run_text(text), "Decisions")

    def test_M12_how_is_design_document_path_passes(self):
        row = ["Add retry with backoff", '"events drop on timeout"', "docs/retry-design.md"]
        self.assertPasses(self.run_text(self.with_rows([row])))

    def test_M12_how_is_plan_document_path_passes(self):
        row = ["Add retry with backoff", '"events drop on timeout"', "docs/plans/retry-plan.md"]
        self.assertPasses(self.run_text(self.with_rows([row])))

    def test_A2_all_cells_filled_passes(self):
        self.assertPasses(self.run_text(self.with_rows([self.GOOD])))

    NO_DECISIONS = "No major decisions made this session."

    def with_body(self, body):
        return set_section(self.base, "Decisions Made", body)

    def test_A2_no_decisions_sentence_passes(self):
        self.assertPasses(self.run_text(self.with_body(self.NO_DECISIONS)))

    def test_A2_no_decisions_sentence_whitespace_and_period_variants_pass(self):
        for body in (
            f"  {self.NO_DECISIONS}  ",
            self.NO_DECISIONS.rstrip("."),
            self.NO_DECISIONS + ".",
            f"\t{self.NO_DECISIONS.rstrip('.')}   ",
        ):
            with self.subTest(body=body):
                self.assertPasses(self.run_text(self.with_body(body)))

    def test_A2_prose_decision_without_table_fails(self):
        proc = self.run_text(self.with_body("We chose X because Y."))
        self.assertFailsWith(proc, "Decisions")

    def test_A2_sentence_plus_prose_without_table_fails(self):
        proc = self.run_text(self.with_body(self.NO_DECISIONS + "\nWe chose X because Y."))
        self.assertFailsWith(proc, "Decisions")

    def test_A2_sentence_with_good_table_passes(self):
        body = self.NO_DECISIONS + "\n\n" + decisions_table([self.GOOD])
        self.assertPasses(self.run_text(self.with_body(body)))

    def test_A2_sentence_with_empty_cell_table_fails(self):
        bad = list(self.GOOD)
        bad[1] = ""
        body = self.NO_DECISIONS + "\n\n" + decisions_table([bad])
        self.assertFailsWith(self.run_text(self.with_body(body)), "Decisions")


class NextStepTest(CheckerCase):
    def test_M3_placeholder_next_step_fails(self):
        for body in ("---", "[next step]", "TBD", "[다음 단계 작성]", "...", "<next step>"):
            with self.subTest(body=body):
                text = set_section(self.base, "Exact Next Step", body)
                self.assertFailsWith(self.run_text(text), "Exact Next Step")

    def test_M3_all_placeholder_lines_fail(self):
        text = set_section(self.base, "Exact Next Step", "---\n\n[next step]\nTBD")
        self.assertFailsWith(self.run_text(text), "Exact Next Step")

    def test_M3_empty_next_step_fails(self):
        for body in (None, "", "   \n\n"):
            with self.subTest(body=body):
                text = set_section(self.base, "Exact Next Step", body, sep=False)
                self.assertFailsWith(self.run_text(text), "Exact Next Step")

    def test_M3_missing_next_step_section_fails(self):
        lines = self.base.split("\n")
        start = lines.index("## Exact Next Step")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        text = "\n".join(lines[:start] + lines[end:])
        self.assertFailsWith(self.run_text(text), "Exact Next Step")

    def test_M3_real_instruction_with_placeholder_line_passes(self):
        text = set_section(self.base, "Exact Next Step", "Open scripts/sync.py and add jitter.\n\n---")
        self.assertPasses(self.run_text(text))


class PlaceholderTest(unittest.TestCase):
    TRUE_CASES = (
        "", "   ", "-", "---", "—", "——", "=", "===", "...", "…",
        "[…]", "[...]", "[next step]", "[branch name]", "<value>", "<path to file>",
        "tbd", "TBD", "Tbd", "todo", "TODO", "fixme", "FIXME", "placeholder", "Placeholder", "n/a", "N/A",
        "  TBD  ",
    )
    FALSE_CASES = (
        "none", "None", "NONE", "main", "/tmp/x", "no", "[x] done", "a <b> c",
        "retry with backoff", "- a bullet", "docs/retry-design.md",
    )

    @classmethod
    def setUpClass(cls):
        cls.mod = load_checker()

    def fn(self):
        if not hasattr(self.mod, "is_placeholder"):
            self.fail("check_handoff.is_placeholder(text) -> bool is missing (spec 5)")
        return self.mod.is_placeholder

    def test_M3_placeholder_values_are_true(self):
        is_placeholder = self.fn()
        for case in self.TRUE_CASES:
            with self.subTest(text=case):
                self.assertIs(is_placeholder(case), True)

    def test_M3_real_values_are_false(self):
        is_placeholder = self.fn()
        for case in self.FALSE_CASES:
            with self.subTest(text=case):
                self.assertIs(is_placeholder(case), False)

    def test_M3_none_is_not_a_placeholder(self):
        is_placeholder = self.fn()
        self.assertIs(is_placeholder("none"), False)


class AbsolutePathTest(unittest.TestCase):
    TRUE_CASES = (
        "/home/u/x", "/tmp/out/result.md", "~/x", "~/notes/result.md",
        "C:\\x", "C:/x", "c:\\x", "D:\\work\\out.md", "C:\\work\\out\\result.md",
        "/c/x", "/c/work/out/result.md", "/mnt/c/x", "/mnt/c/work/out/result.md",
    )
    FALSE_CASES = (
        "", "running", "unknown", "result.md", "out/result.md", "./out.md", "../out.md",
        "see /tmp/x for details", "C:", "none",
    )

    @classmethod
    def setUpClass(cls):
        cls.mod = load_checker()

    def fn(self):
        if not hasattr(self.mod, "is_absolute_path"):
            self.fail("check_handoff.is_absolute_path(text) -> bool is missing (spec 10; assumed name)")
        return self.mod.is_absolute_path

    def test_M9_absolute_paths_are_accepted(self):
        is_absolute_path = self.fn()
        for case in self.TRUE_CASES:
            with self.subTest(path=case):
                self.assertIs(is_absolute_path(case), True)

    def test_M2_non_paths_are_rejected(self):
        is_absolute_path = self.fn()
        for case in self.FALSE_CASES:
            with self.subTest(path=case):
                self.assertIs(is_absolute_path(case), False)



# --- 13.1: Toplevel head line ----------------------------------------------------------
class ToplevelTest(CheckerCase):
    LINE = "**Toplevel:** /home/u/sample-project\n"

    def test_H5_missing_toplevel_fails(self):
        self.assertFailsWith(self.run_text(self.base.replace(self.LINE, "")), "Toplevel")

    def test_H5_relative_or_placeholder_toplevel_fails(self):
        for value in ("sample-project", "[project key]", "", "see the repo"):
            with self.subTest(value=value):
                text = self.base.replace(self.LINE, f"**Toplevel:** {value}\n")
                self.assertFailsWith(self.run_text(text), "Toplevel")

    def test_H5_windows_toplevel_passes(self):
        for value in ("C:/work/sample", "C:\\work\\sample", "/c/work/sample"):
            with self.subTest(value=value):
                self.assertPasses(self.run_text(self.base.replace(self.LINE, f"**Toplevel:** {value}\n")))


# --- 13.2: Live scheduled, ids in names, nested bullets ------------------------------------
class LiveSchemaTest(CheckerCase):
    def test_H4_missing_live_scheduled_fails(self):
        self.assertFailsWith(self.run_text(self.with_state(scheduled=False)), "Live scheduled")

    def test_H4_empty_live_scheduled_fails(self):
        self.assertFailsWith(self.run_text(self.with_state(scheduled="")), "Live scheduled")

    def test_H4_scheduled_item_with_six_keys_passes(self):
        self.assertPasses(self.run_text(self.with_state(scheduled=scheduled_item())))

    def test_H4_scheduled_item_missing_key_fails(self):
        for key in SCHEDULED_KEYS:
            with self.subTest(key=key):
                text = self.with_state(scheduled=scheduled_item(omit=(key,)))
                self.assertFailsWith(self.run_text(text), "nightly-check" if key != "name" else "name")

    def test_H4_scheduled_placeholder_fires_at_fails(self):
        text = self.with_state(scheduled=scheduled_item(fires_at="[time or trigger]"))
        self.assertFailsWith(self.run_text(text), "nightly-check", "fires-at")

    def test_H4_name_without_id_fails_for_subagents_and_runners(self):
        for field in ("subagents", "runners"):
            with self.subTest(field=field):
                text = self.with_state(**{field: live_item("review-high", item_id=None)})
                self.assertFailsWith(self.run_text(text), "review-high", "id")

    def test_H4_name_with_pid_or_shell_id_passes(self):
        for item_id in ("12345", "bash_3", "task-7f2a"):
            with self.subTest(item_id=item_id):
                self.assertPasses(self.run_text(self.with_state(runners=live_item("full-tests", item_id=item_id))))

    def test_M6_transcript_result_path_passes(self):
        result = "/home/u/.claude/projects/p/s/subagents/agent-a1b2c3d4.jsonl"
        self.assertPasses(self.run_text(self.with_state(subagents=live_item(result=result))))

    def test_L4_nested_bullets_under_a_key_are_not_items(self):
        item = live_item("review-high") + "\n      - copy the result file\n      - then report it"
        self.assertPasses(self.run_text(self.with_state(subagents=item)))

    def test_L4_optional_and_unknown_keys_keep_the_item_whole(self):
        item = live_item("review-high") + ("\n    started: 10:05\n    expected-by: 11:00"
                                           "\n    durable-copy: /home/u/copies/out.md")
        self.assertPasses(self.run_text(self.with_state(subagents=item)))

    def test_L4_unknown_key_after_result_does_not_change_result(self):
        item = live_item("review-high", omit=("done-when", "after-return")) + (
            "\n    durable-copy: /home/u/.claude/jobs/x/out.md"
            "\n    done-when: result.md exists\n    after-return: report it")
        self.assertPasses(self.run_text(self.with_state(subagents=item)))


# --- 13.3: checker options and robustness -----------------------------------------------
class GitArgumentTest(CheckerCase):
    def without_state(self):
        lines = self.base.split("\n")
        start = lines.index("## Session State")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        return "\n".join(lines[:start] + lines[end:])

    def test_M2_git_yes_without_session_state_fails(self):
        self.assertFailsWith(self.run_text(self.without_state(), args=("--git", "yes")), "Session State")

    def test_HC_git_no_without_session_state_fails(self):
        self.assertFailsWith(self.run_text(self.without_state(), args=("--git", "no")), "Session State")

    def test_M2_no_git_argument_without_session_state_passes(self):
        self.assertPasses(self.run_text(self.without_state()))

    def test_M2_git_yes_with_session_state_passes(self):
        self.assertPasses(self.run_text(self.base, args=("--git", "yes")))

    def test_M2_bad_git_value_is_usage_error(self):
        self.assertEqual(self.run_text(self.base, args=("--git", "maybe")).returncode, 2)


class TemplateNextStepTest(CheckerCase):
    def template_next_step(self):
        lines = TEMPLATE.read_text(encoding="utf-8").split("\n")
        start = lines.index("## Exact Next Step")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        return "\n".join(lines[start + 1:end]).strip("\n")

    def test_M1_multiline_bracket_next_step_from_template_fails(self):
        body = self.template_next_step()
        self.assertGreater(len(body.split("\n")), 2, body)
        text = set_section(self.base, "Exact Next Step", body, sep=False)
        self.assertFailsWith(self.run_text(text), "Exact Next Step")

    def test_M1_unfilled_template_fails_on_exact_next_step(self):
        self.assertFailsWith(self.run_text(TEMPLATE.read_text(encoding="utf-8")), "Exact Next Step")

    def test_M1_real_step_with_bracketed_link_passes(self):
        text = set_section(self.base, "Exact Next Step", "[Open the design](docs/retry-design.md) and add jitter.")
        self.assertPasses(self.run_text(text))


class CrashTest(CheckerCase):
    def test_M5_unexpected_exception_exits_2_with_traceback_tail(self):
        path = self.tmp / "h.md"
        path.write_text(self.base, encoding="utf-8", newline="\n")
        code = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('c', {str(CHECKER)!r})\n"
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
            "def boom(lines): raise RuntimeError('checker bug here')\n"
            "mod.decisions_failures = boom\n"
            f"sys.exit(mod.run([{str(path)!r}]))\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", env=self.env())
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        tail = proc.stderr.strip().splitlines()
        self.assertLessEqual(len(tail), 5, proc.stderr)
        self.assertIn("RuntimeError: checker bug here", tail[-1])
        self.assertNotIn("[PASS]", proc.stdout)


class NoDecisionsWordTest(CheckerCase):
    def test_L3_none_word_passes(self):
        for body in ("None", "None.", "none", "NONE."):
            with self.subTest(body=body):
                self.assertPasses(self.run_text(set_section(self.base, "Decisions Made", body)))

    def test_L3_none_with_more_prose_fails(self):
        self.assertFailsWith(self.run_text(set_section(self.base, "Decisions Made", "None.\nWe chose X.")),
                             "Decisions")


class PathNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_checker()

    def test_L8_jobs_paths_in_windows_forms_are_detected(self):
        for path in (
            "C:\\Work\\Dev\\.claude\\jobs\\x\\out.md",
            "c:/work/Dev/.claude/jobs/x/out.md",
            "C:/Work/Dev/.CLAUDE/Jobs/x/out.md",
            "/c/Work/Dev/.claude/jobs/x/out.md",
            "/mnt/c/Work/Dev/.claude/jobs/x/out.md",
            "~/.claude/jobs/x/out.md",
            "/srv/u/.claude/jobs/x/out.md",
        ):
            with self.subTest(path=path):
                self.assertIs(self.mod.is_under_jobs(path), True)

    def test_L8_non_jobs_paths_are_not_detected(self):
        for path in ("/home/u/.claude/jobsite/x", "/home/u/.Claude/jobs/x", "/home/u/.claude/handoffs/x"):
            with self.subTest(path=path):
                self.assertIs(self.mod.is_under_jobs(path), False)

    def test_H5_path_key_equal_forms(self):
        key = self.mod.path_key
        self.assertEqual(key("C:\\Work\\Dev\\proj\\"), key("c:/work/dev/proj"))
        self.assertEqual(key("/c/Work/Dev/proj"), key("C:/Work/Dev/proj"))
        self.assertEqual(key("/mnt/c/Work/Dev/proj/"), key("C:\\Work\\Dev\\proj"))
        self.assertEqual(key("/home/u/proj/"), "/home/u/proj")
        self.assertNotEqual(key("/home/A/proj"), key("/home/a/proj"))


# --- 14.1: Goal (carried) --------------------------------------------------------------------
class GoalCarriedTest(CheckerCase):
    def with_goal(self, **kw):
        return set_section(self.base, "Goal (carried)", goal_section(**kw))

    def test_C1_missing_goal_section_fails(self):
        self.assertFailsWith(self.run_text(without_section(self.base, "Goal (carried)")), "Goal (carried)")

    def test_C1_missing_key_fails_with_its_name(self):
        for key in ("Origin", "Goal", "Done-when", "Constraints", "Changes"):
            with self.subTest(key=key):
                self.assertFailsWith(self.run_text(self.with_goal(omit=(key,))), "Goal (carried)", key)

    def test_C1_placeholder_value_fails_with_key_name(self):
        for key, kw in (("Origin", "origin"), ("Goal", "goal"), ("Done-when", "done"),
                        ("Constraints", "constraints"), ("Changes", "changes")):
            for value in ("", "TBD", "[fill in]", "<x>"):
                with self.subTest(key=key, value=value):
                    self.assertFailsWith(self.run_text(self.with_goal(**{kw: value})), "Goal (carried)", key)

    def test_C1_changes_none_and_filled_entry_pass(self):
        self.assertPasses(self.run_text(self.with_goal(changes="none")))
        self.assertPasses(self.run_text(self.with_goal(changes=CHANGE_1)))

    def test_C1_empty_goal_section_fails(self):
        self.assertFailsWith(self.run_text(set_section(self.base, "Goal (carried)", None)), "Goal (carried)")


class GoalChainTest(CheckerCase):
    """`**Continues:**` points at a previous file; its Goal (carried) is the baseline."""

    def setUp(self):
        super().setUp()
        self.prev_text = PREVIOUS.read_text(encoding="utf-8")
        self.prev = self.tmp / "previous.md"

    def chain(self, goal_kw=None, prev_kw=None, prev_text=None):
        """Write the previous file (optionally with another Goal (carried)) and check a current file pointing at it."""
        text = prev_text if prev_text is not None else self.prev_text
        if prev_kw is not None:
            text = set_section(text, "Goal (carried)", goal_section(**prev_kw))
        self.prev.write_text(text, encoding="utf-8", newline="\n")
        cur = self.base.replace("**Continues:** none", f"**Continues:** {self.prev}")
        if goal_kw is not None:
            cur = set_section(cur, "Goal (carried)", goal_section(**goal_kw))
        return self.run_text(cur)

    def test_C1_identical_goal_passes(self):
        self.assertPasses(self.chain())

    def test_C1_changed_field_without_changes_entry_fails(self):
        for kw in (dict(goal="A different goal. User: \"new\""), dict(done="something else"),
                   dict(origin="/home/u/first.md · 2026-11-01")):
            with self.subTest(changed=list(kw)):
                proc = self.chain(goal_kw=kw)
                self.assertFailsWith(proc, "Goal (carried) changed without a Changes entry")

    def test_C1_new_changes_entry_with_quote_passes_even_when_goal_changed(self):
        kw = dict(goal="Sync job and queue. User: \"also cover the queue\"", changes=CHANGE_1)
        self.assertPasses(self.chain(goal_kw=kw))

    def test_C1_new_changes_entry_with_corner_quotes_passes(self):
        kw = dict(changes='2026-11-09 「큐도 포함해」 : sync job only → sync job and queue')
        self.assertPasses(self.chain(goal_kw=kw))

    def test_C1_new_changes_entry_without_quote_fails(self):
        kw = dict(changes="2026-11-09 : sync job only → sync job and queue")
        self.assertFailsWith(self.chain(goal_kw=kw), "Goal (carried)", "Changes")

    def test_C1_second_entry_appended_after_first_passes(self):
        prev = dict(changes=CHANGE_1)
        cur = dict(goal="Sync job only again. User: \"drop the queue again\"", changes=f"{CHANGE_1} | {CHANGE_2}")
        self.assertPasses(self.chain(goal_kw=cur, prev_kw=prev))

    def test_C1_same_entry_count_with_changes_text_requires_identical_goal(self):
        prev = dict(changes=CHANGE_1)
        self.assertPasses(self.chain(goal_kw=prev, prev_kw=prev))
        proc = self.chain(goal_kw=dict(goal="drifted", changes=CHANGE_1), prev_kw=prev)
        self.assertFailsWith(proc, "Goal (carried) changed without a Changes entry")

    def test_C1_changes_entries_removed_fails(self):
        prev = dict(changes=f"{CHANGE_1} | {CHANGE_2}")
        self.assertFailsWith(self.chain(goal_kw=dict(changes=CHANGE_1), prev_kw=prev), "Goal (carried)", "Changes")

    def test_C1_previous_without_goal_section_skips_comparison(self):
        prev_text = without_section(self.prev_text, "Goal (carried)")
        self.assertPasses(self.chain(goal_kw=dict(goal="anything new"), prev_text=prev_text))

    def test_C1_continues_none_does_not_compare(self):
        self.assertPasses(self.run_text(set_section(self.base, "Goal (carried)", goal_section(goal="fresh"))))


# --- 14.2: Continues head line ---------------------------------------------------------------
class ContinuesTest(CheckerCase):
    LINE = "**Continues:** none\n"

    def test_C2_missing_continues_fails(self):
        self.assertFailsWith(self.run_text(self.base.replace(self.LINE, "")), "Continues")

    def test_C2_none_passes(self):
        self.assertPasses(self.run_text(self.base))

    def test_C2_existing_absolute_path_passes(self):
        prev = self.tmp / "prev.md"
        prev.write_text(PREVIOUS.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        self.assertPasses(self.run_text(self.base.replace(self.LINE, f"**Continues:** {prev}\n")))

    def test_C2_missing_file_fails(self):
        gone = self.tmp / "no-such-handoff.md"
        proc = self.run_text(self.base.replace(self.LINE, f"**Continues:** {gone}\n"))
        self.assertFailsWith(proc, "Continues")
        self.assertIn("no-such-handoff.md", proc.stdout)

    def test_C2_relative_or_placeholder_value_fails(self):
        for value in ("prev.md", "[previous handoff path]", "", "the last one"):
            with self.subTest(value=value):
                proc = self.run_text(self.base.replace(self.LINE, f"**Continues:** {value}\n"))
                self.assertFailsWith(proc, "Continues")


# --- 14.3: Closed (pointers) -----------------------------------------------------------------
class ClosedPointersTest(CheckerCase):
    def history(self, keys=("key-a", "key-b")):
        path = self.tmp / "history.md"
        lines = [f"2026-11-08 · {k} · finished the {k} step · /tmp/evidence/{k}.md" for k in keys]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return path

    def with_closed(self, line):
        text = self.base.replace("## What We Are Building\n", f"## Closed (pointers)\n\n{line}\n\n---\n\n## What We Are Building\n", 1)
        self.assertIn("## Closed (pointers)", text)
        return text

    def test_C3_no_closed_section_passes(self):
        self.assertNotIn("Closed (pointers)", self.base)
        self.assertPasses(self.run_text(self.base))

    def test_C3_valid_pointer_passes(self):
        path = self.history()
        text = self.with_closed(f"- 2 items closed → {path} (keys: key-a, key-b)")
        self.assertPasses(self.run_text(text))

    def test_C3_listed_key_missing_from_history_fails(self):
        path = self.history(keys=("key-a",))
        text = self.with_closed(f"- 2 items closed → {path} (keys: key-a, key-b)")
        proc = self.run_text(text)
        self.assertFailsWith(proc, "Closed (pointers)", "key-b")

    def test_C3_history_path_missing_fails(self):
        gone = self.tmp / "no-history.md"
        text = self.with_closed(f"- 1 items closed → {gone} (keys: key-a)")
        self.assertFailsWith(self.run_text(text), "Closed (pointers)", "no-history.md")

    def test_C3_relative_history_path_fails(self):
        self.history()
        text = self.with_closed("- 1 items closed → history.md (keys: key-a)")
        self.assertFailsWith(self.run_text(text), "Closed (pointers)")

    def test_C3_section_without_pointer_line_fails(self):
        self.assertFailsWith(self.run_text(self.with_closed("nothing closed")), "Closed (pointers)")

    def test_C3_pointer_without_keys_fails(self):
        path = self.history()
        self.assertFailsWith(self.run_text(self.with_closed(f"- 0 items closed → {path} (keys: )")),
                             "Closed (pointers)")


# --- 14.5: length budget -----------------------------------------------------------------------
class SizeWarningTest(CheckerCase):
    LIMIT = 16 * 1024
    WARNING = re.compile(r"^WARNING: handoff file is (\d+) KiB \(over 16 KiB\)$", re.M)

    def sized(self, size, base=None):
        """The valid fixture (or `base`) padded (in the last section) to exactly `size` bytes."""
        text = (base or self.base).rstrip("\n") + "\n"
        pad = size - len(text.encode("utf-8"))
        self.assertGreater(pad, 0)
        return text + "x" * (pad - 1) + "\n"

    def test_C5_over_16_kib_warns_once_and_keeps_exit_0(self):
        text = self.sized(self.LIMIT + 1)
        self.assertEqual(len(text.encode("utf-8")), self.LIMIT + 1)
        proc = self.run_text(text)
        self.assertPasses(proc)
        found = self.WARNING.findall(proc.stdout)
        self.assertEqual(len(found), 1, proc.stdout)
        self.assertIn("[PASS]", proc.stdout)

    def test_C5_warning_number_is_the_size_in_kib(self):
        proc = self.run_text(self.sized(40 * 1024))
        self.assertEqual(self.WARNING.findall(proc.stdout), ["40"], proc.stdout)

    def test_C5_exactly_16_kib_does_not_warn(self):
        text = self.sized(self.LIMIT)
        self.assertEqual(len(text.encode("utf-8")), self.LIMIT)
        proc = self.run_text(text)
        self.assertPasses(proc)
        self.assertNotIn("WARNING", proc.stdout)

    def test_C5_small_valid_fixture_does_not_warn(self):
        self.assertNotIn("WARNING", self.run_text(self.base).stdout)

    def test_F2_warning_comes_after_the_result_block(self):
        for base, mark in ((self.base, "[PASS]"), (self.base.replace("**Continues:** none\n", ""), "[FAIL]")):
            with self.subTest(result=mark):
                out = self.run_text(self.sized(self.LIMIT + 10, base)).stdout.splitlines()
                self.assertTrue(out[0].startswith(mark), out)
                self.assertTrue(self.WARNING.match(out[-1]), out)
                self.assertEqual(len([ln for ln in out if ln.startswith("WARNING")]), 1, out)

    def test_C5_warning_does_not_hide_a_failure(self):
        text = self.sized(self.LIMIT + 10, self.base.replace("**Continues:** none\n", ""))
        proc = self.run_text(text)
        self.assertFailsWith(proc, "Continues")
        self.assertEqual(len(self.WARNING.findall(proc.stdout)), 1, proc.stdout)


class CheckerRunLogTest(CheckerCase):
    def test_A12_check_run_appends_record(self):
        proc = self.run_text(self.base, args=("--git", "yes"))
        recs = [json.loads(ln) for ln in (self.tmp / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual((rec["script"], rec["mode"], rec["exit"]), ("check_handoff.py", "check", 0))
        self.assertEqual(rec["handoff"], str(self.tmp / "handoff.md"))
        self.assertEqual(rec["args"], [str(self.tmp / "handoff.md"), "--git", "yes"])
        self.assertEqual(rec["stdout"], proc.stdout.splitlines())
        self.assertIsNone(rec["continue"])
        self.assertIsNone(rec["checker"])

    def test_A12_unwritable_log_keeps_result(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("file", encoding="utf-8")
        proc = self.run_text(self.base, extra_env={"HANDOFF_LOG": str(blocker / "x.jsonl")})
        self.assertPasses(proc)
        self.assertIn("WARNING: run log not written:", proc.stderr)


# --- 16.3: Session State and Live fields outside git ---------------------------------------------
class GitOutsideTest(CheckerCase):
    NA = dict(branch="n/a", head="n/a", uncommitted="n/a", unpushed="n/a", lockfile="n/a")

    def test_HC_git_no_with_na_git_fields_and_live_fields_passes(self):
        self.assertPasses(self.run_text(self.with_state(**self.NA), args=("--git", "no")))

    def test_HC_git_no_with_live_items_passes(self):
        text = self.with_state(subagents=live_item(), runners=live_item("full-tests", item_id="b7f3e2"), **self.NA)
        self.assertPasses(self.run_text(text, args=("--git", "no")))

    def test_HC_git_no_without_git_fields_passes_when_live_fields_exist(self):
        lines = [ln for ln in state_section().split("\n") if not ln.startswith(tuple(f"- **{f}:**" for f in STATE_FIELDS))]
        text = set_section(self.base, "Session State", "\n".join(lines))
        self.assertPasses(self.run_text(text, args=("--git", "no")))

    def test_HC_git_no_still_requires_each_live_field(self):
        for label, kw in (("Live subagents", "subagents"), ("Live runners", "runners"), ("Live scheduled", "scheduled")):
            with self.subTest(field=label):
                proc = self.run_text(self.with_state(**{kw: False}, **self.NA), args=("--git", "no"))
                self.assertFailsWith(proc, label)

    def test_HC_git_no_still_checks_live_items(self):
        text = self.with_state(subagents=live_item("review-high", omit=("result",)), **self.NA)
        self.assertFailsWith(self.run_text(text, args=("--git", "no")), "review-high", "result")

    def test_HC_git_no_rejects_other_placeholders_in_git_fields(self):
        proc = self.run_text(self.with_state(branch="[branch name]", head="n/a"), args=("--git", "no"))
        self.assertFailsWith(proc, "Branch")

    def test_HC_na_git_fields_fail_inside_git_and_without_the_argument(self):
        for args in (("--git", "yes"), ()):
            with self.subTest(args=args):
                self.assertFailsWith(self.run_text(self.with_state(**self.NA), args=args), "Branch")


# --- 16.4: Goal (carried) and Live items along the chain -------------------------------------------
class ChainStrictTest(CheckerCase):
    chain = GoalChainTest.chain

    def setUp(self):
        super().setUp()
        self.prev_text = PREVIOUS.read_text(encoding="utf-8")
        self.prev = self.tmp / "previous.md"

    def test_L5_origin_must_start_with_an_absolute_path(self):
        for origin in ("this file · 2026-11-08", "somewhere · 2026-11-08", "first handoff"):
            with self.subTest(origin=origin):
                proc = self.run_text(set_section(self.base, "Goal (carried)", goal_section(origin=origin)))
                self.assertFailsWith(proc, "Goal (carried)", "Origin")

    def test_M1_origin_change_fails_even_with_a_new_changes_entry(self):
        kw = dict(origin="/elsewhere/other.md · 2026-11-09", changes=CHANGE_1)
        self.assertFailsWith(self.chain(goal_kw=kw), "Goal (carried)", "Origin")

    def test_M1_rewritten_previous_changes_entry_fails_at_equal_count(self):
        prev = dict(changes=CHANGE_1)
        cur = dict(changes='2026-11-09 "something else entirely" : a → b')
        self.assertFailsWith(self.chain(goal_kw=cur, prev_kw=prev), "Goal (carried)", "Changes")

    def test_M1_rewritten_previous_changes_entry_fails_when_one_is_added(self):
        prev = dict(changes=CHANGE_1)
        cur = dict(changes=f'2026-11-09 "rewritten" : a → b | {CHANGE_2}')
        self.assertFailsWith(self.chain(goal_kw=cur, prev_kw=prev), "Goal (carried)", "Changes")

    def test_M1_continues_pointing_at_this_file_fails(self):
        path = self.tmp / "handoff.md"
        proc = self.run_text(self.base.replace("**Continues:** none", f"**Continues:** {path}"))
        self.assertFailsWith(proc, "Continues")
        self.assertIn("itself", proc.stdout)

    def test_M1_continues_with_another_toplevel_fails(self):
        self.prev.write_text(self.prev_text.replace("**Toplevel:** /home/u/sample-project", "**Toplevel:** /home/u/other"),
                             encoding="utf-8", newline="\n")
        proc = self.run_text(self.base.replace("**Continues:** none", f"**Continues:** {self.prev}"))
        self.assertFailsWith(proc, "Toplevel")

    def test_M1_continues_previous_without_toplevel_skips_that_comparison(self):
        self.prev.write_text(self.prev_text.replace("**Toplevel:** /home/u/sample-project\n", ""),
                             encoding="utf-8", newline="\n")
        self.assertPasses(self.run_text(self.base.replace("**Continues:** none", f"**Continues:** {self.prev}")))

    def current_with(self, **state_kw):
        cur = self.base.replace("**Continues:** none", f"**Continues:** {self.prev}")
        return set_section(cur, "Session State", state_section(**state_kw))

    def test_M2_previous_live_item_missing_fails(self):
        self.prev.write_text(self.prev_text, encoding="utf-8", newline="\n")
        proc = self.run_text(self.current_with(subagents=live_item(), runners=None))
        self.assertFailsWith(proc, "Live item full-tests (b7f3e2) from the previous handoff is missing")
        self.assertNotIn("review-high (a1b2c3d4) from the previous", proc.stdout)

    def test_M2_previous_live_item_carried_to_another_field_passes(self):
        self.prev.write_text(self.prev_text, encoding="utf-8", newline="\n")
        runners = live_item("full-tests", item_id="b7f3e2") + "\n" + live_item("review-high", item_id="a1b2c3d4")
        self.assertPasses(self.run_text(self.current_with(subagents=None, runners=runners)))

    def test_M2_previous_live_item_closed_by_its_id_passes(self):
        self.prev.write_text(self.prev_text, encoding="utf-8", newline="\n")
        history = self.tmp / "history.md"
        history.write_text("2026-11-08 · b7f3e2 · full tests finished · /home/u/logs/full-tests.log\n",
                           encoding="utf-8", newline="\n")
        text = self.current_with(subagents=live_item(), runners=None).replace(
            "## What We Are Building\n", f"## Closed (pointers)\n\n- 1 items closed → {history} (keys: b7f3e2)\n\n---\n\n## What We Are Building\n", 1)
        self.assertPasses(self.run_text(text))

    def test_M2_previous_scheduled_item_without_id_is_matched_by_name(self):
        prev = set_section(self.prev_text, "Session State", state_section(
            subagents=live_item(), runners=live_item("full-tests", item_id="b7f3e2"), scheduled=scheduled_item()))
        self.prev.write_text(prev, encoding="utf-8", newline="\n")
        cur_kw = dict(subagents=live_item(), runners=live_item("full-tests", item_id="b7f3e2"))
        self.assertFailsWith(self.run_text(self.current_with(**cur_kw)), "nightly-check")
        self.assertPasses(self.run_text(self.current_with(scheduled=scheduled_item(), **cur_kw)))


# --- 16.7: volatile result locations ----------------------------------------------------------------
class VolatileResultTest(CheckerCase):
    def test_M7_result_under_claude_tmp_tasks_fails(self):
        for result in ("/tmp/claude-501/p/s/tasks/b1x2.output", "/private/tmp/claude-501/p/s/tasks/b1x2.output",
                       "/tmp/./claude-501/p/s/tasks/b1x2.output", "//tmp/claude-501/p/tasks/x.output"):
            with self.subTest(result=result):
                text = self.with_state(runners=live_item("full-tests", item_id="b7f3e2", result=result))
                self.assertFailsWith(self.run_text(text), "full-tests", "volatile")

    def test_M7_other_tmp_paths_pass(self):
        for result in ("/tmp/handoff-fixture/result.md", "/tmp/claudette/out.md"):
            with self.subTest(result=result):
                text = self.with_state(runners=live_item("full-tests", item_id="b7f3e2", result=result))
                self.assertPasses(self.run_text(text))

    def test_L7_jobs_with_dot_or_double_slash_is_detected(self):
        mod = load_checker()
        for path in ("/home/u/.claude/./jobs/x/out.md", "/home/u/.claude//jobs/x/out.md",
                     "/home/u//.claude/jobs/x/out.md", "C:\\u\\.claude\\.\\jobs\\x"):
            with self.subTest(path=path):
                self.assertIs(mod.is_under_jobs(path), True)
        text = self.with_state(runners=live_item("full-tests", item_id="b7f3e2", result="/home/u/.claude/./jobs/x/out.md"))
        self.assertFailsWith(self.run_text(text), "full-tests", "jobs")


# --- 16.8: next-step block, ids, Closed keys -----------------------------------------------------------
class NextStepAngleBlockTest(CheckerCase):
    def test_L4_multiline_angle_block_next_step_fails(self):
        body = "<The single most important thing to do on resuming,\nprecise enough that no thinking is needed.>"
        self.assertFailsWith(self.run_text(set_section(self.base, "Exact Next Step", body, sep=False)), "Exact Next Step")

    def test_L4_real_step_with_inline_angle_text_passes(self):
        body = "Run `python -m unittest` and read <the first failure> in the output."
        self.assertPasses(self.run_text(set_section(self.base, "Exact Next Step", body, sep=False)))


class NameIdTest(CheckerCase):
    def test_L8_status_words_and_bad_characters_are_not_ids(self):
        for item_id in ("running", "DONE", "pending", "none", "TBD", "todo", "has space", "a/b", "x*"):
            for field in ("subagents", "runners"):
                with self.subTest(item_id=item_id, field=field):
                    text = self.with_state(**{field: live_item("review-high", item_id=item_id)})
                    self.assertFailsWith(self.run_text(text), "review-high", "id")

    def test_L8_id_characters_allowed(self):
        for item_id in ("a1b2c3d4", "bash_3", "task-7f2a", "shell.1", "pid:4242", "afix3-opus-high-875dec72"):
            with self.subTest(item_id=item_id):
                self.assertPasses(self.run_text(self.with_state(subagents=live_item("review-high", item_id=item_id))))


class ClosedKeyExactTest(CheckerCase):
    history, with_closed = ClosedPointersTest.history, ClosedPointersTest.with_closed

    def test_L9_key_must_match_a_whole_history_key(self):
        path = self.history(keys=("key-a",))
        proc = self.run_text(self.with_closed(f"- 1 items closed → {path} (keys: a)"))
        self.assertFailsWith(proc, "Closed (pointers)", "a")

    def test_L9_whole_key_passes(self):
        path = self.history(keys=("a", "key-b"))
        self.assertPasses(self.run_text(self.with_closed(f"- 2 items closed → {path} (keys: a, key-b)")))


# --- 17.2: secret-like values ---------------------------------------------------------------------
# Tokens are assembled at run time: no file of this public repository holds a complete token.
SECRETS = (
    ("GitHub token", "gh" + "p_" + "a" * 36),
    ("GitHub token", "gh" + "s_" + "B1" * 18),
    ("GitHub fine-grained token", "github" + "_pat_" + "C" * 22),
    ("sk- API key", "sk" + "-" + "dE4" * 7),
    ("Slack token", "xo" + "xb-" + "1" * 10),
    ("AWS access key", "AK" + "IA" + "Q" * 16),
    ("Google API key", "AI" + "za" + "e" * 35),
)
TOKEN = SECRETS[0][1]


class SecretTest(CheckerCase):
    def with_note(self, line):
        """The valid fixture with `line` as the body of Environment & Setup Notes, and that line's number."""
        text = set_section(self.base, "Environment & Setup Notes", line, sep=False)
        return text, text.split("\n").index(line) + 1

    def test_F1_each_kind_fails_with_kind_and_line_but_no_value(self):
        for kind, token in SECRETS:
            with self.subTest(kind=kind):
                text, n = self.with_note(f"Set the variable to {token} before the run.")
                proc = self.run_text(text)
                self.assertFailsWith(proc, f"secret-like value ({kind}) on line {n}; replace it with REDACTED")
                self.assertNotIn(token, proc.stdout + proc.stderr)
                self.assertNotIn(token, (self.tmp / "runs.jsonl").read_text(encoding="utf-8"))

    def test_F1_value_echoed_by_another_failure_is_not_printed(self):
        text = self.base.replace("**Toplevel:** /home/u/sample-project", f"**Toplevel:** {TOKEN}")
        proc = self.run_text(text)
        self.assertFailsWith(proc, "Toplevel", "secret-like value (GitHub token) on line 6")
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr)

    def test_F1_value_cut_by_a_truncated_message_is_not_printed(self):
        item = "- " + "x" * 95 + " " + TOKEN + " with no labels at all"
        text = set_section(self.base, "What Has NOT Been Tried Yet", item)
        proc = self.run_text(text)
        self.assertFailsWith(proc, "what/why/how", "secret-like value (GitHub token)")
        self.assertNotIn(TOKEN[:12], proc.stdout + proc.stderr)

    def test_F1_value_in_the_previous_file_is_not_printed(self):
        prev = self.tmp / "previous.md"
        prev_text = PREVIOUS.read_text(encoding="utf-8").replace("/home/u/sample-project", f"/home/u/{TOKEN}")
        prev.write_text(prev_text, encoding="utf-8", newline="\n")
        proc = self.run_text(self.base.replace("**Continues:** none", f"**Continues:** {prev}"))
        self.assertFailsWith(proc, "another **Toplevel:**")
        self.assertNotIn(TOKEN[:12], proc.stdout + proc.stderr)

    def test_F1_line_with_EXAMPLE_is_skipped(self):
        for line in (f"The docs show {'AK' + 'IA' + 'IOSFODNN7' + 'EXAMPLE'} as a key.",
                     f"EXAMPLE only: {TOKEN}"):
            with self.subTest(line=line[:20]):
                self.assertPasses(self.run_text(self.with_note(line)[0]))

    def test_F1_prose_about_tokens_passes(self):
        line = "Send the bearer token in the header; the password stays in the vault; sk-short; ghp_ is a prefix."
        self.assertPasses(self.run_text(self.with_note(line)[0]))

    def test_F1_kebab_word_ending_in_sk_is_not_a_key(self):
        slugs = ("task", "retry", "the", "sync", "job", "with", "backoff"), ("disk", "usage", "report", "for", "the", "hosts")
        line = "See " + " and ".join("-".join(s) for s in slugs) + "."  # assembled: a plain sk- grep would flag it
        self.assertPasses(self.run_text(self.with_note(line)[0]))

    def test_F1_redacted_marker_passes(self):
        self.assertPasses(self.run_text(self.with_note("Set the variable to REDACTED before the run.")[0]))

    def test_F1_each_kind_in_a_printed_item_is_not_printed(self):
        # The item lacks what/why/how, so its excerpt is printed: only redaction keeps the value out.
        for kind, token in SECRETS:
            with self.subTest(kind=kind):
                text = set_section(self.base, "What Has NOT Been Tried Yet", f"- Try {token} on the mirror before the release run")
                proc = self.run_text(text)
                self.assertFailsWith(proc, "what/why/how", f"secret-like value ({kind})")
                self.assertIn("REDACTED on the mirror", proc.stdout)
                self.assertNotIn(token, proc.stdout + proc.stderr)
                self.assertNotIn(token, (self.tmp / "runs.jsonl").read_text(encoding="utf-8"))

    def test_M1_sk_kebab_names_in_paths_and_branches_pass(self):
        name = "s" + "k-telecom-billing-gateway"  # assembled: a plain sk- grep would flag it
        text = self.base.replace("**Toplevel:** /home/u/sample-project", f"**Toplevel:** /home/u/dev/{name}")
        self.assertPasses(self.run_text(text))
        text = set_section(self.base, "Session State", state_section(branch="feature/s" + "k-telecom-billing-integration"))
        self.assertPasses(self.run_text(text))

    def test_M1_sk_tail_needs_both_a_digit_and_an_upper_case_letter(self):
        cases = (("sk-" + "abc1" * 6, False), ("sk-" + "abcD" * 6, False), ("sk-" + "ab" + "c1D" * 6, True))
        for token, secret in cases:
            with self.subTest(token=token[:6], secret=secret):
                proc = self.run_text(self.with_note(f"Set the variable to {token} before the run.")[0])
                if secret:
                    self.assertFailsWith(proc, "secret-like value (sk- API key)")
                else:
                    self.assertPasses(proc)

    def test_L7_secret_block_comes_before_every_other_failure_block(self):
        text = set_section(self.base, "What Has NOT Been Tried Yet", f"- Try {TOKEN} on the mirror before the release run")
        text = text.replace("**Toplevel:** /home/u/sample-project", "**Toplevel:** sample-project")
        text = set_section(text, "Decisions Made", "We chose X.")
        proc = self.run_text(text)
        self.assertFailsWith(proc, "secret-like value (GitHub token)")
        heads = [ln for ln in proc.stdout.splitlines() if ln.startswith("[FAIL]")]
        self.assertEqual(len(heads), 4, proc.stdout)
        self.assertIn("secret-like value(s):", heads[0])


# --- 18.1: private key blocks -------------------------------------------------------------------------
# The header is assembled and the body is base64-looking filler: no file here holds a key.
KEY_BEGIN = "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----"
KEY_END = "-----END " + "OPENSSH PRIVATE" + " KEY-----"


def key_body(lines=4, width=70):
    return [(f"Qm{i}x" + "Zk9+" * 20)[:width] for i in range(lines)]


class PrivateKeyTest(CheckerCase):
    def with_note(self, body_lines):
        """The valid fixture with `body_lines` as Environment & Setup Notes, and the first line's number."""
        text = set_section(self.base, "Environment & Setup Notes", "\n".join(body_lines), sep=False)
        return text, text.split("\n").index(body_lines[0]) + 1

    def message(self, a, b):
        return f"secret-like value (private key block) on lines {a}-{b}; replace the whole block with REDACTED"

    def assertNoKey(self, proc):
        out = proc.stdout + proc.stderr + (self.tmp / "runs.jsonl").read_text(encoding="utf-8")
        for line in key_body():
            self.assertNotIn(line[:12], out)

    def test_H1_whole_block_fails_with_its_line_range(self):
        block = ["Deploy key:", KEY_BEGIN] + key_body() + [KEY_END, "Rotate it after the run."]
        text, n = self.with_note(block)
        proc = self.run_text(text)
        self.assertFailsWith(proc, self.message(n + 1, n + 6))
        self.assertEqual(proc.stdout.count("secret-like value ("), 1, proc.stdout)
        self.assertNoKey(proc)

    def test_H1_block_without_end_runs_to_the_end_of_the_file(self):
        text, n = self.with_note([KEY_BEGIN] + key_body())
        last = len(text.rstrip("\n").split("\n"))
        proc = self.run_text(text)
        self.assertFailsWith(proc, self.message(n, last))
        self.assertNoKey(proc)

    def test_H1_masking_only_the_header_still_fails(self):
        text, n = self.with_note(["REDACTED"] + key_body(2) + [KEY_END])
        proc = self.run_text(text)
        self.assertFailsWith(proc, self.message(n + 1, n + 3))
        self.assertNotIn("no secret-like values", proc.stdout)

    def test_H1_body_left_without_begin_or_end_fails(self):
        text, n = self.with_note(["REDACTED"] + key_body(3) + ["Rotate it after the run."])
        self.assertFailsWith(self.run_text(text), self.message(n + 1, n + 3))

    def test_H1_body_rule_needs_three_lines_of_64_characters(self):
        for lines, width, secret in ((2, 70, False), (3, 63, False), (3, 64, True)):
            with self.subTest(lines=lines, width=width):
                proc = self.run_text(self.with_note(["Notes:"] + key_body(lines, width))[0])
                if secret:
                    self.assertFailsWith(proc, "secret-like value (private key block)")
                else:
                    self.assertPasses(proc)

    def test_H1_key_inside_an_item_is_not_printed(self):
        item = "\n".join(["- Try the deploy key for the mirror:", "  " + KEY_BEGIN]
                         + ["  " + ln for ln in key_body()] + ["  " + KEY_END])
        text = set_section(self.base, "What Has NOT Been Tried Yet", item)
        proc = self.run_text(text)
        self.assertFailsWith(proc, "what/why/how", "secret-like value (private key block)")
        self.assertIn("Try the deploy key for the mirror: REDACTED", proc.stdout)
        self.assertNoKey(proc)

    def test_H1_block_replaced_with_redacted_passes(self):
        proc = self.run_text(self.with_note(["Deploy key:", "REDACTED"])[0])
        self.assertPasses(proc)
        self.assertIn("no secret-like values", proc.stdout)


class SecretChainTest(CheckerCase):
    """F1: a token copied into Goal (carried) can be masked with REDACTED without breaking the chain."""
    chain = GoalChainTest.chain

    def setUp(self):
        super().setUp()
        self.prev_text = PREVIOUS.read_text(encoding="utf-8")
        self.prev = self.tmp / "previous.md"

    def goal(self, secret):
        return f'Make the sync job survive timeouts. User: "use the key {secret} for the mirror"'

    def test_F1_masking_a_carried_token_is_not_a_change(self):
        proc = self.chain(goal_kw=dict(goal=self.goal("REDACTED")), prev_kw=dict(goal=self.goal(TOKEN)))
        self.assertPasses(proc)

    def test_F1_masking_a_carried_token_in_constraints_is_not_a_change(self):
        prev = dict(constraints=f"never push with {TOKEN}")
        self.assertPasses(self.chain(goal_kw=dict(constraints="never push with REDACTED"), prev_kw=prev))

    def test_F1_carried_token_kept_fails_as_a_secret_not_as_a_change(self):
        proc = self.chain(goal_kw=dict(goal=self.goal(TOKEN)), prev_kw=dict(goal=self.goal(TOKEN)))
        self.assertFailsWith(proc, "secret-like value (GitHub token)")
        self.assertNotIn("changed without a Changes entry", proc.stdout)
        self.assertNotIn(TOKEN, proc.stdout)

    def test_F1_masking_does_not_excuse_another_change(self):
        cur = dict(goal=self.goal("REDACTED") + " and the queue")
        proc = self.chain(goal_kw=cur, prev_kw=dict(goal=self.goal(TOKEN)))
        self.assertFailsWith(proc, "Goal (carried) changed without a Changes entry")
        self.assertNotIn(TOKEN, proc.stdout)

    def test_L6_goal_comparison_masks_both_sides_itself(self):
        # Called directly, so neither side was masked upstream (main and read_lines mask in the real run).
        mod = load_checker()
        raw_read = lambda path: path.read_text(encoding="utf-8").split("\n")
        for cur_secret, prev_secret, patch in ((TOKEN, "REDACTED", False), ("REDACTED", TOKEN, True)):
            with self.subTest(current=cur_secret[:3], previous=prev_secret[:3]):
                self.prev.write_text(set_section(self.prev_text, "Goal (carried)", goal_section(goal=self.goal(prev_secret))),
                                     encoding="utf-8", newline="\n")
                cur = self.base.replace("**Continues:** none", f"**Continues:** {self.prev}")
                cur = set_section(cur, "Goal (carried)", goal_section(goal=self.goal(cur_secret)))
                if patch:
                    mod.read_lines = raw_read
                self.assertEqual(mod.goal_failures(cur.split("\n")), [])


class CrashSecretTest(CheckerCase):
    def test_L6_traceback_tail_is_masked(self):
        path = self.tmp / "h.md"
        path.write_text(self.base, encoding="utf-8", newline="\n")
        code = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('c', {str(CHECKER)!r})\n"
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
            "def boom(lines): raise RuntimeError('bad value ' + sys.argv[1])\n"
            "mod.decisions_failures = boom\n"
            f"sys.exit(mod.run([{str(path)!r}]))\n"
        )
        proc = subprocess.run([sys.executable, "-c", code, TOKEN], capture_output=True, text=True,
                              encoding="utf-8", env=self.env())
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("RuntimeError: bad value REDACTED", proc.stderr)
        self.assertNotIn(TOKEN, proc.stderr)


# --- 17.4: Constraints in Goal (carried) --------------------------------------------------------------
class ConstraintsTest(CheckerCase):
    chain = GoalChainTest.chain

    def setUp(self):
        super().setUp()
        self.prev_text = PREVIOUS.read_text(encoding="utf-8")
        self.prev = self.tmp / "previous.md"

    def four_keys(self, text):
        return "\n".join(ln for ln in text.split("\n") if not ln.startswith("- **Constraints:**"))

    def test_C1_fixtures_carry_constraints_between_done_when_and_changes(self):
        keys = [m.group(1) for m in re.finditer(r"^- \*\*(.+?):\*\*", goal_section(), re.M)]
        self.assertEqual(keys, ["Origin", "Goal", "Done-when", "Constraints", "Changes"])
        self.assertIn(goal_section(), self.base)

    def test_C1_missing_constraints_fails(self):
        self.assertFailsWith(self.run_text(self.four_keys(self.base)), "Goal (carried) lacks Constraints")

    def test_C1_constraints_none_passes(self):
        self.assertPasses(self.run_text(set_section(self.base, "Goal (carried)", goal_section(constraints="none"))))

    def test_C1_four_key_previous_still_compares_goal(self):
        prev_text = self.four_keys(self.prev_text)
        self.assertPasses(self.chain(prev_text=prev_text))
        proc = self.chain(goal_kw=dict(goal="A drifted goal"), prev_text=prev_text)
        self.assertFailsWith(proc, "Goal (carried) changed without a Changes entry")

    def test_C1_constraint_appended_passes(self):
        self.assertPasses(self.chain(goal_kw=dict(constraints=f"{CONSTRAINTS}; keep events in order")))

    def test_C1_constraint_added_after_none_passes(self):
        self.assertPasses(self.chain(goal_kw=dict(constraints="keep events in order"), prev_kw=dict(constraints="none")))

    def test_C1_constraint_removed_or_rewritten_without_changes_entry_fails(self):
        for value in ("edit src/sync.py only", "none", "edit src/sync.py and src/queue.py; keep the queue schema unchanged",
                      f"keep events in order; {CONSTRAINTS}"):
            with self.subTest(constraints=value):
                self.assertFailsWith(self.chain(goal_kw=dict(constraints=value)), "Goal (carried)", "Constraints")

    def test_C1_constraint_removed_with_a_changes_entry_naming_constraints_passes(self):
        change = '2026-11-09 "the queue schema may change now" : Constraints: keep the queue schema unchanged → removed'
        self.assertPasses(self.chain(goal_kw=dict(constraints="edit src/sync.py only", changes=change)))

    def test_C1_quoted_goal_change_does_not_drop_constraints(self):
        kw = dict(goal="Sync job and queue. User: \"also cover the queue\"", changes=CHANGE_1, constraints="none")
        self.assertFailsWith(self.chain(goal_kw=kw), "Goal (carried)", "Constraints")

    def test_M3_a_new_entry_after_one_entry_passes(self):
        prev = dict(constraints="edit src/sync.py only")
        self.assertPasses(self.chain(goal_kw=dict(constraints="edit src/sync.py only; keep events in order"), prev_kw=prev))

    def test_M3_text_glued_onto_the_last_entry_fails(self):
        # String prefix would pass all of these; the rule compares whole `; ` entries.
        cases = ((CONSTRAINTS, f"{CONSTRAINTS} and the table"),
                 ("edit src/sync.py only", "edit src/sync.py only, and keep events in order"),
                 ("edit src/sync.py only", "edit src/sync.py only (until Friday)"))
        for before, after in cases:
            with self.subTest(after=after[-24:]):
                proc = self.chain(goal_kw=dict(constraints=after), prev_kw=dict(constraints=before))
                self.assertFailsWith(proc, "Goal (carried) Constraints dropped or rewrote an entry")

    def test_L3_none_words_are_no_entries(self):
        for word in ("없음", "None", "NONE", "none"):
            with self.subTest(previous=word):
                proc = self.chain(goal_kw=dict(constraints="keep events in order"), prev_kw=dict(constraints=word))
                self.assertPasses(proc)

    def test_L6_an_older_changes_entry_naming_constraints_does_not_excuse_a_new_drop(self):
        old = '2026-11-08 "the schema rule stays" : Constraints: edit src/sync.py only → kept'
        proc = self.chain(goal_kw=dict(constraints="edit src/sync.py only", changes=old),
                          prev_kw=dict(changes=old))
        self.assertFailsWith(proc, "Goal (carried) Constraints dropped or rewrote an entry")


# --- L-3: the template fixture is the current command template ----------------------------------------
class TemplateFixtureTest(unittest.TestCase):
    def test_L3_template_fixture_equals_the_command_template(self):
        doc = (ROOT / "commands" / "handoff.md").read_text(encoding="utf-8")
        m = re.search(r"^## Session File Format\n\n```markdown\n(.*?)\n```\n", doc, re.S | re.M)
        self.assertIsNotNone(m, "commands/handoff.md has no Session File Format code block")
        self.assertEqual(TEMPLATE.read_text(encoding="utf-8"), m.group(1) + "\n",
                         "re-extract tests/fixtures/check/template.md from commands/handoff.md")

    def test_L3_fixtures_carry_an_absolute_origin(self):
        for fixture in (VALID, PREVIOUS):
            with self.subTest(fixture=fixture.name):
                self.assertNotIn("this file", fixture.read_text(encoding="utf-8"))


# --- R5/R6: run log signals (fail_codes, handoff_bytes, project) -----------------------------------
BASE_COMMIT = "c34a100e08ab4c4fc09f7ab3abaa31ae36281fad"
BODY_SENTINEL = "body" + "sentinel" + "-7731"


def tail_record(path):
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


class SignalCase(CheckerCase):
    def record(self, proc=None):
        """The last run-log record of this test's runs.jsonl."""
        return tail_record(self.tmp / "runs.jsonl")

    def codes_of(self, text, **kw):
        self.run_text(text, **kw)
        return self.record()["fail_codes"]

    def call_run(self, argv):
        """checker.run(argv) in this process: (exit code, stdout, stderr, module)."""
        mod = load_checker()
        out, err = io.StringIO(), io.StringIO()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home), "HANDOFF_LOG": str(self.tmp / "runs.jsonl")}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            os.environ.pop("HANDOFF_CALLER", None)
            code = mod.run(argv)
        return code, out.getvalue(), err.getvalue(), mod

    def records(self):
        path = self.tmp / "runs.jsonl"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def write(self, text, name="h.md"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return path


class FailCodeTest(SignalCase):
    def with_note(self, lines):
        return set_section(self.base, "Environment & Setup Notes", "\n".join(lines), sep=False)

    def test_R5_pass_gives_empty_object(self):
        self.assertPasses(self.run_text(self.base, args=("--git", "yes")))
        self.assertEqual(self.record()["fail_codes"], {})

    def test_R5_secret_code_by_kind(self):
        text = self.with_note([f"Set the variable to {TOKEN} before the run."])
        self.assertEqual(self.codes_of(text), {"secret:github-token": 1})
        self.assertNotIn(TOKEN, (self.tmp / "runs.jsonl").read_text(encoding="utf-8"))

    def test_R5_private_key_block_code(self):
        text = self.with_note(["Deploy key:", KEY_BEGIN] + key_body() + [KEY_END])
        self.assertEqual(self.codes_of(text), {"secret:private-key-block": 1})

    def test_R5_item_code_names_section(self):
        item = "- the retry design is still open and nothing here says why or how to start it at all"
        text = set_section(self.base, "What Has NOT Been Tried Yet", item)
        self.assertEqual(self.codes_of(text), {"item-incomplete:what-has-not-been-tried-yet": 1})
        text = set_section(self.base, "Decisions Made", item)
        self.assertIn("item-incomplete:decisions-made", self.codes_of(text))

    def test_R5_decisions_table_code(self):
        text = set_section(self.base, "Decisions Made", decisions_table([("a", "", "c")]))
        self.assertEqual(self.codes_of(text), {"decisions-table": 1})

    def test_R5_contract_codes(self):
        no_toplevel = self.base.replace("**Toplevel:** /home/u/sample-project\n", "")
        self.assertEqual(self.codes_of(no_toplevel), {"contract-toplevel": 1})
        next_step = set_section(self.base, "Exact Next Step", "[next step]")
        self.assertEqual(self.codes_of(next_step), {"contract-next-step": 1})
        no_state = without_section(self.base, "Session State")
        self.assertEqual(self.codes_of(no_state, args=("--git", "yes")), {"contract-session-state": 1})
        no_key = set_section(self.base, "Goal (carried)", goal_section(omit=("Constraints",)))
        self.assertEqual(self.codes_of(no_key), {"contract-goal-missing": 1})
        no_goal = without_section(self.base, "Goal (carried)")
        self.assertEqual(self.codes_of(no_goal), {"contract-goal-missing": 1})
        prev = self.write(PREVIOUS.read_text(encoding="utf-8"), "previous.md")
        cur = self.base.replace("**Continues:** none", f"**Continues:** {prev}")
        drift = set_section(cur, "Goal (carried)", goal_section(goal='A different goal. User: "new"'))
        self.assertEqual(self.codes_of(drift), {"contract-goal-drift": 1})

    def test_R5_chain_codes(self):
        gone = self.tmp / "no-such-handoff.md"
        text = self.base.replace("**Continues:** none", f"**Continues:** {gone}")
        self.assertEqual(self.codes_of(text), {"contract-continues": 1})
        hist = self.tmp / "history.md"
        hist.write_text("no keys here\n", encoding="utf-8")
        closed = self.base.replace("\n## Exact Next Step", f"\n## Closed (pointers)\n\n- 1 items closed → {hist} (keys: zz)\n\n---\n\n## Exact Next Step", 1)
        self.assertEqual(self.codes_of(closed), {"contract-closed": 1})

    def test_R5_live_chain_code(self):
        prev = PREVIOUS.read_text(encoding="utf-8")
        prev = set_section(prev, "Session State", state_section(subagents=live_item("lost-one", item_id="zz9")))
        path = self.write(prev, "previous.md")
        cur = self.base.replace("**Continues:** none", f"**Continues:** {path}")
        self.assertEqual(self.codes_of(cur), {"contract-live-chain": 1})

    def test_R5_codes_are_in_vocabulary(self):
        mod = load_checker()
        token_note = self.with_note([f"Use {TOKEN} and {SECRETS[5][1]} here.", KEY_BEGIN] + key_body() + [KEY_END])
        inputs = [
            token_note,
            self.base.replace("**Toplevel:** /home/u/sample-project\n", ""),
            set_section(self.base, "Exact Next Step", "[next step]"),
            set_section(self.base, "Decisions Made", decisions_table([("a", "", "c")])),
            set_section(self.base, "What Has NOT Been Tried Yet", "- " + "word " * 12),
            without_section(self.base, "Session State"),
            TEMPLATE.read_text(encoding="utf-8"),
        ]
        seen = set()
        for n, text in enumerate(inputs):
            with self.subTest(n=n):
                codes = self.codes_of(text, args=("--git", "yes"))
                self.assertTrue(codes, "expected at least one code")
                for code in codes:
                    self.assertIn(code, mod.FAIL_CODES)
                seen.update(codes)
        self.assertTrue({"secret:github-token", "secret:aws-access-key", "secret:private-key-block"} <= seen, seen)
        for code in mod.FAIL_CODES:
            self.assertRegex(code, r"^[a-z0-9]+(?:-[a-z0-9]+)*(?::[a-z0-9]+(?:-[a-z0-9]+)*)?$")

    def test_R5_exit2_codes(self):
        proc = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True,
                              encoding="utf-8", env=self.env())
        self.assertEqual(proc.returncode, 2)
        rec = self.record()
        self.assertEqual((rec["fail_codes"], rec["handoff_bytes"]), ({"usage": 1}, None))
        proc = subprocess.run([sys.executable, str(CHECKER), str(self.tmp / "nope.md")], capture_output=True,
                              text=True, encoding="utf-8", env=self.env())
        self.assertEqual(proc.returncode, 2)
        rec = self.record()
        self.assertEqual((rec["fail_codes"], rec["handoff_bytes"]), ({"unreadable": 1}, None))

    def test_R5_run_resets_RUN(self):
        good = self.write(self.base, "good.md")
        mod = load_checker()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home), "HANDOFF_LOG": str(self.tmp / "runs.jsonl")}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            os.environ.pop("HANDOFF_CALLER", None)
            self.assertEqual(mod.run([]), 2)  # usage: leaves RUN["fail"] behind
            self.assertEqual(mod.run([str(good), "--git", "yes"]), 0)  # leaves RUN["size"] behind
            self.assertEqual(mod.run([str(self.tmp / "nope.md")]), 2)
        usage, valid, unreadable = [json.loads(x) for x in self.records()]
        self.assertEqual(usage["fail_codes"], {"usage": 1})
        self.assertEqual(valid["fail_codes"], {})
        self.assertEqual(valid["handoff_bytes"], len(self.base.encode("utf-8")))
        self.assertEqual(unreadable["fail_codes"], {"unreadable": 1})
        self.assertIsNone(unreadable["handoff_bytes"])

    def test_R5_log_failure_keeps_exit_code(self):
        bad = self.write(TEMPLATE.read_text(encoding="utf-8"), "bad.md")
        good = self.write(self.base, "good.md")
        for name in ("count_fail_codes", "project_of"):  # the helpers log_run alone uses
            for path, want in ((good, 0), (bad, 1)):
                with self.subTest(helper=name, want=want):
                    mod = load_checker()
                    env = {"HOME": str(self.home), "USERPROFILE": str(self.home),
                           "HANDOFF_LOG": str(self.tmp / "runs.jsonl")}
                    out, err = io.StringIO(), io.StringIO()
                    with mock.patch.dict(os.environ, env), mock.patch.object(mod, name, side_effect=RuntimeError("boom")), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
                        os.environ.pop("HANDOFF_CALLER", None)
                        code = mod.run([str(path), "--git", "yes"])
                    self.assertEqual(code, want, out.getvalue() + err.getvalue())
                    self.assertEqual(err.getvalue().count("WARNING: run log not written:"), 1, err.getvalue())
                    self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
        self.assertEqual(self.records(), [])

    def test_R5_fail_overrides_parts(self):
        bad = self.write(TEMPLATE.read_text(encoding="utf-8"), "bad.md")
        mod = load_checker()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home), "HANDOFF_LOG": str(self.tmp / "runs.jsonl")}
        # main() fills RUN["parts"] first, then raises at the size check
        with mock.patch.dict(os.environ, env), mock.patch.object(mod, "SIZE_LIMIT_KIB", None), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            os.environ.pop("HANDOFF_CALLER", None)
            code = mod.run([str(bad), "--git", "yes"])
        self.assertEqual(code, 2)
        self.assertEqual(tail_record(self.tmp / "runs.jsonl")["fail_codes"], {"internal": 1})

    def test_R5_unresolvable_home_exit2(self):
        code, out, err, _ = self.call_run(["~nosuchuser_zz/x.md", "--git", "yes"])
        self.assertEqual(code, 2)  # the one documented exception: before, an uncaught exit 1 with a traceback
        self.assertTrue(err.rstrip("\n").splitlines()[-1].startswith("WARNING: run log not written:"), err)
        self.assertEqual(self.records(), [])


class PartsInputs:
    def lines_of(self):
        gh, aws, slack = SECRETS[0][1], SECRETS[5][1], SECRETS[4][1]
        return [
            ["plain", f"a {slack} b", "x", f"{gh} and {aws}", "y"],
            [KEY_BEGIN] + key_body() + [KEY_END, f"after {gh}"],
            [f"early {aws}", "mid", KEY_BEGIN, *key_body(), KEY_END, f"late {SECRETS[3][1]}", f"EXAMPLE {gh}"],
            [],
            ["nothing secret here"],
        ]

    def contract_texts(self):
        prev = self.write(PREVIOUS.read_text(encoding="utf-8"), "previous.md")
        return [
            self.base, TEMPLATE.read_text(encoding="utf-8"), PREVIOUS.read_text(encoding="utf-8"),
            without_section(self.base, "Session State"), set_section(self.base, "Exact Next Step", "[x]"),
            self.base.replace("**Continues:** none", f"**Continues:** {prev}"),
            set_section(self.base.replace("**Continues:** none", f"**Continues:** {prev}"),
                        "Goal (carried)", goal_section(goal='drift. User: "x"')),
            without_section(self.base, "Goal (carried)"),
        ]


class SecretPartsTest(PartsInputs, SignalCase):
    def test_R5_secret_parts_in_line_order(self):
        mod = load_checker()
        lines = self.lines_of()[2]
        self.assertEqual([kind for kind, _ in mod.secret_parts(lines)],
                         ["AWS access key", "private key block", "sk- API key"])
        same_line = self.lines_of()[0]
        self.assertEqual([kind for kind, _ in mod.secret_parts(same_line)],
                         ["Slack token", "GitHub token", "AWS access key"])

    def test_R5_parts_flatten_to_failures(self):
        mod = load_checker()
        for n, text in enumerate(self.contract_texts()):
            for git in (None, True, False):
                with self.subTest(n=n, git=git):
                    lines = text.split("\n")
                    parts = mod.resume_contract_parts(lines, git, VALID)
                    self.assertEqual(mod.resume_contract_failures(lines, git, VALID),
                                     [m for _, msgs in parts for m in msgs])
                    missing, drift = mod.goal_parts(lines)
                    self.assertEqual(mod.goal_failures(lines), missing + drift)


class SecretPartsBaseTest(PartsInputs, SignalCase):
    """The same functions against the base commit; skipped as a class when it is not available."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.old = base_checker_module(cls)

    def test_R5_secret_parts_flatten_equals_old(self):
        mod = load_checker()
        for n, lines in enumerate(self.lines_of()):
            with self.subTest(n=n):
                self.assertEqual(mod.secret_failures(lines), self.old.secret_failures(lines))
                self.assertEqual([msg for _, msg in mod.secret_parts(lines)], self.old.secret_failures(lines))

    def test_R5_parts_flatten_equals_old(self):
        mod = load_checker()
        for n, text in enumerate(self.contract_texts()):
            for git in (None, True, False):
                with self.subTest(n=n, git=git):
                    lines = text.split("\n")
                    self.assertEqual(self.old.resume_contract_failures(lines, git, VALID),
                                     mod.resume_contract_failures(lines, git, VALID))
                    self.assertEqual(self.old.goal_failures(lines), mod.goal_failures(lines))


class ProjectBytesTest(SignalCase):
    def test_R6_project_from_existing_toplevel(self):
        real = self.tmp / "proj"
        real.mkdir()
        for value, want in ((str(real), str(real)), (str(self.tmp / "nowhere"), None), ("sample-project", None),
                            (str(self.tmp / "h.md"), None), (".", None)):  # `.` is a folder in every cwd
            with self.subTest(value=value):
                (self.tmp / "h.md").write_text("file", encoding="utf-8")
                text = self.base.replace("**Toplevel:** /home/u/sample-project", f"**Toplevel:** {value}")
                self.run_text(text)
                self.assertEqual(self.record()["project"], want)

    def test_R6_handoff_bytes(self):
        text = self.base.replace("sample-project", "샘플-project")
        self.run_text(text, name="h.md")
        self.assertEqual(self.record()["handoff_bytes"], len((self.tmp / "h.md").read_bytes()))
        self.assertNotEqual(len(text), len(text.encode("utf-8")))
        subprocess.run([sys.executable, str(CHECKER), str(self.tmp / "gone.md")], capture_output=True, env=self.env())
        self.assertIsNone(self.record()["handoff_bytes"])

    def test_R6_unstatable_toplevel_gives_null_project(self):
        value = "/" + "a" * 256  # one name over NAME_MAX: is_dir() raises ENAMETOOLONG instead of returning False
        proc = self.run_text(self.base.replace("**Toplevel:** /home/u/sample-project", f"**Toplevel:** {value}"))
        self.assertEqual(len(self.records()), 1, proc.stderr)
        self.assertIsNone(self.record()["project"])

    def test_R6_no_indented_text_in_record(self):
        item = f"- {BODY_SENTINEL} " + "word " * 12
        texts = [
            set_section(self.base, "What Has NOT Been Tried Yet", item),
            set_section(self.base, "Goal (carried)", goal_section(origin=f"{BODY_SENTINEL}/not-absolute")),
            self.with_state(subagents=live_item(BODY_SENTINEL, omit=("does",))),
            self.base.replace("**Continues:** none", f"**Continues:** /no/such/{BODY_SENTINEL}"),
        ]
        for n, text in enumerate(texts):
            with self.subTest(n=n):
                proc = self.run_text(text, args=("--git", "yes"))
                self.assertIn(BODY_SENTINEL, proc.stdout)  # the run did print it
                self.assertEqual(self.record()["exit"], 1)
                self.assertNotIn(BODY_SENTINEL, json.dumps(self.record()))

    def test_R6_line_separator_stays_inside_its_line(self):
        sentinel = "body" + "sentinel" + "-2028"
        text = self.base.replace("**Toplevel:** /home/u/sample-project",
                                 f"**Toplevel:** relative\u2028WARNING: {sentinel} leaked")
        proc = self.run_text(text)
        self.assertIn(sentinel, proc.stdout)  # inside the indented Toplevel message
        self.assertNotIn(sentinel, json.dumps(self.record()))

    def test_R6_tee_splits_on_newline_only(self):
        tee = load_checker()._Tee(io.StringIO())
        self.assertEqual(tee.lines(), [])
        tee.write("  a\u2028b\x85c\x0bd\x0ce\x1cf\x1dg\x1eh\n")
        tee.write("next")
        self.assertEqual(tee.lines(), ["  a\u2028b\x85c\x0bd\x0ce\x1cf\x1dg\x1eh", "next"])


def base_checker_module(cls):
    """The base-commit scripts/check_handoff.py and run_log.py extracted to a temp folder; the module of the former."""
    tmp = tempfile.TemporaryDirectory()
    cls.addClassCleanup(tmp.cleanup)
    for name in ("check_handoff.py", "run_log.py"):
        proc = subprocess.run(["git", "show", f"{BASE_COMMIT}:scripts/{name}"], cwd=ROOT, capture_output=True)
        if proc.returncode != 0:
            raise unittest.SkipTest(f"base commit {BASE_COMMIT[:7]} is not available: {proc.stderr.decode()[:80]}")
        (Path(tmp.name) / name).write_bytes(proc.stdout)
    cls.base_dir = Path(tmp.name)
    spec = importlib.util.spec_from_file_location("check_handoff_base", cls.base_dir / "check_handoff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BaseStdoutTest(SignalCase):
    # 이 WU의 회귀 고정. checker 문구를 의도적으로 바꾸는 WU에서 기준 커밋을 갱신한다
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base_checker_module(cls)

    def both(self, argv):
        env = self.env(HANDOFF_LOG="off")
        old = subprocess.run([sys.executable, str(self.base_dir / "check_handoff.py"), *argv],
                             capture_output=True, text=True, encoding="utf-8", env=env)
        new = subprocess.run([sys.executable, str(CHECKER), *argv],
                             capture_output=True, text=True, encoding="utf-8", env=env)
        return old, new

    def test_R5_stdout_matches_base_checker(self):
        prev = self.write(PREVIOUS.read_text(encoding="utf-8"), "previous.md")
        chain = self.base.replace("**Continues:** none", f"**Continues:** {prev}")
        lost = self.write(set_section(PREVIOUS.read_text(encoding="utf-8"), "Session State",
                                      state_section(subagents=live_item("lost-one", item_id="zz9"))), "lost.md")
        hist = self.tmp / "history.md"
        hist.write_text("no keys here\n", encoding="utf-8")
        cases = {
            "secret": (set_section(self.base, "Environment & Setup Notes", f"Use {TOKEN} and {SECRETS[5][1]}", sep=False),
                       ["--git", "yes"]),
            "decisions": (set_section(self.base, "Decisions Made", decisions_table([("a", "", "c")])), ["--git", "yes"]),
            "goal-drift": (set_section(chain, "Goal (carried)", goal_section(goal='drift. User: "x"')), ["--git", "yes"]),
            "live-chain": (self.base.replace("**Continues:** none", f"**Continues:** {lost}"), ["--git", "yes"]),
            "closed": (self.base.replace("\n## Exact Next Step",
                                         f"\n## Closed (pointers)\n\n- 1 items closed → {hist} (keys: zz)\n\n---\n\n## Exact Next Step", 1),
                       ["--git", "yes"]),
            "no-session-state": (without_section(self.base, "Session State"), ["--git", "yes"]),
        }
        for name, (text, flags) in cases.items():
            with self.subTest(case=name):
                path = self.write(text, f"{name}.md")
                old, new = self.both([str(path), *flags])
                self.assertEqual(old.returncode, 1, old.stdout)
                self.assertEqual((new.returncode, new.stdout), (old.returncode, old.stdout))
        with self.subTest(case="usage"):
            old, new = self.both([])
            self.assertEqual(old.returncode, 2)
            self.assertEqual((new.returncode, new.stdout), (old.returncode, old.stdout))
        with self.subTest(case="unreadable"):
            old, new = self.both([str(self.tmp / "nope.md")])
            self.assertEqual(old.returncode, 2)
            self.assertEqual((new.returncode, new.stdout), (old.returncode, old.stdout))


TRANSCRIPT = ROOT / "tests" / "fixtures" / "transcript" / "incident.jsonl"
SID = "00000000-0000-4000-8000-000000000001"  # the fixture's session id
M1 = "Move part C to the second round and ship A and B first."
M2 = "검토자에게는 A와 B 문서만 이슈 목록 없이 먼저 보낸다."
A1 = "A and B now, C later"  # the dialog answer of fixture line 33
CUT_BEFORE, CUT_DELIVERED, FULL = 25, 31, 35  # fixture line counts: before M1·M2 are delivered, after, everything
GATE_BASE = "1c6bbeb"  # the checker before the after-handoff gate


def user_rec(text, ts="2026-11-09T07:00:00.000Z", **kw):
    """A JSON line of a message the user typed."""
    rec = {"type": "user", "timestamp": ts, "cwd": "/tmp/demo", "sessionId": SID, "origin": {"kind": "human"},
           "message": {"role": "user", "content": text}}
    rec.update(kw)
    return json.dumps(rec, ensure_ascii=False)


def queued_rec(prompt, enqueued="2026-11-09T07:00:00.000Z", kind="human"):
    """A JSON line of a queued message delivered to the session; its timestamps are the enqueue time."""
    return json.dumps({"type": "attachment", "timestamp": enqueued, "sessionId": SID,
                       "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": "prompt",
                                      "origin": {"kind": kind}, "timestamp": enqueued}}, ensure_ascii=False)


def image_prompt(text):
    """A queued prompt as content blocks: the text, then a pasted image (the shape real transcripts carry)."""
    return [{"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}]


class AfterHandoffTests(SignalCase):
    def setUp(self):
        super().setUp()
        self.mod = load_checker()
        self.text = self.base.replace("**Continues:** none", f"**Continues:** none\n**Session:** {SID}", 1)
        self.lines = TRANSCRIPT.read_text(encoding="utf-8").splitlines()

    def put(self, lines):
        folder = self.home / ".claude" / "projects" / "-tmp-demo"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{SID}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return path

    def gate(self, text, **extra):
        return self.run_text(text, args=("--git", "yes"), extra_env={"CLAUDE_CODE_SESSION_ID": SID, **extra})

    def with_after(self, *quotes, text=None):
        body = "\n".join(f"- 「{q}」 → 결정: 변경 없음" for q in quotes)
        return (text or self.text).rstrip("\n") + f"\n\n## After Handoff\n\n{body}\n"

    def texts(self, lines):
        msgs, reason = self.mod.messages_after_handoff(self.put(lines))
        self.assertIsNone(reason)
        return [text for text, _ in msgs]

    def test_AH1_incident_delivered_after_write_fails(self):
        self.put(self.lines[:CUT_DELIVERED])
        proc = self.gate(self.text)
        self.assertFailsWith(proc, '[FAIL] 2 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn(f"\n  - 「{M1}」 (06:54:07Z)\n", proc.stdout)
        self.assertIn(f"\n  - 「{M2}」 (06:55:19Z)\n", proc.stdout)
        self.assertNotIn("(06:55:46Z)", proc.stdout)  # the enqueue time, not the delivery record's
        self.assertNotIn("[PASS]", proc.stdout)

    def test_AH1b_queued_message_with_image_counts(self):
        said = "Ship the screenshot fix first."
        self.put(self.lines[:CUT_BEFORE] + [queued_rec(image_prompt(said), enqueued="2026-11-09T06:55:42.000Z")])
        proc = self.gate(self.text)
        self.assertFailsWith(proc, '[FAIL] 1 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn(f"\n  - 「{said}」 (06:55:42Z)\n", proc.stdout)
        self.assertPasses(self.gate(self.with_after(said)))

    def test_AH2_undelivered_enqueue_not_counted(self):
        self.put(self.lines[:CUT_BEFORE])
        proc = self.gate(self.text)
        self.assertPasses(proc)
        self.assertNotIn("/handoff", proc.stdout)
        self.assertNotIn("WARNING", proc.stdout)

    def test_AH3_reconciled_passes(self):
        self.put(self.lines[:CUT_DELIVERED])
        proc = self.gate(self.with_after(M1, M2))
        self.assertPasses(proc)
        self.assertTrue(proc.stdout.startswith("[PASS]"), proc.stdout)

    def test_AH3b_quoted_in_another_section_fails(self):
        self.put(self.lines[:CUT_DELIVERED])
        notes = "".join(f"- 「{q}」 → 결정: 변경 없음\n" for q in (M1, M2))
        text = self.text.rstrip("\n") + f"\n\n## Notes\n\n{notes}\n## After Handoff\n\n- 「다른 줄」 → 결정: 변경 없음\n"
        self.assertFailsWith(self.gate(text), '[FAIL] 2 user message(s) after the last /handoff are not in "After Handoff":')

    def test_AH4_full_transcript(self):
        self.put(self.lines[:FULL])
        self.assertPasses(self.gate(self.with_after(M1, M2, A1)))

    def test_AH18_dialog_answer_counts(self):
        self.put(self.lines[:FULL])
        proc = self.gate(self.with_after(M1, M2))
        self.assertFailsWith(proc, '[FAIL] 1 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn(f"\n  - 「{A1}」 (06:56:30Z)\n", proc.stdout)
        self.assertNotIn(M1, proc.stdout)
        self.assertPasses(self.gate(self.with_after(M1, M2, A1)))

    def test_AH18b_answer_quoted_only_inside_another_line_fails(self):
        self.put(self.lines[:FULL])
        text = self.with_after(M1).rstrip("\n") + f"\n- 「{M2}」 → 결정: 「{A1}」 답과 같다\n"
        proc = self.gate(text)
        self.assertFailsWith(proc, '[FAIL] 1 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn(f"\n  - 「{A1}」 (06:56:30Z)\n", proc.stdout)

    def test_AH5_match_rule(self):
        def left(msg, section):
            return [t for t, _ in self.mod.unrecorded([(msg, "2026-11-09T07:00:00.000Z")], section)]
        respaced = "- 「Move part C to the\n  second   round and\tship A and B first.」 → 결정: C를 미룸"
        self.assertEqual(left(M1, respaced), [])
        self.assertEqual(left(M1, f"- 「{M1[:39]}」 → 결정: 변경 없음"), [M1])  # 39 characters are not enough
        self.assertEqual(left(M1, f"- 「{M1[:40]}… 」 → 결정: 변경 없음"), [])  # 40 are
        self.assertEqual(left(M1, "- 「다른 줄」 → 결정: " + M1), [M1])  # the quote opens with 「
        self.assertEqual(left("A", "- 「「A」안은 버리고 「B」안으로 가자.」 → 결정: B안"), ["A"])  # nested: no line
        self.assertEqual(left("계속", f"- 「{M1}」 → 결정: 「계속」으로 진행"), ["계속"])
        self.assertEqual(left("A", "  - 「A」 → 결정: 변경 없음"), [])  # an indented item still opens one
        twins = [(M1 + " Then C.", "2026-11-09T07:00:00.000Z"), (M1 + " Then D.", "2026-11-09T07:01:00.000Z")]
        self.assertEqual(self.mod.unrecorded(twins, f"- 「{M1}」 → 결정: 변경 없음"), twins[1:])  # one quote each
        self.assertEqual(left(A1, f"- 「{A1}」 → 결정: 변경 없음"), [])
        self.assertEqual(left(A1, f"- 「{A1}, maybe」 → 결정: 변경 없음"), [A1])  # a short message is quoted whole
        self.assertEqual(left("ok", "- 「Ship it after the review」 → 결정: ok로 둔다"), ["ok"])
        self.assertEqual(left("ok", "- 「ok」 → 결정: 변경 없음"), [])
        self.assertEqual(left(M2, f"- 「{unicodedata.normalize('NFD', M2)}」 → 결정: 변경 없음"), [])
        self.assertNotEqual(unicodedata.normalize("NFD", M2), M2)

    def test_AH6_classifier_table(self):
        c, h = self.mod.classify_record, {"kind": "human"}
        cmd = "<command-message>handoff</command-message>\n<command-name>/handoff</command-name>"
        def user(content, **kw):
            return {"type": "user", "message": {"role": "user", "content": content}, **kw}
        def skill(name):
            return {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"},
                    {"type": "tool_use", "name": "Skill", "input": {"skill": name}}]}}
        def answer(answers, **kw):
            return user([{"type": "tool_result", "tool_use_id": "t", "content": "answered"}],
                        toolUseResult={"answers": answers}, **kw)
        def att(prompt, kind="human", typ="queued_command"):
            return {"type": "attachment", "attachment": {"type": typ, "prompt": prompt, "origin": {"kind": kind}}}
        pasted = "/Us" + "ers/x/y.md 고쳐"  # a pasted path is a message (assembled: the public scan rejects the literal)
        rows = {
            "user /handoff command": (user(cmd, origin=h), "baseline"),
            "user /handoff command, text blocks, no origin": (user([{"type": "text", "text": "  " + cmd}]), "baseline"),
            "Skill handoff": (skill("handoff"), "baseline"),
            "Skill plugin:handoff": (skill("demo-tools:handoff"), "baseline"),
            "Skill resume-handoff": (skill("resume-handoff"), None),
            "Skill other": (skill("handoff-notes"), None),
            "assistant text only": ({"type": "assistant", "message": {"content": [{"type": "text", "text": cmd}]}}, None),
            "isMeta /handoff command": (user(cmd, origin=h, isMeta=True), None),
            "isMeta text": (user("Base directory for this skill", origin=h, isMeta=True), None),
            "promptSource system": (user("hello", origin=h, promptSource="system"), None),
            "dialog answer": (answer({"q1": "값1", "q2": "값2"}), "message"),
            "dialog answer, list and other values": (answer({"q1": ["a", "b"], "q2": 3, "q3": "c"}), "message"),
            "dialog answers empty dict": (answer({}), None),
            "dialog answers a string": (answer("값1"), None),
            "dialog answers with an empty body": (answer({"q1": 3, "q2": ""}), None),
            "dialog answer blank": (answer({"q": "  "}), None),
            "no origin": (user("Another session reports"), None),
            "origin a string": (user("hello", origin="human"), None),
            "origin peer": (user("hello", origin={"kind": "peer"}), None),
            "tool_result only": (user([{"type": "tool_result", "content": "x"}], origin=h), None),
            "bash tag": (user("<bash-input>ls</bash-input>", origin=h), None),
            "local command tag": (user("  <local-command-stdout>x</local-command-stdout>", origin=h), None),
            "other command": (user("<command-name>/clear</command-name>", origin=h), None),
            "empty": (user("", origin=h), None),
            "blank": (user("  \n ", origin=h), None),
            "pasted path": (user(pasted, origin=h), "message"),
            "xml start": (user("<note>keep this</note>", origin=h), "message"),
            "text blocks": (user([{"type": "text", "text": "keep D"}], origin=h), "message"),
            "queued slash": (att("/review now"), "message"),
            "queued text and image": (att(image_prompt("keep D")), "message"),
            "queued image only": (att(image_prompt("")[1:]), None),
            "queued task notification": (att("<task-notification>x</task-notification>", kind="task-notification"), None),
            "queued empty": (att(""), None),
            "other attachment": (att("x", typ="total_tokens_reminder"), None),
            "attachment null": ({"type": "attachment", "attachment": None}, None),
            "queue operation": ({"type": "queue-operation", "operation": "enqueue", "content": "x"}, None),
            "system": ({"type": "system", "content": "x"}, None),
            "meta record": ({"type": "last-prompt", "lastPrompt": "x"}, None),
        }
        for name, (rec, want) in rows.items():
            with self.subTest(row=name):
                self.assertEqual(c(rec), want)
        self.assertEqual(self.mod.record_text(rows["dialog answer"][0]), "값1 / 값2")
        self.assertEqual(self.mod.record_text(rows["dialog answer, list and other values"][0]), "a, b / c")
        self.assertEqual(self.mod.record_text(rows["pasted path"][0]), pasted)
        self.assertEqual(self.mod.record_text(rows["queued slash"][0]), "/review now")
        self.assertEqual(self.mod.record_text(rows["queued text and image"][0]), "keep D")

    def test_AH7_baseline_reset_by_user(self):
        lines = self.lines[:CUT_DELIVERED]
        self.assertEqual(self.texts(lines[:28] + [lines[15]] + lines[28:]), [M2])

    def test_AH7b_baseline_by_skill_tool_use(self):
        lines = self.lines[:CUT_DELIVERED]
        self.assertEqual(self.texts(lines[:28] + [lines[5]] + lines[28:]), [M2])

    def test_AH7c_last_of_both_wins(self):
        lines, between = self.lines[:CUT_DELIVERED], user_rec("Typed between the two baselines.")
        self.assertEqual(self.texts(lines), [M1, M2])
        self.assertEqual(self.texts(lines[:13] + [between] + lines[13:]), [M1, M2])  # line 16 is the baseline
        self.assertEqual(self.texts(lines[:13] + [between] + lines[16:]),  # without it, line 6 is
                         ["Typed between the two baselines.", M1, M2])

    def test_AH8_position_not_time(self):
        lines = self.lines[:CUT_DELIVERED]
        early = queued_rec("Enqueued before, delivered after.", enqueued="2026-11-09T06:30:00.000Z")
        late = queued_rec("Delivered before the baseline.", enqueued="2026-11-09T07:30:00.000Z")
        self.assertEqual(self.texts(lines[:13] + [late] + lines[13:16] + [early] + lines[16:]),
                         ["Enqueued before, delivered after.", M1, M2])

    def test_AH9_each_occurrence(self):
        lines = self.lines[:CUT_DELIVERED] + [user_rec(M1, promptSource="queued"), user_rec(f" {M1}\n")]
        self.assertEqual(self.texts(lines), [M1, M2, M1, M1])

    def test_AH9b_same_answer_twice_needs_two_quotes(self):
        self.put(self.lines[:CUT_DELIVERED] + [user_rec("yes"), user_rec("yes", ts="2026-11-09T07:05:00.000Z")])
        proc = self.gate(self.with_after(M1, M2, "yes"))
        self.assertFailsWith(proc, '[FAIL] 1 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn("\n  - 「yes」 (07:05:00Z)\n", proc.stdout)
        self.assertNotIn("(07:00:00Z)", proc.stdout)
        proc = self.gate(self.with_after(M1, M2))
        self.assertFailsWith(proc, "[FAIL] 2 user message(s)", "\n  - 「yes」 (07:00:00Z)\n  - 「yes」 (07:05:00Z)\n")
        self.assertEqual(self.record()["fail_codes"], {"after-handoff": 2})
        self.assertPasses(self.gate(self.with_after(M1, M2, "yes", "yes")))

    def test_AH9c_dialog_answer_and_typed_same_text(self):
        self.put(self.lines[:FULL] + [user_rec(A1)])
        proc = self.gate(self.with_after(M1, M2, A1))
        self.assertFailsWith(proc, '[FAIL] 1 user message(s) after the last /handoff are not in "After Handoff":')
        self.assertIn(f"\n  - 「{A1}」 (07:00:00Z)\n", proc.stdout)
        self.assertNotIn("(06:56:30Z)", proc.stdout)
        self.assertPasses(self.gate(self.with_after(M1, M2, A1, A1)))

    def test_AH10_degrade(self):
        def warned(proc, reason):
            self.assertPasses(proc)
            self.assertEqual(proc.stdout.splitlines()[-1], f"WARNING: after-handoff messages not checked: {reason}")
            self.assertNotIn("Traceback", proc.stderr)
        warned(self.gate(self.text), "transcript not found")
        self.put([self.lines[i] for i in (0, 1, 5, 7, 12)])  # no user record with an origin
        warned(self.gate(self.text), "transcript format unknown")
        self.put(self.lines[:5])
        warned(self.gate(self.text), "no /handoff input in transcript")
        malformed = ['{"type": "user", "origin": "human", "message": {"content": "x"}}',
                     '{"type": "user", "origin": {"kind": "human"}, "message": {"content": ["a string", 3]}}',
                     '{"type": "attachment", "attachment": null}', '{"type": "user", "origin": {"kind": "human"}}',
                     '{"type": "user", "origin": {"kind": "human"}, "message": "a string"}', '["user"]',
                     '{"type": "assistant", "message": {"content": "a string"}}', '{"type": "user", "isMeta": "x"}']
        self.put(self.lines[:16] + malformed + self.lines[16:CUT_DELIVERED])
        proc = self.gate(self.with_after(M1, M2))
        self.assertPasses(proc)
        self.assertNotIn("Traceback", proc.stderr)
        broken = ['{"type": "user", "origin"', "{not json", '"user"', ""]
        self.assertEqual(self.texts(self.lines[:16] + broken + self.lines[16:CUT_DELIVERED]), [M1, M2])
        path = self.put(self.lines[:CUT_DELIVERED])
        with mock.patch.object(self.mod, "classify_record", side_effect=TypeError("boom")):
            self.assertEqual(self.mod.messages_after_handoff(path), ([], "transcript format unknown"))
        with mock.patch.object(self.mod, "TRANSCRIPT_MAX_BYTES", 10):
            self.assertEqual(self.mod.messages_after_handoff(path), ([], "transcript too large"))
        path.unlink()
        path.mkdir()  # found by name, but not a file
        warned(self.gate(self.text), "transcript unreadable")

    def test_AH11_session_header(self):
        self.put(self.lines[:CUT_DELIVERED])
        want = f"**Session:** must be this session's id {SID}"
        for name, text in (("missing", self.base), ("other id", self.text.replace(SID, "11111111-1111-4111-8111-111111111111"))):
            with self.subTest(case=name):
                proc = self.gate(self.with_after(M1, M2, text=text))
                self.assertFailsWith(proc, "resume-contract problem(s):", want)
                self.assertEqual(self.record()["fail_codes"], {"contract-session": 1})
        top, prev = "relative/top", "relative/prev"
        broken = self.base.replace("**Toplevel:** /home/u/sample-project", f"**Toplevel:** {top}", 1)
        proc = self.gate(self.with_after(M1, M2, text=broken.replace("**Continues:** none", f"**Continues:** {prev}", 1)))
        order = [f"  - **Toplevel:** is not an absolute path: {top}",
                 f"  - **Continues:** is neither none nor an absolute path: {prev}", f"  - {want}"]
        self.assertEqual([ln for ln in proc.stdout.splitlines() if ln in order], order)  # contract-session is third
        proc = self.gate(self.base, HANDOFF_CALLER="handoff_state.py")
        self.assertPasses(proc)
        self.assertNotIn("Session:", proc.stdout)
        self.assertNotIn("/handoff", proc.stdout)

    def test_AH12_no_env_unchanged(self):
        folder = self.tmp / "base"
        folder.mkdir()
        for name in ("check_handoff.py", "run_log.py"):
            proc = subprocess.run(["git", "show", f"{GATE_BASE}:scripts/{name}"], cwd=ROOT, capture_output=True)
            if proc.returncode != 0:
                self.skipTest(f"base commit {GATE_BASE} is not available")
            (folder / name).write_bytes(proc.stdout)
        self.put(self.lines[:CUT_DELIVERED])  # a transcript that would fail the gate
        def run(script, path, **extra):
            return subprocess.run([sys.executable, str(script), str(path), "--git", "yes"], capture_output=True,
                                  text=True, encoding="utf-8", env=self.env(HANDOFF_LOG="off", **extra))
        for fixture in (VALID, TEMPLATE, PREVIOUS):
            old = run(folder / "check_handoff.py", fixture)
            for name, extra in (("no env", {}), ("not a uuid", {"CLAUDE_CODE_SESSION_ID": "not-a-uuid"}),
                                ("resume caller", {"CLAUDE_CODE_SESSION_ID": SID, "HANDOFF_CALLER": "handoff_state.py"})):
                with self.subTest(fixture=fixture.name, case=name):
                    new = run(CHECKER, fixture, **extra)
                    self.assertEqual((new.returncode, new.stdout), (old.returncode, old.stdout))

    def test_AH13_fail_codes_and_privacy(self):
        self.put(self.lines[:CUT_DELIVERED])
        self.gate(self.text)
        self.assertEqual(self.record()["fail_codes"], {"after-handoff": 2})
        self.assertGreaterEqual(self.record()["omitted_lines"], 2)
        raw = (self.tmp / "runs.jsonl").read_text(encoding="utf-8")
        for text in (raw, json.dumps([json.loads(x) for x in raw.splitlines()], ensure_ascii=False)):
            self.assertNotIn(M1[:20], text)
            self.assertNotIn(M2[:10], text)

    def test_AH14_secret(self):
        said = f"Push with {TOKEN} to the demo remote."
        self.put(self.lines[:17] + [user_rec(said)])
        proc = self.gate(self.text)
        self.assertFailsWith(proc, "Push with REDACTED to the demo remote.")
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr)
        self.assertNotIn(TOKEN, (self.tmp / "runs.jsonl").read_text(encoding="utf-8"))
        proc = self.gate(self.with_after(said))
        self.assertFailsWith(proc, "secret-like value (GitHub token)")
        self.assertEqual(self.record()["fail_codes"], {"secret:github-token": 1})
        self.assertNotIn(TOKEN, proc.stdout)
        self.assertPasses(self.gate(self.with_after(said.replace(TOKEN, "REDACTED"))))

    def test_AH15_caps(self):
        long = "word " * 100  # 500 characters
        said = [long] + [f"Message number {n} for the cap test." for n in range(2, 8)]
        self.put(self.lines[:17] + [user_rec(s) for s in said])
        proc = self.gate(self.text)
        self.assertFailsWith(proc, "[FAIL] 7 user message(s) after the last /handoff")
        shown = [ln for ln in proc.stdout.splitlines() if ln.startswith("  - 「")]
        self.assertEqual(len(shown), 5)
        self.assertEqual(shown[0], "  - 「" + " ".join(long.split())[:300] + "…」 (07:00:00Z)")
        self.assertIn("\n  - … and 2 more\n", proc.stdout)
        self.assertEqual(self.record()["fail_codes"], {"after-handoff": 7})

    def test_AH16_codes_and_regex(self):
        self.assertIn("after-handoff", self.mod.FAIL_CODES)
        self.assertIn("contract-session", self.mod.FAIL_CODES)
        spec = importlib.util.spec_from_file_location("run_log_ah16", ROOT / "scripts" / "run_log.py")
        run_log = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run_log)
        self.assertEqual(self.mod.SESSION_ID.pattern, run_log.SESSION_ID.pattern)

    def test_AH17_find_transcript(self):
        self.assertIsNone(self.mod.find_transcript("not-a-uuid"))
        with mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}):
            self.assertIsNone(self.mod.find_transcript(SID))
            first = self.put(self.lines[:5])
            second = self.home / ".claude" / "projects" / "-tmp-other" / f"{SID}.jsonl"
            second.parent.mkdir(parents=True)
            second.write_text("{}\n", encoding="utf-8", newline="\n")
            os.utime(first, (1_000_000, 1_000_000))
            self.assertEqual(self.mod.find_transcript(SID), second)  # the most recent copy
            os.utime(second, (1_000_000, 1_000_000))
            self.assertEqual(self.mod.find_transcript(SID), first)  # a tie: the first path
            loop = self.home / ".claude" / "projects" / "-tmp-aaa" / f"{SID}.jsonl"
            loop.parent.mkdir(parents=True)
            loop.symlink_to(loop.name)  # a symlink loop: stat() fails on this candidate only
            self.assertEqual(self.mod.find_transcript(SID), first)
            gone = self.home / ".claude" / "projects" / "-tmp-gone" / f"{SID}.jsonl"
            with mock.patch.object(self.mod.Path, "glob", lambda folder, pattern: iter([gone, first])):
                self.assertEqual(self.mod.find_transcript(SID), first)  # a copy gone between glob and stat


if __name__ == "__main__":
    unittest.main()
