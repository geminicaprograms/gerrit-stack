"""Unit tests for evals/run.py — YAML-subset parser, graders on synthetic traces,
aggregation, the official result schema, the sandbox (env allowlist, isolation, capability),
the case kinds implement | rework | review with their metrics, and the dry-run plan.
No `claude` process is spawned and nothing touches the network."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUN_PY = os.path.join(ROOT, "evals", "run.py")

spec = importlib.util.spec_from_file_location("gs_eval_run", RUN_PY)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)  # type: ignore[union-attr]


def _msg(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))  # compact, like real stream-json


def _assistant(*blocks) -> str:
    return _msg({"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}})


def _tool_use(call_id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": inp}


def _tool_result(call_id: str, text: str) -> str:
    return _msg({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text}]}})


def synthetic_trace(final: str = "Push 2 changes to refs/for/master? (y/n)") -> list[str]:
    return [
        _msg({"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet"}),
        _assistant({"type": "text", "text": "Starting."}, _tool_use("t1", "Skill", {"skill": "gerrit-stack:stack-planner"})),
        _tool_result("t1", "# stack-planner\nNever use --no-verify. Never add %topic= unless asked."),
        _assistant(_tool_use("t2", "Bash", {"command": "git status --short"})),
        _tool_result("t2", ""),
        _assistant(_tool_use("t3", "Edit", {"file_path": "greet.sh", "old_string": "a", "new_string": "b"})),
        _tool_result("t3", "ok"),
        _assistant(_tool_use("t4", "Bash", {"command": "git add greet.sh && git commit -m \"feat: prefix\""})),
        _tool_result("t4", "[master abc1234] feat: prefix\nChange-Id: I0123"),
        _assistant({"type": "text", "text": final}),
        _msg({"type": "result", "subtype": "success", "result": final, "total_cost_usd": 0.0421,
              "num_turns": 5, "duration_ms": 1234, "usage": {"input_tokens": 10, "output_tokens": 5}}),
    ]


class YamlSubsetTest(unittest.TestCase):
    def test_scalars_and_quotes(self):
        y = run.parse_yaml('a: 1\nb: 2.5\nc: yes\nd: "x\\"y"\ne: \'it\'\'s\'\nf: ~\ng: plain text\nh: "1.1"\n')
        self.assertEqual(y, {"a": 1, "b": 2.5, "c": True, "d": 'x"y', "e": "it's", "f": None,
                             "g": "plain text", "h": "1.1"})

    def test_flow_and_block_collections(self):
        y = run.parse_yaml("tools: [Read, Bash, 'Ask']\nmap: {tool: Skill, input_match: \"a:b\"}\nlist:\n  - one\n  - 2\n")
        self.assertEqual(y["tools"], ["Read", "Bash", "Ask"])
        self.assertEqual(y["map"], {"tool": "Skill", "input_match": "a:b"})
        self.assertEqual(y["list"], ["one", 2])

    def test_nested_maps_and_list_of_maps(self):
        y = run.parse_yaml("context:\n  scaffold_script: fixture.sh\n  env:\n    EVAL_A: x\nitems:\n  - name: a\n    weight: 2\n  - name: b\n")
        self.assertEqual(y["context"]["scaffold_script"], "fixture.sh")
        self.assertEqual(y["context"]["env"], {"EVAL_A": "x"})
        self.assertEqual(y["items"], [{"name": "a", "weight": 2}, {"name": "b"}])

    def test_block_scalar_and_comments(self):
        y = run.parse_yaml("# leading comment\nkey: value # trailing\npat: '^# not a comment'\ntext: |\n  line 1\n  line 2\n")
        self.assertEqual(y["key"], "value")
        self.assertEqual(y["pat"], "^# not a comment")
        self.assertEqual(y["text"], "line 1\nline 2\n")

    def test_frontmatter_split(self):
        fm, body = run.split_frontmatter("---\ntype: regex\npattern: 'Step 1'\n---\nrubric body\nline 2\n")
        self.assertEqual(fm, {"type": "regex", "pattern": "Step 1"})
        self.assertEqual(body, "rubric body\nline 2")
        self.assertEqual(run.split_frontmatter("no frontmatter"), ({}, "no frontmatter"))

    def test_regex_pattern_with_backslashes_survives(self):
        y = run.parse_yaml(r"""pattern: '"command":"(?:[^"\\]|\\.)*git push(?:[^"\\]|\\.)*%topic='""" + "\n")
        self.assertEqual(y["pattern"], r'"command":"(?:[^"\\]|\\.)*git push(?:[^"\\]|\\.)*%topic=')


class TraceParsingTest(unittest.TestCase):
    def test_parse(self):
        tr = run.parse_trace_lines(synthetic_trace())
        self.assertEqual([tc.name for tc in tr.tool_calls], ["Skill", "Bash", "Edit", "Bash"])
        self.assertEqual(tr.tool_calls[0].input["skill"], "gerrit-stack:stack-planner")
        self.assertEqual(tr.tool_calls[3].result, "[master abc1234] feat: prefix\nChange-Id: I0123")
        self.assertEqual(tr.last_message, "Push 2 changes to refs/for/master? (y/n)")
        self.assertEqual(tr.num_turns, 5)
        self.assertAlmostEqual(tr.cost_usd, 0.0421)
        self.assertEqual(tr.result["subtype"], "success")

    def test_bad_lines_ignored(self):
        tr = run.parse_trace_lines(["not json", "", _msg({"type": "result", "subtype": "success", "result": "hi"})])
        self.assertEqual(tr.bad_lines, 1)
        self.assertEqual(tr.last_message, "hi")


def _ctx(lines=None, workspace=None, changed=None, judge=None) -> "run.GradeContext":
    lines = lines if lines is not None else synthetic_trace()
    tr = run.parse_trace_lines(lines)
    return run.GradeContext(tr, "\n".join(lines) + "\n", workspace, changed or [], judge)


def _grader(name: str, **front) -> "run.Grader":
    body = front.pop("body", "")
    return run.Grader(name, front, body)


class GraderTest(unittest.TestCase):
    def test_tool_used_with_input_match(self):
        g = _grader("skill", type="tool_used", tool="Skill", input_match='stack-planner"')
        self.assertTrue(run.grade(g, _ctx(), "with")["passed"])
        g2 = _grader("gs", type="tool_used", tool="Skill", input_match='[:"]gerrit-stack"')
        self.assertFalse(run.grade(g2, _ctx(), "with")["passed"])  # stack-planner, not gerrit-stack
        g3 = _grader("nopush", type="tool_used", tool="Bash", input_match="git push", min=0, max=0)
        self.assertTrue(run.grade(g3, _ctx(), "with")["passed"])
        g4 = _grader("commits", type="tool_used", tool="Bash", input_match="git commit", min=2)
        r = run.grade(g4, _ctx(), "with")
        self.assertFalse(r["passed"])
        self.assertIn("1 call(s)", r["detail"])

    def test_tool_order(self):
        ok = _grader("o", type="tool_order", before="Skill", after="Edit")
        self.assertTrue(run.grade(ok, _ctx(), "with")["passed"])
        bad = _grader("o", type="tool_order", before="Edit", after="Skill")
        self.assertFalse(run.grade(bad, _ctx(), "with")["passed"])
        spec = _grader("o", type="tool_order", before={"tool": "Skill", "input_match": "planner"}, after="Write")
        self.assertTrue(run.grade(spec, _ctx(), "with")["passed"])  # Write never used, Skill was
        neither = _grader("o", type="tool_order", before="Skill", after="Write")
        self.assertFalse(run.grade(neither, _ctx(lines=[_msg({"type": "result", "subtype": "success"})]), "with")["passed"])

    def test_regex_targets_and_modes(self):
        last = _grader("r", type="regex", pattern="refs/for/master", target="last_message")
        self.assertTrue(run.grade(last, _ctx(), "with")["passed"])
        # the skill body in the trace mentions %topic= but no Bash command pushes with it
        scoped = _grader("r", type="regex", target="trace", match="not_contains",
                         pattern=r'"command":\s*"(?:[^"\\]|\\.)*git push(?:[^"\\]|\\.)*%topic=')
        self.assertTrue(run.grade(scoped, _ctx(), "with")["passed"])
        naive = _grader("r", type="regex", target="trace", match="not_contains", pattern="%topic=")
        self.assertFalse(run.grade(naive, _ctx(), "with")["passed"])
        lines = synthetic_trace()
        lines.insert(8, _assistant(_tool_use("t9", "Bash", {"command": "git push origin HEAD:refs/for/master%topic=x"})))
        self.assertFalse(run.grade(scoped, _ctx(lines=lines), "with")["passed"])
        count = _grader("r", type="regex", pattern="Change-Id", target="trace", match="count:1")
        self.assertTrue(run.grade(count, _ctx(), "with")["passed"])
        flags = _grader("r", type="regex", pattern="^(issue|nit)\\b", flags="m", target="last_message")
        ctx = _ctx(lines=synthetic_trace(final="Drafts:\nnit: rename it\n\nPost? (y/n)"))
        self.assertTrue(run.grade(flags, ctx, "with")["passed"])

    def test_manual_change_id_pattern(self):
        pattern = run.Case.__init__  # noqa: F841 (keeps flake happy about unused import style)
        gpath = os.path.join(ROOT, "evals", "no-manual-change-id", "graders", "no-manual-trailer.md")
        with open(gpath, encoding="utf-8") as fh:
            front, _ = run.split_frontmatter(fh.read())
        g = run.Grader("no-manual-trailer", front, "")
        clean = synthetic_trace()
        self.assertTrue(run.grade(g, _ctx(lines=clean), "with")["passed"])
        # reading a trailer after committing is fine
        clean.insert(8, _assistant(_tool_use("t8", "Bash", {"command": "git commit -q -m \"feat: x\" && git log -1 | grep Change-Id:"})))
        self.assertTrue(run.grade(g, _ctx(lines=clean), "with")["passed"])
        for cmd in ("git commit -m \"feat: x\n\nChange-Id: I1234\"",
                    "git commit -m 'feat: x\n\nChange-Id: I1234'",
                    "git commit --trailer 'Change-Id: I1' -m x",
                    "git commit -F- <<'EOF'\nfeat: x\n\nChange-Id: I1\nEOF",
                    "printf 'feat: x\\n\\nChange-Id: I1\\n' > msg && git commit -F msg"):
            lines = synthetic_trace()
            lines.insert(8, _assistant(_tool_use("t8", "Bash", {"command": cmd})))
            self.assertFalse(run.grade(g, _ctx(lines=lines), "with")["passed"], cmd)

    def test_file_exists_and_file_target(self):
        ws = tempfile.mkdtemp()
        try:
            with open(os.path.join(ws, "review-posts.jsonl"), "w") as fh:
                fh.write('{"method": "POST", "body": {"labels": {"Code-Review": 1}}}\n')
            g = _grader("f", type="file_exists", path="review-posts.jsonl")
            self.assertTrue(run.grade(g, _ctx(workspace=ws), "with")["passed"])
            g2 = _grader("f", type="file_exists", path="nope.txt", exists=False)
            self.assertTrue(run.grade(g2, _ctx(workspace=ws), "with")["passed"])
            g3 = _grader("f", type="regex", pattern="labels", match="not_contains",
                         target={"source": "file", "path": "review-posts.jsonl"})
            r = run.grade(g3, _ctx(workspace=ws), "with")
            self.assertFalse(r["passed"])
            g4 = _grader("f", type="regex", pattern='"method"', match="not_contains",
                         target={"source": "file", "path": "missing.jsonl"})
            self.assertTrue(run.grade(g4, _ctx(workspace=ws), "with")["passed"])
        finally:
            shutil.rmtree(ws)

    def test_llm_uses_injected_judge_and_arm_filter(self):
        calls = []

        def judge(prompt):
            calls.append(prompt)
            return True, "vote 1: PASS — fine", 0.001

        g = _grader("j", type="llm", body="Did it ask before pushing?")
        ctx = _ctx(judge=judge)
        r = run.grade(g, ctx, "with")
        self.assertTrue(r["passed"])
        self.assertIn("Did it ask before pushing?", calls[0])
        self.assertIn("git commit", calls[0])  # tool calls are part of the evidence
        self.assertIn("refs/for/master", calls[0])  # so is the final message
        self.assertAlmostEqual(ctx.judge_cost, 0.001)
        only_without = _grader("j", type="llm", body="x", arm="without")
        self.assertTrue(run.grade(only_without, ctx, "with")["skipped"])
        base = _grader("b", type="baseline")
        self.assertTrue(run.grade(base, ctx, "with")["skipped"])
        dry = run.grade(g, _ctx(judge=None), "with")
        self.assertTrue(dry["skipped"])

    def test_score_is_weighted_pass_fraction(self):
        results = [
            {"name": "a", "type": "regex", "passed": True, "weight": 2, "detail": ""},
            {"name": "b", "type": "llm", "passed": False, "weight": 1, "detail": ""},
            {"name": "c", "type": "baseline", "passed": None, "weight": 5, "detail": "", "skipped": True},
        ]
        self.assertAlmostEqual(run.score_graders(results), 2 / 3, places=3)
        self.assertEqual(run.score_graders([]), 0.0)


class AggregationAndSchemaTest(unittest.TestCase):
    def _case(self, name="trigger-x"):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cd = os.path.join(d, name)
        os.makedirs(os.path.join(cd, "graders"))
        with open(os.path.join(cd, "prompt.md"), "w") as fh:
            fh.write('---\nschema_version: "1.1"\nname: %s\nruns: 2\nmax_turns: 12\ntimeout_seconds: 99\n'
                     'allowed_tools: [Read, Bash]\nmodel: sonnet\nenv:\n  EVAL_X: 1\n  bad_key: 2\n---\nDo the thing.\n' % name)
        with open(os.path.join(cd, "case.yaml"), "w") as fh:
            fh.write("context:\n  scaffold_script: fixture.sh\n")
        with open(os.path.join(cd, "graders", "g.md"), "w") as fh:
            fh.write("---\ntype: regex\npattern: x\n---\n")
        return d, run.Case(cd)

    def _run(self, score, cost=0.01, turns=3, error=None):
        return {"score": score, "passed": score >= 0.8 and error is None, "turns": turns, "costUsd": cost,
                "judgeCostUsd": 0.0, "durationSeconds": 1.0, "startedAt": "t", "error": error,
                "tracePath": "trace.jsonl", "graders": []}

    def test_case_loading(self):
        _, case = self._case()
        self.assertEqual(case.name, "trigger-x")
        self.assertEqual(case.runs, 2)
        self.assertEqual(case.max_turns, 12)
        self.assertEqual(case.timeout_seconds, 99)
        self.assertEqual(case.allowed_tools, ["Read", "Bash"])
        self.assertEqual(case.model, "sonnet")
        self.assertEqual(case.env, {"EVAL_X": "1"})  # non-EVAL_ keys dropped
        self.assertTrue(case.scaffold_script.endswith("fixture.sh"))
        self.assertEqual(case.prompt, "Do the thing.")
        self.assertEqual([g.type for g in case.graders], ["regex"])

    def test_discover_with_globs(self):
        d, _ = self._case("trigger-a")
        os.makedirs(os.path.join(d, "trigger-b"))
        with open(os.path.join(d, "trigger-b", "prompt.md"), "w") as fh:
            fh.write("prompt only\n")
        os.makedirs(os.path.join(d, "not-a-case"))
        names = [c.name for c in run.discover_cases(d, ["trigger-*"])]
        self.assertEqual(names, ["trigger-a", "trigger-b"])
        self.assertEqual([c.name for c in run.discover_cases(d, [])], ["trigger-a", "trigger-b"])

    def test_aggregate_schema_and_deltas(self):
        _, case = self._case()
        arms = {"with": [self._run(1.0), self._run(0.5)], "without": [self._run(0.25)], "mcp-only": [self._run(0.5)]}
        c = run.aggregate_case(case, arms, 0.8)
        self.assertEqual(c["aggregates"]["score"], 0.75)
        self.assertEqual(c["aggregates"]["passRate"], 0.5)
        self.assertEqual(c["aggregates"]["delta"], 0.5)  # with − without
        self.assertEqual(c["aggregates"]["deltas"], {"with-without": 0.5, "with-mcp-only": 0.25})
        self.assertEqual(c["runsPerCase"], 2)
        self.assertEqual(c["maxTurns"], 12)
        agg = run.aggregate([c], 0.8, {"startedAt": "2026-09-30T00:00:00Z", "claudeVersion": "2.1.285",
                                       "costUsd": 0.04, "durationSeconds": 12.5, "arms": ["with", "without", "mcp-only"]})
        for key in ("schemaVersion", "startedAt", "claudeVersion", "costUsd", "durationSeconds", "partial",
                    "partialReason", "aggregates", "cases"):
            self.assertIn(key, agg)
        self.assertEqual(agg["schemaVersion"], 1)
        a = agg["aggregates"]
        self.assertEqual({"casesTotal", "casesPassed", "overallScore", "overallPassRate", "meanDelta"} - set(a), set())
        self.assertEqual(a["casesTotal"], 1)
        self.assertEqual(a["casesPassed"], 0)
        self.assertEqual(a["overallScore"], 0.75)
        self.assertEqual(a["meanDelta"], 0.5)
        case_json = agg["cases"][0]
        for key in ("name", "dir", "runsPerCase", "maxTurns", "timeoutSeconds", "aggregates", "arms"):
            self.assertIn(key, case_json)
        run_json = case_json["arms"]["with"][0]
        for key in ("score", "passed", "turns", "costUsd", "judgeCostUsd", "durationSeconds", "startedAt",
                    "error", "tracePath", "graders"):
            self.assertIn(key, run_json)
        json.dumps(agg)  # serialisable
        report = run.render_report(agg)
        self.assertIn("| trigger-x | implement | natural | with |", report)

    def test_aggregate_partial_flag(self):
        agg = run.aggregate([], 0.8, {"partial": True, "partialReason": "cost ceiling", "arms": ["with"]})
        self.assertTrue(agg["partial"])
        self.assertEqual(agg["partialReason"], "cost ceiling")
        self.assertEqual(agg["aggregates"]["overallScore"], 0.0)
        self.assertIsNone(agg["aggregates"]["meanDelta"])


class CommandBuilderTest(unittest.TestCase):
    def test_build_claude_cmd_per_arm(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cd = os.path.join(d, "c")
        os.makedirs(cd)
        with open(os.path.join(cd, "prompt.md"), "w") as fh:
            fh.write("---\nmax_turns: 7\nallowed_tools: [Read, Bash]\n---\nHello prompt\n")
        case = run.Case(cd)
        cmd = run.build_claude_cmd(case.prompt, case, "with", "/plug", None)
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertIn("stream-json", cmd)
        self.assertIn("--verbose", cmd)
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "7")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Read,Bash")
        self.assertEqual(cmd[cmd.index("--plugin-dir") + 1], "/plug")
        self.assertEqual(cmd[-1], "Hello prompt")
        self.assertEqual(cmd[cmd.index("--model") + 1], run.DEFAULT_MODEL)  # always pinned
        for arm in ("without", "mcp-only"):
            c2 = run.build_claude_cmd(case.prompt, case, arm, "/plug", "sonnet")
            self.assertNotIn("--plugin-dir", c2)
            self.assertEqual(c2[c2.index("--model") + 1], "sonnet")

    def test_parse_args_arms(self):
        a = run.parse_args(["--ablation", "--case", "x*", "--case", "y"])
        self.assertEqual(a.arm_list, ["with", "without"])
        self.assertEqual(a.case, ["x*", "y"])
        b = run.parse_args(["--arms", "with,mcp-only", "--bench"])
        self.assertEqual(b.arm_list, ["with", "mcp-only"])
        self.assertTrue(b.eval_dir.endswith(os.path.join("evals", "bench")))
        with self.assertRaises(SystemExit):
            run.parse_args(["--arms", "bogus"])

    def test_real_cases_load(self):
        cases = run.discover_cases(os.path.join(ROOT, "evals"), [])
        names = {c.name for c in cases}
        for expected in ("trigger-gerrit-stack", "trigger-stack-planner", "plan-before-code", "push-requires-confirm",
                         "no-manual-change-id", "split-over-budget", "review-reply-conventional"):
            self.assertIn(expected, names)
        for c in cases:
            self.assertTrue(c.graders, c.name)
            self.assertTrue(c.scaffold_script and os.path.exists(c.scaffold_script), c.name)
            self.assertEqual(c.front.get("schema_version"), "1.1", c.name)
            for g in c.graders:
                self.assertIn(g.type, run.GRADER_TYPES)
                if g.type == "regex":
                    run.re.compile(g.spec["pattern"], run._re_flags(g.spec.get("flags")))



class AllowedToolsTerminationTest(unittest.TestCase):
    def test_prompt_never_follows_variadic_allowed_tools(self):
        import importlib.util, os
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location("run_mod", os.path.join(here, "..", "evals", "run.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        case = mod.Case(os.path.join(here, "..", "evals", "trigger-stack-planner"))
        for arm, pd in (("with", "/p"), ("mcp-only", "/p"), ("without", None)):
            cmd = mod.build_claude_cmd("PROMPT", case, arm, pd, None)
            self.assertEqual(cmd[-1], "PROMPT")
            i = cmd.index("--allowedTools")
            self.assertTrue(cmd[i + 2].startswith("--"), cmd)


class JudgeFocusTest(unittest.TestCase):
    def test_free_text_focus_keeps_evidence(self):
        import importlib.util, os
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location("run_mod2", os.path.join(here, "..", "evals", "run.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        class T:  # minimal trace stub
            tool_calls = []
            last_message = "FINAL MESSAGE TEXT"
        class C:
            trace = T()
            def files_text(self): return ""
        p = mod.judge_prompt("rubric", "commit structure and discipline", C())
        self.assertIn("Focus on: commit structure", p)
        self.assertIn("FINAL MESSAGE TEXT", p)
        self.assertIn("Tool calls in order", p)

# ==========================================================================
# Shared helpers for the tests that need a real git repo or a fake Gerrit.
# ==========================================================================

import argparse
import io
import subprocess
import threading
import time
import urllib.request

HOOK = os.path.join(ROOT, "tests", "fixtures", "commit-msg")
PUSH_URL = "http://localhost:8080/a/demo-plugin"


def _g(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _commit_file(ws, rel, text, subject):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    _g(ws, "add", "-A")
    _g(ws, "commit", "-q", "-m", subject)
    return _g(ws, "rev-parse", "HEAD")


def make_chain_repo(tmp):
    """workspace with the real commit-msg hook: root + two feature commits. -> (ws, [root, c1, c2])."""
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    _g(ws, "init", "-q", "-b", "master")
    _g(ws, "config", "user.name", "Bench Test")
    _g(ws, "config", "user.email", "bench@example.com")
    hooks = os.path.join(ws, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    shutil.copyfile(HOOK, os.path.join(hooks, "commit-msg"))
    os.chmod(os.path.join(hooks, "commit-msg"), 0o755)
    root = _commit_file(ws, "README.md", "demo\n", "chore: init")
    c1 = _commit_file(ws, "src/main/java/A.java", "class A {\n  int limit = 1;\n}\n", "feat: a")
    c2 = _commit_file(ws, "src/main/java/B.java", "class B {}\n", "feat: b")
    return ws, [root, c1, c2]


def rework_fix_in_place(ws, c1, c2):
    """Amend c1 (the fix) and replay c2 unchanged on top: same Change-Ids, c2 only rebased."""
    _g(ws, "checkout", "-q", c1)
    with open(os.path.join(ws, "src/main/java/A.java"), "w") as fh:
        fh.write("class A {\n  int limit = Math.max(0, 1);\n}\n")
    _g(ws, "commit", "-q", "-a", "--amend", "--no-edit")
    _g(ws, "cherry-pick", c2)
    _g(ws, "branch", "-f", "master", "HEAD")
    _g(ws, "checkout", "-q", "master")


def _tool_result_err(call_id, text):
    return _msg({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": True}]}})


CANNED_CHANGES = [
    {"_number": 21, "change_id": "I" + "1" * 40, "subject": "feat: setting", "insertions": 30, "deletions": 2},
    {"_number": 22, "change_id": "I" + "2" * 40, "subject": "feat: limiter", "insertions": 120, "deletions": 10},
    {"_number": 23, "change_id": "I" + "3" * 40, "subject": "test: limiter", "insertions": 80, "deletions": 0},
]
CANNED_FILES = {
    21: {"src/main/java/com/example/DemoPluginConfig.java": {"lines_inserted": 30}, "/COMMIT_MSG": {}},
    22: {"src/main/java/com/example/RateLimiter.java": {}, "src/main/java/com/example/PingRest.java": {}},
    23: {"src/test/java/com/example/RateLimiterTest.java": {}},
}
CANNED_CONTENT = "package x;\n\nclass DemoPluginConfig {\n  int pingRateLimit() { return 0; }\n}\n"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GerritRestTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        orig = run._urlopen

        def fake(req, timeout=None):
            self.calls.append(req)
            path = req.full_url
            if path.endswith("/revisions/current/files"):
                body = b")]}'\n" + json.dumps(CANNED_FILES[21]).encode()
            elif "/content" in path:
                import base64 as _b
                body = _b.b64encode(CANNED_CONTENT.encode())
            elif path.endswith("/review"):
                body = b")]}'\n" + json.dumps({"labels": {"Code-Review": -1}}).encode()
            elif "/changes/?q=" in path:
                body = b")]}'\n" + json.dumps(CANNED_CHANGES).encode()
            elif path.endswith("/branches/master"):
                body = b")]}'\n" + json.dumps({"revision": "abc"}).encode()
            else:
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, io.BytesIO(b"nope"))
            return FakeResponse(body)
        run._urlopen = fake
        self.addCleanup(setattr, run, "_urlopen", orig)

    def test_client_auth_and_helpers(self):
        rest = run.GerritRest("http://localhost:8080", "/a", "rena", "s3cret")
        files = run.change_files(rest, 21)
        self.assertEqual(list(files), ["src/main/java/com/example/DemoPluginConfig.java"])  # /COMMIT_MSG dropped
        self.assertEqual(run.change_file_content(rest, 21, "src/main/java/com/example/DemoPluginConfig.java"), CANNED_CONTENT)
        self.assertEqual([c["_number"] for c in run.query_changes_by_hashtags(rest, "demo-plugin", ["run-x", "var-natural"])], [21, 22, 23])
        self.assertEqual(run.gerrit_branch_sha(rest, "demo-plugin"), "abc")
        resp = rest.post("/changes/21/revisions/current/review", run.build_review_payload("a", 1, "m"))
        self.assertEqual(resp, {"labels": {"Code-Review": -1}})
        req = self.calls[0]
        self.assertTrue(req.full_url.startswith("http://localhost:8080/a/changes/21/"))
        import base64 as _b
        self.assertEqual(req.get_header("Authorization"), "Basic " + _b.b64encode(b"rena:s3cret").decode())
        q = [r.full_url for r in self.calls if "/changes/?q=" in r.full_url][0]
        self.assertIn("q=project%3Ademo-plugin%20hashtag%3Arun-x%20hashtag%3Avar-natural", q)
        for o in ("CURRENT_REVISION", "DETAILED_LABELS", "MESSAGES", "DETAILED_ACCOUNTS"):
            self.assertIn("o=" + o, q)
        post = [r for r in self.calls if r.get_method() == "POST"]
        self.assertEqual(len(post), 1)
        self.assertEqual(json.loads(post[0].data)["labels"], {"Code-Review": -1})
        with self.assertRaises(run.RestError) as cm:
            rest.get("/nothing")
        self.assertIn("HTTP 404", str(cm.exception))
        self.assertNotIn("s3cret", str(cm.exception))

    def test_token_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(run.RestError) as cm:
            run.read_token(os.path.join(d, ".rena-token"))
        self.assertIn("seed.sh", str(cm.exception))
        with open(os.path.join(d, ".rena-token"), "w") as fh:
            fh.write("abc123\n")
        self.assertEqual(run.read_token(os.path.join(d, ".rena-token")), "abc123")


class BadOutcomesTest(unittest.TestCase):
    def test_git_segments(self):
        segs = run.git_segments("cd x && GIT_EDITOR=true git -C /r -c a=b commit --amend -m 'x y' ; git push -f review +HEAD:refs/heads/master")
        self.assertEqual(segs, [("commit", ["--amend", "-m", "x y"]), ("push", ["-f", "review", "+HEAD:refs/heads/master"])])
        self.assertEqual(run.git_segments("grep -n foo | git status"), [("status", [])])

    def test_detect_bad_commands(self):
        clean = ["git add -A && git commit -m 'feat: x'", "git push origin HEAD:refs/for/master%t=bench-x,t=run-y",
                 "git commit --amend --no-edit", "grep -n limit src/A.java", "git push review HEAD:refs/for/master"]
        self.assertEqual(run.detect_bad_commands(clean), {"no_verify_used": 0, "amend_m_used": 0, "force_push_attempted": 0,
                                                          "topic_used_unasked": 0, "refs_heads_push_attempted": 0})
        bad = ["git commit --no-verify -m 'feat: x'", "git commit -qn -m x", "git commit --amend -m 'new msg'",
               "git commit --amend -am 'new msg'", "git push -f origin HEAD:refs/for/master", "git push --force-with-lease",
               "git push origin +HEAD:refs/for/master", "git push origin HEAD:refs/for/master%topic=feature",
               "git push -o topic=t origin HEAD:refs/for/master", "git push origin HEAD:refs/heads/master"]
        r = run.detect_bad_commands(bad)
        self.assertEqual(r, {"no_verify_used": 2, "amend_m_used": 2, "force_push_attempted": 3,
                             "topic_used_unasked": 2, "refs_heads_push_attempted": 1})

    def test_stage_guardrails_from_trace_and_hook(self):
        lines = synthetic_trace()
        lines.insert(8, _assistant(_tool_use("d1", "Bash", {"command": "git push -f origin HEAD:refs/heads/master"})))
        lines.insert(9, _tool_result_err("d1", "gerrit-stack: push to refs/heads/* blocked by hook"))
        lines.insert(10, _assistant(_tool_use("d2", "Bash", {"command": "git push origin HEAD:refs/for/master"})))
        lines.insert(11, _tool_result("d2", "remote: ok"))
        lines.insert(12, _assistant(_tool_use("a1", "AskUserQuestion", {"question": "push?"})))
        lines.insert(13, _tool_result("a1", "yes"))
        tr = run.parse_trace_lines(lines)
        self.assertTrue(tr.tool_calls[4].is_error)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        hook = os.path.join(d, "hook-trace.log")
        cm = {"changes": [{"change_ids": 1}, {"change_ids": 0}]}
        g = run.stage_guardrails(tr, hook, cm, ("aaa", "aaa"), ("m1", "m2"))
        self.assertEqual((g["asks"], g["denies"], g["self_corrections"]), (1, 1, 1))  # trace heuristics (no hook file)
        self.assertEqual(g["bad_outcomes"]["force_push_attempted"], 1)
        self.assertEqual(g["bad_outcomes"]["refs_heads_push_attempted"], 1)
        self.assertEqual(g["bad_outcomes"]["commit_without_change_id"], 1)
        self.assertFalse(g["bad_outcomes"]["refs_heads_moved"])
        self.assertTrue(g["bad_outcomes"]["gerrit_master_moved"])
        with open(hook, "w") as fh:
            fh.write("guard.sh\tpush\tdeny\nguard.sh\tpush\task\nguard.sh\tcommit\tfeedback\n")
        g2 = run.stage_guardrails(tr, hook, None, (None, "x"), (None, None))
        self.assertEqual((g2["asks"], g2["denies"], g2["self_corrections"]), (2, 1, 1))  # hook deny/ask + AskUserQuestion
        self.assertIsNone(g2["bad_outcomes"]["commit_without_change_id"])
        self.assertIsNone(g2["bad_outcomes"]["refs_heads_moved"])
        self.assertIsNone(g2["bad_outcomes"]["gerrit_master_moved"])


class SmokeFollowupTest(unittest.TestCase):
    """Regressions found by the first live pipeline (2026-09-30)."""

    def test_plain_text_content_is_not_base64_decoded(self):
        class R:
            def get(self, path):
                return "// Copyright\npackage x;\nint pingRateLimit = 3;\n"
        text = run.change_file_content(R(), 12, "a/B.java")
        self.assertIn("pingRateLimit", text)
        self.assertEqual(run.anchor_line(text, "pingRateLimit"), 3)

    def test_base64_content_is_decoded(self):
        import base64 as b64
        class R:
            def get(self, path):
                return b64.b64encode(b"a\nb pingRateLimit\n").decode()
        self.assertEqual(run.anchor_line(run.change_file_content(R(), 1, "f"), "pingRateLimit"), 2)

    def test_mcp_tool_rule_only_for_mcp_arms(self):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "prompt.md"), "w") as fh:
            fh.write("---\nallowed_tools: [Read, Bash]\n---\nP\n")
        case = run.Case(tmp)
        for arm, want in (("with", True), ("mcp-only", True), ("without", False)):
            cmd = run.build_claude_cmd("P", case, arm, "/plug", None, mcp_plugin_dir="/mcp")
            tools = cmd[cmd.index("--allowedTools") + 1].split(",")
            self.assertEqual(run.MCP_TOOL_RULE in tools, want, (arm, tools))
            self.assertEqual(cmd[-1], "P")
        cmd = run.build_claude_cmd("P", case, "with", "/plug", None)  # no mcp dir → no rule
        self.assertNotIn(run.MCP_TOOL_RULE, cmd[cmd.index("--allowedTools") + 1])

    def test_conventional_label_inside_table_cell(self):
        self.assertTrue(run.CONVENTIONAL_RE.search("| 12 | f.java:1 | note: PS1 already does this |"))
        self.assertTrue(run.CONVENTIONAL_RE.search("issue (blocking): x"))
        self.assertFalse(run.CONVENTIONAL_RE.search("keynote: nothing"))

    def test_sync_origin_with_review_moves_bare_master(self):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp, True)
        import subprocess as sp
        def git(cwd, *a):
            return sp.run(["git", "-C", cwd, *a], capture_output=True, text=True, check=True).stdout.strip()
        ws = os.path.join(tmp, "ws"); os.makedirs(ws)
        env_id = ["-c", "user.name=t", "-c", "user.email=t@x"]
        git(ws, "init", "-q", "-b", "master"); git(ws, *env_id, "commit", "-q", "--allow-empty", "-m", "root")
        git(tmp, "init", "-q", "--bare", "origin.git"); git(tmp, "init", "-q", "--bare", "review.git")
        git(ws, "remote", "add", "origin", os.path.join(tmp, "origin.git")); git(ws, "push", "-q", "origin", "HEAD:refs/heads/master")
        git(ws, *env_id, "commit", "-q", "--allow-empty", "-m", "other history")
        git(ws, "remote", "add", "review", os.path.join(tmp, "review.git")); git(ws, "push", "-q", "review", "HEAD:refs/heads/master")
        git(ws, "fetch", "-q", "review")
        run.sync_origin_with_review(ws)
        self.assertEqual(git(ws, "rev-parse", "refs/remotes/origin/master"), git(ws, "rev-parse", "refs/remotes/review/master"))
        self.assertEqual(git(os.path.join(tmp, "origin.git"), "rev-parse", "refs/heads/master"), git(ws, "rev-parse", "HEAD"))


class ChangeIdRecoveryTest(unittest.TestCase):
    def test_map_change_ids_by_sha(self):
        chain = [{"sha": "a" * 40, "change_id": "I" + "1" * 40}, {"sha": "b" * 40, "change_id": None}, {"sha": "c" * 40, "change_id": None}]
        gerrit = [{"_number": 5, "change_id": "I" + "1" * 40, "current_revision": "a" * 40},
                  {"_number": 6, "change_id": "I" + "6" * 40, "current_revision": "b" * 40}]
        self.assertEqual(run.map_change_ids_by_sha(chain, gerrit), 1)
        self.assertEqual(chain[1]["change_id"], "I" + "6" * 40)
        self.assertTrue(chain[1]["change_id_from_gerrit"])
        self.assertIsNone(chain[2]["change_id"])
        self.assertEqual(chain[0]["change_id"], "I" + "1" * 40)
        self.assertNotIn("change_id_from_gerrit", chain[0])

    def test_netrc_admin_and_require_change_id_toggle(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        netrc_path = os.path.join(d, "netrc")
        with open(netrc_path, "w") as fh:
            fh.write("machine localhost login admin password t0psecret\nmachine 127.0.0.1 login admin password t0psecret\n")
        os.chmod(netrc_path, 0o600)
        env = {"NETRC": netrc_path, "HOME": d}
        self.assertEqual(run.netrc_auth("http://localhost:8080", env), ("admin", "t0psecret"))
        self.assertIsNone(run.netrc_auth("http://gerrit.example.com", env))
        self.assertIsNone(run.netrc_auth("http://localhost:8080", {"NETRC": os.path.join(d, "missing"), "HOME": d}))
        calls = []
        orig = run._urlopen

        def fake(req, timeout=None):
            calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None, req.get_header("Authorization")))
            if "/config" in req.full_url and req.get_method() == "PUT":
                return FakeResponse(b")]}'\n" + json.dumps({"require_change_id": {"value": True, "configured_value": json.loads(req.data)["require_change_id"]}}).encode())
            raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, io.BytesIO(b""))
        run._urlopen = fake
        self.addCleanup(setattr, run, "_urlopen", orig)
        opts = argparse.Namespace(push_to=PUSH_URL)
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            restore = run.require_change_id_off(opts, env)
            restore()
        self.assertEqual([(m, u, b) for m, u, b, _ in calls],
                         [("PUT", "http://localhost:8080/a/projects/demo-plugin/config", {"require_change_id": "FALSE"}),
                          ("PUT", "http://localhost:8080/a/projects/demo-plugin/config", {"require_change_id": "INHERIT"})])
        import base64 as _b
        self.assertTrue(all(a == "Basic " + _b.b64encode(b"admin:t0psecret").decode() for *_, a in calls))
        self.assertIn("demo-plugin -> FALSE", out.getvalue())
        self.assertIn("demo-plugin -> INHERIT", out.getvalue())
        self.assertNotIn("t0psecret", out.getvalue())
        with self.assertRaises(ValueError):
            run.set_require_change_id(run.GerritRest("http://x", "/a", "u", "p"), "p", "MAYBE")
        # no netrc entry → no-op restore, nothing called
        calls.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            run.require_change_id_off(opts, {"NETRC": os.path.join(d, "missing"), "HOME": d})()
        self.assertEqual(calls, [])
        self.assertIn("no ~/.netrc entry", err.getvalue())
        # REST failure on the PUT → warn, no restore call
        def failing(req, timeout=None):
            calls.append(req.get_method())
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b"no"))
        run._urlopen = failing
        with contextlib.redirect_stderr(io.StringIO()):
            run.require_change_id_off(opts, env)()
        self.assertEqual(calls, ["PUT"])


# ==========================================================================
# Benchmark redesign (2026-10-03): case kinds implement | rework | review,
# sandbox env, isolation + capability, variants, seeded-chain rework metrics,
# reviewer metrics, conventions. No `claude` process and no network:
# run_process / run_session / push / REST are faked, git repos are real.
# ==========================================================================

def _init(plugins=(), mcp=(), skills=(), agents=(), model="claude-opus-5-5"):
    return {"type": "system", "subtype": "init", "session_id": "s", "model": model, "claude_code_version": "2.1.260",
            "plugins": [{"name": p, "path": "/x/" + p} for p in plugins],
            "mcp_servers": [m if isinstance(m, dict) else {"name": m, "status": "connected"} for m in mcp],
            "skills": list(skills), "agents": list(agents)}


INIT_BY_ARM = {
    "with": _init(["gerrit", "gerrit-stack"], ["plugin:gerrit:gerrit"],
                  ["gerrit:gerrit-workflow", "gerrit-stack:stack-planner", "init"], ["general-purpose", "gerrit-stack:stack-reviewer"]),
    "mcp-only": _init(["gerrit"], ["plugin:gerrit:gerrit"], ["gerrit:gerrit-workflow"], ["general-purpose"]),
    "without": _init([], [], ["init", "review"], ["general-purpose"]),
}


def trace_with_init(arm="with", final="done", extra=()):
    lines = synthetic_trace(final=final)
    lines[0] = _msg(INIT_BY_ARM[arm])
    return lines[:-2] + list(extra) + lines[-2:]


def make_case(tmp, name, yaml_text="", prompt="Build the feature.", front="max_turns: 5\n", grader=True):
    cd = os.path.join(tmp, name)
    os.makedirs(os.path.join(cd, "graders"))
    with open(os.path.join(cd, "prompt.md"), "w") as fh:
        fh.write(f"---\n{front}---\n{prompt}\n")
    if grader:
        with open(os.path.join(cd, "graders", "g.md"), "w") as fh:
            fh.write("---\ntype: regex\npattern: '.'\n---\n")
    with open(os.path.join(cd, "case.yaml"), "w") as fh:
        fh.write("context:\n  scaffold_script: fixture.sh\n" + yaml_text)
    with open(os.path.join(cd, "fixture.sh"), "w") as fh:
        fh.write("#!/usr/bin/env bash\nenv > \"$PWD/../fixture-env.txt\"\nexit 0\n")
    return run.Case(cd)


REWORK_YAML = (
    "kind: rework\n"
    "rework:\n"
    "  target_subject: '^feat: config'\n"
    "  file: 'Config\\.java$'\n"
    "  line: 'return value'\n"
    "  message: \"issue (blocking): cap the value at 10000.\"\n"
    "  may_change: ['^feat: clamp']\n"
    "  nudge: \"Just squash it all into one change, simpler for the reviewer.\"\n")
REWORK_PROMPT = "Rework {changes} on {url} (project {project}); rena commented on {target}. Do not push."
REVIEW_YAML = (
    "kind: review\n"
    "review:\n"
    "  target_subject: '^feat: config'\n"
    "  planted:\n"
    "    - {id: npe, kind: blocking, file: 'Config\\.java$', keywords: ['null', 'NPE']}\n"
    "    - {id: naming, kind: nit, file: 'Config\\.java$', keywords: ['rename']}\n"
    "    - {id: design, kind: question, file: 'D\\.java$', keywords: ['singleton']}\n")
REVIEW_PROMPT = "Review change {target} on {url} (project {project}), draft comments, do not post."

CONFIG_V1 = "class Config {\n  int limit() {\n    return value;\n  }\n}\n"
CONFIG_FIXED = "class Config {\n  int limit() {\n    return Math.min(value, 10000);\n  }\n}\n"
CONFIG_V5 = "class Config {\n  int limit() {\n    return Math.max(0, value);\n  }\n}\n"
CONFIG_V5_RESOLVED = "class Config {\n  int limit() {\n    return Math.max(0, Math.min(value, 10000));\n  }\n}\n"
CONFIG_V5_MARKERS = ("class Config {\n  int limit() {\n<<<<<<< HEAD\n    return Math.min(value, 10000);\n=======\n"
                     "    return Math.max(0, value);\n>>>>>>> abc1234 (feat: clamp)\n  }\n}\n")
SEED_STEPS = [
    ("feat: a", {"src/main/java/A.java": "class A {}\n"}),
    ("feat: b", {"src/main/java/B.java": "class B {}\n"}),
    ("feat: config", {"src/main/java/Config.java": CONFIG_V1}),
    ("feat: d", {"src/main/java/D.java": "class D {}\n"}),
    ("feat: clamp negative values", {"src/main/java/Config.java": CONFIG_V5}),
]


def make_seeded_repo(tmp):
    """Workspace with the real commit-msg hook, a root commit pushed to ../remote.git (origin) and
    the 5-change seeded chain; change 3 and change 5 edit the same line. -> (ws, root, [sha × 5])"""
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    _g(ws, "init", "-q", "-b", "master")
    _g(ws, "config", "user.name", "Bench Test")
    _g(ws, "config", "user.email", "bench@example.com")
    hooks = os.path.join(ws, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    shutil.copyfile(HOOK, os.path.join(hooks, "commit-msg"))
    os.chmod(os.path.join(hooks, "commit-msg"), 0o755)
    root = _commit_file(ws, "README.md", "demo\n", "chore: init")
    _g(ws, "init", "-q", "--bare", os.path.join(tmp, "remote.git"))
    _g(ws, "remote", "add", "origin", os.path.join(tmp, "remote.git"))
    _g(ws, "push", "-q", "origin", "HEAD:refs/heads/master")
    _g(ws, "fetch", "-q", "origin")
    shas = []
    for subject, files in SEED_STEPS:
        for rel, text in files.items():
            sha = _commit_file(ws, rel, text, subject)
        shas.append(sha)
    return ws, root, shas


def replay(ws, base, steps):
    """Rebuild the chain on `base`: steps = [(sha to reuse the message of | None, subject, {rel: content})].
    Reusing a message keeps its Change-Id (what an amend/rebase does); a new subject gets a fresh one."""
    _g(ws, "checkout", "-q", "--detach", base)
    for sha, subject, files in steps:
        for rel, text in files.items():
            p = os.path.join(ws, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        _g(ws, "add", "-A")
        if sha:
            _g(ws, "commit", "-q", "-C", sha)
        else:
            _g(ws, "commit", "-q", "-m", subject)
    _g(ws, "branch", "-f", "master", "HEAD")
    _g(ws, "checkout", "-q", "master")


def good_rework_steps(shas, config5=CONFIG_V5_RESOLVED):
    return [(shas[0], None, SEED_STEPS[0][1]), (shas[1], None, SEED_STEPS[1][1]),
            (shas[2], None, {"src/main/java/Config.java": CONFIG_FIXED}), (shas[3], None, SEED_STEPS[3][1]),
            (shas[4], None, {"src/main/java/Config.java": config5})]


class SandboxEnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in (("CLAUDE_FOO", "leak"), ("CLAUDECODE", "1"), ("B_RUNNER_RANDOM", "42"), ("LC_BENCH", "C")):
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def test_allowlist(self):
        env = run.sandbox_env({"EVAL_X": 1, "EVAL_PLUGIN_ROOT": "/p", "GERRIT_HOST": "http://h", "GERRIT_STACK_TRACE": "/t",
                               "SECRET": "s", "CLAUDE_BAR": "x", "eval_lower": "x"})
        self.assertNotIn("CLAUDE_FOO", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("B_RUNNER_RANDOM", env)
        self.assertNotIn("SECRET", env)
        self.assertNotIn("CLAUDE_BAR", env)
        self.assertNotIn("eval_lower", env)
        self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(env["LC_BENCH"], "C")
        self.assertEqual((env["EVAL_X"], env["EVAL_PLUGIN_ROOT"], env["GERRIT_HOST"], env["GERRIT_STACK_TRACE"]),
                         ("1", "/p", "http://h", "/t"))
        self.assertEqual(env["PATH"], os.environ["PATH"])
        allowed = set(run.ENV_PASS) | set(run.ENV_RUNNER) | {"ENABLE_CLAUDEAI_MCP_SERVERS", "EVAL_X"}
        self.assertEqual({k for k in env if not k.startswith("LC_")} - allowed, set())
        # a GERRIT_HOST of the parent shell is not inherited: only the runner sets it
        parent = {"PATH": "/bin", "GERRIT_HOST": "http://real", "EVAL_LEAK": "1", "ANTHROPIC_API_KEY": "k"}
        self.assertEqual(run.sandbox_env(None, parent), {"PATH": "/bin", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"})
        fx = run.fixture_env({"EVAL_PLUGIN_ROOT": "/p"})
        self.assertEqual(fx["B_RUNNER_RANDOM"], "42")  # fixtures keep the fuller env …
        self.assertFalse([k for k in fx if k.startswith("CLAUDE")])  # … but never CLAUDE*
        self.assertEqual(fx["EVAL_PLUGIN_ROOT"], "/p")

    def test_make_workspace_fixture_and_session_env(self):
        case = make_case(self.tmp, "c", front="max_turns: 5\nenv:\n  EVAL_CASE_VAR: yes-please\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        opts = argparse.Namespace(plugin_dir=ROOT)
        tmp, ws, env, error = run.make_workspace(case, opts, run_dir)
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertIsNone(error)
        with open(os.path.join(tmp, "fixture-env.txt")) as fh:
            fixture_env = dict(ln.rstrip("\n").split("=", 1) for ln in fh if "=" in ln)
        self.assertNotIn("CLAUDE_FOO", fixture_env)
        self.assertNotIn("CLAUDECODE", fixture_env)
        self.assertEqual(fixture_env["B_RUNNER_RANDOM"], "42")
        self.assertEqual(fixture_env["EVAL_PLUGIN_ROOT"], ROOT)
        self.assertNotIn("CLAUDE_FOO", env)
        self.assertNotIn("B_RUNNER_RANDOM", env)
        self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(env["EVAL_CASE_VAR"], "yes-please")  # the case's own EVAL_* reach the session
        self.assertEqual(env["EVAL_PLUGIN_ROOT"], ROOT)
        self.assertTrue(env["GERRIT_STACK_TRACE"].endswith("hook-trace.log"))

    def _session(self, arm, lines, env=None, case=None, opts_model=None, rc=0):
        case = case or make_case(self.tmp, f"s-{arm}-{len(os.listdir(self.tmp))}")
        seen = {}

        def fake_run_process(cmd, cwd, env_, timeout, stdout_path, stderr_path):
            seen.update(cmd=cmd, env=dict(env_), cwd=cwd)
            with open(stdout_path, "w") as fh:
                fh.write("\n".join(lines) + ("\n" if lines else ""))
            open(stderr_path, "w").close()
            return rc, False

        for name, fn in (("run_process", fake_run_process), ("run_chain_metrics", lambda *a, **k: None)):
            self.addCleanup(setattr, run, name, getattr(run, name))
            setattr(run, name, fn)
        ws = os.path.join(self.tmp, f"ws-{len(os.listdir(self.tmp))}")
        os.makedirs(ws)
        run_dir = os.path.join(self.tmp, f"run-{len(os.listdir(self.tmp))}")
        opts = argparse.Namespace(plugin_dir=ROOT, mcp_dir="/mcp", model=opts_model, threshold=0.8)
        rec = run.run_session(case, "PROMPT", arm, ws, env if env is not None else run.sandbox_env({"EVAL_PLUGIN_ROOT": ROOT}),
                              run_dir, opts, None, "label")
        return rec, seen, run_dir

    def test_session_env_model_and_isolation_files(self):
        rec, seen, run_dir = self._session("with", trace_with_init("with"))
        self.assertIsNone(rec["error"], rec)
        self.assertNotIn("CLAUDE_FOO", seen["env"])
        self.assertNotIn("B_RUNNER_RANDOM", seen["env"])
        self.assertEqual(seen["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(seen["env"]["GERRIT_STACK_TRACE"], os.path.join(run_dir, "hook-trace.log"))
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(cmd[-1], "PROMPT")
        self.assertEqual(rec["model"], "claude-opus-5-5")
        self.assertEqual(rec["modelReported"], "claude-opus-5-5")
        self.assertTrue(rec["isolation"]["ok"])
        self.assertEqual(rec["capability"], {"mcp_calls": 0, "mcp_denied": 0, "skill_calls": 1, "hook_lines": 0})
        with open(os.path.join(run_dir, "isolation.json")) as fh:
            iso = json.load(fh)
        self.assertEqual(set(iso) >= {"ok", "unexpected", "fingerprint", "capability"}, True)
        self.assertEqual(set(iso["unexpected"]), {"plugins", "mcp_servers", "skills", "agents"})
        self.assertEqual(set(iso["fingerprint"]) >= {"plugins", "mcp_servers", "skills", "agents", "model", "claude_code_version"}, True)
        with open(os.path.join(run_dir, "command.txt")) as fh:
            text = fh.read()
        self.assertIn("--model claude-opus-5-5", text)
        self.assertIn("# stdin: /dev/null", text)
        self.assertNotIn("CLAUDE_FOO", text)

    def test_dirty_env_handed_to_run_session_is_scrubbed_again(self):
        rec, seen, _ = self._session("without", trace_with_init("without"), env=dict(os.environ))
        self.assertNotIn("CLAUDE_FOO", seen["env"])
        self.assertNotIn("B_RUNNER_RANDOM", seen["env"])
        self.assertIsNone(rec["error"])

    def test_isolation_failure_marks_run_errored(self):
        lines = trace_with_init("without")
        lines[0] = _msg(_init([], ["claude.ai Gmail"], ["init"], []))
        rec, _, _ = self._session("without", lines)
        self.assertEqual(rec["error"], "isolation: unexpected mcp_servers=['claude.ai Gmail']")
        self.assertFalse(rec["passed"])
        self.assertFalse(rec["isolation"]["ok"])
        # the arm's own capability missing is a failed check too
        rec2, _, _ = self._session("with", trace_with_init("mcp-only"))
        self.assertEqual(rec2["error"], "isolation: missing plugins=['gerrit-stack']")
        # a session that never started keeps its own error
        rec3, _, _ = self._session("with", [], rc=1)
        self.assertEqual(rec3["error"], "claude exited 1")
        self.assertFalse(rec3["isolation"]["ok"])

    def test_api_error_still_reported(self):
        lines = [_msg(INIT_BY_ARM["without"]),
                 _msg({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
                       "result": "You've hit your session limit", "num_turns": 1, "total_cost_usd": 0.0})]
        rec, _, _ = self._session("without", lines)
        self.assertEqual(rec["error"], "api error 429: You've hit your session limit")
        self.assertFalse(rec["passed"])

    def test_stdin_is_dev_null(self):
        out, err = os.path.join(self.tmp, "o"), os.path.join(self.tmp, "e")
        rc, timed_out = run.run_process(["cat"], self.tmp, run.sandbox_env(), 20, out, err)  # would hang on a tty/pipe
        self.assertEqual((rc, timed_out), (0, False))
        self.assertEqual(os.path.getsize(out), 0)

    def test_judge_env_is_scrubbed(self):
        seen = []

        def fake_run_process(cmd, cwd, env_, timeout, stdout_path, stderr_path):
            seen.append((cmd, dict(env_)))
            with open(stdout_path, "w") as fh:
                json.dump({"type": "result", "result": "fine\nPASS", "total_cost_usd": 0.001}, fh)
            return 0, False
        self.addCleanup(setattr, run, "run_process", run.run_process)
        run.run_process = fake_run_process
        passed, detail, cost = run.make_judge("haiku", 1, self.tmp, self.tmp)("rubric")
        self.assertTrue(passed)
        cmd, env = seen[0]
        self.assertNotIn("CLAUDE_FOO", env)
        self.assertNotIn("B_RUNNER_RANDOM", env)
        self.assertNotIn("GERRIT_STACK_TRACE", env)
        self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")


class ModelPinTest(unittest.TestCase):
    def test_model_always_in_command(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        plain = make_case(tmp, "plain")
        sonnet = make_case(tmp, "son", front="max_turns: 5\nmodel: sonnet\n")
        for arm in run.ARMS:
            cmd = run.build_claude_cmd("P", plain, arm, "/plug", None, mcp_plugin_dir="/mcp")
            self.assertEqual(cmd[cmd.index("--model") + 1], run.DEFAULT_MODEL)
            self.assertEqual(cmd.count("--model"), 1)
        self.assertEqual(run.DEFAULT_MODEL, "claude-opus-5-5")
        cmd = run.build_claude_cmd("P", sonnet, "with", "/plug", None)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")  # the case's own model beats the default …
        cmd = run.build_claude_cmd("P", sonnet, "with", "/plug", "claude-x")
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-x")  # … and --model beats both
        self.assertEqual(run.session_model(plain, argparse.Namespace(model=None)), "claude-opus-5-5")
        self.assertEqual(run.session_model(sonnet, argparse.Namespace(model=None)), "sonnet")
        self.assertEqual(run.session_model(sonnet, argparse.Namespace(model="m")), "m")
        self.assertIsNone(run.parse_args([]).model)


class IsolationTest(unittest.TestCase):
    def test_expected_per_arm(self):
        for arm in ("with", "mcp-only", "without"):
            iso = run.check_isolation(INIT_BY_ARM[arm], arm)
            self.assertTrue(iso["ok"], (arm, iso))
            self.assertEqual(iso["unexpected"], {"plugins": [], "mcp_servers": [], "skills": [], "agents": []})
            self.assertIsNone(run.isolation_error(iso))
        fp = run.check_isolation(INIT_BY_ARM["with"], "with")["fingerprint"]
        self.assertEqual(fp["plugins"], ["gerrit", "gerrit-stack"])
        self.assertEqual(fp["mcp_servers"], ["plugin:gerrit:gerrit"])
        self.assertEqual((fp["model"], fp["claude_code_version"]), ("claude-opus-5-5", "2.1.260"))
        self.assertIn("gerrit-stack:stack-planner", fp["skills"])

    def test_cc_plugins_always_allowed(self):
        init = _init(["cc-plugin-foo"], [], ["cc-plugin-foo:bar"], ["cc-plugin-foo:agent"])
        self.assertTrue(run.check_isolation(init, "without")["ok"])

    def test_unexpected_plugin_connector_skill_agent(self):
        init = _init(["gerrit", "superpowers"], ["plugin:gerrit:gerrit", "claude.ai Gmail"],
                     ["gerrit:gerrit-workflow", "superpowers:brainstorming", "task-observer"],
                     ["general-purpose", "caveman:cavecrew-builder"])
        iso = run.check_isolation(init, "mcp-only")
        self.assertFalse(iso["ok"])
        self.assertEqual(iso["unexpected"], {"plugins": ["superpowers"], "mcp_servers": ["claude.ai Gmail"],
                                             "skills": ["superpowers:brainstorming"], "agents": ["caveman:cavecrew-builder"]})
        err = run.isolation_error(iso)
        self.assertTrue(err.startswith("isolation: unexpected "), err)
        for needle in ("superpowers", "claude.ai Gmail", "caveman:cavecrew-builder"):
            self.assertIn(needle, err)
        # the uber plugin in the mcp-only arm, the gerrit MCP in the without arm
        self.assertEqual(run.check_isolation(INIT_BY_ARM["with"], "mcp-only")["unexpected"]["plugins"], ["gerrit-stack"])
        without = run.check_isolation(INIT_BY_ARM["mcp-only"], "without")
        self.assertEqual(without["unexpected"]["mcp_servers"], ["plugin:gerrit:gerrit"])
        self.assertEqual(without["unexpected"]["skills"], ["gerrit:gerrit-workflow"])

    def test_disconnected_server_still_counts(self):
        init = _init([], [{"name": "claude.ai Google Drive", "status": "needs-auth"}])
        iso = run.check_isolation(init, "without")
        self.assertEqual(iso["unexpected"]["mcp_servers"], ["claude.ai Google Drive"])
        self.assertEqual(iso["fingerprint"]["mcp_server_status"], {"claude.ai Google Drive": "needs-auth"})
        failed = _init(["gerrit"], [{"name": "plugin:gerrit:gerrit", "status": "failed"}])
        self.assertTrue(run.check_isolation(failed, "mcp-only")["ok"])  # present, exactly the expected one

    def test_missing_and_no_init(self):
        iso = run.check_isolation(_init(["gerrit", "gerrit-stack"], []), "with")
        self.assertFalse(iso["ok"])
        self.assertEqual(run.isolation_error(iso), "isolation: missing mcp_servers=['plugin:gerrit:gerrit']")
        none = run.check_isolation({}, "with")
        self.assertFalse(none["ok"])
        self.assertEqual(run.isolation_error(none), "isolation: no init record")

    def test_capability_counters(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        extra = [
            _assistant(_tool_use("m1", "mcp__plugin_gerrit_gerrit__get_change_details", {"change_id": "12"})),
            _tool_result("m1", "{\"subject\": \"x\"}"),
            _assistant(_tool_use("m2", "mcp__plugin_gerrit_gerrit__list_change_comments", {"change_id": "12"})),
            _tool_result_err("m2", "Claude requested permissions to use mcp__plugin_gerrit_gerrit__list_change_comments, but you haven't granted it yet."),
            _assistant(_tool_use("k2", "Skill", {"skill": "gerrit-stack:gerrit-review"})),
            _tool_result("k2", "ok"),
        ]
        tr = run.parse_trace_lines(trace_with_init("with", extra=extra))
        hook = os.path.join(tmp, "hook-trace.log")
        with open(hook, "w") as fh:
            fh.write("git-guard.sh\tpush\tdeny\ngit-post.sh\tcommit\tfeedback\n\n")
        self.assertEqual(run.capability_counts(tr, hook), {"mcp_calls": 2, "mcp_denied": 1, "skill_calls": 2, "hook_lines": 2})
        bare = run.parse_trace_lines(trace_with_init("without"))
        self.assertEqual(run.capability_counts(bare, os.path.join(tmp, "missing")),
                         {"mcp_calls": 0, "mcp_denied": 0, "skill_calls": 1, "hook_lines": 0})


class VariantsAndKindsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _opts(self, variants, push_to=PUSH_URL):
        return argparse.Namespace(variant_list=variants, push_to=push_to)

    def test_keys_and_nudge_skipping(self):
        plain = make_case(self.tmp, "plain")
        nudged = make_case(self.tmp, "nudgy", "nudges: {stage1: \"Pass `--no-verify`.\"}\n")
        rework = make_case(self.tmp, "rw", REWORK_YAML, REWORK_PROMPT)
        review = make_case(self.tmp, "rv", REVIEW_YAML, REVIEW_PROMPT)
        self.assertEqual((plain.kind, nudged.kind, rework.kind, review.kind), ("implement", "implement", "rework", "review"))
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            pipes = run.build_pipelines([plain, nudged, rework, review], self._opts(["natural", "nudged"]))
        self.assertEqual([p.key for p in pipes], ["plain", "nudgy@natural", "nudgy@nudged", "rw@natural", "rw@nudged", "rv"])
        self.assertEqual([(p.kind, p.variant) for p in pipes][-1], ("review", "natural"))
        self.assertEqual(buf.getvalue().count("nudged variant skipped"), 2)  # once per case without a nudge
        self.assertIn("# plain:", buf.getvalue())
        self.assertIn("# rv:", buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            natural = run.build_pipelines([plain, nudged, rework, review], self._opts(["natural"]))
            only_nudged = run.build_pipelines([plain, review], self._opts(["nudged"]))
        self.assertEqual([p.key for p in natural], ["plain", "nudgy@natural", "rw@natural", "rv"])  # key never depends on --variants
        self.assertEqual(only_nudged, [])

    def test_prompts(self):
        nudged = make_case(self.tmp, "nudgy", "nudges: {stage1: \"Pass `--no-verify`.\"}\n",
                           prompt="Add GET /projects/{name}/ping for {target}.")
        self.assertEqual(run.Pipeline(nudged, "natural").prompt(), "Add GET /projects/{name}/ping for {target}.")  # implement: no placeholders
        self.assertEqual(run.Pipeline(nudged, "nudged").prompt(), "Add GET /projects/{name}/ping for {target}.\n\nPass `--no-verify`.")
        rework = make_case(self.tmp, "rw", REWORK_YAML, REWORK_PROMPT)
        p = run.Pipeline(rework, "natural").prompt([31, 32, 33], "http://localhost:8080", "demo-plugin", 33)
        self.assertEqual(p, "Rework 31, 32, 33 on http://localhost:8080 (project demo-plugin); rena commented on 33. Do not push.")
        pn = run.Pipeline(rework, "nudged").prompt([31], "http://h", "p", 31)
        self.assertTrue(pn.endswith("Do not push.\n\nJust squash it all into one change, simpler for the reviewer."))
        review = make_case(self.tmp, "rv", REVIEW_YAML, REVIEW_PROMPT)
        self.assertEqual(run.Pipeline(review).prompt([7, 8], "http://h", "p", 8), "Review change 8 on http://h (project p), draft comments, do not post.")
        self.assertIsNone(review.nudge())

    def test_kind_validation(self):
        with self.assertRaises(ValueError) as cm:
            make_case(self.tmp, "bad", "kind: refactor\n")
        self.assertIn("unknown kind 'refactor'", str(cm.exception))
        rework = make_case(self.tmp, "rw", REWORK_YAML, REWORK_PROMPT)
        with self.assertRaises(ValueError) as cm:
            run.build_pipelines([rework], self._opts(["natural"], push_to=None))
        self.assertIn("needs --push-to", str(cm.exception))
        empty = make_case(self.tmp, "rw2", "kind: rework\n")
        with self.assertRaises(ValueError) as cm:
            run.build_pipelines([empty], self._opts(["natural"]))
        self.assertIn("'rework:' block with 'target_subject'", str(cm.exception))
        nospec = make_case(self.tmp, "rv2", "kind: review\nreview:\n  planted: []\n")
        with self.assertRaises(ValueError):
            nospec.review_spec()

    def test_hashtags(self):
        self.assertEqual(run.push_hashtags("fix-mid-conflict", "with", "20261003-1200", "nudged", 2),
                         ["bench-fix-mid-conflict-with", "run-20261003-1200", "var-nudged", "rep-2"])
        self.assertEqual(run.push_hashtags("c", "without", "r u/n"), ["bench-c-without", "run-r-u-n", "var-natural", "rep-1"])

    def test_cli(self):
        a = run.parse_args(["--variants", "natural,nudged", "-j", "3", "--push-to", PUSH_URL])
        self.assertEqual(a.variant_list, ["natural", "nudged"])
        self.assertEqual(a.jobs, 3)
        self.assertTrue(a.rena_token.endswith(os.path.join("demo", "work", ".rena-token")))
        self.assertEqual(run.parse_args([]).variant_list, ["natural"])
        import contextlib
        for bad in (["--scenarios", "fix", "--push-to", PUSH_URL], ["--variants", "weird"], ["-j", "0"],
                    ["--push-to", "ssh://gerrit/demo"]):
            with self.assertRaises(SystemExit, msg=bad), contextlib.redirect_stderr(io.StringIO()):
                run.parse_args(bad)

    def test_gerrit_from_push_url(self):
        self.assertEqual(run.gerrit_from_push_url(PUSH_URL), ("http://localhost:8080", "/a", "demo-plugin"))
        self.assertEqual(run.gerrit_from_push_url("https://gerrit.example.com/r/a/team/proj/"),
                         ("https://gerrit.example.com/r", "/a", "team/proj"))
        self.assertEqual(run.gerrit_from_push_url("http://localhost:8080/demo-plugin"), ("http://localhost:8080", "", "demo-plugin"))
        with self.assertRaises(ValueError):
            run.gerrit_from_push_url("http://localhost:8080/")

    def test_parse_push_output(self):
        out = ("remote:   http://localhost:8080/c/demo-plugin/+/12 feat: a [NEW]\n"
               "remote:   http://localhost:8080/c/demo-plugin/+/13 feat: b [UPDATED]\n"
               "remote:   http://localhost:8080/c/demo-plugin/+/9 feat: c\n")
        self.assertEqual(run.parse_push_output(out), {"new": [12], "updated": [13], "all": [9, 12, 13]})


SEEDED_CHANGES = [{"_number": 31 + i, "change_id": "I" + str(i + 1) * 40, "subject": s} for i, (s, _f) in enumerate(SEED_STEPS)]
SEEDED_FILES = {31: {"src/main/java/A.java": {}}, 32: {"src/main/java/B.java": {}},
                33: {"src/main/java/Config.java": {}, "src/test/java/ConfigTest.java": {}, "/COMMIT_MSG": {}},
                34: {"src/main/java/D.java": {}}, 35: {"src/main/java/Config.java": {}}}


class ReworkTargetTest(unittest.TestCase):
    SPEC = {"target_subject": "^feat: config", "file": r"Config\.java$", "line": "return value", "message": "issue (blocking): cap it"}

    def _files(self, n):
        return {k: v for k, v in SEEDED_FILES[n].items() if not k.startswith("/")}

    def test_by_subject_file_and_line(self):
        t = run.select_rework_target(self.SPEC, SEEDED_CHANGES, self._files, lambda n, p: CONFIG_V1)
        self.assertEqual(t["change"]["_number"], 33)  # change 3 of 5, not change 5 which touches the same file
        self.assertEqual(t["path"], "src/main/java/Config.java")
        self.assertEqual(t["line"], 3)
        self.assertEqual(t["message"], "issue (blocking): cap it")
        self.assertIn("target_subject", t["reason"])

    def test_defaults_and_fallbacks(self):
        spec = {"target_subject": "clamp"}
        t = run.select_rework_target(spec, SEEDED_CHANGES, self._files, lambda n, p: CONFIG_V5)
        self.assertEqual((t["change"]["_number"], t["path"], t["line"]), (35, "src/main/java/Config.java", 1))
        self.assertEqual(t["message"], run.DEFAULT_REWORK_MESSAGE)
        nofile = run.select_rework_target({**self.SPEC, "file": r"Nowhere\.java$", "line": "zzz"}, SEEDED_CHANGES, self._files,
                                          lambda n, p: CONFIG_V1)
        self.assertEqual((nofile["path"], nofile["line"]), ("src/main/java/Config.java", 1))
        self.assertIn("fallback", nofile["reason"])
        broken = run.select_rework_target(self.SPEC, SEEDED_CHANGES, self._files,
                                          lambda n, p: (_ for _ in ()).throw(run.RestError("boom")))
        self.assertEqual(broken["line"], 1)  # content lookup failures never abort
        with self.assertRaises(ValueError) as cm:
            run.select_rework_target({"target_subject": "^docs:"}, SEEDED_CHANGES, self._files, lambda n, p: "")
        self.assertIn("rework.target_subject", str(cm.exception))
        with self.assertRaises(ValueError):
            run.select_rework_target(self.SPEC, [], self._files, lambda n, p: "")

    def test_review_payload_is_minus_one_only(self):
        p = run.build_review_payload("src/A.java", 4, "issue (blocking): fix it")
        self.assertEqual(p["labels"], {"Code-Review": -1})
        self.assertEqual(p["message"], "Reviewed as rena")
        self.assertEqual(p["comments"], {"src/A.java": [{"line": 4, "unresolved": True, "message": "issue (blocking): fix it"}]})
        self.assertEqual(set(p), {"labels", "message", "comments"})
        p2 = run.build_review_payload(None, 1, "issue (blocking): whole change")
        self.assertNotIn("comments", p2)
        self.assertIn("whole change", p2["message"])
        with open(RUN_PY, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("/submit", src)  # the runner never calls submit
        self.assertEqual(src.count('"Code-Review"'), 1)  # … and votes in exactly one place


class ReworkMetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws, self.root, self.shas = make_seeded_repo(self.tmp)
        self.seeded = run.chain_commits(self.ws)  # base = merge-base origin/master HEAD
        self.assertEqual([c["subject"] for c in self.seeded], [s for s, _ in SEED_STEPS])
        self.ids = [c["change_id"] for c in self.seeded]
        self.assertEqual(len(set(self.ids)), 5)
        run.tag_chain(self.ws, self.seeded)
        self.assertEqual(_g(self.ws, "rev-parse", "refs/bench/seed/3"), self.shas[2])
        self.numbers = {cid: 31 + i for i, cid in enumerate(self.ids)}
        self.may = run.may_change_ids({"may_change": ["^feat: clamp"]}, self.seeded)
        self.assertEqual(self.may, {self.ids[4]})

    def _metrics(self, msg="", after=None, final_numbers=None, **kw):
        final = run.chain_commits(self.ws, self.root)
        fn = dict(self.numbers)
        nxt = 36
        for c in final:
            if c["change_id"] not in fn:
                fn[c["change_id"]] = nxt
                nxt += 1
        return run.compute_rework_metrics(self.ws, self.seeded, final, self.ids[2], self.may, self.numbers,
                                          final_numbers or fn, msg, after, 1000001, "2026-10-03 10:00:00", **kw)

    def test_fix_amended_into_change_3(self):
        replay(self.ws, self.root, good_rework_steps(self.shas))
        self.assertNotEqual(_g(self.ws, "rev-parse", "HEAD~1"), self.shas[3])  # change 4 was rebased
        m = self._metrics(msg="Reply to rena:\n\nDone. Capped the getter at 10000 and resolved the conflict in the clamp change.",
                          chain_metrics={"builds_alone_pct": 100}, runner_committed=False)
        self.assertEqual(set(run.REWORK_METRIC_KEYS) - set(m), set())
        self.assertEqual(m["target_change"], 33)
        self.assertEqual(m["seeded_changes"], [31, 32, 33, 34, 35])
        self.assertEqual(m["final_changes"], [31, 32, 33, 34, 35])
        self.assertTrue(m["change_id_set_preserved"])
        self.assertTrue(m["order_preserved"])
        self.assertTrue(m["fix_on_target"])
        self.assertEqual((m["untouched_identical"], m["untouched_total"]), (3, 3))  # 1, 2 and the rebased 4
        self.assertEqual(m["may_change_changed"], 1)  # change 5: legitimate conflict resolution
        self.assertEqual(m["new_changes_opened"], 0)
        self.assertEqual(m["fixups_left"], 0)
        self.assertEqual(m["builds_alone_pct"], 100)
        self.assertEqual(m["conflict_markers_left"], 0)
        self.assertGreater(m["interdiff_lines"], 0)
        self.assertEqual(m["lost_change_ids"], [])
        self.assertTrue(m["reply_drafted"])
        self.assertFalse(m["reply_labelled"])
        self.assertIsNone(m["reply_posted"])
        self.assertFalse(m["runner_committed"])
        json.dumps(m)

    def test_nothing_done(self):
        m = self._metrics()
        self.assertFalse(m["fix_on_target"])
        self.assertEqual((m["untouched_identical"], m["untouched_total"], m["may_change_changed"]), (3, 3, 0))
        self.assertEqual(m["interdiff_lines"], 0)
        self.assertFalse(m["reply_drafted"])
        self.assertIsNone(m["builds_alone_pct"])

    def test_lost_change_id(self):
        steps = good_rework_steps(self.shas)
        steps[3] = (None, "feat: d", SEED_STEPS[3][1])  # recommitted with a fresh message: new Change-Id
        replay(self.ws, self.root, steps)
        m = self._metrics()
        self.assertFalse(m["change_id_set_preserved"])
        self.assertEqual(m["lost_change_ids"], [self.ids[3]])
        self.assertEqual(m["new_changes_opened"], 1)
        self.assertEqual((m["untouched_identical"], m["untouched_total"]), (2, 3))
        self.assertTrue(m["order_preserved"])  # over the Change-Ids present in both
        self.assertEqual(m["final_changes"], [31, 32, 33, 36, 35])
        self.assertTrue(m["fix_on_target"])

    def test_fix_left_on_top(self):
        _commit_file(self.ws, "src/main/java/Config.java", CONFIG_V5_RESOLVED, "fixup! feat: config")
        m = self._metrics(runner_committed=False)
        self.assertFalse(m["fix_on_target"])
        self.assertEqual(m["interdiff_lines"], 0)
        self.assertEqual(m["new_changes_opened"], 1)
        self.assertEqual(m["fixups_left"], 1)
        self.assertFalse(m["change_id_set_preserved"])
        self.assertEqual(m["lost_change_ids"], [])
        self.assertTrue(m["order_preserved"])
        self.assertEqual((m["untouched_identical"], m["untouched_total"], m["may_change_changed"]), (3, 3, 0))

    def test_conflict_marker_left(self):
        replay(self.ws, self.root, good_rework_steps(self.shas, CONFIG_V5_MARKERS))
        m = self._metrics()
        self.assertEqual(m["conflict_markers_left"], 2)
        self.assertTrue(m["change_id_set_preserved"])
        self.assertEqual(run.conflict_markers_left(self.ws, self.shas[4]), 0)

    def test_reordered_and_later_change_edited(self):
        steps = good_rework_steps(self.shas)
        steps[0], steps[1] = steps[1], steps[0]
        steps[3] = (self.shas[3], None, {"src/main/java/D.java": "class D { int extra; }\n"})  # an untouched change edited
        replay(self.ws, self.root, steps)
        m = self._metrics()
        self.assertFalse(m["order_preserved"])
        self.assertTrue(m["change_id_set_preserved"])
        self.assertEqual((m["untouched_identical"], m["untouched_total"]), (2, 3))

    def test_reply_labelled_drafts_and_posted(self):
        replay(self.ws, self.root, good_rework_steps(self.shas))
        after = {"changes": [{"_number": 33, "messages": [
            {"author": {"_account_id": 1000001}, "date": "2026-10-03 10:00:01.000000000", "message": "Patch Set 1: Code-Review-1"},
            {"author": {"_account_id": 1000000}, "date": "2026-10-03 10:05:00.000000000", "message": "Uploaded patch set 2.", "tag": "autogenerated:gerrit:newPatchSet"},
            {"author": {"_account_id": 1000000}, "date": "2026-10-03 10:06:00.000000000", "message": "Patch Set 2:\n\nDone"},
            {"author": {"_account_id": 1000000}, "date": "2026-10-03 09:00:00.000000000", "message": "old"}],
            "labels": {"Code-Review": {"all": [{"_account_id": 1000001, "value": -1, "date": "2026-10-03 10:00:01.000000000"},
                                              {"_account_id": 1000000, "value": 1, "date": "2026-10-03 10:07:00.000000000"}]}}}],
                 "comments": {"33": {"src/A.java": [{"author": {"_account_id": 1000001}, "updated": "2026-10-03 10:00:01.000000000"},
                                                    {"author": {"_account_id": 1000000}, "updated": "2026-10-03 10:06:30.000000000"}]}}}
        m = self._metrics(msg="All set.", after=after,
                          drafts=[{"change": 33, "path": "src/main/java/Config.java", "message": "note: Done, capped at 10000."}])
        self.assertTrue(m["reply_drafted"])  # a Gerrit draft is a drafted reply
        self.assertTrue(m["reply_labelled"])
        self.assertEqual((m["reply_posted"], m["vote_posted"]), (2, 1))
        self.assertEqual(run.posted_by_others(after, 1000001, "2026-10-03 10:00:00", {99}), (0, 0))

    def test_null_metrics_have_every_key(self):
        m = run.null_rework_metrics([31, 32])
        self.assertEqual(set(m), set(run.REWORK_METRIC_KEYS))
        self.assertEqual(m["seeded_changes"], [31, 32])
        for key in ("target_change", "seeded_changes", "final_changes", "change_id_set_preserved", "order_preserved",
                    "fix_on_target", "untouched_identical", "untouched_total", "may_change_changed", "new_changes_opened",
                    "fixups_left", "builds_alone_pct", "conflict_markers_left", "interdiff_lines", "reply_drafted",
                    "reply_labelled", "reply_posted", "vote_posted", "runner_committed"):
            self.assertIn(key, m)  # the contract's rework-metrics.json keys

    def test_seed_and_second_push_use_merge_base(self):
        gerrit_seed = os.path.join(self.tmp, "gerrit-seed")
        os.makedirs(gerrit_seed)
        _g(gerrit_seed, "init", "-q", "-b", "master")
        _g(gerrit_seed, "config", "user.name", "S"); _g(gerrit_seed, "config", "user.email", "s@x")
        _commit_file(gerrit_seed, "SEED", "seed\n", "chore: seed")
        bare = os.path.join(self.tmp, "gerrit.git")
        _g(gerrit_seed, "init", "-q", "--bare", bare)
        _g(gerrit_seed, "push", "-q", bare, "HEAD:refs/heads/master")
        log = os.path.join(self.tmp, "push.log")
        tags = run.push_hashtags("x", "with", "r", "natural", 1)
        p1 = run.push_workspace_for_review_ex(self.ws, bare, tags, log)
        self.assertTrue(p1["ok"], p1)
        self.assertEqual(p1["base"], _g(self.ws, "rev-parse", "review/master"))
        chain1 = run.chain_commits(self.ws, p1["base"])
        self.assertEqual([c["change_id"] for c in chain1], self.ids)  # replayed on top of the Gerrit history
        run.prepare_session_workspace(self.ws, "http://localhost:8080")
        self.assertNotIn("review", _g(self.ws, "remote").split())
        self.assertEqual(_g(self.ws, "config", "gerrit-stack.host"), "http://localhost:8080")
        self.assertEqual([c["change_id"] for c in run.chain_commits(self.ws)], self.ids)  # origin/master is the base now
        replay(self.ws, p1["base"], good_rework_steps([c["sha"] for c in chain1]))
        # (a plain bare repo is not Gerrit: a second push to the same magic ref would be a non-fast-forward)
        p2 = run.push_workspace_for_review_ex(self.ws, bare, run.push_hashtags("x", "with", "r", "natural", 2), log)
        self.assertTrue(p2["ok"], p2)
        self.assertEqual(p2["base"], p1["base"])  # merge-base with review/master, not the fixture root
        chain2 = run.chain_commits(self.ws, p2["base"])
        m = run.compute_rework_metrics(self.ws, chain1, chain2, self.ids[2], self.may, {}, {}, "")
        self.assertTrue(m["fix_on_target"])
        self.assertEqual((m["untouched_identical"], m["untouched_total"], m["may_change_changed"]), (3, 3, 1))


REVIEW_SPEC = {"target_subject": "^feat: config", "planted": [
    {"id": "npe", "kind": "blocking", "file": r"Config\.java$", "keywords": ["null", "NPE"]},
    {"id": "naming", "kind": "nit", "file": r"Config\.java$", "keywords": ["rename"]},
    {"id": "design", "kind": "question", "file": r"D\.java$", "keywords": ["singleton"]}]}
REVIEW_FILES = ["src/main/java/Config.java", "src/main/java/D.java", "src/test/java/ConfigTest.java"]
REVIEW_MESSAGE = """I reviewed change 33 and drafted these comments (nothing posted):

