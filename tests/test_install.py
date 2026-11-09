import contextlib
import datetime
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.py"

MAPPING = [
    "commands/handoff.md",
    "commands/resume-handoff.md",
    "scripts/handoff_state.py",
    "scripts/check_handoff.py",
    "scripts/run_log.py",
    "scripts/run_report.py",
]
COMMAND_DOCS = ["commands/handoff.md", "commands/resume-handoff.md"]
OLD_STATE = "python3 ~/.claude/scripts/handoff_state.py"
OLD_CHECK = "python3 ~/.claude/scripts/check_handoff.py"


def load_module():
    spec = importlib.util.spec_from_file_location("install_under_test", INSTALL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_install(*args):
    return subprocess.run(
        [sys.executable, str(INSTALL), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
        for p in sorted(root.rglob("*"))
    }


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.src = base / "src"
        self.dst = base / "dst"
        self.backup = base / "backups"
        self.dst.mkdir()
        self.backup.mkdir()
        for rel in MAPPING:
            f = self.src / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            body = f"source {rel}\n"
            if rel in COMMAND_DOCS:
                body += f"{OLD_STATE} <cwd>\nRun `{OLD_CHECK} <file>`.\n"
            f.write_bytes(body.encode("utf-8") + bytes([0, 255, 13, 10]))

    def expected(self, rel):
        """Installed bytes for rel, built with plain str.replace (independent of install.py)."""
        data = (self.src / rel).read_bytes()
        if rel not in COMMAND_DOCS:
            return data
        exe = Path(sys.executable).as_posix()
        scripts = self.dst.resolve().as_posix() + "/scripts/"
        text = data.decode("utf-8", "surrogateescape")
        text = text.replace(OLD_STATE, f'"{exe}" "{scripts}handoff_state.py"')
        text = text.replace(OLD_CHECK, f'"{exe}" "{scripts}check_handoff.py"')
        return text.encode("utf-8", "surrogateescape")

    def args(self, *extra):
        return [
            "--src", str(self.src),
            "--dst", str(self.dst),
            "--backup-root", str(self.backup),
            *extra,
        ]

    def test_dry_run_changes_nothing(self):
        before = snapshot(self.dst), snapshot(self.backup)
        r = run_install(*self.args())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((snapshot(self.dst), snapshot(self.backup)), before)
        self.assertIn("summary: new=6 changed=0 same=0", r.stdout)
        self.assertEqual(
            r.stdout.rstrip("\n").splitlines()[-1],
            "dry-run: no files changed. Re-run with --yes to apply.",
        )

    def test_explicit_dry_run_flag_changes_nothing(self):
        r = run_install(*self.args("--dry-run"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(snapshot(self.dst), {})

    def test_fresh_install(self):
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in MAPPING:
            self.assertEqual((self.dst / rel).read_bytes(), self.expected(rel))
        self.assertEqual(list(self.backup.iterdir()), [])
        self.assertIn("summary: new=6 changed=0 same=0", r.stdout)

    def test_changed_file_is_backed_up_then_replaced(self):
        target = self.dst / "commands/handoff.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old local edit\n")
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("summary: new=5 changed=1 same=0", r.stdout)
        self.assertEqual(target.read_bytes(), self.expected("commands/handoff.md"))
        folders = list(self.backup.iterdir())
        self.assertEqual(len(folders), 1)
        self.assertTrue(folders[0].name.startswith("session-handoff-"))
        self.assertEqual((folders[0] / "commands/handoff.md").read_bytes(), b"old local edit\n")

    def test_second_run_is_idempotent(self):
        target = self.dst / "scripts/check_handoff.py"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"stale\n")
        self.assertEqual(run_install(*self.args("--yes")).returncode, 0)
        folders = list(self.backup.iterdir())
        self.assertEqual(len(folders), 1)
        before = snapshot(self.dst), snapshot(self.backup)
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("summary: new=0 changed=0 same=6", r.stdout)
        self.assertEqual((snapshot(self.dst), snapshot(self.backup)), before)
        self.assertEqual(list(self.backup.iterdir()), folders)

    def test_all_same_creates_no_backup_folder(self):
        self.assertEqual(run_install(*self.args("--yes")).returncode, 0)
        self.assertEqual(run_install(*self.args("--yes")).returncode, 0)
        self.assertEqual(list(self.backup.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_target_is_refused(self):
        real = Path(self._tmp.name) / "real.md"
        real.write_bytes(b"keep me\n")
        link = self.dst / "commands/handoff.md"
        link.parent.mkdir(parents=True)
        link.symlink_to(real)
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(real.read_bytes(), b"keep me\n")
        self.assertTrue(link.is_symlink())

    def test_directory_target_is_refused(self):
        (self.dst / "commands/handoff.md").mkdir(parents=True)
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.dst / "commands/handoff.md").is_dir())

    def test_src_equals_dst_is_refused(self):
        r = run_install("--src", str(self.src), "--dst", str(self.src),
                        "--backup-root", str(self.backup), "--yes")
        self.assertEqual(r.returncode, 1)

    def test_dry_run_with_yes_is_usage_error(self):
        r = run_install(*self.args("--dry-run", "--yes"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(snapshot(self.dst), {})

    def test_missing_source_file_fails_without_writing(self):
        (self.src / "scripts/check_handoff.py").unlink()
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(snapshot(self.dst), {})

    def test_existing_backup_folder_stops_without_overwrite(self):
        mod = load_module()
        target = self.dst / "commands/handoff.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old\n")
        stamp = datetime.datetime(2026, 1, 2, 3, 4, 5)
        clash = self.backup / "session-handoff-20260102-030405"
        (clash / "commands").mkdir(parents=True)
        (clash / "commands/handoff.md").write_bytes(b"earlier backup\n")
        with mock.patch.object(mod, "_now", return_value=stamp), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(self.args("--yes"))
        self.assertEqual(rc, 1)
        self.assertEqual((clash / "commands/handoff.md").read_bytes(), b"earlier backup\n")
        self.assertEqual(target.read_bytes(), b"old\n")

    def test_command_docs_get_interpreter_and_dst_path(self):
        before = {rel: (self.src / rel).read_bytes() for rel in MAPPING}
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        exe = Path(sys.executable).as_posix()
        scripts = self.dst.resolve().as_posix() + "/scripts/"
        for rel in COMMAND_DOCS:
            text = (self.dst / rel).read_bytes().decode("utf-8", "surrogateescape")
            self.assertNotIn("python3 ~/.claude/scripts/", text)
            self.assertIn(f'"{exe}" "{scripts}handoff_state.py" <cwd>', text)
            self.assertIn(f'`"{exe}" "{scripts}check_handoff.py" <file>`', text)
        for rel in ("scripts/handoff_state.py", "scripts/check_handoff.py", "scripts/run_log.py",
                    "scripts/run_report.py"):
            self.assertEqual((self.dst / rel).read_bytes(), before[rel])
        self.assertEqual({rel: (self.src / rel).read_bytes() for rel in MAPPING}, before)

    def test_run_log_script_is_installed(self):
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.dst / "scripts/run_log.py").read_bytes(),
                         (self.src / "scripts/run_log.py").read_bytes())

    def test_mapping_has_six_entries(self):
        self.assertEqual(len(MAPPING), 6)
        self.assertEqual(len(load_module().MAPPING), 6)

    def test_run_report_script_is_installed(self):
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.dst / "scripts/run_report.py").read_bytes(),
                         (self.src / "scripts/run_report.py").read_bytes())

    def test_resume_notes_report_call_rewritten(self):
        # lane E adds the real sentence; here a temp src copy carries a stand-in
        doc = self.src / "commands/resume-handoff.md"
        doc.write_bytes(doc.read_bytes() + b"Only when the user asks: python3 ~/.claude/scripts/run_report.py --since 7d\n")
        r = run_install(*self.args("--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        exe = Path(sys.executable).as_posix()
        scripts = self.dst.resolve().as_posix() + "/scripts/"
        text = (self.dst / "commands/resume-handoff.md").read_bytes().decode("utf-8", "surrogateescape")
        self.assertIn(f'"{exe}" "{scripts}run_report.py" --since 7d', text)
        self.assertNotIn("python3 ~/.claude/scripts/", text)

    def test_rewrite_interpreter_uses_slash_separators_and_quotes(self):
        mod = load_module()
        data = (b"a python3 ~/.claude/scripts/handoff_state.py <cwd>\r\n"
                b"`python3 ~/.claude/scripts/check_handoff.py <file>` and python3 other.py\n")
        out = mod.rewrite_interpreter(data, "C:/Py 3/python.exe", "C:/Home/u/.claude")
        self.assertEqual(out, (
            b'a "C:/Py 3/python.exe" "C:/Home/u/.claude/scripts/handoff_state.py" <cwd>\r\n'
            b'`"C:/Py 3/python.exe" "C:/Home/u/.claude/scripts/check_handoff.py" <file>` and python3 other.py\n'
        ))

    def test_midrun_failure_restores_replaced_files_and_removes_new_ones(self):
        mod = load_module()
        old = {}
        for rel in ("commands/resume-handoff.md", "scripts/handoff_state.py"):
            f = self.dst / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(f"old {rel}\n".encode("utf-8"))
            old[rel] = f.read_bytes()
        real = mod.replace_atomically
        calls = []

        def flaky(*a, **k):
            calls.append(a)
            if len(calls) == 3:
                raise OSError("simulated write failure")
            return real(*a, **k)

        err = io.StringIO()
        with mock.patch.object(mod, "replace_atomically", flaky), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = mod.main(self.args("--yes"))
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 3)
        self.assertFalse((self.dst / "commands/handoff.md").exists())
        for rel, data in old.items():
            self.assertEqual((self.dst / rel).read_bytes(), data)
        self.assertFalse((self.dst / "scripts/check_handoff.py").exists())
        self.assertFalse((self.dst / "scripts/run_log.py").exists())
        self.assertEqual([p.name for p in (self.dst / "scripts").iterdir()], ["handoff_state.py"])
        self.assertIn("simulated write failure", err.getvalue())

    def fake_python(self, *parts):
        """An existing file that stands in for an interpreter."""
        f = Path(self._tmp.name).joinpath("interp", *parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"#!/bin/sh\n")
        return f

    def test_python_option_replaces_interpreter_in_command_docs(self):
        fake = self.fake_python("bin", "python3")
        r = run_install(*self.args("--yes", "--python", str(fake)))
        self.assertEqual(r.returncode, 0, r.stderr)
        scripts = self.dst.resolve().as_posix() + "/scripts/"
        for rel in COMMAND_DOCS:
            text = (self.dst / rel).read_bytes().decode("utf-8", "surrogateescape")
            self.assertIn(f'"{fake.as_posix()}" "{scripts}handoff_state.py" <cwd>', text)
            self.assertNotIn(sys.executable, text)

    def test_python_option_missing_path_is_usage_error(self):
        missing = Path(self._tmp.name) / "no-such-python"
        for extra in ([], ["--yes"]):
            r = run_install(*self.args("--python", str(missing), *extra))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertEqual(snapshot(self.dst), {})
            self.assertEqual(snapshot(self.backup), {})

    def test_plan_output_starts_with_interpreter_line(self):
        fake = self.fake_python("bin", "python3")
        for extra in ([], ["--yes"]):
            r = run_install(*self.args("--python", str(fake), *extra))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], f"interpreter: {fake.as_posix()}")
        r = run_install(*self.args())
        self.assertEqual(r.stdout.splitlines()[0],
                         f"interpreter: {Path(sys.executable).absolute().as_posix()}")

    def test_virtualenv_interpreter_warns_but_proceeds(self):
        warning = ("WARNING: interpreter is inside a virtual environment; "
                   "pass --python <stable path>")
        for parts in ((".venv", "bin", "python3"), ("venv", "bin", "python3"),
                      ("virtualenvs", "x", "bin", "python3")):
            fake = self.fake_python(*parts)
            r = run_install(*self.args("--python", str(fake)))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(warning, r.stdout.splitlines())
        r = run_install(*self.args("--python", str(fake), "--yes"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(warning, r.stdout.splitlines())
        self.assertTrue((self.dst / "commands/handoff.md").exists())

    def test_stable_interpreter_does_not_warn(self):
        fake = self.fake_python("bin", "python3")
        r = run_install(*self.args("--python", str(fake)))
        self.assertNotIn("WARNING", r.stdout + r.stderr)

    def test_in_virtualenv_recognises_segments_and_backslashes(self):
        mod = load_module()
        for path in ("/home/u/.venv/bin/python", "/home/u/venv/bin/python",
                     "/home/u/.local/share/virtualenvs/x/bin/python",
                     "C:\\Home\\u\\.venv\\Scripts\\python.exe",
                     "C:\\Home\\u\\venv\\Scripts\\python.exe",
                     "C:/Home/u/virtualenvs/x/python.exe"):
            self.assertTrue(mod.in_virtualenv(path), path)
        for path in ("/usr/bin/python3", "/opt/homebrew/opt/python@3.13/bin/python3.13",
                     "/home/u/.venv-old/bin/python", "/home/u/myvenv/bin/python",
                     "C:\\Python313\\python.exe"):
            self.assertFalse(mod.in_virtualenv(path), path)

    def test_changing_python_marks_only_command_docs_changed(self):
        self.assertEqual(run_install(*self.args("--yes")).returncode, 0)
        fake = self.fake_python("bin", "python3")
        r = run_install(*self.args("--yes", "--python", str(fake)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("summary: new=0 changed=2 same=4", r.stdout)
        self.assertEqual(len(list(self.backup.iterdir())), 1)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_backup_and_rollback_preserve_permissions_and_mtime(self):
        mod = load_module()
        target = self.dst / "commands/resume-handoff.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old\n")
        os.chmod(target, 0o640)
        os.utime(target, (1_700_000_000, 1_700_000_000))
        real = mod.replace_atomically
        calls = []

        def flaky(*a, **k):
            calls.append(a)
            if len(calls) == 3:
                raise OSError("simulated write failure")
            return real(*a, **k)

        with mock.patch.object(mod, "replace_atomically", flaky), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = mod.main(self.args("--yes"))
        self.assertEqual(rc, 1)
        self.assertEqual(target.read_bytes(), b"old\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(int(target.stat().st_mtime), 1_700_000_000)
        backup_file = next(self.backup.iterdir()) / "commands/resume-handoff.md"
        self.assertEqual(backup_file.stat().st_mode & 0o777, 0o640)
        self.assertEqual(int(backup_file.stat().st_mtime), 1_700_000_000)

    def test_install_source_has_no_os_path(self):
        self.assertNotIn("os.path", INSTALL.read_text(encoding="utf-8"))

    def test_gitattributes_forces_lf(self):
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn("* text=auto eol=lf", lines)


if __name__ == "__main__":
    unittest.main()
