#!/usr/bin/env python3
"""Install the /handoff and /resume-handoff skill pair into a Claude config folder.

Usage: python install.py [--src DIR] [--dst DIR] [--backup-root DIR] [--python PATH] [--yes]
Without --yes the run is a dry-run that only prints the plan.
Exit codes: 0 success, 1 runtime error, 2 usage error.
"""
import argparse
import datetime
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# source path (relative to --src) -> target path (relative to --dst)
MAPPING = [
    ("commands/handoff.md", "commands/handoff.md"),
    ("commands/resume-handoff.md", "commands/resume-handoff.md"),
    ("scripts/handoff_state.py", "scripts/handoff_state.py"),
    ("scripts/check_handoff.py", "scripts/check_handoff.py"),
    ("scripts/run_log.py", "scripts/run_log.py"),
]

# command documents whose script calls are rewritten to the installing interpreter and --dst
COMMAND_DOCS = {"commands/handoff.md", "commands/resume-handoff.md"}
SCRIPT_CALL = re.compile(rb"python3 ~/\.claude/scripts/([A-Za-z0-9_.-]+)")

VENV_SEGMENTS = ("/.venv/", "/venv/", "/virtualenvs/")
VENV_WARNING = "WARNING: interpreter is inside a virtual environment; pass --python <stable path>"

DRY_RUN_FOOTER = "dry-run: no files changed. Re-run with --yes to apply."


class InstallError(Exception):
    pass


def _now():
    return datetime.datetime.now()


def build_parser():
    p = argparse.ArgumentParser(prog="install.py", description=__doc__.splitlines()[0])
    p.add_argument("--src", type=Path, default=Path(__file__).resolve().parent)
    p.add_argument("--dst", type=Path, default=Path.home() / ".claude")
    p.add_argument("--backup-root", type=Path, default=Path.home() / ".claude-backups")
    p.add_argument("--python", type=Path, default=None,
                   help="interpreter written into the command documents (default: this one)")
    p.add_argument("--yes", action="store_true", help="apply the plan")
    p.add_argument("--dry-run", action="store_true", help="print the plan only (default)")
    return p


def rewrite_interpreter(data, exe, dst):
    """Point `python3 ~/.claude/scripts/<file>` at exe and dst (both absolute, `/` separated)."""
    prefix = f'"{exe}" "{dst}/scripts/'.encode("utf-8")
    return SCRIPT_CALL.sub(lambda m: prefix + m.group(1) + b'"', data)


def in_virtualenv(exe):
    """True when the interpreter path has a virtual-environment folder segment."""
    return any(seg in exe.replace("\\", "/").lower() for seg in VENV_SEGMENTS)


def interpreter_path(option):
    """Absolute `/` separated interpreter path from --python or sys.executable."""
    if option is None:
        if not sys.executable:
            raise InstallError("cannot determine the Python interpreter path")
        return Path(sys.executable).absolute().as_posix()
    return option.absolute().as_posix()


def render(src_file, dst_rel, dst, exe):
    """Bytes to install for one mapping entry."""
    data = src_file.read_bytes()
    if dst_rel not in COMMAND_DOCS:
        return data
    dst_posix = dst.as_posix()
    if '"' in exe or '"' in dst_posix:
        raise InstallError('interpreter and --dst paths must not contain a double quote')
    return rewrite_interpreter(data, exe, dst_posix)


def classify(content, dst_file):
    """Return new, same, or changed; refuse symlink and directory targets."""
    if dst_file.is_symlink():
        raise InstallError(f"refusing symlink target: {dst_file}")
    if dst_file.is_dir():
        raise InstallError(f"refusing directory target: {dst_file}")
    if not dst_file.exists():
        return "new"
    return "same" if dst_file.read_bytes() == content else "changed"


def plan(src, dst, exe):
    items = []
    for src_rel, dst_rel in MAPPING:
        src_file = src / src_rel
        if not src_file.is_file():
            raise InstallError(f"source file missing: {src_file}")
        dst_file = dst / dst_rel
        content = render(src_file, dst_rel, dst, exe)
        items.append((src_file, dst_file, dst_rel, classify(content, dst_file), content))
    return items


def replace_atomically(src_file, dst_file, content):
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst_file.parent, prefix=dst_file.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        shutil.copymode(src_file, tmp_name)
        os.replace(tmp_name, dst_file)
    except BaseException:
        if Path(tmp_name).exists():
            os.unlink(tmp_name)
        raise


def roll_back(done):
    """Undo this run's replacements: restore from backup, delete files it created."""
    for dst_file, backup_file in reversed(done):
        if backup_file is None:
            dst_file.unlink(missing_ok=True)
        else:
            shutil.copy2(backup_file, dst_file)


def apply(items, backup_root):
    changed = [it for it in items if it[3] == "changed"]
    backups = {}
    if changed:
        backup_dir = backup_root / f"session-handoff-{_now():%Y%m%d-%H%M%S}"
        if backup_dir.exists():
            raise InstallError(f"backup folder already exists: {backup_dir}")
        for _, dst_file, dst_rel, _, _ in changed:
            backup_file = backup_dir / dst_rel
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst_file, backup_file)
            backups[dst_file] = backup_file
        print(f"backup: {backup_dir}")
    done = []
    try:
        for src_file, dst_file, _, status, content in items:
            if status != "same":
                # record first: a failure inside the call may already have replaced the file
                done.append((dst_file, backups.get(dst_file)))
                replace_atomically(src_file, dst_file, content)
        for _, dst_file, _, _, content in items:
            if dst_file.read_bytes() != content:
                raise InstallError(f"installed file differs from source: {dst_file}")
    except BaseException:
        roll_back(done)
        print("rolled back: files replaced in this run were restored from the backup.", file=sys.stderr)
        raise


def run(args):
    src, dst = args.src.resolve(), args.dst.resolve()
    if src == dst:
        raise InstallError("--src and --dst are the same path")
    exe = interpreter_path(args.python)
    print(f"interpreter: {exe}")
    if in_virtualenv(exe):
        print(VENV_WARNING)
    items = plan(src, dst, exe)
    for _, dst_file, _, status, _ in items:
        print(f"{status:<7} {dst_file}")
    counts = {s: sum(1 for it in items if it[3] == s) for s in ("new", "changed", "same")}
    print(f"summary: new={counts['new']} changed={counts['changed']} same={counts['same']}")
    if not args.yes:
        print(DRY_RUN_FOOTER)
        return 0
    apply(items, args.backup_root)
    print("installed: installed files match the expected content byte for byte.")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.dry_run and args.yes:
            parser.error("--dry-run and --yes cannot be used together")
        if args.python is not None and not args.python.is_file():
            parser.error(f"--python path does not exist: {args.python}")
        return run(args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except (InstallError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
