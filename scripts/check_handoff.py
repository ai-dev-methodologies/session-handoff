#!/usr/bin/env python3
"""Check a /handoff session file: open items, Decisions table, and the resume contract.

Usage: check_handoff.py <session-file> [--git yes|no]

Open items (handoff.md Step 1): every bullet under "What Has NOT Been Tried Yet" and "Decisions Made"
must carry what/why/how, or point at a design file. An item passes when its text (the bullet plus
its indented continuation lines) contains either
  - all three labels  what:/무엇을  why:/왜  how:/어떻게   (case-insensitive, Korean or English), or
  - a path that looks like a design file (…-design.md / …-plan.md / plans/…).
Items shorter than MIN_ITEM_CHARS are treated as status lines, not work items, and skipped.

Decisions table: when "Decisions Made" holds a table, it has a header row, a separator row, and at least
one data row. Every data row has three filled cells (What | Why | How), none a placeholder. A section with
no table at all passes only when its body is the single sentence NO_DECISIONS, or one line `None`.

Resume contract (what /resume-handoff needs without running anything but handoff_state.py):
  - The head carries `**Toplevel:** <absolute path>` (the project key handoff_state.py printed) and
    `**Continues:** none | <absolute path of an existing previous handoff>`: not this file itself, and
    of the same Toplevel when both files carry one.
  - "Goal (carried)" carries Origin, Goal, Done-when and Changes with real values; Origin starts with an
    absolute path. When Continues names a file that has the same section, Origin is byte-identical to
    it, its Changes entries are the first entries here unchanged, and Goal and Done-when are
    byte-identical unless Changes gained an entry; a new entry carries a quoted user sentence.
  - Every live item of the Continues file (key: the id in its name, else the whole name) appears in one
    of the live fields here, or among the keys of "Closed (pointers)".
  - "Closed (pointers)", when present, is `- <N> items closed -> <history file> (keys: a, b)`: the file is
    an existing absolute path and has a ` · <key> · ` line part for every listed key.
  - "Session State", when present, is not empty and carries the five state fields STATE_FIELDS with real
    values, plus the three live fields of LIVE_KEYS. Each live field is "none" or a list of items; every
    item has its field's six keys with non-placeholder values. Subagent and runner names end with
    `(<id>)`, the id of letters, digits and `_ . : -` and not a status word (NOT_IDS). Their `result` is
    an absolute path that is not under .claude/jobs/ or /tmp/claude-* (both are deleted). Scheduled items
    carry `fires-at` instead of `result`.
  - With `--git yes`, a missing "Session State" section fails. With `--git no` (outside git) it is required
    too, with the three live fields; the git fields may be n/a or absent. Without `--git`, a missing
    section is not checked.
  - "Exact Next Step" exists and is neither empty, only placeholder lines, nor one [...] or <...> block.

A file over SIZE_LIMIT_KIB prints one `WARNING: handoff file is <N> KiB (over 16 KiB)` line; the exit code is unchanged.
Every run appends one record to the run log (run_log.py next to this file).

Exit 0 = every check passes. Exit 1 = at least one check failed (listed). Exit 2 = usage error, file missing
or unreadable, or an unexpected error (traceback tail on stderr).
"""
import importlib.util
import re
import sys
import traceback
from pathlib import Path

