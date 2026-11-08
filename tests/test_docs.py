import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDOFF = ROOT / "commands" / "handoff.md"
RESUME = ROOT / "commands" / "resume-handoff.md"
README = ROOT / "README.md"
FIELD_MAP = ROOT / "docs" / "field-map.md"

# Only used when git cannot list the files; with git, the ignore rules decide.
SKIP_DIRS = {".git", "scratch", "__pycache__"}

# Forbidden patterns are split so this file never matches itself.
FORBIDDEN = [
    ("E" + "BS", re.compile("E" + "BS")),
    ("eb" + "sap", re.compile("eb" + "sap")),
    ("ky" + "jin", re.compile("ky" + "jin")),
    ("migration" + "-docs", re.compile("migration" + "-docs")),
    ("/Us" + "ers/", re.compile("/Us" + "ers/")),
    ("K" + "-<digit>", re.compile("K" + "-[0-9]")),
    ("co" + "dex", re.compile("co" + "dex", re.I)),
    ("g" + "pt-", re.compile("g" + "pt-", re.I)),
    ("O" + "MC", re.compile("O" + "MC")),
    ("oh-my-" + "claudecode", re.compile("oh-my-" + "claudecode")),
    ("회사" + "망", re.compile("회사" + "망")),
    ("Git" + "Lab", re.compile("Git" + "Lab", re.I)),
    ("start" + "_runner", re.compile("start" + "_runner")),
    ("k" + "_gate", re.compile("k" + "_gate")),
    ("agent" + "_gate", re.compile("agent" + "_gate")),
    ("." + "omc", re.compile(r"\." + "omc" + r"\b")),
    ("rebuild" + "coord", re.compile("rebuild" + "coord")),
    ("context" + " guard", re.compile("context" + " guard", re.I)),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"(?!example\.invalid)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("gh token", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{20,}")),
    ("sk token", re.compile("sk" + r"-[A-Za-z0-9]{20,}")),
]


def read(path):
    return path.read_text(encoding="utf-8")


def paragraphs(text):
    return re.split(r"\n\s*\n", text)


class Base(unittest.TestCase):
    longMessage = False

    def has(self, needle, text=None):
        text = self.text if text is None else text
        self.assertTrue(needle in text, f"missing: {needle!r}")

    def lacks(self, needle, text=None):
        text = self.text if text is None else text
        self.assertFalse(needle in text, f"unexpected: {needle!r}")

    def matches(self, pattern, text=None, flags=0):
        text = self.text if text is None else text
        self.assertTrue(re.search(pattern, text, flags), f"no match: {pattern!r}")


class HandoffDocTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.text = read(HANDOFF)

    def test_G2_no_git_diff_stat_head(self):
        self.lacks("git diff --stat HEAD")

    def test_spec2_no_auto_resume(self):
        self.lacks("Auto-resume")

    def test_M12_A2_decisions_table_header(self):
        self.has("| What | Why | How |")

    def test_A3_live_subagents_and_runners_with_six_keys(self):
        self.has("Live subagents")
        self.has("Live runners")
        for key in ("name:", "does:", "decision:", "result:", "done-when:", "after-return:"):
            self.has(key)

    def test_A3_no_live_background_work(self):
        self.lacks("Live background work")

    def test_G4_jobs_results_copied_to_durable_location(self):
        hits = [
            p for p in paragraphs(self.text)
            if ".claude/jobs/" in p and re.search(r"copy|복사", p, re.I)
        ]
        self.assertTrue(hits, "no paragraph mentions .claude/jobs/ together with copy/복사")

    def test_M11_no_mkdir_write_creates_parent(self):
        self.lacks("mkdir")
        lines = [l for l in self.text.splitlines() if "Write" in l and re.search(r"parent|부모", l)]
        self.assertTrue(lines, "no line says Write creates the parent folder")

    def test_rules_layer_both_files_mentioned(self):
        self.has("user rules file:")
        self.matches(r"(?<!user )rules file:")

    def test_rules_layer_apply_order(self):
        en = re.search(r"core.{0,80}user.{0,80}project", self.text, re.I | re.S)
        ko = re.search(r"핵심.{0,80}사용자.{0,80}프로젝트", self.text, re.S)
        self.assertTrue(en or ko, "no core -> user -> project order statement")

    def test_H5_toplevel_header_right_after_project(self):
        self.matches(r"\*\*Project:\*\*[^\n]*\n\*\*Toplevel:\*\*")
        self.has("project key:")

    def test_H4_live_scheduled_field_with_fires_at(self):
        self.has("Live scheduled")
        self.has("fires-at")
        self.matches(r"(?i)monitor.{0,40}cron.{0,40}wakeup", flags=re.S)

    def test_H4_runner_and_subagent_name_carry_id(self):
        self.assertGreaterEqual(self.text.count("(<id>)"), 2, "name `<name> (<id>)` not described for both fields")
        self.matches(r"(?i)runner.{0,120}\(<id>\)", flags=re.S)

    def test_M7_optional_keys_started_and_expected_by(self):
        self.has("started")
        self.has("expected-by")

    def test_M7_step1_uses_listagents(self):
        step1 = self.text.split("### Step 1", 1)[1].split("### Step 2", 1)[0]
        self.has("ListAgents", step1)

    def test_M7_exclusion_rule_covers_every_section(self):
        hits = [para for para in paragraphs(self.text) if para.startswith("**Which")]
        self.assertTrue(hits, "no paragraph starts with **Which")
        self.matches(r"(?i)every section", hits[0])

    def test_M9_status_file_is_read_in_step1(self):
        step1 = self.text.split("### Step 1", 1)[1].split("### Step 2", 1)[0]
        self.matches(r"(?s)Read.{0,200}`status file:`|`status file:`.{0,200}Read", step1)

    def test_H3_in_progress_jobs_result_goes_to_done_when(self):
        paras = [p for p in paragraphs(self.text) if ".claude/jobs/" in p and "done-when" in p and "after-return" in p]
        self.assertTrue(paras, "no paragraph ties jobs path, done-when and after-return")

    def test_M7_values_point_lists_short_id_format(self):
        rows = [l for l in self.text.splitlines() if l.startswith("| `values`")]
        self.assertTrue(rows and "short-id" in rows[0], "values row lacks short-id format")

    def test_M2_checker_gets_git_argument(self):
        self.matches(r"check_handoff\.py <file> --git yes\|no")

    def test_A12_run_log_location_documented(self):
        self.has("runs.jsonl")

    def test_L5_example_is_not_personal(self):
        self.lacks("context " + "guard")
        self.lacks("rebuild" + "coord")

    def test_H4_report_line_counts_scheduled(self):
        self.matches(r"Live work:[^\n]*scheduled")

    # --- 14. carried goal, chain, history, HEAD, length budget ---

    def _format_block(self):
        return self.text.split("## Session File Format", 1)[1]

    def _step(self, n):
        return self.text.split(f"### Step {n}", 1)[1].split("### Step", 1)[0].split("\n## ", 1)[0]

    def test_C1_goal_carried_section_before_what_we_are_building(self):
        block = self._format_block()
        self.has("## Goal (carried)", block)
        self.assertLess(block.index("## Goal (carried)"), block.index("## What We Are Building"))

    def test_C1_goal_carried_has_four_keys(self):
        carried = self._format_block().split("## Goal (carried)", 1)[1].split("\n---", 1)[0]
        for key in ("Origin", "Goal", "Done-when", "Changes"):
            self.has(f"- **{key}:**", carried)
        # The first file writes its own absolute path, so copies stay byte-identical and correct.
        self.assertNotIn("this file", carried)
        self.has("its own absolute path", self.text)

    def test_C1_continuing_handoff_copies_goal_verbatim(self):
        for n in (1, 5):
            step = self._step(n)
            self.matches(r"(?is)Goal \(carried\).{0,400}(verbatim|exactly as written)", step)
            self.matches(r"(?is)(not|never)[^.]{0,40}(re-?summar|reword|paraphras)", step)

    def test_C1_goal_changes_only_on_user_quote(self):
        step = self._step(5)
        self.matches(r"(?is)only when the user.{0,300}`Changes`", step)
        self.has("Requirement Changes", step)

    def test_C2_continues_header_right_after_toplevel(self):
        self.matches(r"\*\*Toplevel:\*\*[^\n]*\n\*\*Continues:\*\*")

    def test_C2_step0_prints_previous_handoff_history_and_head(self):
        step0 = self._step(0)
        for line in ("`previous handoff:`", "`history file:`", "`head:`"):
            self.has(line, step0)

    def test_C2_continues_value_rule_in_step5(self):
        step5 = self._step(5)
        self.has("**Continues:**", step5)
        self.has("/resume-handoff", step5)
        self.has("none", step5)

    def test_C4_session_state_head_field(self):
        block = self._format_block()
        self.has("- **HEAD:**", block.split("## Session State", 1)[1].split("---", 1)[0])
        self.matches(r"`head:`[^\n]*\*\*HEAD\*\*", self._step(2))

    def test_C3_closed_pointers_optional_section(self):
        block = self._format_block()
        self.has("## Closed (pointers)", block)
        self.matches(r"(?is)## Closed \(pointers\).{0,400}(omit|only when)", block)
        self.has("items closed", block)

    def test_C3_ground_rules_one_handoff_file_plus_history_append(self):
        ground = self.text.split("## Ground rules", 1)[1].split("## When to Use", 1)[0]
        self.matches(r"(?is)one handoff file.{0,300}history file", ground)
        self.matches(r"(?is)append", ground)
        self.matches(r"(?is)never (rewrites|deletes|edits)[^.]{0,80}(existing|earlier) lines", ground)

    def test_C3_exclusion_criteria_move_to_history_with_one_line(self):
        self.has("history file", self._step(1))
        self.has("<YYYY-MM-DD> · <key> · <what ended> · <result or evidence path>", self.text)
        self.matches(r"(?i)already (there|in the history file|has the key|present)", self.text)

    def test_C3_failed_and_discarded_never_move_to_history(self):
        self.matches(
            r"(?is)What Did NOT Work.{0,120}Discarded Hypotheses[^.]{0,160}(never|not)[^.]{0,80}history",
            self.text,
        )

    def test_C5_step5_trim_order_and_protected_list(self):
        step5 = self._step(5)
        self.has("16 KiB", step5)
        self.has("`values`", step5)
        order = ["closed items", "What WORKED", "Current State of Files", "`Why`"]
        pos = [step5.lower().find(w.lower()) for w in order]
        self.assertTrue(all(i >= 0 for i in pos), f"missing in trim order: {pos}")
        self.assertEqual(sorted(pos), pos, "trim order is not closed items > WORKED > files > Why")
        never = re.split(r"(?i)never (?:trim|cut|shorten)", step5, 1)
        self.assertEqual(2, len(never), "no never-trim list")
        for protected in ("Goal (carried)", "Live", "What Did NOT Work", "Discarded Hypotheses",
                          "Exact Next Step", "what, why and how"):
            self.has(protected, never[-1])

    def test_C14_checker_contract_listed_in_step6(self):
        step6 = self._step(6)
        for needle in ("Goal (carried)", "**Continues:**", "**HEAD:**", "Closed (pointers)", "16 KiB"):
            self.has(needle, step6)
        self.lacks("seven fields", step6)

    def test_C3_history_file_is_not_a_second_handoff(self):
        self.matches(r"(?is)Every session gets its own handoff file\. Never append to a previous handoff file")

    # --- 16. re-review contract (documents) ---

    def test_M6_ground_rules_write_exception_names_the_design_file(self):
        ground = self.text.split("## Ground rules", 1)[1].split("## When to Use", 1)[0]
        self.matches(r"(?is)design file.{0,120}Step 1|Step 1.{0,120}design file", ground)

    def test_M2_step1_carries_or_closes_unresolved_live_items(self):
        step1 = self._step(1)
        self.matches(
            r"(?is)unresolved[^.]{0,40}`?Live`? item.{0,200}(copied|carried)[^.]{0,120}closed[^.]{0,120}history[^.]{0,80}\bid\b",
            step1,
        )

    def test_HC_outside_git_session_state_stays_with_na_git_fields(self):
        row = [l for l in self._step(2).splitlines() if "not a git repository" in l]
        self.assertTrue(row, "no Step 2 row for a folder outside git")
        self.has("n/a", row[0])
        self.lacks("omit", row[0].lower())
        state = self._format_block().split("## Session State", 1)[1].split("\n---", 1)[0]
        self.has("n/a", state)
        self.lacks("Omit this whole section", state)
        self.matches(r"(?is)Live", state)

    def test_HC_step2_says_live_fields_are_always_written(self):
        self.matches(r"(?is)three Live fields are always\s+written", self._step(2))

    def test_L13_history_file_line_says_exists_or_new(self):
        self.has("(exists)", self._step(0))
        self.has("(new)", self._step(0))

    def test_L13_step5_picks_write_or_append_by_history_file_tag(self):
        step5 = self._step(5)
        self.matches(r"(?is)`\(new\)`.{0,160}Write", step5)
        self.matches(r"(?is)`\(exists\)`.{0,200}(append|cat >>)", step5)

    def test_M2_step6_lists_the_live_chain_check(self):
        self.matches(r"(?is)Live item.{0,160}previous handoff", self._step(6))


class ResumeDocTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.text = read(RESUME)

    def test_A4_continue_marker_replaces_auto_resume(self):
        self.has("continue:")
        self.assertIsNone(re.search(r"(?i)auto-resume", self.text), "unexpected: auto-resume")

    def _table_rows(self):
        rows = [l for l in self.text.splitlines() if l.lstrip().startswith("|")]
        return rows

    def test_L1_steps_table_header(self):
        headers = [r.lower() for r in self._table_rows()
                   if all(w in r.lower() for w in ("call", "input", "output"))]
        self.assertTrue(headers, "no table header with Call, Input, Output columns")

    def test_L1_steps_table_rows(self):
        body = "\n".join(self._table_rows())
        self.has("handoff_state.py", body)
        self.has("Read", body)
        self.has("ListAgents", body)
        self.matches(r"(?i)agent", body)

    def test_rules_layer_user_rules_file(self):
        self.has("user rules file:")

    def test_H5_project_key_and_mismatch_contract(self):
        self.has("project key:")
        self.has("**Toplevel:**")
        self.has("continue: no (project mismatch)")
        self.has("none found for this project")
        self.has("WARNING: newest handoff belongs to another project")
        self.has("WARNING: handoff project differs")

    def test_L5_example_slug_is_generic(self):
        self.lacks("rebuild" + "coord")

    def test_M9_reads_at_most_three(self):
        self.matches(r"(?i)(at most 3|up to 3|max(imum)? 3)")

    def test_L11_listagents_may_be_unavailable(self):
        self.matches(r"(?is)listagents.{0,40}(not available|unavailable)")

    def test_M9_agent_input_has_result_paths_status_file_and_state_files(self):
        rows = [r for r in self._table_rows() if "read-only agent" in r.lower() and r.count("|") >= 3]
        self.assertTrue(rows, "no table row for the read-only agent")
        row = rows[0]
        for needle in ("result", "done-when", "status file:", "state-files"):
            self.has(needle, row)

    def test_M6_subagent_judgement_has_running_question_status(self):
        self.has("`running?`")

    def test_M6_scheduled_items_listed(self):
        self.has("Live scheduled")
        self.has("fires-at")

    def _briefing_block(self):
        return self.text.split("### Step 4", 1)[1].split("### Step 5", 1)[0]

    def test_M10_live_work_line_shows_decision_and_after_return(self):
        live = self._briefing_block().split("LIVE WORK:", 1)[1].split("OPEN WORK:", 1)[0]
        self.has("decision", live)
        self.has("after-return", live)

    def test_M10_step5_runs_after_return_first(self):
        step5 = self.text.split("### Step 5", 1)[1].split("## Warnings", 1)[0]
        self.has("after-return", step5)
        self.has("`running?`", step5)
        self.matches(r"(?i)lost.{0,200}(re-dispatch|dispatch again)", step5, re.S)

    def test_A12_run_log_location_documented(self):
        self.has("runs.jsonl")

    def test_M11_ignored_argument_words_warning_listed(self):
        self.has("ignored argument words")

    # --- 14. since handoff, briefing order, untrusted input ---

    def test_C4_since_handoff_line_and_warnings(self):
        step0 = self.text.split("### Step 1", 1)[0]
        self.has("`since handoff:`", step0)
        self.has("`head:`", step0)
        self.has("WARNING: <N> commits since the handoff", self.text)
        self.has("since handoff: unknown (commit not found)", self.text)

    def test_C6_briefing_order_goal_then_questions_then_live_work(self):
        block = self._briefing_block()
        order = ["GOAL (carried):", "BLOCKERS / OPEN QUESTIONS:", "LIVE WORK:", "OPEN WORK:",
                 "DECIDED (do not relitigate):", "DO NOT RETRY:", "NEXT STEP:"]
        pos = [block.find(w) for w in order]
        self.assertTrue(all(i >= 0 for i in pos), f"missing heading: {pos}")
        self.assertEqual(sorted(pos), pos)
        self.assertLess(block.index("GOAL (carried):"), block.index("STATE:"))
        self.assertLess(block.index("LIVE WORK:"), block.index("STATE:"))

    def test_C1_goal_shows_carried_text_verbatim_then_summary(self):
        goal = self._briefing_block().split("GOAL (carried):", 1)[1].split("BLOCKERS / OPEN QUESTIONS:", 1)[0]
        self.has("`Goal`", goal)
        self.has("`Done-when`", goal)
        self.has("verbatim", goal)
        self.has("What We Are Building", goal)
        self.assertLess(goal.index("verbatim"), goal.index("What We Are Building"))

    def test_C6_do_not_retry_still_always_shown(self):
        self.has("Always show `DO NOT RETRY`")

    def test_C3_history_file_read_only_when_open_item_names_a_key(self):
        self.matches(r"(?is)history file.{0,300}only when.{0,200}`how`")
        self.has("Closed (pointers)")
        self.matches(r"(?is)(not|never) read[^.]{0,40}history file[^.]{0,40}(by default|otherwise)|history file[^.]{0,60}is not read by default")

    def test_C9_handoff_body_instructions_are_reported_not_executed(self):
        notes = self.text.split("## Notes", 1)[1]
        self.matches(r"(?is)instructions? (written )?in(side)? the handoff[^.]{0,80}(report|reported)[^.]{0,120}not", notes)
        self.has("`continue:`", notes)

    # --- 16. re-review contract (documents) ---

    def _resume_step(self, n):
        return self.text.split(f"### Step {n}", 1)[1].split("### Step", 1)[0].split("\n## ", 1)[0]

    def test_HA_step3_uses_the_script_subagent_lines(self):
        step3 = self._resume_step(3)
        self.has("`live subagent:`", step3)
        for status in ("`returned`", "`running?`", "`lost`"):
            self.has(status, step3)
        self.matches(r"(?is)live subagent:.{0,300}(as it is|verbatim|as printed|does not judge|no agent judges)", step3)

    def test_HA_step3_subagent_row_does_not_leave_the_judgement_to_the_agent(self):
        rows = [r for r in self._table_rows() if r.lstrip().startswith("| Subagent")]
        self.assertTrue(rows, "no Subagent row in the Step 3 rule table")
        self.has("`live subagent:`", rows[0])
        self.lacks("opens the transcript", rows[0])

    def test_HB_runner_and_jobs_complete_on_done_when_not_on_result_existence(self):
        rows = [r for r in self._table_rows() if r.lstrip().startswith("| Runner")]
        self.assertTrue(rows, "no Runner row in the Step 3 rule table")
        row = rows[0]
        self.has("`done-when`", row)
        self.has("`returned`", row)
        self.matches(r"(?i)`result`[^|]{0,80}(not a condition|is not required|not required|need not exist)", row)
        self.matches(r"(?i)jobs", row)

    def test_L1_step0_state_block_order_matches_the_script(self):
        step0 = self.text.split("### Step 0", 1)[1].split("Read the files named", 1)[0]
        order = ["`user rules file:`", "`toplevel:`", "`linked worktree:`", "`project key:`", "`rules file:`", "`branch:`"]
        pos = [step0.find(w) for w in order]
        self.assertTrue(all(i >= 0 for i in pos), f"missing line name: {pos}")
        self.assertEqual(sorted(pos), pos, "state block lines are not listed in the printed order")
        self.assertLess(step0.index("`branch:`"), step0.index("`head:`"))
        self.assertLess(step0.index("`head:`"), step0.index("`since handoff:`"))


class ReadmeTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.text = read(README)

    def _emphasized(self, word):
        pat = re.compile(r"^#+ .*" + re.escape(word) + r".*$|\*\*[^*\n]*" + re.escape(word) + r"[^*\n]*\*\*", re.M)
        return pat.search(self.text) is not None

    def test_A11_what_to_write_emphasized(self):
        self.assertTrue(self._emphasized("무엇을 적는"))

    def test_A11_overwrite_point_emphasized(self):
        self.assertTrue(self._emphasized("덮어쓰"))

    def test_A11_must_not_write_emphasized(self):
        self.assertTrue(self._emphasized("적으면 안 되는"))

    def test_A11_mermaid_block(self):
        self.assertGreaterEqual(self.text.count("```mermaid"), 1, "no mermaid block")

    def test_A11_override_point_identifiers(self):
        for ident in ("runner-completion", "state-files", "forbidden-writes", "values"):
            self.has(ident)

    def test_A11_install_section(self):
        self.has("install.py")
        self.has("--yes")

    def test_H2_minimum_python_and_os_specific_commands(self):
        self.has("Python 3.9")
        self.has("python3 install.py")
        self.has("py -3 install.py")

    def test_L6_restore_procedure(self):
        self.has("복원")
        self.has("session-handoff-<날짜시각>")

    def test_L5_environment_variables_documented(self):
        self.has("HANDOFF_LOG")
        self.has("HANDOFF_CHECKER")
        self.has("runs.jsonl")

    def test_L5_context_budget_attachment_point(self):
        self.has("context_budget.py")
        self.has("context-budget:")

    def test_M7_values_point_lists_short_id_format(self):
        rows = [l for l in self.text.splitlines() if l.startswith("| `values`")]
        self.assertTrue(rows and "short-id" in rows[0], "values row lacks short-id format")

    def test_H5_resume_is_limited_to_this_project(self):
        self.has("project key:")

    def test_install_rewrites_command_interpreter(self):
        self.has("python3 ~/.claude/scripts/")

    # --- 14. carried goal and history file ---

    def test_C14_principle_paragraph(self):
        self.has("## 맥락 이월")
        self.has("최초 목표·목적과 진행 중 맥락은 체인 전체에서 유지한다")
        self.has("끝났거나 필요 없는 맥락은 옮기지 않는다")
        self.has("기록하고 잊는다")

    def test_C14_principle_section_documents_goal_carried_and_history_file(self):
        sec = self.text.split("## 맥락 이월", 1)[1].split("\n## ", 1)[0]
        for needle in ("Goal (carried)", "**Continues:**", "~/.claude/handoffs/history/", "이력 파일",
                       "덧붙", "What Did NOT Work", "Discarded Hypotheses", "Closed (pointers)"):
            self.has(needle, sec)
        self.matches(r"기본(으로)?[^.\n]{0,30}읽지 않는다", sec)

    def test_C14_role_list_counts_the_new_fact(self):
        head = self.text.split("## 역할", 1)[0]
        self.has("5가지", head)
        self.has("맥락 이월", head)

    def test_C14_no_em_dash_in_readme(self):
        self.lacks("\u2014")

    # --- 16. re-review contract (documents) ---

    def test_M5_install_python_option_and_interpreter_line(self):
        self.has("--python")
        self.has("interpreter:")

    def test_M5_install_warns_about_virtual_environment(self):
        self.has("가상환경")
        self.has("WARNING: interpreter is inside a virtual environment")

    def test_L12_exit_code_table_lists_internal_error_4(self):
        rows = [l for l in self.text.splitlines() if re.match(r"\|\s*`?4`?\s*\|", l)]
        self.assertTrue(rows, "no exit code table row for 4")
        self.has("내부 오류", rows[0])
        for code in ("0", "1", "2", "3"):
            self.matches(rf"(?m)^\|\s*`?{code}`?\s*\|")

    def test_L13_history_file_line_shows_exists_or_new(self):
        self.has("(exists)")
        self.has("(new)")


