"""Unit tests for evals/run.py — YAML-subset parser, graders on synthetic traces,
aggregation, the official result schema and the dry-run command builder.
No `claude` process is spawned."""
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
        self.assertIn("| trigger-x | with |", report)

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
        self.assertNotIn("--model", cmd)
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


if __name__ == "__main__":
    unittest.main()


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