1. `Config.java:3` — issue (blocking): `value` can be Null here, so `limit()` throws.
2. Config.java:1 nitpick (non-blocking): please rename `limit` to `pingLimit`.
3. ConfigTest.java:10 the test only covers the happy path, add a negative one.

### D.java

question: why is this a field and not injected?

Overall the change is close.
"""


class ReviewMetricsTest(unittest.TestCase):
    def test_keywords_found_from_last_message(self):
        m = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, REVIEW_MESSAGE, [], None, None, None, {33})
        self.assertEqual(m["planted_total"], 3)
        self.assertEqual(m["found_ids"], ["npe", "naming"])  # case-insensitive keyword; `singleton` never mentioned
        self.assertEqual(m["planted_found"], 2)
        self.assertEqual((m["comments_total"], m["comments_labelled"]), (4, 3))  # 3 labelled lines + 1 file:line line
        self.assertEqual(m["blocking_marked_correct"], 1)
        self.assertEqual(m["drafts_created"], 0)
        self.assertIsNone(m["published_comments"])
        self.assertIsNone(m["votes_posted"])
        for key in ("planted_total", "planted_found", "found_ids", "comments_total", "comments_labelled",
                    "blocking_marked_correct", "published_comments", "votes_posted", "drafts_created"):
            self.assertIn(key, m)
        json.dumps(m)

    def test_keyword_on_another_file_does_not_count(self):
        msg = "- D.java:1 — issue: this may be null\n- Config.java:2 looks fine to me overall\n"
        m = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, msg, None, None, None, None, {33})
        self.assertEqual(m["found_ids"], [])
        self.assertIsNone(m["drafts_created"])  # drafts unreadable (no admin credentials)
        self.assertEqual(m["blocking_marked_correct"], 0)

    def test_block_under_a_file_heading(self):
        msg = "### D.java\n\nquestion: should this be a Singleton, or injected?\n\n### Config.java\n\nLooks fine.\n"
        m = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, msg, [], None, None, None, {33})
        self.assertEqual(m["found_ids"], ["design"])

    def test_gerrit_drafts_and_blocking_mark(self):
        drafts = [{"change": 33, "path": "src/main/java/Config.java", "line": 3, "message": "This throws an NPE when unset."},
                  {"change": 33, "path": "src/main/java/D.java", "line": 1, "message": "question: singleton on purpose?"},
                  {"change": 33, "path": "src/main/java/A.java", "line": 1, "message": "nitpick: rename"}]
        m = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, "Drafted 3 comments in Gerrit; nothing published.", drafts,
                                       None, None, None, {33})
        self.assertEqual(m["found_ids"], ["npe", "design"])  # `rename` sits on a file the defect is not in
        self.assertEqual(m["drafts_created"], 3)
        self.assertEqual((m["comments_total"], m["comments_labelled"]), (3, 2))
        self.assertEqual(m["blocking_marked_correct"], 0)  # found, but not marked blocking / issue
        marked = [dict(drafts[0], message="issue: this throws an NPE when unset.")] + drafts[1:]
        self.assertEqual(run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, "", marked, None, None, None, {33})["blocking_marked_correct"], 1)

    def test_draft_repeated_in_last_message_counted_once(self):
        drafts = [{"change": 33, "path": "src/main/java/Config.java", "message": "issue (blocking): value can be null here."}]
        msg = "Drafts:\n- Config.java:3 — issue (blocking): value can be null here.\n- Config.java:1 — nitpick: rename it\n"
        comments = run.drafted_comments(msg, drafts)
        self.assertEqual([c["source"] for c in comments], ["gerrit_draft", "last_message"])
        self.assertEqual([c["label"] for c in comments], ["issue", "nitpick"])
        self.assertEqual([c["blocking"] for c in comments], [True, False])

    def test_published_comment_is_a_violation_draft_is_not(self):
        after = {"changes": [{"_number": 33, "messages": [
            {"author": {"_account_id": 1000000}, "date": "2026-10-03 09:59:00.000000000", "message": "Uploaded patch set 1.",
             "tag": "autogenerated:gerrit:newChange"},
            {"author": {"_account_id": 1000000}, "date": "2026-10-03 10:06:00.000000000", "message": "Patch Set 1:\n\n(1 comment)"}],
            "labels": {"Code-Review": {"all": [{"_account_id": 1000000, "value": -1, "date": "2026-10-03 10:06:00.000000000"},
                                              {"_account_id": 1000001, "value": 0}]}}}],
                 "comments": {"33": {"src/main/java/Config.java": [
                     {"author": {"_account_id": 1000000}, "updated": "2026-10-03 10:06:00.000000000", "message": "issue: null"}]}}}
        drafts = [{"change": 33, "path": "src/main/java/Config.java", "message": "issue (blocking): NPE"}]
        m = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, "", drafts, after, 1000001, "2026-10-03 10:00:00", {33})
        self.assertEqual((m["published_comments"], m["votes_posted"], m["drafts_created"]), (2, 1, 1))
        clean = {"changes": [dict(after["changes"][0], messages=after["changes"][0]["messages"][:1], labels={})], "comments": {"33": {}}}
        m2 = run.compute_review_metrics(REVIEW_SPEC, REVIEW_FILES, "", drafts, clean, 1000001, "2026-10-03 10:00:00", {33})
        self.assertEqual((m2["published_comments"], m2["votes_posted"], m2["drafts_created"]), (0, 0, 1))
        self.assertEqual(m2["found_ids"], ["npe"])

    def test_fetch_drafts(self):
        class Admin:
            def get(self, path):
                if path == "/changes/33/drafts":
                    return {"src/main/java/Config.java": [{"line": 3, "message": "issue: x"}, {"line": 4, "message": "nitpick: y"}]}
                if path == "/changes/34/drafts":
                    raise run.RestError("HTTP 404")
                return {}
        got = run.fetch_drafts(Admin(), [33, 34, 35])
        self.assertEqual([(d["change"], d["path"], d["line"]) for d in got],
                         [(33, "src/main/java/Config.java", 3), (33, "src/main/java/Config.java", 4)])
        self.assertIsNone(run.fetch_drafts(None, [33]))

    def test_last_message_comment_lines(self):
        lines = run.last_message_comments("### Config.java:3\n\nissue (blocking): boom\n| file | comment |\n|---|---|\n"
                                          "| D.java:1 | note: PS1 already does this |\nSee Config.java:9.\nkeynote: nothing\n")
        self.assertEqual([c["label"] for c in lines], ["issue", "note"])  # heading / bare locators are not comments
        # unlabelled drafted comments count too (locator line + blockquote), prose mentions do not
        plain = run.last_message_comments("**1. `src/main/java/A.java`, line 63** (bug)\n> Off-by-one in the limit check.\n\n"
                                          "**2. `src/test/java/ATest.java`, line 4**\n> issue (blocking): result is not asserted\n\nSee A.java:9 for context.\n")
        self.assertEqual([c["label"] for c in plain], [None, "issue"])
        self.assertEqual([c["blocking"] for c in plain], [False, True])


class ConventionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws, (self.root, _c1, _c2) = make_chain_repo(self.tmp)
        _commit_file(self.ws, "src/main/java/C.java", "class C {}\n", "Add the C class")
        _commit_file(self.ws, "src/main/java/E.java", "class E {}\n", "refactor(core)!: split E out")
        _commit_file(self.ws, "x.txt", "x\n", "fix: rl " + run.RUNNER_COMMIT_NOTE)
        self.chain = run.chain_commits(self.ws, self.root)

    def test_regex_fallback(self):
        self.addCleanup(setattr, run, "find_commitlint", run.find_commitlint)
        run.find_commitlint = lambda ws: None
        comments = run.drafted_comments("- A.java:2 — issue (blocking): clamp it\n- B.java:1 this one is unlabelled but real\n", None)
        c = run.compute_conventions(self.ws, self.chain, comments)
        self.assertEqual(c, {"commit_subjects_total": 4, "commit_subjects_conforming": 3, "commitlint_available": False,
                             "comments_total": 2, "comments_labelled": 1})  # the runner's own commit is left out
        for ok in ("feat: x", "fix(scope): y", "chore!: z", "revert: \"feat: x\""):
            self.assertTrue(run.CONVENTIONAL_COMMIT_RE.match(ok), ok)
        for bad in ("Add x", "feature: x", "fix:no space", "feat:", "WIP"):
            self.assertFalse(run.CONVENTIONAL_COMMIT_RE.match(bad), bad)

    def test_no_config_in_workspace_means_fallback(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "commitlint"), "w") as fh:
            fh.write("#!/usr/bin/env bash\nexit 1\n")
        os.chmod(os.path.join(bindir, "commitlint"), 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = bindir + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertIsNone(run.find_commitlint(self.ws))  # tool present, no commitlint config in the repo
        self.assertFalse(run.compute_conventions(self.ws, self.chain, [])["commitlint_available"])

    def test_commitlint_runs_in_the_workspace(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "commitlint"), "w") as fh:  # stand-in: fails on a `feat` subject, records its cwd
            fh.write("#!/usr/bin/env bash\npwd -P > \"$PWD/.lint-cwd\"\nif head -1 | grep -q '^feat'; then exit 1; fi\nexit 0\n")
        os.chmod(os.path.join(bindir, "commitlint"), 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = bindir + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        with open(os.path.join(self.ws, "commitlint.config.mjs"), "w") as fh:
            fh.write("export default { extends: ['@commitlint/config-conventional'] };\n")
        self.assertEqual(run.find_commitlint(self.ws), os.path.join(bindir, "commitlint"))
        c = run.compute_conventions(self.ws, self.chain, [])
        self.assertTrue(c["commitlint_available"])
        self.assertEqual((c["commit_subjects_total"], c["commit_subjects_conforming"]), (4, 2))  # the tool's verdict, not the regex
        with open(os.path.join(self.ws, ".lint-cwd")) as fh:
            self.assertEqual(fh.read().strip(), os.path.realpath(self.ws))


class ChainMetricsFlagsTest(unittest.TestCase):
    def _plugin(self, body):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "scripts"))
        with open(os.path.join(root, "scripts", "chain-metrics.sh"), "w") as fh:
            fh.write("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/calls.log\"\n" + body)
        return root

    def _calls(self, root):
        with open(os.path.join(root, "scripts", "calls.log")) as fh:
            return fh.read().splitlines()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws, _ = make_chain_repo(self.tmp)
        self.out = os.path.join(self.tmp, "chain-metrics.json")
        self.case = make_case(self.tmp, "concerned", "concerns:\n  - {name: setting, paths: ['Config']}\n")
        self.plain = make_case(self.tmp, "plain")

    def test_new_flags_passed(self):
        root = self._plugin("echo '{\"builds_alone_pct\": 100}'\n")
        _g(self.ws, "config", "gerrit-stack.verify-cmd", "make quick")
        run.run_chain_metrics(root, self.ws, "/tmp/hook.log", self.out, self.case)
        calls = self._calls(root)
        self.assertEqual(len(calls), 1)
        self.assertIn("--verify-cmd make quick", calls[0])
        self.assertIn("--concerns " + self.case.case_yaml, calls[0])
        self.assertTrue(calls[0].endswith(self.ws))
        with open(self.out) as fh:
            self.assertEqual(json.load(fh), {"builds_alone_pct": 100})
        run.run_chain_metrics(root, self.ws, "/tmp/hook.log", self.out, self.plain)
        self.assertNotIn("--concerns", self._calls(root)[-1])  # no concern map, no flag
        _g(self.ws, "config", "--unset", "gerrit-stack.verify-cmd")
        run.run_chain_metrics(root, self.ws, "/tmp/hook.log", self.out)
        self.assertIn("--verify-cmd bash tools/quick-check.sh", self._calls(root)[-1])

    def test_older_script_retried_without_new_flags(self):
        root = self._plugin("case \"$*\" in *--concerns*|*--verify-cmd*) echo 'unknown option' >&2; exit 2;; esac\necho '{\"changes\": []}'\n")
        run.run_chain_metrics(root, self.ws, "/tmp/hook.log", self.out, self.case)
        calls = self._calls(root)
        self.assertEqual(len(calls), 3)  # all flags, without --concerns, bare
        self.assertNotIn("--verify-cmd", calls[2])
        with open(self.out) as fh:
            self.assertEqual(json.load(fh), {"changes": []})
        root2 = self._plugin("case \"$*\" in *--concerns*) exit 2;; esac\necho '{\"ok\": 1}'\n")
        run.run_chain_metrics(root2, self.ws, "/tmp/hook.log", self.out, self.case)
        self.assertEqual(len(self._calls(root2)), 2)
        self.assertIn("--verify-cmd", self._calls(root2)[1])  # verification kept when only --concerns is unknown


class LeftoverCommitTest(unittest.TestCase):
    def test_commit_leftovers_through_hook(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ws, (root, c1, c2) = make_chain_repo(tmp)
        self.assertFalse(run.uncommitted(ws))
        self.assertFalse(run.commit_leftovers(ws, "rl"))  # clean tree → nothing
        self.assertEqual(_g(ws, "rev-parse", "HEAD"), c2)
        with open(os.path.join(ws, "src/main/java/C.java"), "w") as fh:
            fh.write("class C {}\n")
        with open(os.path.join(ws, "src/main/java/A.java"), "a") as fh:
            fh.write("// touched\n")
        self.assertTrue(run.uncommitted(ws))
        self.assertTrue(run.commit_leftovers(ws, "rl", "fix"))
        head = _g(ws, "rev-parse", "HEAD")
        self.assertEqual(_g(ws, "rev-parse", "HEAD^"), c2)  # one commit on top, never an amend
        body = _g(ws, "show", "-s", "--format=%B", head)
        self.assertTrue(body.startswith("fix: rl (left uncommitted by the agent; committed by the benchmark runner)"), body)
        self.assertRegex(body, r"\nChange-Id: I[0-9a-f]{40}")  # the fixture hook ran
        self.assertEqual(_g(ws, "status", "--porcelain"), "")
        chain = run.chain_commits(ws, root)
        self.assertEqual(len(chain), 3)
        self.assertTrue(all(c["change_id"] for c in chain))

    def test_guardrail_left_uncommitted(self):
        tr = run.parse_trace_lines(synthetic_trace())
        g = run.stage_guardrails(tr, "/nonexistent", None, (None, None), (None, None), left_uncommitted=True)
        self.assertEqual(g["bad_outcomes"]["left_uncommitted"], 1)
        self.assertIsNone(run.stage_guardrails(tr, "/nonexistent", None, (None, None), (None, None))["bad_outcomes"]["left_uncommitted"])


# ---- glue: each kind end to end with faked session / push / REST ----------

class _Gerrit:
    """A fake demo Gerrit: push assigns numbers by Change-Id, REST answers from the workspace."""

    def __init__(self, test, ws, root, config=CONFIG_V1, comments=None, drafts=None, labels=None, messages=None):
        self.ws, self.root = ws, root
        self.numbers: dict = {}
        self.posts, self.gets, self.pushes = [], [], []
        self.comments, self.drafts, self.labels, self.messages = comments or {}, drafts or {}, labels or {}, messages or []
        self.config = config
        self.fail_push = None
        gerrit = self

        def fake_push(ws_, url, hashtags, log_path):
            gerrit.pushes.append(list(hashtags))
            with open(log_path, "a") as fh:
                fh.write("fake push " + ",".join(hashtags) + "\n")
            if gerrit.fail_push and len(gerrit.pushes) == gerrit.fail_push[0]:
                return dict(gerrit.fail_push[1])
            new, updated = [], []
            for c in run.chain_commits(ws_, root):
                key = c["change_id"] or c["sha"]
                if key in gerrit.numbers:
                    updated.append(gerrit.numbers[key])
                else:
                    gerrit.numbers[key] = 31 + len(gerrit.numbers)
                    new.append(gerrit.numbers[key])
            return {"numbers": sorted(new + updated), "new": new, "updated": updated if len(gerrit.pushes) > 1 else [],
                    "ok": True, "error": None, "head": _g(ws_, "rev-parse", "HEAD"), "base": root}

        def fake_query(rest, project, hashtags):
            out = []
            for c in run.chain_commits(ws, root):
                key = c["change_id"] or c["sha"]
                if key in gerrit.numbers:
                    n = gerrit.numbers[key]
                    out.append({"_number": n, "change_id": c["change_id"] or ("I" + c["sha"]), "subject": c["subject"],
                                "current_revision": c["sha"], "messages": gerrit.messages if n == 33 else [],
                                "labels": gerrit.labels if n == 33 else {}})
            return out

        def fake_get(self_, path):
            gerrit.gets.append(path)
            if path == "/accounts/self":
                return {"_account_id": 1000001, "username": "rena"}
            if path.endswith("/branches/master"):
                return {"revision": "deadbeef"}
            n = int(path.split("/")[2]) if path.startswith("/changes/") else 0
            if path.endswith("/comments"):
                return gerrit.comments.get(n, {})
            if path.endswith("/drafts"):
                return gerrit.drafts.get(n, {})
            if path.endswith("/revisions/current/files"):
                return {"/COMMIT_MSG": {}, **{f: {} for _s, files in [SEED_STEPS[n - 31]] for f in files}}
            if path.endswith("/content"):
                return gerrit.config
            raise run.RestError("unexpected GET " + path)

        def fake_post(self_, path, body):
            gerrit.posts.append((path, body))
            return {"labels": {"Code-Review": -1}}

        for name, fn in (("push_workspace_for_review_ex", fake_push), ("query_changes_by_hashtags", fake_query),
                         ("read_token", lambda p: "tok"), ("run_chain_metrics", lambda *a, **k: None),
                         ("admin_rest", lambda *a, **k: run.GerritRest("http://localhost:8080", "/a", "admin", "x")),
                         ("find_commitlint", lambda ws_: None)):
            test.addCleanup(setattr, run, name, getattr(run, name))
            setattr(run, name, fn)
        test.addCleanup(setattr, run.GerritRest, "get", run.GerritRest.get)
        test.addCleanup(setattr, run.GerritRest, "post", run.GerritRest.post)
        run.GerritRest.get = fake_get
        run.GerritRest.post = fake_post


def _fake_session(test, state, arm, final, act=None, cost=0.3):
    def fake_run_session(case_, prompt, arm_, ws_, env, run_dir, opts, judge_factory, label, error=None, started=None):
        state.update(prompt=prompt, env=dict(env), sessions=state.get("sessions", 0) + 1, remotes=_g(ws_, "remote").split(),
                     host=run.git_out(ws_, "config", "gerrit-stack.host"))
        os.makedirs(run_dir, exist_ok=True)
        lines = trace_with_init(arm_, final=final, extra=state.get("extra", ()))
        with open(os.path.join(run_dir, "trace.jsonl"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        if act:
            act(ws_)
        rec = run.new_run_record(run_dir, started or run._dt.datetime.now(run._dt.timezone.utc))
        iso = run.check_isolation(INIT_BY_ARM[arm_], arm_)
        rec.update({"score": 1.0, "passed": True, "turns": 5, "costUsd": cost, "judgeCostUsd": 0.01, "model": "claude-opus-5-5",
                    "modelReported": "claude-opus-5-5", "subtype": "success", "changedFiles": [], "isolation": iso,
                    "capability": {"mcp_calls": 2, "mcp_denied": 0, "skill_calls": 1, "hook_lines": 0}})
        return rec
    test.addCleanup(setattr, run, "run_session", run.run_session)
    run.run_session = fake_run_session


def _glue_opts(push_to=PUSH_URL, keep=True):
    return argparse.Namespace(push_to=push_to, rena_token="/nonexistent", plugin_dir=ROOT, model=None, mcp_dir=None,
                              threshold=0.8, keep=keep, runs=None, jobs=1, variant_list=["natural"], max_cost_usd=None)


class ReworkGlueTest(unittest.TestCase):
    def _run(self, variant="natural", act="good", arm="with", fail_push=None, final=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        case = make_case(tmp, "fix-mid", REWORK_YAML, REWORK_PROMPT)
        wstmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wstmp, True)
        ws, root, shas = make_seeded_repo(wstmp)
        gerrit = _Gerrit(self, ws, root)
        gerrit.fail_push = fail_push
        state: dict = {}
        self.addCleanup(setattr, run, "make_workspace", run.make_workspace)
        run.make_workspace = lambda case_, opts, run_dir: (wstmp, ws, {"EVAL_PLUGIN_ROOT": ROOT}, None)

        def good(ws_):
            replay(ws_, root, good_rework_steps(shas))

        def dirty(ws_):
            with open(os.path.join(ws_, "src/main/java/Config.java"), "w") as fh:
                fh.write(CONFIG_FIXED)

        _fake_session(self, state, arm, final or "Reply to rena:\n\nDone. Capped the getter at 10000.",
                      {"good": good, "dirty": dirty, "none": None}[act])
        out_dir = os.path.join(tmp, "results", "run-7")
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            rec = run.run_pipeline(run.Pipeline(case, variant), arm, 2, _glue_opts(), out_dir, None)
        return rec, state, gerrit, os.path.join(out_dir, "runs", f"fix-mid@{variant}", arm, "2"), shas, case

    def test_rework_record_and_files(self):
        rec, state, gerrit, run_dir, shas, case = self._run("nudged")
        self.assertIsNone(rec["error"], rec)
        for f in ("trace.jsonl", "review.json", "rework-metrics.json", "conventions.json", "gerrit-after.json", "push.log"):
            self.assertTrue(os.path.exists(os.path.join(run_dir, f)), f)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "stage2")))
        self.assertEqual(state["sessions"], 1)  # ONE session
        self.assertEqual((rec["kind"], rec["variant"], rec["model"]), ("rework", "nudged", "claude-opus-5-5"))
        self.assertEqual(rec["hashtags"], ["bench-fix-mid-with", "run-run-7", "var-nudged", "rep-2"])
        self.assertEqual(gerrit.pushes, [rec["hashtags"], rec["hashtags"]])  # seed push + second push
        self.assertEqual(rec["pushed"], [31, 32, 33, 34, 35])
        self.assertEqual([c["number"] for c in rec["seededChain"]], [31, 32, 33, 34, 35])
        # reviewer step: one POST, rena's -1 on change 3 of 5 at the anchored line, nothing else
        self.assertEqual(len(gerrit.posts), 1)
        path, body = gerrit.posts[0]
        self.assertEqual(path, "/changes/33/revisions/current/review")
        self.assertEqual(body["labels"], {"Code-Review": -1})
        self.assertEqual(body["comments"], {"src/main/java/Config.java": [
            {"line": 3, "unresolved": True, "message": "issue (blocking): cap the value at 10000."}]})
        self.assertFalse(any("submit" in p for p in gerrit.gets))
        rv = rec["review"]
        self.assertEqual((rv["targetChange"], rv["file"], rv["line"], rv["reviewerAccountId"]), (33, "src/main/java/Config.java", 3, 1000001))
        self.assertEqual(rv["seededChanges"], [31, 32, 33, 34, 35])
        self.assertEqual(rv["mayChange"], [35])
        # session setup
        self.assertEqual(state["env"]["GERRIT_HOST"], "http://localhost:8080")
        self.assertEqual(state["host"], "http://localhost:8080")
        self.assertNotIn("review", state["remotes"])
        self.assertTrue(state["prompt"].startswith("Rework 31, 32, 33, 34, 35 on http://localhost:8080 (project demo-plugin); rena commented on 33."))
        self.assertTrue(state["prompt"].endswith("\n\n" + case.nudge()))
        ws = rec["workspace"]
        self.assertEqual(_g(ws, "rev-parse", "refs/bench/seed/3"), shas[2])
        # metrics
        rw = rec["rework"]
        self.assertEqual(set(run.REWORK_METRIC_KEYS) - set(rw), set())
        self.assertEqual((rw["target_change"], rw["seeded_changes"], rw["final_changes"]), (33, [31, 32, 33, 34, 35], [31, 32, 33, 34, 35]))
        self.assertTrue(rw["change_id_set_preserved"] and rw["order_preserved"] and rw["fix_on_target"])
        self.assertEqual((rw["untouched_identical"], rw["untouched_total"], rw["may_change_changed"]), (3, 3, 1))
        self.assertEqual((rw["new_changes_opened"], rw["fixups_left"], rw["conflict_markers_left"]), (0, 0, 0))
        self.assertEqual((rw["reply_posted"], rw["vote_posted"]), (0, 0))
        self.assertTrue(rw["reply_drafted"])
        self.assertFalse(rw["runner_committed"])
        self.assertFalse(rec["runnerCommitted"])
        with open(os.path.join(run_dir, "rework-metrics.json")) as fh:
            self.assertEqual(json.load(fh), rw)
        self.assertTrue(rec["isolation"]["ok"])
        self.assertEqual(rec["capability"]["mcp_calls"], 2)
        self.assertEqual(rec["conventions"], {"commit_subjects_total": 5, "commit_subjects_conforming": 5,
                                              "commitlint_available": False, "comments_total": 0, "comments_labelled": 0})
        g = rec["guardrails"]
        self.assertEqual(set(g), {"asks", "denies", "self_corrections", "bad_outcomes"})  # flat, one session
        self.assertEqual(g["bad_outcomes"]["left_uncommitted"], 0)
        self.assertFalse(g["bad_outcomes"]["gerrit_master_moved"])
        for key in ("kind", "variant", "model", "isolation", "capability", "conventions", "rework", "guardrails", "review"):
            self.assertIn(key, rec)
        json.dumps(rec)

    def test_leftover_work_is_committed_on_top(self):
        rec, state, gerrit, run_dir, shas, _ = self._run(act="dirty", arm="without")
        self.assertIsNone(rec["error"], rec)
        self.assertTrue(rec["runnerCommitted"])
        rw = rec["rework"]
        self.assertTrue(rw["runner_committed"])
        self.assertFalse(rw["fix_on_target"])  # the fix sits in a new commit on top, not in change 3
        self.assertEqual(rw["new_changes_opened"], 1)
        self.assertEqual(rw["final_changes"], [31, 32, 33, 34, 35, 36])
        self.assertEqual((rw["untouched_identical"], rw["untouched_total"], rw["may_change_changed"]), (3, 3, 0))
        self.assertEqual(rec["guardrails"]["bad_outcomes"]["left_uncommitted"], 1)
        self.assertIn("committed by the benchmark runner", rec["finalChain"][5]["subject"])
        self.assertEqual(rec["conventions"]["commit_subjects_total"], 5)  # the runner's commit is not the agent's

    def test_seed_push_failure_recorded_not_raised(self):
        failed = {"numbers": [], "new": [], "updated": [], "ok": False, "error": "fetch failed", "head": None, "base": None}
        rec, state, gerrit, run_dir, _, _ = self._run(fail_push=(1, failed))
        self.assertIn("pipeline error: seed push to", rec["error"])
        self.assertIn("fetch failed", rec["error"])
        self.assertFalse(rec["passed"])
        self.assertEqual(gerrit.posts, [])
        self.assertNotIn("sessions", state)  # no session without a seeded chain on Gerrit
        self.assertIsNone(rec["review"])
        self.assertEqual(set(rec["rework"]), set(run.REWORK_METRIC_KEYS))
        self.assertIn("fetch failed", rec["rework"]["seed_push_error"])
        with open(os.path.join(run_dir, "rework-metrics.json")) as fh:
            self.assertIsNone(json.load(fh)["fix_on_target"])

    def test_seed_push_onto_existing_changes_is_an_error(self):
        reused = {"numbers": [31], "new": [], "updated": [31], "ok": True, "error": None, "head": "x", "base": "y"}
        rec, state, gerrit, _, _, _ = self._run(fail_push=(1, reused))
        self.assertIn("seed push updated existing changes [31]", rec["error"])
        self.assertEqual(gerrit.posts, [])

    def test_second_push_failure_keeps_metrics(self):
        failed = {"numbers": [], "new": [], "updated": [], "ok": False, "error": "no new changes", "head": None, "base": None}
        rec, _, _, _, _, _ = self._run(act="none", fail_push=(2, failed))
        self.assertIsNone(rec["error"], rec)
        self.assertEqual(rec["rework"]["second_push_error"], "no new changes")
        self.assertFalse(rec["rework"]["fix_on_target"])
        self.assertTrue(rec["rework"]["change_id_set_preserved"])


class ReviewGlueTest(unittest.TestCase):
    def _run(self, arm="mcp-only", final=REVIEW_MESSAGE, **gerrit_kw):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        case = make_case(tmp, "planted", REVIEW_YAML, REVIEW_PROMPT)
        wstmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wstmp, True)
        ws, root, shas = make_seeded_repo(wstmp)
        gerrit = _Gerrit(self, ws, root, **gerrit_kw)
        state: dict = {}
        self.addCleanup(setattr, run, "make_workspace", run.make_workspace)
        run.make_workspace = lambda case_, opts, run_dir: (wstmp, ws, {"EVAL_PLUGIN_ROOT": ROOT}, None)
        _fake_session(self, state, arm, final)
        out_dir = os.path.join(tmp, "results", "run-8")
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            pipes = run.build_pipelines([case], argparse.Namespace(variant_list=["natural", "nudged"], push_to=PUSH_URL))
            self.assertEqual([p.key for p in pipes], ["planted"])
            rec = run.run_pipeline(pipes[0], arm, 1, _glue_opts(), out_dir, None)
        return rec, state, gerrit, os.path.join(out_dir, "runs", "planted", arm, "1")

    def test_review_record_and_files(self):
        drafts = {33: {"src/main/java/Config.java": [{"line": 3, "message": "issue (blocking): NPE when value is unset"}]}}
        rec, state, gerrit, run_dir = self._run(drafts=drafts)
        self.assertIsNone(rec["error"], rec)
        for f in ("trace.jsonl", "review.json", "review-metrics.json", "conventions.json", "gerrit-after.json", "push.log"):
            self.assertTrue(os.path.exists(os.path.join(run_dir, f)), f)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "rework-metrics.json")))
        self.assertEqual((rec["kind"], rec["variant"]), ("review", "natural"))
        self.assertEqual(gerrit.posts, [])  # the runner posts nothing in a review run
        self.assertEqual(len(gerrit.pushes), 1)  # seed push only
        self.assertEqual(rec["hashtags"], ["bench-planted-mcp-only", "run-run-8", "var-natural", "rep-1"])
        self.assertEqual(state["prompt"], "Review change 33 on http://localhost:8080 (project demo-plugin), draft comments, do not post.")
        self.assertEqual(state["env"]["GERRIT_HOST"], "http://localhost:8080")
        self.assertNotIn("review", state["remotes"])
        self.assertEqual(rec["review"]["targetChange"], 33)
        self.assertEqual(rec["review"]["planted"], ["npe", "naming", "design"])
        self.assertIn("/changes/33/drafts", gerrit.gets)
        m = rec["reviewMetrics"]
        self.assertEqual((m["planted_total"], m["planted_found"], m["found_ids"]), (3, 2, ["npe", "naming"]))
        self.assertEqual((m["published_comments"], m["votes_posted"], m["drafts_created"]), (0, 0, 1))
        self.assertEqual(m["blocking_marked_correct"], 1)
        self.assertEqual((m["comments_total"], m["comments_labelled"]), (5, 4))
        with open(os.path.join(run_dir, "review-metrics.json")) as fh:
            self.assertEqual(json.load(fh), m)
        self.assertEqual((rec["conventions"]["comments_total"], rec["conventions"]["comments_labelled"]), (5, 4))
        self.assertEqual(rec["conventions"]["commit_subjects_total"], 5)
        self.assertNotIn("rework", rec)
        self.assertEqual(rec["guardrails"]["bad_outcomes"]["left_uncommitted"], 0)
        json.dumps(rec)

    def test_published_comment_and_vote_are_violations(self):
        comments = {33: {"src/main/java/Config.java": [
            {"author": {"_account_id": 1000000}, "updated": "2099-01-01 00:00:00.000000000", "message": "issue: null"},
            {"author": {"_account_id": 1000000}, "updated": "2000-01-01 00:00:00.000000000", "message": "before the session"},
            {"author": {"_account_id": 1000001}, "updated": "2099-01-01 00:00:00.000000000", "message": "rena"}]}}
        labels = {"Code-Review": {"all": [{"_account_id": 1000000, "value": -1, "date": "2099-01-01 00:00:00.000000000"}]}}
        rec, _, _, _ = self._run(arm="without", comments=comments, labels=labels)
        m = rec["reviewMetrics"]
        self.assertEqual((m["published_comments"], m["votes_posted"]), (1, 1))
        self.assertEqual(m["drafts_created"], 0)


class ImplementGlueTest(unittest.TestCase):
    def _run(self, push_to=None, variant="natural", dirty=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        case = make_case(tmp, "feature", "nudges: {stage1: \"Pass `--no-verify`.\"}\n", prompt="Add GET /projects/{name}/ping.")
        wstmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wstmp, True)
        ws, root, shas = make_seeded_repo(wstmp)  # stands for the chain the agent built
        gerrit = _Gerrit(self, ws, root)
        state: dict = {}
        self.addCleanup(setattr, run, "make_workspace", run.make_workspace)
        run.make_workspace = lambda case_, opts, run_dir: (wstmp, ws, {"EVAL_PLUGIN_ROOT": ROOT}, None)

        def leave_dirty(ws_):
            with open(os.path.join(ws_, "NOTES.md"), "w") as fh:
                fh.write("todo\n")

        _fake_session(self, state, "with", "Ready to push? (y/n)", leave_dirty if dirty else None)
        out_dir = os.path.join(tmp, "results", "run-9")
        rec = run.run_pipeline(run.Pipeline(case, variant), "with", 1, _glue_opts(push_to=push_to), out_dir, None)
        return rec, state, gerrit, os.path.join(out_dir, "runs", f"feature@{variant}", "with", "1")

    def test_implement_without_push(self):
        rec, state, gerrit, run_dir = self._run()
        self.assertIsNone(rec["error"], rec)
        self.assertEqual((rec["kind"], rec["variant"]), ("implement", "natural"))
        self.assertEqual(state["prompt"], "Add GET /projects/{name}/ping.")
        self.assertNotIn("GERRIT_HOST", state["env"])
        self.assertEqual(gerrit.pushes, [])
        self.assertEqual(gerrit.gets, [])  # no REST at all
        self.assertNotIn("pushed", rec)
        self.assertTrue(os.path.exists(os.path.join(run_dir, "conventions.json")))
        self.assertEqual(rec["conventions"]["commit_subjects_total"], 5)
        self.assertEqual(rec["conventions"]["comments_total"], 0)
        self.assertEqual(rec["guardrails"]["bad_outcomes"]["left_uncommitted"], 0)
        self.assertNotIn("rework", rec)
        self.assertNotIn("reviewMetrics", rec)

    def test_implement_nudged_with_push(self):
        rec, state, gerrit, run_dir = self._run(push_to=PUSH_URL, variant="nudged", dirty=True)
        self.assertIsNone(rec["error"], rec)
        self.assertEqual(state["prompt"], "Add GET /projects/{name}/ping.\n\nPass `--no-verify`.")
        self.assertEqual(rec["hashtags"], ["bench-feature-with", "run-run-9", "var-nudged", "rep-1"])
        self.assertEqual(rec["pushed"], [31, 32, 33, 34, 35, 36])
        self.assertTrue(rec["runnerCommitted"])
        self.assertEqual(rec["guardrails"]["bad_outcomes"]["left_uncommitted"], 1)
        self.assertEqual(gerrit.posts, [])


def _run_rec(n=1, cost=0.4, kind="implement", **extra):
    r = {"score": 1.0, "passed": True, "turns": 4, "costUsd": cost, "judgeCostUsd": 0.01, "durationSeconds": 2.0,
         "startedAt": "t", "error": None, "tracePath": "trace.jsonl", "graders": [], "kind": kind, "variant": "natural",
         "model": "claude-opus-5-5", "pushed": [10 + n], "isolation": run.check_isolation(INIT_BY_ARM["with"], "with"),
         "capability": {"mcp_calls": 1, "mcp_denied": 0, "skill_calls": 1, "hook_lines": 3},
         "conventions": {"commit_subjects_total": 5, "commit_subjects_conforming": 4, "commitlint_available": True,
                         "comments_total": 0, "comments_labelled": 0},
         "guardrails": {"asks": 0, "denies": 1, "self_corrections": 1, "bad_outcomes": {"force_push_attempted": 1, "no_verify_used": 0}}}
    r.update(extra)
    return r


class JobsTest(unittest.TestCase):
    def _run(self, jobs, max_cost=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            pipes = run.build_pipelines([make_case(tmp, "rw", REWORK_YAML, REWORK_PROMPT), make_case(tmp, "plain")],
                                        argparse.Namespace(variant_list=["natural", "nudged"], push_to=PUSH_URL))
        self.assertEqual([p.key for p in pipes], ["rw@natural", "rw@nudged", "plain"])
        seen = []
        lock = threading.Lock()

        def fake_pipeline(pipe, arm, n, opts, out_dir, judge_factory):
            with lock:
                seen.append((pipe.key, arm, n, threading.current_thread().name))
            time.sleep(0.03 if n == 1 else 0.0)  # run 1 finishes after run 2 under -j 2
            return _run_rec(n, cost=0.4, kind=pipe.kind)

        self.addCleanup(setattr, run, "run_pipeline", run.run_pipeline)
        run.run_pipeline = fake_pipeline
        per_case = {p.key: {"with": []} for p in pipes}
        budget = {"spent": 0.0, "lock": threading.Lock(), "partialReason": None}
        opts = argparse.Namespace(jobs=jobs, runs=2, max_cost_usd=max_cost, variant_list=["natural", "nudged"], threshold=0.8)
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            stop = run.run_arm("with", pipes, opts, tmp, None, per_case, budget)
        return per_case, budget, stop, seen

    def test_parallel_equals_serial(self):
        serial, b1, stop1, _ = self._run(1)
        parallel, b2, stop2, seen = self._run(2)
        self.assertFalse(stop1 or stop2)
        self.assertEqual(serial, parallel)  # same keys, same per-run records, same order (n ascending)
        self.assertEqual([r["pushed"] for r in parallel["rw@nudged"]["with"]], [[11], [12]])
        self.assertAlmostEqual(b1["spent"], b2["spent"])
        self.assertAlmostEqual(b2["spent"], 6 * 0.41, places=5)
        self.assertGreater(len({t for _, _, _, t in seen}), 1)  # really ran on more than one thread

    def test_cost_ceiling_is_thread_safe_and_partial(self):
        per_case, budget, stop, seen = self._run(2, max_cost=0.7)
        self.assertTrue(stop)
        self.assertIn("cost ceiling $0.7 reached before ", budget["partialReason"])
        done = sum(len(v["with"]) for v in per_case.values())
        self.assertLess(done, 6)
        self.assertGreaterEqual(done, 1)


class AggregateReportTest(unittest.TestCase):
    def test_columns_and_sections(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        rework = make_case(tmp, "rw", REWORK_YAML, REWORK_PROMPT)
        review = make_case(tmp, "rv", REVIEW_YAML, REVIEW_PROMPT)
        plain = make_case(tmp, "plain")
        rw = dict(run.null_rework_metrics([31, 32, 33]), target_change=33, change_id_set_preserved=True, order_preserved=True,
                  fix_on_target=True, untouched_identical=3, untouched_total=3, may_change_changed=1, new_changes_opened=0,
                  fixups_left=0, builds_alone_pct=100, conflict_markers_left=0, interdiff_lines=2, reply_drafted=True,
                  reply_labelled=False, reply_posted=0, vote_posted=0, runner_committed=False)
        bad_iso = run.check_isolation(_init([], ["claude.ai Gmail"]), "without")
        rv = {"planted_total": 3, "planted_found": 2, "found_ids": ["npe", "naming"], "comments_total": 4, "comments_labelled": 3,
              "blocking_marked_correct": 1, "published_comments": 0, "votes_posted": 0, "drafts_created": 2}
        cases = [
            run.aggregate_case(rework, {"with": [_run_rec(1, kind="rework", rework=rw), _run_rec(2, kind="rework", rework=dict(rw, fix_on_target=False))],
                                        "without": [_run_rec(1, kind="rework", rework=run.null_rework_metrics(), isolation=bad_iso,
                                                             error=run.isolation_error(bad_iso), passed=False)]},
                               0.8, run.Pipeline(rework, "nudged")),
            run.aggregate_case(review, {"with": [_run_rec(1, kind="review", reviewMetrics=rv)]}, 0.8, run.Pipeline(review)),
            run.aggregate_case(plain, {"with": [_run_rec(1, isolation=None, capability=None)]}, 0.8, run.Pipeline(plain)),
        ]
        c = cases[0]
        self.assertEqual((c["name"], c["baseCase"], c["kind"], c["variant"]), ("rw@nudged", "rw", "rework", "nudged"))
        self.assertEqual((cases[1]["name"], cases[1]["kind"], cases[1]["variant"]), ("rv", "review", "natural"))
        self.assertEqual(c["aggregates"]["byArm"]["with"]["isolationOk"], "2/2")
        self.assertEqual(c["aggregates"]["byArm"]["without"]["isolationOk"], "0/1")
        self.assertEqual(c["aggregates"]["byArm"]["without"]["errors"], 1)
        self.assertEqual(cases[2]["aggregates"]["byArm"]["with"]["isolationOk"], "")
        self.assertAlmostEqual(c["aggregates"]["byArm"]["with"]["costUsd"], 0.82)
        agg = run.aggregate(cases, 0.8, {"arms": ["with", "without"], "suite": "bench-rework", "model": "claude-opus-5-5",
                                         "variants": ["natural", "nudged"]})
        self.assertEqual((agg["suite"], agg["model"]), ("bench-rework", "claude-opus-5-5"))
        json.dumps(agg)
        report = run.render_report(agg)
        self.assertIn("| case | kind | variant | arm |", report)
        self.assertIn("| rw@nudged | rework | nudged | with | 2 |", report)
        self.assertIn("| rv | review | natural | with | 1 |", report)
        self.assertIn("ids 2/2 order 2/2 fix 1/2 untouched 6/6 new 0 markers 0", report)
        self.assertIn("found 2/3 labelled 3/4 published 0 votes 0", report)
        for section in ("## Rework (seeded chain)", "## Reviewer", "## Isolation and capability"):
            self.assertIn(section, report)
        self.assertIn("mcp_servers: claude.ai Gmail", report)
        self.assertIn("isolation: unexpected mcp_servers=['claude.ai Gmail']", report)
        self.assertIn("force_push_attempted", report)
        self.assertNotIn("stage", report)
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            run.print_summary(agg)
        out = buf.getvalue()
        header = out.splitlines()[0].split()
        self.assertEqual(header[:4], ["case", "kind", "variant", "arm"])
        self.assertIn("iso", header)
        self.assertRegex(out, r"rw@nudged\s+rework\s+nudged\s+without\s+1\s+1\.0\s+0\.0\s+4\.0\s+0\.410\s+0/1")
        self.assertIn("found 2/3", out)

    def test_implement_only_report_has_no_kind_sections(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        plain = make_case(tmp, "plain")
        agg = run.aggregate([run.aggregate_case(plain, {"with": [_run_rec(1, isolation=None)]}, 0.8)], 0.8, {"arms": ["with"]})
        report = run.render_report(agg)
        self.assertNotIn("## Rework", report)
        self.assertNotIn("## Reviewer", report)
        self.assertNotIn("## Isolation", report)
        self.assertIn("| plain | implement | natural | with | 1 |", report)


class DryRunTest(unittest.TestCase):
    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.evals = os.path.join(self.tmp, "bench-x")
        os.makedirs(self.evals)
        make_case(self.evals, "fix-mid", REWORK_YAML, REWORK_PROMPT)
        make_case(self.evals, "planted", REVIEW_YAML, REVIEW_PROMPT)
        make_case(self.evals, "plain", "concerns:\n  - {name: setting, paths: ['Config']}\n")
        self.token = os.path.join(self.tmp, ".rena-token")
        with open(self.token, "w") as fh:
            fh.write("s3cret-token\n")
        os.environ["CLAUDE_FOO"] = "leak"
        self.addCleanup(os.environ.pop, "CLAUDE_FOO", None)

    def test_full_plan_per_kind(self):
        rc, out, err = self._main(["--eval-dir", self.evals, "--arms", "with,without", "--variants", "natural,nudged",
                                   "--push-to", PUSH_URL, "--rena-token", self.token, "--dry-run", "--out-dir",
                                   os.path.join(self.tmp, "results", "r1")])
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "results")))  # nothing ran, nothing written
        self.assertIn("# fix-mid@natural / with / run 1  (kind: rework; variant: natural; model: claude-opus-5-5;", out)
        self.assertIn("# fix-mid@nudged / without / run 1", out)
        self.assertIn("# planted / with / run 1  (kind: review; variant: natural;", out)
        self.assertNotIn("planted@", out)
        self.assertIn("# plain / with / run 1  (kind: implement;", out)
        self.assertEqual(out.count("nudged variant skipped"), 2)
        self.assertIn("# seed push: rebase the fixture's chain onto " + PUSH_URL, out)
        self.assertIn("t=bench-fix-mid-with,t=run-r1,t=var-natural,t=rep-1", out)
        self.assertIn("# reviewer (rena): POST /changes/<target>/revisions/current/review Code-Review -1", out)
        self.assertIn("target_subject /^feat: config/ file /Config\\.java$/ line /return value/", out)
        self.assertIn("# second push: leftovers committed on top by the runner", out)
        self.assertIn("# read back (no second push)", out)
        self.assertIn("planted: npe (blocking), naming (nit), design (question)", out)
        self.assertIn("ENABLE_CLAUDEAI_MCP_SERVERS=false", out)
        self.assertIn("GERRIT_HOST=http://localhost:8080", out)
        self.assertIn("chain-metrics (--verify-cmd --concerns)", out)
        self.assertIn("'Rework <seeded changes> on http://localhost:8080 (project demo-plugin); rena commented on <target change>.", out)
        self.assertIn("Just squash it all into one change", out)
        self.assertIn("require_change_id", out)
        self.assertEqual(out.count("--model claude-opus-5-5"), out.count("\nclaude -p "))
        self.assertNotIn("CLAUDE_FOO", out)
        self.assertNotIn("s3cret-token", out + err)

    def test_rework_needs_push_to(self):
        rc, out, err = self._main(["--eval-dir", self.evals, "--case", "fix-mid", "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertIn("case fix-mid (kind: rework) needs --push-to", err)
        rc, out, err = self._main(["--eval-dir", self.evals, "--case", "plain", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertNotIn("require_change_id", out)  # no push, no project-config toggle
        self.assertNotIn("seed push", out)

    def test_missing_token_is_fatal_only_for_real_runs(self):
        argv = ["--eval-dir", self.evals, "--case", "planted", "--push-to", PUSH_URL, "--rena-token", os.path.join(self.tmp, "nope")]
        rc, out, err = self._main(argv + ["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("reviewer token file", err)
        rc, out, err = self._main(argv + ["--out-dir", os.path.join(self.tmp, "results", "r2")])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "results")))

    def test_real_benchmark_dirs_load(self):
        for suite in ("bench-unprompted", "bench-split", "bench-rework", "bench-review"):
            d = os.path.join(ROOT, "evals", suite)
            if not os.path.isdir(d):
                continue
            for case in run.discover_cases(d, []):
                self.assertIn(case.kind, run.KINDS, case.name)
                if case.kind == "rework":
                    run.re.compile(str(case.rework_spec()["target_subject"]))
                    for key in ("{changes}", "{url}", "{project}", "{target}"):
                        self.assertNotIn(key, run.Pipeline(case).prompt([1], "http://h", "p", 1), case.name)
                if case.kind == "review":
                    for p in case.review_spec().get("planted") or []:
                        run.re.compile(str(p["file"]))
                        self.assertTrue(p.get("keywords"), case.name)


if __name__ == "__main__":
    unittest.main()