HANDLING_VALUES = [
    "유지", "이름 변경", "분리", "선택 줄", "프로젝트 덮어쓰기 지점",
    "스크립트 출력으로 대체", "제거(사용자 결정)",
]


class FieldMapTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.text = read(FIELD_MAP)
        cls.rows = []
        for line in cls.text.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) == 6 and cells[0].isdigit():
                cls.rows.append(cells)

    def test_every_row_uses_a_listed_handling_value(self):
        bad = [(r[0], r[4]) for r in self.rows if r[4] not in HANDLING_VALUES]
        self.assertEqual([], bad)

    def test_row_numbers_are_consecutive(self):
        self.assertEqual(list(range(1, len(self.rows) + 1)), [int(r[0]) for r in self.rows])

    def test_closing_line_counts_match_the_table(self):
        total = len(self.rows)
        removed = sum(1 for r in self.rows if r[4].startswith("제거"))
        last = [l for l in self.text.splitlines() if l.startswith("대조 결과:")]
        self.assertEqual(1, len(last))
        expected = f"대조 결과: 현행 {total}개 · 새 양식에 위치 있음 {total - removed}개 · 사용자 결정 제거 {removed}개 · 빠짐 0개"
        self.assertEqual(expected, last[0])

    def test_handling_value_list_in_prose_matches_the_values_in_use(self):
        used = {r[4] for r in self.rows}
        self.assertTrue(used <= set(HANDLING_VALUES))
        self.matches(rf"{len(HANDLING_VALUES)}가지|{len(HANDLING_VALUES)} 가지|일곱 가지|여덟 가지")
        self.lacks("사용자 층으로 이동")

    def test_live_scheduled_and_runner_id_are_reflected(self):
        self.has("Live scheduled")
        self.has("fires-at")
        rows = {r[0]: r for r in self.rows}
        self.has("(<id>)", rows["35"][3] + rows["35"][5])
        self.has("Live scheduled", rows["34"][3] + rows["34"][5])
        self.has("Live scheduled", rows["40"][3] + rows["40"][5])

    def test_removed_rows_are_the_auto_resume_rows_and_the_session_continue_rule(self):
        removed = [int(r[0]) for r in self.rows if r[4].startswith("제거")]
        self.assertEqual([6, 7, 8, 9, 10, 141], removed)

    def test_rows_that_changed_are_not_marked_unchanged(self):
        rows = {r[0]: r for r in self.rows}
        for n in ("43", "76", "81", "82", "84"):
            self.assertNotEqual("유지", rows[n][4], f"row {n}")

    def test_no_dates_or_outside_references_in_body(self):
        self.assertIsNone(re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", self.text))
        self.assertIsNone(re.search(r"판정문", self.text))
        self.assertIsNone(re.search(r"결정 표", self.text))

    def test_intro_says_it_is_a_record_of_the_migration(self):
        self.has("이관 시점의 대조 기록")


def tracked_files():
    """Files that would be published: tracked plus untracked-not-ignored."""
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return sorted({ROOT / n for n in proc.stdout.splitlines() if n})
    return sorted(
        p for p in ROOT.rglob("*")
        if not SKIP_DIRS & set(p.relative_to(ROOT).parts)
    )


class PublicScanTests(unittest.TestCase):
    def test_A9_scans_the_files_git_would_publish(self):
        names = {p.relative_to(ROOT).as_posix() for p in tracked_files()}
        for expected in ("README.md", "commands/handoff.md", "docs/field-map.md", "tests/test_docs.py"):
            self.assertIn(expected, names)
        self.assertFalse([n for n in names if n.startswith("scratch/")])

    def test_A9_no_forbidden_strings(self):
        found = []
        for path in tracked_files():
            rel = path.relative_to(ROOT)
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for name, rx in FORBIDDEN:
                    if rx.search(line):
                        found.append(f"{rel}:{n}: {name}")
        self.assertEqual([], found, "forbidden strings:\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
