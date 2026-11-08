---
description: Write a session handoff file to ~/.claude/handoffs/ without stopping any running work, so a future session can resume with full context. Read back with /resume-handoff.
---

# Handoff Command

Capture what happened in this session and write it to a dated file that `/resume-handoff` can load.

`/handoff` and `/resume-handoff` are one pair. They do not call or depend on any other session
command.

## Ground rules

- `/handoff` writes one handoff file plus lines appended to one history file (Step 5), and nothing
  else, apart from durable copies of live results that would otherwise sit under `~/.claude/jobs/`
  (Step 2) and the design file of Step 1 (a location the project decides, outside its
  `forbidden-writes`). The history file is appended to, never written over: the command never rewrites,
  deletes, or edits existing lines in it.
- It never stops subagents, background jobs, monitors, or scheduled tasks, and never tells them
  to stop.
- It never runs `/compact`, opens or suggests a new session, or ends the session.
- It never asks a confirmation question. After writing, it reports the path, the live work, and
  whether the user can switch sessions now (`Resume: ready` or `not ready — <reason>`); the
  current task continues in the same turn.

## When to Use

- End of a work session before closing Claude Code
- Before hitting context limits, or when a context-usage check blocks the end of a response. Write
  the file and keep working; a new session or `/compact` happens only when the user asks for it
- After solving a problem whose reasoning would be expensive to rediscover
- Any time context must survive into a future session

## Process

### Step 0: Load state and rules

Run exactly one shell command:

```
python3 ~/.claude/scripts/handoff_state.py <cwd>
```

It prints toplevel, the `project key:` (the main worktree's top level for a linked worktree, the
top level for a git repository, the cwd outside git), whether the cwd is a linked worktree, the two
rules file paths, branch, upstream ahead/behind, a one-line git status summary, the
lockfile-changed flag, a `diffstat:` line, a `head:` line (the short commit id, or `none`), a
`previous handoff:` line (the most recent handoff of the same project key, or `none`; a candidate
for the `**Continues:**` header), a `history file:` line (the path of this project's history file
followed by `(exists)` or `(new)`, printed even when the file does not exist yet), and optionally a
`context-budget:` line. The status block never lists file names: the full list goes to the file
named on the `status file:` line.

Read the rules files it names, in this order, with the `Read` tool before anything else:

1. `user rules file: <path|none>`: `~/.claude/handoff-rules.local.md`, the user's own operating
   rules. It has no section restriction.
2. `rules file: <path|none>`: `<toplevel>/.claude/handoff-rules.local.md` (falling back to the main
   worktree), the project's rules.

**Rules apply in the order core, then user, then project.** A later layer wins wherever it
differs from an earlier one; everything it does not change still follows this file. The project
layer may change only the four override points below, each as a `## <id>` section. For any other
`## ` section the script prints `WARNING: rules file section outside override points: <title>`;
that section is not applied.

| Override point | What the project layer decides |
|---|---|
| `runner-completion` | How a running runner's completion is judged (read by `/resume-handoff`) |
| `state-files` | Extra state or design files to read |
| `forbidden-writes` | Paths this command must never write |
| `values` | Item-unit name, session file size cap, short-id format |

`/resume-handoff` runs the same script with `--resume`. Whatever this step prints is what the next
session will compare against, so `Session State` is written from this output and nothing else.

### Step 1: Gather what only this session knows

Before writing, collect:

- Files modified this session (recall from the conversation; the `status:` and `diffstat:` lines
  from Step 0 give the totals; when the names are needed, Read the file named on `status file:`)