SECTIONS = ("What Has NOT Been Tried Yet", "Decisions Made")  # open designs and decisions; blockers and next-step lines are navigation
LABELS = (("what:", "무엇을"), ("why:", "왜"), ("how:", "어떻게"))
DESIGN_PATH_RE = re.compile(r"(?:[\w./-]*-design\.md|[\w./-]*-plan\.md|\bplans/[\w./-]+)")
MIN_ITEM_CHARS = 40
STATE_FIELDS = ("Branch", "HEAD", "Uncommitted changes", "Unpushed commits", "Lockfile changed")
RETIRED_LIVE_FIELD = "Live background work"
ITEM_KEYS = ("name", "does", "decision", "result", "done-when", "after-return")
SCHEDULED_KEYS = ("name", "does", "decision", "fires-at", "done-when", "after-return")
OPTIONAL_KEYS = ("started", "expected-by")
LIVE_KEYS = {"Live subagents": ITEM_KEYS, "Live runners": ITEM_KEYS, "Live scheduled": SCHEDULED_KEYS}
ID_FIELDS = ("Live subagents", "Live runners")  # their `name` is `<name> (<id>)`
LIVE_FIELDS = tuple(LIVE_KEYS)
NONE_WORDS = ("none", "없음")
NO_DECISIONS = "No major decisions made this session."  # byte-identical to the sentence in commands/handoff.md
NO_DECISIONS_WORDS = ("none", "none.")
PLACEHOLDER_WORDS = ("...", "…", "tbd", "todo", "fixme", "placeholder", "n/a")
FIELD_RE = re.compile(r"^[-*]\s+\*\*(.+?):\*\*\s*(.*)$")
ABS_PATH_RE = re.compile(r"^(?:/|~/|[A-Za-z]:[\\/])\S")  # POSIX, ~/, C:\ and C:/ ; /c/ and /mnt/c/ are POSIX
SEPARATOR_RE = re.compile(r"^\|?[\s:|-]*-[\s:|-]*\|?$")
TOPLEVEL_RE = re.compile(r"^\*\*Toplevel:\*\*\s*(.*)$")
CONTINUES_RE = re.compile(r"^\*\*Continues:\*\*\s*(.*)$")
GOAL_SECTION = "Goal (carried)"
GOAL_KEYS = ("Origin", "Goal", "Done-when", "Changes")
GOAL_SAME_KEYS = ("Origin", "Goal", "Done-when")  # carried verbatim unless Changes gained an entry
CHANGE_SPLIT_RE = re.compile(r"\s*\|\s+")
QUOTE_RE = re.compile(r"\"[^\"]+\"|“[^”]+”|「[^」]+」")
CLOSED_SECTION = "Closed (pointers)"
CLOSED_RE = re.compile(r"^\s*[-*]\s+.*?(?:→|->)\s*(.+?)\s*\(keys:\s*(.*?)\)\s*$")
SIZE_LIMIT_KIB = 16
NAME_ID_RE = re.compile(r"\(([^()]*)\)\s*$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
NOT_IDS = ("running", "done", "pending", "none", "tbd", "todo")
NA_WORD = "n/a"  # the value of a git field outside git (--git no)
VOLATILE_PREFIXES = ("/tmp/claude-", "/private/tmp/claude-")  # background task output, cleared with /tmp
DRIVE_RE = re.compile(r"^(?:/mnt)?/([A-Za-z])(?=/|$)")  # /c/... and /mnt/c/... -> c:/...
JOBS_PART = "/.claude/jobs/"


def is_placeholder(text: str) -> bool:
    """True when `text` is an unfilled template value. `none` is a real value, not a placeholder."""
    s = text.strip()
    if not s or set(s) <= set("-—="):
        return True
    if s.lower() in PLACEHOLDER_WORDS:
        return True
    return (s[0], s[-1]) in (("[", "]"), ("<", ">"))


def is_absolute_path(text: str) -> bool:
    """True for POSIX, ~/ and Windows (C:\\, C:/, /c/, /mnt/c/) absolute paths; a sentence is not a path."""
    return bool(ABS_PATH_RE.match(text.strip()))


def path_key(text: str) -> str:
    """Comparable form of a path: `/` separators, no trailing slash; Windows forms lower-cased as `c:/...`."""
    s = text.strip().replace("\\", "/")
    s = DRIVE_RE.sub(lambda m: f"{m.group(1)}:", s, count=1)
    if len(s) > 1:
        s = s.rstrip("/") or "/"
    if re.match(r"^[A-Za-z]:(?:/|$)", s):
        s = s.lower()
    return s


def clean_path(text: str) -> str:
    """`/` separators with `/./` and repeated slashes collapsed, so a spelled-out path cannot dodge a check."""
    s = text.strip().replace("\\", "/")
    while "/./" in s:
        s = s.replace("/./", "/")
    return re.sub(r"/{2,}", "/", s)


def is_under_jobs(path: str) -> bool:
    """True when `path` has a /.claude/jobs/ part (that folder is deleted by cleanup)."""
    return JOBS_PART in path_key(clean_path(path)) + "/"


def is_volatile(path: str) -> bool:
    """True for background-task output under /tmp/claude-* (cleared with /tmp)."""
    return clean_path(path).startswith(VOLATILE_PREFIXES)


def item_id(name: str) -> "str | None":
    """The id in a `<name> (<id>)` value, or None when there is none or it cannot be an id."""
    m = NAME_ID_RE.search(name)
    value = m.group(1).strip() if m else ""
    if not ID_RE.match(value) or value.lower() in NOT_IDS:
        return None
    return value


def section_body(lines: "list[str]", title: str) -> "str | None":
    """Body of `## <title>`, or None when the section is absent."""
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"## {title}")
    except StopIteration:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end])


