# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are dates (`YYYY.MM.DD-N`), the
same value that every run writes to the run log as `version`.

## [2026.11.09-1] - 2026-11-09

### Added
- `Goal (carried)` has a fifth key, `Constraints`: what was decided not to do and what is out of
  scope. It is copied along the chain like the goal. New items may be added at the end, separated
  by `; `; existing items stay as they are unless a `Changes` entry with a user quote names
  `Constraints:`. `none` means no items.
- `check_handoff.py` fails on provider-specific secret values (GitHub, OpenAI-style `sk-`, Slack,
  AWS, Google API keys, and whole private key blocks). The message names the kind and the line or
  lines, never the value. Replace the token, or the whole key block, with `REDACTED` (no brackets).
- The resume briefing shows `Constraints` verbatim in the GOAL block.
- `README.ko.md` (Korean). `README.md` is now the English default, with a language switch at the
  top of both files.
- This changelog.

### Changed
- When `/resume-handoff` picks a handoff but a file of the same project that is as new or newer was
  passed over, it prints `WARNING: newer handoff skipped (<reason>): <file name>` and
  `continue: no (newer handoff skipped)`, even with `--c`. Reasons: `no usable Exact Next Step`,
  `empty file`, `project unknown`, `not valid UTF-8`.
- The size warning (`WARNING: handoff file is <N> KiB (over 16 KiB)`) now comes after the check
  result and is left out of the resume output. `/handoff` keeps an over-limit file once the
  never-trim list is respected.
- The secret failure block is printed before other failures, so the resume excerpt keeps it.
- `docs/field-map.md` is in English.

### Fixed
- A newer handoff with an empty or placeholder `Exact Next Step` was dropped silently and an older
  file was resumed with `--c`.
- A secret copied into `Goal (carried)` was locked along the chain: masking it counted as a goal
  change. Masked values now compare equal.
- A truncated failure message could print the start of a secret value.
- Adding a fifth `Goal (carried)` key no longer switches the chain comparison off when the previous
  handoff has only four keys.
- A kebab-case name that starts with `sk-` in a path or branch is no longer reported as an API key.
- Masking only the header line of a private key left its body in the file and passed; the whole
  block is now checked and masked.

## [2026.11.08-1] - 2026-11-08

### Added
- First release of `/handoff` and `/resume-handoff` as a canonical, cross-platform repository with
  `install.py` (dry-run by default, `--yes` to apply, backups, rollback, `--python` to choose the
  interpreter written into the command files).
- The continue decision comes only from the resume argument (`--c`, `--continue`, or the word `계속`).
- Live work is carried in three fields (`Live subagents`, `Live runners`, `Live scheduled`), each item
  with `name (id)`, `does`, `decision`, `result`, `done-when`, `after-return`. The resume script
  judges each subagent from its newest transcript copy.
- `Goal (carried)` is copied verbatim along the `**Continues:**` chain; closed items go to a history
  file and the handoff keeps only a pointer.
- Resume picks only handoffs of the current project (`**Toplevel:**`).
- Rule layers: core, then a user layer, then a project layer limited to four override points.
- Every run appends one JSON line to `~/.claude/handoffs/.state/runs.jsonl`.
