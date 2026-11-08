"""Tests for scripts/run_log.py (run log, spec section 12, A12).

Every test points HOME, USERPROFILE and HANDOFF_LOG at a temporary folder so the
real ~/.claude is never read or written.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from fnmatch import fnmatch
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_log  # noqa: E402

TS_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FIELDS = {
    "ts", "script", "version", "mode", "cwd", "toplevel", "args", "exit",
    "stdout", "omitted_lines", "warnings", "handoff", "continue", "checker",
}


def sample_record(**over):
    kwargs = dict(
        script="handoff_state.py", mode="resume", cwd="/work/proj", toplevel="/work/proj",
        args=["--resume"], exit_code=0, stdout_lines=["branch: main"],
    )
    kwargs.update(over)
    return run_log.build_record(**kwargs)


class RunLogTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("HANDOFF_LOG", None)
        self.default_log = self.home / ".claude" / "handoffs" / ".state" / "runs.jsonl"

    def lines(self, path):
        return path.read_text(encoding="utf-8").splitlines()


class LogPathTest(RunLogTestCase):
    def test_A12_default_path_under_home(self):
        self.assertEqual(run_log.log_path(), self.default_log)

    def test_A12_env_path_wins(self):
        target = self.tmp / "custom" / "x.jsonl"
        os.environ["HANDOFF_LOG"] = str(target)
        self.assertEqual(run_log.log_path(), target)

    def test_A12_off_returns_none(self):
        os.environ["HANDOFF_LOG"] = "off"
        self.assertIsNone(run_log.log_path())

    def test_A12_file_name_is_not_a_session_candidate(self):
        self.assertFalse(fnmatch(run_log.log_path().name, "*-session.tmp"))
        self.assertFalse(fnmatch(run_log.log_path().name + ".1", "*-session.tmp"))


class BuildRecordTest(RunLogTestCase):
    def test_A12_build_record_fields(self):
        record = sample_record(
            handoff="/h/a.md", cont="yes", checker=0,
        )
        self.assertEqual(
            set(record) - {"ts", "version"}, FIELDS - {"ts", "version"},
        )
        self.assertEqual(record["script"], "handoff_state.py")
        self.assertEqual(record["mode"], "resume")
        self.assertEqual(record["cwd"], "/work/proj")
        self.assertEqual(record["toplevel"], "/work/proj")
        self.assertEqual(record["args"], ["--resume"])
        self.assertEqual(record["exit"], 0)
        self.assertEqual(record["stdout"], ["branch: main"])
        self.assertEqual(record["handoff"], "/h/a.md")
        self.assertEqual(record["continue"], "yes")
        self.assertEqual(record["checker"], 0)

    def test_A12_optional_fields_default_to_null(self):
        record = sample_record()
        self.assertIsNone(record["handoff"])
        self.assertIsNone(record["continue"])
        self.assertIsNone(record["checker"])

    def test_L11_warnings_are_lines_with_warning_anywhere(self):
        lines = ["branch: main", "WARNING: a", "handoff age: 9.0 days  WARNING: handoff is older than 7 days",
                 "WARNING: b"]
        record = sample_record(stdout_lines=lines)
        self.assertEqual(record["warnings"], [lines[1], lines[2], lines[3]])
        self.assertEqual(record["stdout"], lines)
        self.assertEqual(record["omitted_lines"], 0)

    def test_M3_indented_lines_are_not_recorded_only_counted(self):
        lines = ["handoff check: exit 1", "  [FAIL] 1 item(s) lack what/why/how or a design-file path:",
                 "  - [What Has NOT Been Tried Yet] SECRET-ITEM body text that must stay out of the log",
                 "\t- tab-indented detail", "continue: no"]
        record = sample_record(stdout_lines=lines)
        self.assertEqual(record["stdout"], ["handoff check: exit 1", "continue: no"])
        self.assertEqual(record["omitted_lines"], 3)
        self.assertNotIn("SECRET-ITEM", json.dumps(record))

    def test_M3_indented_warning_line_is_omitted_not_listed(self):
        record = sample_record(stdout_lines=["  - item text WARNING: leaked body", "WARNING: top"])
        self.assertEqual(record["warnings"], ["WARNING: top"])
        self.assertEqual(record["omitted_lines"], 1)

    def test_A12_no_warnings_gives_empty_list(self):
        self.assertEqual(sample_record()["warnings"], [])

    def test_A12_toplevel_may_be_null(self):
        self.assertIsNone(sample_record(toplevel=None)["toplevel"])


class AppendRunTest(RunLogTestCase):
    def test_A12_default_path_written_with_parents_created(self):
        self.assertIsNone(run_log.append_run(sample_record()))
        self.assertTrue(self.default_log.is_file())
        self.assertEqual(len(self.lines(self.default_log)), 1)

    def test_A12_env_path_written(self):
        target = self.tmp / "elsewhere" / "log.jsonl"
        os.environ["HANDOFF_LOG"] = str(target)
        self.assertIsNone(run_log.append_run(sample_record()))
        self.assertTrue(target.is_file())
        self.assertFalse(self.default_log.exists())

    def test_A12_off_writes_nothing(self):
        os.environ["HANDOFF_LOG"] = "off"
        self.assertIsNone(run_log.append_run(sample_record()))
        self.assertFalse((self.home / ".claude").exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_A12_each_line_is_valid_json_with_all_fields(self):
        run_log.append_run(sample_record())
        run_log.append_run(sample_record(mode="check", exit_code=1))
        lines = self.lines(self.default_log)
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(set(json.loads(line)), FIELDS)
        self.assertEqual(json.loads(lines[1])["mode"], "check")
        self.assertEqual(json.loads(lines[1])["exit"], 1)

    def test_A12_ts_is_utc_iso_seconds_with_z(self):
        run_log.append_run(sample_record())
        record = json.loads(self.lines(self.default_log)[0])
        self.assertRegex(record["ts"], TS_FORMAT)

    def test_A12_version_is_the_module_constant(self):
        run_log.append_run(sample_record())
        record = json.loads(self.lines(self.default_log)[0])
        self.assertEqual(record["version"], run_log.VERSION)
        self.assertTrue(run_log.VERSION)

    def test_A12_korean_text_is_written_as_is(self):
        run_log.append_run(sample_record(stdout_lines=["브랜치: 메인"]))
        raw = self.default_log.read_bytes().decode("utf-8")
        self.assertIn("브랜치: 메인", raw)
        self.assertNotIn("\\u", raw)

    def test_A12_lines_end_with_lf_only(self):
        run_log.append_run(sample_record())
        raw = self.default_log.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)

    def test_A12_input_record_is_not_mutated_by_ts_and_version_fill(self):
        record = {"script": "x.py", "mode": "check"}
        run_log.append_run(record)
        self.assertEqual(record, {"script": "x.py", "mode": "check"})
        written = json.loads(self.lines(self.default_log)[0])
        self.assertIn("ts", written)
        self.assertEqual(written["script"], "x.py")

    def test_A12_rotates_over_limit_and_replaces_old_backup(self):
        with mock.patch.object(run_log, "MAX_BYTES", 200):
            for _ in range(10):
                run_log.append_run(sample_record())
            backup = self.default_log.with_name("runs.jsonl.1")
            self.assertTrue(backup.is_file())
            self.assertTrue(self.default_log.is_file())
            self.assertLessEqual(self.default_log.stat().st_size, 200 + 1024)
            backup.write_text("OLD\n", encoding="utf-8", newline="\n")
            self.default_log.write_text("x" * 300 + "\n", encoding="utf-8", newline="\n")
            run_log.append_run(sample_record())
            self.assertNotIn("OLD", backup.read_text(encoding="utf-8"))
            lines = self.lines(self.default_log)
            self.assertEqual(len(lines), 1)
            json.loads(lines[0])

    def test_A12_no_rotation_at_or_below_limit(self):
        run_log.append_run(sample_record())
        size = self.default_log.stat().st_size
        with mock.patch.object(run_log, "MAX_BYTES", size):
            run_log.append_run(sample_record())
        self.assertFalse(self.default_log.with_name("runs.jsonl.1").exists())
        self.assertEqual(len(self.lines(self.default_log)), 2)

    def test_A12_default_limit_is_5_mib(self):
        self.assertEqual(run_log.MAX_BYTES, 5 * 1024 * 1024)

    def test_A12_unwritable_path_returns_reason_without_raising(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("a file where a folder is needed", encoding="utf-8", newline="\n")
        os.environ["HANDOFF_LOG"] = str(blocker / "sub" / "runs.jsonl")
        result = run_log.append_run(sample_record())
        self.assertIsInstance(result, str)
        self.assertTrue(result)

    def test_A12_unserializable_record_returns_reason_without_raising(self):
        record = sample_record()
        record["args"] = object()
        result = run_log.append_run(record)
        self.assertIsInstance(result, str)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
