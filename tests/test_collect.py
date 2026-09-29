"""tests/test_collect.py — evals/metrics/collect.py (WP A11).

Builds a synthetic results directory (official aggregate-result.json shape +
runs/<case>/<arm>/<n>/{trace.jsonl,hook-trace.log,chain-metrics.json}) in a
temp dir and checks per-run parsing, per case x arm aggregation, deltas,
targets and the rendered markdown cells. Stdlib only.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COLLECT = os.path.join(REPO_ROOT, "evals", "metrics", "collect.py")


def _load_collect():
    spec = importlib.util.spec_from_file_location("collect", COLLECT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collect = _load_collect()


def _assistant(*tool_uses):
    content = [{"type": "tool_use", "id": tid, "name": name, "input": inp} for tid, name, inp in tool_uses]
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _tool_result(tid, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


def _result(cost, turns, ms):
    return {"type": "result", "subtype": "success", "total_cost_usd": cost, "num_turns": turns,
            "duration_ms": ms, "usage": {"input_tokens": 100, "output_tokens": 50}}


def _trace_lines(cost, turns, ms, commits=1, pushes=0, denied_then_fixed=False):
    events = [{"type": "system", "subtype": "init"}]
    tid = 0
    for _ in range(commits):
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": "git add -A && git commit -q -m 'feat: x'"})))
        events.append(_tool_result(f"t{tid}", "[master abc] feat: x"))
    tid += 1
    events.append(_assistant((f"t{tid}", "Read", {"file_path": "/x"})))
    events.append(_tool_result(f"t{tid}", "contents"))
    if denied_then_fixed:
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": "git push origin HEAD:refs/heads/master"})))
        events.append(_tool_result(f"t{tid}", "PreToolUse:Bash hook blocked the command: direct push denied", True))
        tid += 1
        events.append(_assistant((f"t{tid}", "AskUserQuestion", {"question": "push to refs/for/master?"})))
        events.append(_tool_result(f"t{tid}", "yes"))
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": "git push origin HEAD:refs/for/master"})))
        events.append(_tool_result(f"t{tid}", "ok"))
        pushes += 2
    for _ in range(pushes - (2 if denied_then_fixed else 0)):
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": "git -C . push origin HEAD:refs/for/master"})))
        events.append(_tool_result(f"t{tid}", "ok"))
    events.append(_result(cost, turns, ms))
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _chain(chain_length, lines_median, within, one_cid, violations=0, **extra):
    d = {
        "chain_length": chain_length, "lines_median": lines_median, "lines_p75": lines_median + 10,
        "lines_max": lines_median + 20, "files_median": 2, "within_budget_pct": within,
        "one_change_id_pct": one_cid, "conventional_pct": 100, "single_concern_pct": 100,
        "builds_alone_pct": None, "fixups_present": False, "violations": violations,
        "refs_for_pushed": chain_length, "change_id_set": ["I%040d" % i for i in range(chain_length)],
    }
    d.update(extra)
    return d


class SyntheticResults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="collect-test.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.results = os.path.join(self.tmp, "2026-09-30T00-00-00")
        os.makedirs(self.results)
        # case feature-3-concern: with x2, mcp-only x2, without x1 (+ without run 2 only in aggregate)
        self.write_run("feature-3-concern", "with", 1, _trace_lines(0.50, 20, 100_000, commits=3, denied_then_fixed=True),
                       "git-guard.sh\tpush\tdeny\ngit-guard.sh\tpush\task\n", _chain(3, 40, 100, 100, violations=1))
        self.write_run("feature-3-concern", "with", 2, _trace_lines(0.70, 30, 200_000, commits=3),
                       "git-guard.sh\tpush\task\n", _chain(3, 60, 100, 100))
        self.write_run("feature-3-concern", "mcp-only", 1, _trace_lines(0.40, 15, 80_000, commits=1, pushes=1),
                       None, _chain(1, 200, 0, 100))
        self.write_run("feature-3-concern", "mcp-only", 2, _trace_lines(0.60, 25, 120_000, commits=2, pushes=1),
                       None, _chain(2, 120, 50, 100))
        self.write_run("feature-3-concern", "without", 1, _trace_lines(0.30, 12, 60_000, commits=1, pushes=1),
                       None, _chain(1, 250, 0, 0))
        # a run with a chain-metrics.json but no trace and no hook trace
        self.write_run("bugfix-1-concern", "with", 1, None, None, _chain(1, 8, 100, 100))
        aggregate = {
            "schemaVersion": 1,
            "claudeVersion": "2.1.284",
            "costUsd": 2.5,
            "aggregates": {"overallScore": 0.9, "casesPassed": 1, "casesTotal": 2, "meanDelta": 0.2},
            "cases": [
                {"name": "feature-3-concern", "aggregates": {"score": 0.9, "delta": 0.3},
                 "arms": {"with": [{"score": 1.0}, {"score": 0.8}],
                          "mcp-only": [{"score": 0.6}, {"score": 0.7}],
                          "without": [{"score": 0.5}, {"score": 0.4, "costUsd": 0.2, "durationSeconds": 30, "numTurns": 9}]}},
                {"name": "bugfix-1-concern", "aggregates": {"score": 1.0},
                 "arms": {"with": [{"passed": True}]}},
            ],
        }
        with open(os.path.join(self.results, "aggregate-result.json"), "w") as fh:
            json.dump(aggregate, fh)

    def write_run(self, case, arm, n, trace, hook, chain):
        d = os.path.join(self.results, "runs", case, arm, str(n))
        os.makedirs(d)
        if trace is not None:
            with open(os.path.join(d, "trace.jsonl"), "w") as fh:
                fh.write(trace)
        if hook is not None:
            with open(os.path.join(d, "hook-trace.log"), "w") as fh:
                fh.write(hook)
        if chain is not None:
            with open(os.path.join(d, "chain-metrics.json"), "w") as fh:
                json.dump(chain, fh)
        return d

    def rows(self):
        rows, sources = collect.collect([self.results])
        return rows, sources

    def row(self, rows, case, arm, n):
        return next(r for r in rows if (r["case"], r["arm"], r["n"]) == (case, arm, n))

    # ---- per-run parsing --------------------------------------------------
    def test_trace_process_metrics(self):
        rows, _ = self.rows()
        r = self.row(rows, "feature-3-concern", "with", 1)
        self.assertEqual(r["turns"], 20)
        self.assertEqual(r["cost_usd"], 0.50)
        self.assertEqual(r["wall_s"], 100.0)
        self.assertEqual(r["tool_calls"], 7)        # 3 commits + Read + denied push + Ask + push
        self.assertEqual(r["bash_calls"], 5)
        self.assertEqual(r["git_commit_calls"], 3)
        self.assertEqual(r["git_push_calls"], 2)    # the denied attempt still counts
        self.assertEqual(r["asks"], 2)              # 1 hook ask + 1 AskUserQuestion
        self.assertEqual(r["denies"], 1)            # from hook-trace.log
        self.assertEqual(r["self_corrections"], 1)  # push denied, later push succeeded
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["chain_length"], 3)
        self.assertEqual(r["violations"], 1)
        self.assertEqual(len(r["change_id_set"]), 3)

    def test_denies_fall_back_to_trace_when_no_hook_log(self):
        rows, _ = self.rows()
        r = self.row(rows, "feature-3-concern", "mcp-only", 1)
        self.assertEqual(r["denies"], 0)
        self.assertEqual(r["asks"], 0)
        self.assertEqual(r["git_push_calls"], 1)    # `git -C . push` is recognised
        self.assertEqual(r["score"], 0.6)

    def test_missing_trace_uses_aggregate_and_chain_only(self):
        rows, _ = self.rows()
        r = self.row(rows, "bugfix-1-concern", "with", 1)
        self.assertIsNone(r["turns"])
        self.assertIsNone(r["tool_calls"])
        self.assertIsNone(r["denies"])
        self.assertEqual(r["score"], 1.0)           # from `passed: true`
        self.assertEqual(r["chain_length"], 1)
        self.assertEqual(r["within_budget_pct"], 100)

    def test_aggregate_only_run_is_included(self):
        rows, sources = self.rows()
        r = self.row(rows, "feature-3-concern", "without", 2)
        self.assertEqual(r["dir"], "")
        self.assertEqual(r["cost_usd"], 0.2)
        self.assertEqual(r["wall_s"], 30)
        self.assertEqual(r["turns"], 9)
        self.assertIsNone(r["chain_length"])
        self.assertEqual(len(rows), 7)
        self.assertEqual(sources[0]["claudeVersion"], "2.1.284")
        self.assertEqual(sources[0]["overallScore"], 0.9)

    def test_hook_trace_parser_tolerates_blank_and_space_separated(self):
        p = os.path.join(self.tmp, "h.log")
        with open(p, "w") as fh:
            fh.write("git-guard.sh push deny\n\n  \ngit-guard.sh\tcommit\task\r\nbroken\n")
        c = collect.parse_hook_trace(p)
        self.assertEqual((c["deny"], c["ask"], c["lines"], c["present"]), (1, 1, 3, True))
        self.assertFalse(collect.parse_hook_trace(os.path.join(self.tmp, "none"))["present"])

    def test_git_verbs(self):
        self.assertEqual(collect.git_verbs("cd x && git -C y push origin HEAD:refs/for/master"), ["push"])
        self.assertEqual(collect.git_verbs("git -c sequence.editor=true rebase -i main; git log"), ["rebase", "log"])
        self.assertEqual(collect.git_verbs("echo git push"), [])
        self.assertEqual(collect.git_verbs("GIT_EDITOR=true git commit --amend"), ["commit"])
        self.assertEqual(collect.git_verbs(None), [])

    # ---- aggregation ------------------------------------------------------
    def test_per_case_mean_median_and_deltas(self):
        rows, _ = self.rows()
        rep = collect.aggregate(rows)
        self.assertEqual(rep["cases"], ["bugfix-1-concern", "feature-3-concern"])
        self.assertEqual(rep["arms"], ["with", "mcp-only", "without"])
        fc = rep["per_case"]["feature-3-concern"]
        self.assertEqual(fc["with"]["runs"], 2)
        self.assertAlmostEqual(fc["with"]["metrics"]["cost_usd"]["mean"], 0.60)
        self.assertAlmostEqual(fc["with"]["metrics"]["lines_median"]["median"], 50)
        self.assertAlmostEqual(fc["mcp-only"]["metrics"]["within_budget_pct"]["mean"], 25)
        self.assertAlmostEqual(fc["with"]["metrics"]["chain_length"]["mean"], 3)
        self.assertAlmostEqual(fc["mcp-only"]["metrics"]["chain_length"]["mean"], 1.5)
        d = rep["deltas"]["cases"]["feature-3-concern"]
        self.assertAlmostEqual(d["with-mcp-only"]["chain_length"], 1.5)
        self.assertAlmostEqual(d["with-mcp-only"]["within_budget_pct"], 75)
        self.assertAlmostEqual(d["with-mcp-only"]["cost_usd"], 0.10)
        self.assertAlmostEqual(d["with-without"]["cost_usd"], 0.60 - 0.25)   # without: 0.30 and 0.20
        self.assertAlmostEqual(d["with-without"]["one_change_id_pct"], 100)
        # metrics no arm has stay None instead of raising
        self.assertIsNone(fc["with"]["metrics"]["builds_alone_pct"]["mean"])

    def test_overall_and_targets(self):
        rows, _ = self.rows()
        rep = collect.aggregate(rows)
        ov = rep["overall"]
        self.assertEqual(ov["with"]["runs"], 3)
        self.assertAlmostEqual(ov["with"]["metrics"]["cost_usd"]["mean"], 0.60)       # 0.5, 0.7 (bugfix has none)
        self.assertAlmostEqual(ov["with"]["metrics"]["within_budget_pct"]["mean"], 100)
        self.assertAlmostEqual(ov["with"]["metrics"]["violations"]["mean"], 1 / 3)
        targets = {t["key"]: t for t in rep["targets"]}
        self.assertTrue(targets["within_budget_pct"]["pass"])
        self.assertTrue(targets["one_change_id_pct"]["pass"])
        self.assertFalse(targets["violations"]["pass"])
        # cost overhead: with 0.60 vs mcp-only 0.50 -> +20 % -> pass
        self.assertAlmostEqual(targets["cost_overhead_pct"]["value"], 20.0)
        self.assertTrue(targets["cost_overhead_pct"]["pass"])

    def test_empty_report(self):
        rep = collect.aggregate([])
        self.assertEqual(rep["runs_total"], 0)
        self.assertTrue(all(t["pass"] is None for t in rep["targets"]))
        md = collect.render_markdown(rep, [], generated="now")
        self.assertIn("No runs yet", md)
        self.assertIn("make bench", md)
        self.assertIn("| budget compliance | >= 90 % | – | – |", md)

    # ---- rendering --------------------------------------------------------
    def test_markdown_cells(self):
        rows, sources = self.rows()
        rep = collect.aggregate(rows)
        md = collect.render_markdown(rep, sources, generated="now")
        self.assertIn("### feature-3-concern", md)
        self.assertIn("Runs — C with: 2, B mcp-only: 2, A without: 2", md)
        self.assertIn("| chain length | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |", md)
        self.assertIn("| within budget | 100 % / 100 % | 25 % / 25 % | 0 % / 0 % | +75 % | +100 % |", md)
        self.assertIn("| cost | $0.600 / $0.600 | $0.500 / $0.500 | $0.250 / $0.250 | +$0.100 | +$0.350 |", md)
        self.assertIn("| rule violations | == 0 | 0.3 | FAIL |", md)
        self.assertIn("| budget compliance | >= 90 % | 100.0 % | PASS |", md)
        self.assertIn("| cost overhead vs mcp-only | <= 30 % | 20.0 % | PASS |", md)
        self.assertIn("claude 2.1.284", md)
        self.assertIn("**A — `without`**", md)
        self.assertIn("**C — `with`**", md)
        self.assertNotIn("builds alone", md)        # no arm has the metric -> row omitted

    def test_main_writes_markdown_and_json(self):
        out_md = os.path.join(self.tmp, "bench.md")
        out_json = os.path.join(self.tmp, "bench.json")
        rc = collect.main(["--results", self.results, "--out", out_md, "--json", out_json])
        self.assertEqual(rc, 0)
        with open(out_md) as fh:
            self.assertIn("## Overall (all cases, 7 runs)", fh.read())
        with open(out_json) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["runs"]), 7)
        self.assertEqual(data["overall"]["with"]["runs"], 3)
        self.assertEqual(data["targets"][0]["key"], "within_budget_pct")

    def test_main_rejects_missing_dir(self):
        rc = collect.main(["--results", os.path.join(self.tmp, "nope"), "--out", os.path.join(self.tmp, "x.md")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