def session_fields(body: str) -> "dict[str, tuple[str, list[str]]]":
    """`- **Label:** value` lines of Session State -> label: (inline value, indented continuation lines)."""
    fields, current = {}, None
    for line in body.split("\n"):
        m = FIELD_RE.match(line)
        if m:
            current = m.group(1).strip()
            fields[current] = (m.group(2).strip(), [])
        elif current and (line.startswith((" ", "\t")) or not line.strip()):
            fields[current][1].append(line)
        else:
            current = None
    return fields


def live_items(block: "list[str]") -> "list[dict[str, str]]":
    """Parse `- name: ...` / `  key: value` lines into one dict per item.

    Only a bullet at the first bullet's indentation starts an item. A deeper bullet continues the last key,
    and an unknown `key:` line ends it, so neither creates a fake item nor changes a known value.
    """
    known = set(ITEM_KEYS) | set(SCHEDULED_KEYS) | set(OPTIONAL_KEYS)
    items, current, last, indent = [], None, None, None
    for line in block:
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if bullet and indent is None:
            indent = len(bullet.group(1))
        if bullet and len(bullet.group(1)) <= indent:
            current, last = {}, None
            items.append(current)
            rest = bullet.group(2).strip()
        elif bullet:
            if last:
                current[last] = f"{current[last]} {bullet.group(2).strip()}".strip()
            continue
        else:
            rest = line.strip()
        if not rest or current is None:
            continue
        m = re.match(r"^([a-z][a-z-]*):\s*(.*)$", rest)
        if m and m.group(1) in known:
            last = m.group(1)
            current[last] = m.group(2).strip()
        elif m:
            last = None
        elif last:
            current[last] = f"{current[last]} {rest}".strip()
    return items


def live_item_failures(label: str, items: "list[dict[str, str]]") -> "list[str]":
    problems, keys = [], LIVE_KEYS[label]
    for n, item in enumerate(items, 1):
        name = item.get("name", "").strip()
        who = f"{label} item '{name}'" if name and not is_placeholder(name) else f"{label} item #{n}"
        missing = [k for k in keys if k not in item]
        if missing:
            problems.append(f"{who} lacks key(s): {', '.join(missing)}")
        empty = [k for k in keys if k in item and is_placeholder(item[k])]
        if empty:
            problems.append(f"{who} has empty or placeholder value(s): {', '.join(empty)}")
        if label in ID_FIELDS and name and not is_placeholder(name) and item_id(name) is None:
            problems.append(f"{who} name lacks a usable id: write <name> (<id>), id of letters, digits, _ . : -")
        if "result" not in keys:
            continue
        result = item.get("result", "").strip("` ")
        if result and not is_placeholder(result):
            if not is_absolute_path(result):
                problems.append(f"{who} result is not an absolute path: {result[:60]}")
            elif is_under_jobs(result):
                problems.append(f"{who} result is under .claude/jobs/ (deleted on cleanup): copy it to a durable path")
            elif is_volatile(result):
                problems.append(f"{who} result is in a volatile location (/tmp/claude-*): copy it to a durable path")
    return problems