- What was attempted, what failed, and the exact reason each failure happened
- Approaches that were consciously rejected, and why they were rejected
- Claims the user made about the system that are not visible in the code
- The goal chain. When this session started with `/resume-handoff`, take `Goal (carried)` from
  that handoff's section and copy it verbatim; Read that file again if it is no longer in context.
  Do not re-summarize it. Change `Goal` or `Done-when` only when the user said to change them, and
  keep the user's quote. Copy `Constraints` verbatim too; new items may be added at the end, separated by `; `, and existing
  items stay exactly as they are. Delete
  or edit existing `Constraints` text only when the user said to, and then keep the quote and add
  a `Changes` entry that contains `Constraints:`. When the session did not start from a handoff,
  the user's own words in this session become `Goal` (quote them), `Done-when`, and `Constraints`
- Whether the requirement changed during the session, and in what order
- The unresolved `Live` items of the previous handoff (the file `**Continues:**` will name; Read it
  again if needed). Each one is copied into this handoff or closed into the history file (key = its
  id), so it never drops out of the chain silently
- Work still running in the background: call `ListAgents` to see which subagents are running now
  (skip the call when the tool is not available), then collect each subagent, runner, and scheduled
  wait (monitor, cron, wakeup): its id, what it is doing, when it was spawned and when it should
  return (`started`, `expected-by`), and where its result will land (transcript, output file, or
  log path). Record only; do not stop or restart anything
