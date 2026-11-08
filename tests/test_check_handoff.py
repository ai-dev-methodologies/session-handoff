"""Specifies the new check_handoff.py behavior (spec sections 2, 5, 6, 7, 8, 9, 10, 14.1-14.5).

Test names carry the review-verdict item id (M1, A3, ...). A negative test asserts the exit code and a
needle the message must carry (an item name, a field label, a section title), so a handoff that already
fails for an unrelated reason cannot turn a negative test green.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


def goal_section(origin=ORIGIN,
                 goal='Make the sync job survive timeouts without dropping events. User: "retry the sync job instead of dropping events"',
                 done="a 30s outage loses no events; `python -m unittest tests.test_sync` passes",
                 changes="none", omit=()):
    values = {"Origin": origin, "Goal": goal, "Done-when": done, "Changes": changes}
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
        for key in ("Origin", "Goal", "Done-when", "Changes"):
            with self.subTest(key=key):
                self.assertFailsWith(self.run_text(self.with_goal(omit=(key,))), "Goal (carried)", key)

    def test_C1_placeholder_value_fails_with_key_name(self):
        for key, kw in (("Origin", "origin"), ("Goal", "goal"), ("Done-when", "done"), ("Changes", "changes")):
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


if __name__ == "__main__":
    unittest.main()