def session_state_failures(body: str, git: "bool | None" = None) -> "list[str]":
    """With git False (outside git) the git fields may be n/a or absent; the live fields are always required."""
    problems = []
    if is_placeholder(body):
        return ["Session State is empty"]
    fields = session_fields(body)
    for label in STATE_FIELDS:
        if label not in fields:
            if git is not False:
                problems.append(f"Session State lacks {label}")
            continue
        inline, rest = fields[label]
        value = " ".join([inline] + [ln.strip() for ln in rest])
        if is_placeholder(value) and not (git is False and value.strip().lower() == NA_WORD):
            problems.append(f"Session State field {label} has no value or a placeholder")
    if RETIRED_LIVE_FIELD in fields:
        problems.append(f"Session State uses the retired field {RETIRED_LIVE_FIELD}; use {', '.join(LIVE_FIELDS)}")
    for label in LIVE_FIELDS:
        if label not in fields:
            problems.append(f"Session State lacks {label}")
            continue
        inline, rest = fields[label]
        items = live_items(rest)
        if items:
            problems.extend(live_item_failures(label, items))
        elif not inline:
            problems.append(f"{label} has no value: write none or a list of items")
        elif inline.lower().split("(")[0].strip() not in NONE_WORDS:
            problems.append(f"{label} must be none or a list of items, got: {inline[:60]}")
    return problems


def head_value(lines: "list[str]", pattern: "re.Pattern[str]") -> "str | None":
    """First value of a `**Key:** value` line in the head (before the first `## `), or None when absent."""
    head = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    values = [m.group(1).strip().strip("`") for m in (pattern.match(l) for l in lines[:head]) if m]
    return values[0] if values else None


def toplevel_failures(lines: "list[str]") -> "list[str]":
    """The head carries `**Toplevel:** <absolute path>`."""
    value = head_value(lines, TOPLEVEL_RE)
    if value is None:
        return ["head line **Toplevel:** is missing (the project key handoff_state.py printed)"]
    if is_placeholder(value) or not is_absolute_path(value):
        return [f"**Toplevel:** is not an absolute path: {value[:60]}"]
    return []


def continues_path(lines: "list[str]") -> "Path | None":
    """The previous handoff `**Continues:**` names, when it is an existing file; None otherwise."""
    value = head_value(lines, CONTINUES_RE)
    if value is None or is_placeholder(value) or value.lower() == "none" or not is_absolute_path(value):
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def continues_failures(lines: "list[str]", self_path: "Path | None" = None) -> "list[str]":
    """The head carries `**Continues:** none` or the absolute path of an existing previous handoff:
    not this file itself, and of the same `**Toplevel:**` when both files carry one."""
    value = head_value(lines, CONTINUES_RE)
    if value is None:
        return ["head line **Continues:** is missing (none, or the previous handoff's absolute path)"]
    if value.lower() == "none":
        return []
    if is_placeholder(value) or not is_absolute_path(value):
        return [f"**Continues:** is neither none nor an absolute path: {value[:60]}"]
    prev = Path(value).expanduser()
    if not prev.is_file():
        return [f"**Continues:** names a file that does not exist: {value[:100]}"]
    if self_path is not None and prev.resolve() == self_path.resolve():
        return ["**Continues:** names this file itself; name the handoff this session started from, or none"]
    prev_lines = read_lines(prev)
    mine, theirs = head_value(lines, TOPLEVEL_RE), head_value(prev_lines or [], TOPLEVEL_RE)
    if mine and theirs and path_key(mine) != path_key(theirs):
        return [f"**Continues:** names a handoff of another **Toplevel:** ({theirs[:100]})"]
    return []


def read_lines(path: Path) -> "list[str] | None":
    """Lines of a file, or None when it cannot be read as UTF-8."""
    try:
        return path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return None


def goal_fields(lines: "list[str]") -> "dict[str, str] | None":
    """`Goal (carried)` as key -> value (inline plus continuation lines), or None when the section is absent."""
    body = section_body(lines, GOAL_SECTION)
    if body is None:
        return None
    return {k: " ".join([inline] + [ln.strip() for ln in rest if ln.strip()]).strip()
            for k, (inline, rest) in session_fields(body).items()}