- For every open work item and every decision, three lines: **what** (the end state), **why**
  (the user's own words, quoted), **how** (the mechanism and files). A title or action list alone
  is incomplete; the next session will shrink it into a smaller task than the one decided
- A design the user settled in conversation that is not yet in a durable project file (plan or
  design note): write it to that file first, then point the handoff at the path. Designs that live
  only in the conversation are what a handoff loses

**Which items go in the file (every section, not only live work):** leave an item out only when all
three hold: it has finished, its result is not used by any later step, and no decision depends on
it. When in doubt, include it. A closed item stays as one line with its result and evidence path
only when an open item still expects something from it.

An item that meets all three conditions is forgotten with a record: take it out of the handoff body
and append one line to the history file (Step 5). `What Did NOT Work` and `Discarded Hypotheses`
are never moved to the history file; they stay in the handoff because they still prevent retries.

Read a file before asserting its state when the session has been compacted three or more times
(for the Uncommitted field, that file is the one named on `status file:`). Degraded memory is the
main source of wrong handoffs.

### Step 2: Fill Session State from the Step 0 output

No further shell command. Map the Step 0 lines onto the fields:

| Step 0 line | Session State field |
|---|---|
| `toplevel: not a git repository` | keep Session State; write `n/a` in **Branch**, **Uncommitted changes**, **Unpushed commits**, **Lockfile changed**, and **HEAD** |
| `branch:` | **Branch** |
| `upstream: … ahead N behind M` / `upstream: none` | **Unpushed commits** (`N` · "none" · "no upstream configured") |
| `status:` + `status file:` + `diffstat:` | **Uncommitted changes**: names of the files this session changed, then the count of the rest (`status:` gives the total; the full list is in `status file:`) |
| `lockfile changed:` | **Lockfile changed** |
| `head:` | **HEAD** (`none` when it prints `none`) |

Live work comes from the conversation, not from the script, and the three Live fields are always
written, inside a git repository or not. Write `Live subagents`, `Live
runners`, and `Live scheduled` (monitor, cron, and wakeup waits that have no result path) as three
separate fields, each `none` or a list of items. Subagent and runner items have six keys; a
scheduled item has the same keys with `fires-at` in place of `result`:

| Key | Value |
|---|---|
| `name` | Subagent and runner: `<name> (<id>)`, the id in parentheses being the agent id, task id, shell id, or PID (required; the checker looks for it, and the next session finds the subagent transcript `agent-<id>.jsonl` by it). Scheduled: the name of the wait |
| `does` | What it is doing |
| `decision` | The decision it serves: a `What` value from the Decisions table, or a design file path |
| `result` | Absolute path where the result lands (subagent: its transcript `…/subagents/agent-<id>.jsonl`, or the file it writes its result to; runner: log file) |
| `fires-at` | Scheduled only: the time or trigger at which it fires |
| `done-when` | The one observation that means it has returned (a log's last line, a marker file, a transcript stop reason) |
| `after-return` | What to do once it has returned |
| `started`, `expected-by` | Optional: spawn time and expected return time. Not checked |

`result` must be an absolute path (`/…`, `~/…`, or a Windows `C:\…`, `C:/…`, `/c/…`, `/mnt/c/…`
path), never a sentence. A `result` under `.claude/jobs/` fails the checker, because that location
is cleaned up automatically. A `result` under `/tmp/claude-` or `/private/tmp/claude-` (background
task output) fails the same way. For a finished item whose result sits there, copy the result to a
durable location (for example `~/.claude/handoffs/results/<short-id>-<name>.md`, written with Write)
and put the copy's path in `result`. For an item still running whose output will land there, put
the jobs path in `done-when` (where to watch), put the durable destination in `result`, and make
the first step of `after-return` "copy the jobs result to `result`". The next session judges live
work from those paths alone.

Never invent a value. Do not collect listening ports, Docker state, migration status, or CI
results; they cost network and database calls, and several of the usual commands are Linux-only.

### Step 3: Locate the folder

The handoff folder is `~/.claude/handoffs/`. Write creates the parent folder when the file is
written, so no separate folder-creation command is needed. Never write into
`~/.claude/session-data/` or any other folder owned by a plugin, and never into a path the project
layer lists under `forbidden-writes`.

### Step 4: Choose the filename

Write to `~/.claude/handoffs/YYYY-MM-DD-<short-id>-session.tmp` using today's actual date.

The short-id rules:

- Allowed: letters `a-zA-Z`, digits `0-9`, hyphens `-`, underscores `_`
- First character must be a letter, digit, or underscore
- Use 8 or more lowercase characters to avoid same-day collisions
- The project layer may replace this format through its `values` section

Valid example: `2026-09-16-a1b2c3d4-session.tmp`

Getting this wrong means `/resume-handoff` will not find the file.

### Step 5: Write the file

Use the format below. Core sections are always present; write "None" rather than deleting them,
because an honest empty section tells the next session that the question was considered.

Put the `project key:` value from Step 0 in the `**Toplevel:**` header line, right after
`**Project:**`. `/resume-handoff` uses it to pick only this project's files.

Put the handoff this session started from in the `**Continues:**` header line, right after
`**Toplevel:**`: the file `/resume-handoff` loaded at the start of this session, or `none` when the
session did not start that way. The `previous handoff:` line from Step 0 is a candidate; confirm it
against how this session actually started.

Write `## Goal (carried)` before `## What We Are Building`, with its five keys:

- `Origin`: the absolute path of the first handoff of the chain, and its date. The first handoff of
  a chain writes its own absolute path, so every later copy stays byte-identical and still correct.
- `Goal` and `Done-when`: copy the previous file's values verbatim. Do not re-summarize them. Change
  them only when the user said to change the goal or the done condition in this session; then add
  the date, the user's quote, and `<before> → <now>` to `Changes`, and keep the earlier `Changes`
  entries and `Origin` as they are. Also record the same event in `Requirement Changes`, and have
  each of the two sections point at the other.
- `Constraints`: what was decided not to do, and what is out of scope. Copy the previous file's
  value verbatim; new items may be added at the end, separated by `; `, and existing items stay exactly as
  they are. `none` means zero items. To delete or edit existing `Constraints` items, the
  user must have said so in this session; keep the user's quote and add a `Changes` entry that
  contains `Constraints:`, the date, and `<before> → <now>`.
- `Changes`: `none` until the user changes the goal.

When the previous file has no `Goal (carried)` (it was written in an older format), build the
section from its `What We Are Building` and the user's own words, and set `Origin` to that file.

**Size cap.** The cap is the project `values` size cap, or 16 KiB when `values` sets none. When the
file would exceed it, shorten in this order and stop as soon as it fits:

1. Closed items that can move to the history file.
2. `What WORKED` rows that no open item leans on.
3. `Current State of Files` rows for files that are already committed.
4. Long quotes in the `Why` cells of Decisions Made, shortened.

Never trim these: `Goal (carried)`, `Live` items, `What Did NOT Work`, `Discarded Hypotheses`,
`Exact Next Step`, and the what, why and how of an open item.

**History file.** Append one line per forgotten item to the file named on the `history file:` line
of Step 0, in this form, and skip an item whose key is already in the history file:

```
<YYYY-MM-DD> · <key> · <what ended> · <result or evidence path>
```

If the `history file:` line ends in `(new)`, create the file with Write (the parent folder is
created with it). If it ends in `(exists)`, append with a quoted heredoc (`cat >> "<history file>" <<'EOF'`) so the existing
lines stay as they are and the shell does not expand anything in the new lines. Then add
`## Closed (pointers)` to the handoff with one line (`- <N> items closed → <history file path>
(keys: a, b, c)`). When nothing was closed, omit the section.

The four added sections (Discarded Hypotheses, User Assumptions, Requirement Changes,
Environment & Setup Notes) are conditional. Omit an added section entirely when it has no content,
since an empty one carries no signal.

If Step 0 printed a `context-budget:` line, put it as the first line of the file as
`<!-- context-budget: … -->`. If it printed none, write no such line.

### Step 6: Check, report and continue

Run `python3 ~/.claude/scripts/check_handoff.py <file> --git yes|no` (`no` only when Step 0
printed `toplevel: not a git repository`). It exits 1 and lists every open item that lacks the
what/why/how lines or a design-file path, every Decisions row with an empty `What`, `Why`, or `How`
cell, and every break of the resume contract (a missing or relative `**Toplevel:**` line, Session
State without its eight fields (the seven earlier ones and `**HEAD:**`; the five git fields may be
`n/a` outside git), a live item missing one of
its six keys, a `name` without its `(<id>)`, or a `result` that is not an absolute path, a missing
or placeholder-only `Exact Next Step`), and every break of the goal chain: a `Goal (carried)`
without its five keys, a `Goal`, `Done-when`, or `Origin` that differs from the `**Continues:**`
file without a matching `Changes` entry, a `Constraints` whose items do not start with all of the previous items unchanged
(new items are added at the end, separated by `; `) and has no `Changes` entry that contains `Constraints:`, a `**Continues:**` that is
neither `none` nor an existing absolute path, a `Closed (pointers)` path that does not exist or
lacks a listed key, and a Live item of the previous handoff that is neither carried nor closed
(`Live item <name> from the previous handoff is missing`). It also fails on a secret value in a
provider-specific format: a token, or a private key block (from the `BEGIN … PRIVATE KEY` line to
its `END` line, or a run of three or more long base64 lines). It prints the kind and the line or
lines and never prints the value. The fix is to replace the token, or the whole key block, with
`REDACTED` (no brackets); replacing only the header line still fails. These failures are printed
before every other failure. The
`Goal (carried)` comparison does not count that `REDACTED` replacement as a change. Fix those
items and run it again; do not report until it exits 0. The checker also prints
`WARNING: handoff file is <N> KiB (over 16 KiB)` for a large file, after the result block
(`[PASS]` or `[FAIL]` and its list); the exit code does not change. Shorten the file as Step 5
describes. Keep the never-trim list; a file that still exceeds the cap after that is left as it
is. The checker's limit is fixed at 16 KiB. The project `values` size cap is used only for the
Step 5 shortening order, and the checker never reads it.
`/resume-handoff` runs the same checker on load, so a file that passes here is a file the next
session can resume with one command.

Both scripts append one JSON line per run to `~/.claude/handoffs/.state/runs.jsonl` (the handoff
file's path, never its body). When the user later asks what happened, read that file together with
the handoff file it names.

Report three lines, without printing the file or asking anything:

```
Handoff saved to [resolved path]
Live work: [n] subagent(s), [m] runner(s), [k] scheduled, not yet finished
Resume: ready — run `/clear`, then `/resume-handoff <short-id>`
```

The third line is the user's cue to switch sessions. Write `ready` only when all of these hold;
otherwise write `not ready — <which condition failed>`, fix it, and run `/handoff` again:

- The checker above exited 0.
- Every live item in the file names where its result lands (an absolute path that is not under
  `~/.claude/jobs/`) or, for a scheduled item, when it fires, so the next session can judge it
  without this one. In-process subagents and background jobs survive `/clear`; their transcripts
  continue under the new session's directory.
- Nothing decided or designed in this conversation is missing from the file or from a durable
  file the handoff points at.
- No write to the handoff file is still pending (a lane whose result you are about to fold in
  within the next minute: fold it in first, then report).

`/handoff` never runs `/clear` itself. The user decides when to switch.

Then continue the task that was in progress. If the user later points out a mistake in the file,
rewrite the file with the correction.

## Safety rules

- Record environment variable names only, never their values.
- Do not paste diffs into the file. Reference `path:L10-L45` and say what changed and why.
- If an error message or command contains a credential, redact it with `REDACTED` (no brackets) and
  say that it was redacted. The Step 6 checker scans the file for provider-specific secret formats
  mechanically and fails on a match.
- The file lives in the home directory, never inside the repository.

---

## Session File Format

```markdown
<!-- context-budget: … -->   (optional first line: only when Step 0 printed a context-budget: line)
# Session: YYYY-MM-DD

**Started:** [approximate time if known]
**Last Updated:** [current time]
**Project:** [project name or path]
**Toplevel:** [the `project key:` value from Step 0, an absolute path]
**Continues:** [absolute path of the handoff this session started from | none]
**Topic:** [one-line summary of what this session was about]

---

## Confidence

[high | medium | low]

- `high`: 0 or 1 compactions, nothing important inferred
- `medium`: exactly 2 compactions, or some context reconstructed rather than recalled
- `low`: 3 or more compactions. The next session verifies against the code before acting

---

## Session State

[Outside a git repository, write `n/a` in the five git fields (Branch, HEAD, Uncommitted changes,
Unpushed commits, Lockfile changed); the three Live fields stay required.]

- **Branch:** [branch name]
- **HEAD:** [short commit id | none]
- **Uncommitted changes:** [names of files this session changed · count of the rest | "none"]
- **Unpushed commits:** [list | "none" | "no upstream configured"]
- **Lockfile changed:** [yes, install before running anything | no]
- **Live subagents:** none | list of items, each:
  - name: [<name> (<id>)]
    does: [what it is doing]
    decision: [a `What` value from Decisions Made, or a design file path]
    result: [absolute path]
    done-when: [observation that means it has returned]
    after-return: [what to do then]
    started: [optional]
    expected-by: [optional]
- **Live runners:** none | list of items, same keys (`name`: `<runner name> (<id>)`, `result`: log path)
- **Live scheduled:** none | list of items, same keys with `fires-at` in place of `result`

---

## Goal (carried)

- **Origin:** [absolute path of the first handoff of the chain] · [YYYY-MM-DD]
- **Goal:** [the original goal; on the first handoff, include the user's own words quoted]
- **Done-when:** [the done condition; several conditions separated by `; `]
- **Constraints:** [what was decided not to do, and what is out of scope; `none` when there is nothing; several separated by `; `]
- **Changes:** [none | YYYY-MM-DD "[user's quote]" : [before → now]; several entries joined by `| `]

---

## What We Are Building

[1-3 paragraphs. Enough that someone with zero memory of this session understands the goal:
what it does, why it is needed, how it fits the larger system.]

---

## What WORKED (with evidence)

[Only confirmed items. Each needs the evidence that confirms it. Without evidence, move the item
to "What Has NOT Been Tried Yet" instead.]

- **[thing that works]** — confirmed by: [test name, HTTP status, observed output]

If nothing is confirmed: "Nothing confirmed working yet."

---

## What Did NOT Work (and why)

[Technical failures. The exact reason, so the next session does not retry them.
"Threw X because Y" is useful. "Did not work" is not.]

- **[approach]** — failed because: [exact error or reason]

If nothing failed: "No failed approaches yet."

---

## Discarded Hypotheses

[Omit when empty. Approaches rejected by choice rather than by failure. These are different from
the section above: nothing broke, the direction was reasoned away. Record the reasoning, or it
will be proposed again next session.]

- **[approach]** — rejected because: [reasoning]

---

## What Has NOT Been Tried Yet

[Promising directions nobody attempted. Specific enough to act on. Each item that was decided
with the user carries its three lines: what (end state), why (the user's words), how (mechanism
and files), or a path to the design file that holds them.]

- [approach] — what: [end state] · why: "[user's words]" · how: [mechanism, files]

If nothing is queued: "No specific untried approaches identified."

---

## Current State of Files

| File | Status | Notes |
| --- | --- | --- |
| `path/to/file.ts` | Complete | [what it does] |
| `path/to/file.ts` | In progress | [what is done, what is left] |
| `path/to/file.ts` | Broken | [what is wrong] |
| `path/to/file.ts` | Not started | [planned, untouched] |

Use concrete paths, never globs. Add `:L10-L45` when a specific range matters.

If no files were touched: "No files modified this session."

---

## Decisions Made

[Choices and their reasons, so the next session does not relitigate them. Every row has all three
cells filled; `How` may be a design file path.]

| What | Why | How |
|---|---|---|
| [the end state decided] | [why this over the alternatives, the user's words quoted] | [mechanism and files, or a design file path] |

If none: "No major decisions made this session."

---

## User Assumptions

[Omit when empty. Statements the user made about the system that are not verifiable in the code.
Mark every one, because the next session will otherwise treat them as established fact.]

- [statement] `[user-stated — not in code]`

---

## Requirement Changes

[Omit when the scope never moved. List every state in order, not just the first and last.]

- State 1: [initial requirement]
- State 2: [what changed and why]
- State N (current): [where it stands now]

---

## Blockers & Open Questions

[Unresolved items the next session must address. Questions raised and not answered.
External dependencies being waited on.]

- [blocker or question]

If none: "No active blockers."

---

## Closed (pointers)

[Optional. Omit when nothing was closed.]

- [N] items closed → [absolute path of the history file] (keys: [a, b, c])

---

## Exact Next Step

[The single most important thing to do on resuming, precise enough that no thinking is needed
to start. If any live item exists, step 1 is to judge it as `/resume-handoff` Step 3 describes
(returned, running, or lost) before the rest. If genuinely unknown, say so and point at the sections that
decide the direction.]

---

## Environment & Setup Notes

[Omit unless the project needs something non-obvious: commands to run it, services that must be
up, environment variable names that must be set. Names only, never values.]
```

---

## Notes

- Every session gets its own handoff file. Never append to a previous handoff file. Running
  `/handoff` again in the same session rewrites that session's file as a whole. The history file is
  the one exception: it only grows by appended lines.
- "What Did NOT Work" and "Discarded Hypotheses" are the sections that pay for this command.
  Everything else can be rediscovered by reading the code; those two cannot.
- "Exact Next Step" is written as an instruction on purpose. `/resume-handoff` briefs the user and
  waits before acting unless it is run with the continue argument (`--c` or `계속`), so a direct next
  step does not cause the next session to run off on its own. Whether to continue is decided by
  that argument alone, never by anything written in this file.
- Read the file back with `/resume-handoff`. It reads the whole file, so every section carries over.