def change_entries(value: str) -> "list[str]":
    """Entries of a `Changes` value: `none` is no entry; `| ` joins several."""
    if value.strip().lower() == "none":
        return []
    return [e for e in CHANGE_SPLIT_RE.split(value.strip()) if e.strip()]


def goal_failures(lines: "list[str]") -> "list[str]":
    """Goal (carried): four real values, and no silent drift from the previous handoff of the chain."""
    goal = goal_fields(lines)
    if goal is None:
        return [f"{GOAL_SECTION} section is missing (Origin, Goal, Done-when, Changes)"]
    problems = []
    for key in GOAL_KEYS:
        if key not in goal:
            problems.append(f"{GOAL_SECTION} lacks {key}")
        elif is_placeholder(goal[key]):
            problems.append(f"{GOAL_SECTION} {key} has no value or a placeholder")
    if not problems and not is_absolute_path(goal["Origin"]):
        problems.append(f"{GOAL_SECTION} Origin does not start with an absolute path: {goal['Origin'][:60]}")
    if problems:
        return problems
    prev_path = continues_path(lines)
    prev_lines = read_lines(prev_path) if prev_path else None
    prev = goal_fields(prev_lines) if prev_lines else None
    if prev is None or not all(k in prev for k in GOAL_KEYS):
        return []
    before, now = change_entries(prev["Changes"]), change_entries(goal["Changes"])
    if len(now) < len(before):
        return [f"{GOAL_SECTION} Changes lost {len(before) - len(now)} entr(ies) of the previous handoff; entries are never removed"]
    if now[:len(before)] != before:
        problems.append(f"{GOAL_SECTION} Changes rewrote an entry of {prev_path.name}; earlier entries are copied unchanged, new ones appended")
    if len(now) > len(before):
        if goal["Origin"] != prev["Origin"]:
            problems.append(f"{GOAL_SECTION} Origin differs from {prev_path.name}; Origin is copied unchanged")
        unquoted = [e[:60] for e in now[len(before):] if not QUOTE_RE.search(e)]
        return problems + [f"{GOAL_SECTION} Changes entry has no quoted user sentence: {e}" for e in unquoted]
    changed = [k for k in GOAL_SAME_KEYS if goal[k] != prev[k]]
    if changed:
        problems.append(f"{GOAL_SECTION} changed without a Changes entry ({', '.join(changed)} differ from {prev_path.name})")
    return problems


def live_keys(lines: "list[str]") -> "list[tuple[str, str]]":
    """(name, key) of every item in the three live fields; the key is the id, or the whole name without one."""
    body = section_body(lines, "Session State")
    fields = session_fields(body) if body else {}
    out = []
    for label in LIVE_FIELDS:
        for item in live_items(fields.get(label, ("", []))[1]):
            name = item.get("name", "").strip()
            if name and not is_placeholder(name):
                out.append((name, item_id(name) or name))
    return out


def closed_keys(lines: "list[str]") -> "set[str]":
    body = section_body(lines, CLOSED_SECTION) or ""
    pointers = [m for m in (CLOSED_RE.match(ln) for ln in body.split("\n")) if m]
    return {k.strip() for m in pointers for k in m.group(2).split(",") if k.strip()}


def live_chain_failures(lines: "list[str]") -> "list[str]":
    """Every live item of the previous handoff is carried (same id) or closed (its id among the Closed keys)."""
    prev_path = continues_path(lines)
    prev_lines = read_lines(prev_path) if prev_path else None
    if not prev_lines:
        return []
    kept = {key for _, key in live_keys(lines)} | closed_keys(lines)
    return [f"Live item {name} from the previous handoff is missing (carry it or close it)"
            for name, key in live_keys(prev_lines) if key not in kept]


def closed_failures(lines: "list[str]") -> "list[str]":
    """Closed (pointers), when present: each pointer names an existing absolute history file holding its keys."""
    body = section_body(lines, CLOSED_SECTION)
    if body is None:
        return []
    pointers = [m for m in (CLOSED_RE.match(ln) for ln in body.split("\n")) if m]
    if not pointers:
        return [f"{CLOSED_SECTION} has no `- <N> items closed → <history file> (keys: a, b)` line; omit the section when nothing was closed"]
    problems = []
    for m in pointers:
        target, keys = m.group(1).strip("` "), [k.strip() for k in m.group(2).split(",") if k.strip()]
        if not is_absolute_path(target):
            problems.append(f"{CLOSED_SECTION} history path is not absolute: {target[:60]}")
            continue
        path = Path(target).expanduser()
        if not path.is_file():
            problems.append(f"{CLOSED_SECTION} history file does not exist: {target[:100]}")
            continue
        if not keys:
            problems.append(f"{CLOSED_SECTION} lists no keys for {path.name}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            problems.append(f"{CLOSED_SECTION} history file is unreadable: {target[:100]}")
            continue
        missing = [k for k in keys if f" · {k} · " not in text]
        if missing:
            problems.append(f"{CLOSED_SECTION} keys not found in {path.name}: {', '.join(missing)}")
    return problems


def next_step_missing(lines: "list[str]") -> bool:
    """True when Exact Next Step is absent, empty, only placeholder lines, or one [...] or <...> block."""
    nxt = section_body(lines, "Exact Next Step") or ""
    real = [ln for ln in nxt.split("\n") if not is_placeholder(ln)]
    block = "\n".join(real).strip()
    return not real or (block[:1], block[-1:]) in (("[", "]"), ("<", ">"))


def resume_contract_failures(lines: "list[str]", git: "bool | None" = None, path: "Path | None" = None) -> "list[str]":
    """What /resume-handoff needs from the file itself. Empty list when satisfied."""
    problems = (toplevel_failures(lines) + continues_failures(lines, path) + goal_failures(lines)
                + closed_failures(lines) + live_chain_failures(lines))
    state = section_body(lines, "Session State")
    if state is not None:
        problems.extend(session_state_failures(state, git))
    elif git:
        problems.append("Session State section is missing inside a git repository")
    elif git is False:
        problems.append("Session State section is missing: outside git it still carries the three Live fields")
    if next_step_missing(lines):
        problems.append("Exact Next Step is missing, empty, or only placeholders")
    return problems


def split_cells(line: str) -> "list[str]":
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def decisions_failures(lines: "list[str]") -> "list[str]":
    """Decisions Made table: header, separator, and data rows with three filled cells. Empty when absent or the no-decisions sentence alone."""
    body = section_body(lines, "Decisions Made")
    if body is None:
        return []
    rows = [ln for ln in body.split("\n") if ln.strip().startswith("|")]
    if not rows:
        text = [ln.strip() for ln in body.split("\n") if ln.strip() and not set(ln.strip()) <= set("-—=")]
        if len(text) == 1 and (text[0].rstrip(".").strip() == NO_DECISIONS.rstrip(".")
                               or text[0].lower() in NO_DECISIONS_WORDS):
            return []
        if not text:
            return ["Decisions Made has no table rows"]
        return [f'Decisions Made has prose but no table: add a | What | Why | How | table, or write only "{NO_DECISIONS}"']
    if len(rows) < 2 or not SEPARATOR_RE.match(rows[1].strip()):
        return ["Decisions Made table lacks its header and separator rows (| What | Why | How |)"]
    if len(rows) == 2:
        return ["Decisions Made table has no data rows"]
    problems = []
    for n, row in enumerate(rows[2:], 1):
        cells = split_cells(row)
        if len(cells) != 3:
            problems.append(f"Decisions Made row {n} has {len(cells)} cell(s), expected 3 (What | Why | How)")
            continue
        bad = [name for name, cell in zip(("What", "Why", "How"), cells) if is_placeholder(cell)]
        if bad:
            problems.append(f"Decisions Made row {n} has empty or placeholder cell(s): {', '.join(bad)}")
    return problems


def items_of(section_lines: "list[str]") -> "list[str]":
    items, current = [], []
    for line in section_lines:
        if re.match(r"^\s*(?:[-*]|\d+\.)\s+", line):
            if current:
                items.append("\n".join(current))
            current = [line]
        elif current and (line.startswith((" ", "\t")) or line.strip() == ""):
            current.append(line)
        else:
            if current:
                items.append("\n".join(current))
                current = []
    if current:
        items.append("\n".join(current))
    return items


def passes(item: str) -> bool:
    low = item.lower()
    if DESIGN_PATH_RE.search(item):
        return True
    return all(any(label in low for label in pair) for pair in LABELS)


def parse_argv(argv: "list[str]") -> "tuple[str, bool | None] | None":
    """(session file, git flag or None), or None on a usage error."""
    files, git, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--git" and i + 1 < len(argv) and argv[i + 1] in ("yes", "no"):
            git, i = argv[i + 1] == "yes", i + 2
        else:
            files.append(argv[i])
            i += 1
    return (files[0], git) if len(files) == 1 else None


def main(argv: "list[str]") -> int:
    parsed = parse_argv(argv)
    if parsed is None:
        print(__doc__)
        return 2
    path, git = Path(parsed[0]).expanduser(), parsed[1]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read {path}: {exc}")
        return 2
    lines = text.split("\n")
    size = len(text.encode("utf-8"))
    if size > SIZE_LIMIT_KIB * 1024:
        print(f"WARNING: handoff file is {-(-size // 1024)} KiB (over {SIZE_LIMIT_KIB} KiB)")
    failures = []
    for title in SECTIONS:
        try:
            start = next(i for i, l in enumerate(lines) if l.strip() == f"## {title}")
        except StopIteration:
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        for item in items_of(lines[start + 1:end]):
            flat = " ".join(item.split())
            if len(flat) < MIN_ITEM_CHARS or flat.lower().startswith(("- none", "- 없음", "- no ")):
                continue
            if not passes(item):
                failures.append((title, flat[:120]))
    decisions = decisions_failures(lines)
    contract = resume_contract_failures(lines, git, path)
    if failures:
        print(f"[FAIL] {len(failures)} item(s) lack what/why/how or a design-file path:")
        for title, head in failures:
            print(f"  - [{title}] {head}")
    if decisions:
        print(f"[FAIL] {len(decisions)} Decisions table problem(s):")
        for msg in decisions:
            print(f"  - {msg}")
    if contract:
        print(f"[FAIL] {len(contract)} resume-contract problem(s):")
        for msg in contract:
            print(f"  - {msg}")
    if failures or decisions or contract:
        return 1
    print("[PASS] open items carry what/why/how or a design path; Decisions table and resume contract satisfied")
    return 0


class _Tee:
    """Writes through to a stream and keeps a copy, so the run log gets the exact stdout lines."""

    def __init__(self, stream):
        self.stream, self.parts = stream, []

    def write(self, s):
        self.parts.append(s)
        return self.stream.write(s)

    def flush(self):
        self.stream.flush()

    def lines(self) -> "list[str]":
        return "".join(self.parts).splitlines()


def log_run(record_args: dict) -> None:
    """Append one run record; a failure only prints a WARNING line on stderr."""
    try:
        spec = importlib.util.spec_from_file_location("run_log", Path(__file__).with_name("run_log.py"))
        run_log = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run_log)
        reason = run_log.append_run(run_log.build_record(**record_args))
    except Exception as exc:  # a missing or broken run_log.py never changes the result
        reason = f"{type(exc).__name__}: {exc}"
    if reason:
        sys.stderr.write(f"WARNING: run log not written: {reason}\n")


def run(argv: "list[str]") -> int:
    """main() plus the run log; an unexpected exception prints its traceback tail and exits 2."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    tee = _Tee(sys.stdout)
    sys.stdout = tee
    try:
        code = main(argv)
    except Exception:
        tail = traceback.format_exc().rstrip("\n").splitlines()[-5:]
        sys.stderr.write("\n".join(tail) + "\n")
        code = 2
    finally:
        sys.stdout = tee.stream
    parsed = parse_argv(argv)
    log_run(dict(
        script="check_handoff.py", mode="check", cwd=str(Path.cwd()), toplevel=None, args=argv,
        exit_code=code, stdout_lines=tee.lines(),
        handoff=str(Path(parsed[0]).expanduser()) if parsed else None,
    ))
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
