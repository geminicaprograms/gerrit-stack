"""tests/test_collect.py — evals/metrics/collect.py (WP A11).

Builds a synthetic results directory (official aggregate-result.json shape +
runs/<case>/<arm>/<n>/{trace.jsonl,hook-trace.log,chain-metrics.json}) in a
temp dir and checks per-run parsing, per case x arm aggregation, deltas,
targets and the rendered markdown cells. Stdlib only.

W3 (rework benchmark): ``PipelineResults`` builds ``<base>@<scenario>-<variant>``
pipelines with ``stage2/``, ``rework-metrics.json`` and guardrails and checks the
stage rows, rates, deltas and the Rework / Guardrails / Cost per stage sections;
``LegacyRegression`` pins the legacy rendering to a golden captured from the
pre-pipeline collector.
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
                          "without": [{"score": 0.5}, {"score": 0.4, "costUsd": 0.2, "durationSeconds": 30, "turns": 9}]}},
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
        # run.py writes the field as "turns" (not "numTurns"); this run has no trace.jsonl,
        # so the value must come from the aggregate-result.json fallback.
        self.assertEqual(r["turns"], 9)
        self.assertIsNone(r["chain_length"])
        self.assertEqual(len(rows), 7)
        self.assertEqual(sources[0]["claudeVersion"], "2.1.284")
        self.assertEqual(sources[0]["overallScore"], 0.9)

    def test_turns_falls_back_to_aggregate_entry_when_no_trace(self):
        # Regression for a key mismatch: run.py's aggregate entries carry "turns", not
        # "numTurns" — load_run must read agg_entry.get("turns") for the fallback to work
        # when there is no trace.jsonl (aggregate-only run, or a run dir without a trace).
        row = collect.load_run("", "case", "with", 1, {"turns": 7})
        self.assertEqual(row["turns"], 7)
        # A trace's own turns count (when present) still wins over the aggregate value.
        row_dir = self.write_run("turns-fallback-case", "with", 1,
                                 _trace_lines(0.1, 42, 1_000), None, None)
        row2 = collect.load_run(row_dir, "case", "with", 1, {"turns": 7})
        self.assertEqual(row2["turns"], 42)

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


# ---------------------------------------------------------------------------
# Rework pipelines (W3): <base>@<scenario>-<variant> entries with stage2/,
# rework-metrics.json and guardrails; legacy entries must render unchanged.
# ---------------------------------------------------------------------------
def _rework(arm, scenario):
    d = {
        "target_change": 12, "target_change_id": "I" + "a" * 40,
        "stage1_changes": [11, 12, 13], "stage2_changes": [11, 12, 13],
        "fixup_on_target": True, "change_id_set_preserved": True, "new_changes_opened": 0,
        "descendants_total": 1, "descendants_rebased": 1, "changes_needing_reread": 1,
        "interdiff_lines": 12 if arm == "with" else 30, "landable_below": 1 if arm == "with" else 0,
        "landable_below_lines": 40 if arm == "with" else 0, "split_count": None, "split_equivalent": None,
        "reply_drafted": True, "reply_conventional": arm == "with", "reply_posted": False,
        "vote_posted": arm == "without",
    }
    if scenario == "split":
        d.update({"split_count": 3, "split_equivalent": True, "stage2_changes": [11, 12, 13, 14, 15, 16]})
    return d


def _guardrails(arm):
    zero = {k: 0 for k in ("no_verify_used", "amend_m_used", "force_push_attempted", "topic_used_unasked",
                           "refs_heads_push_attempted", "commit_without_change_id")}
    zero.update({"refs_heads_moved": False, "gerrit_master_moved": False})
    bad2 = dict(zero)
    if arm == "without":
        bad2.update({"amend_m_used": 1, "force_push_attempted": 1, "refs_heads_moved": True})
    return {
        "stage1": {"asks": 1, "denies": 0, "self_corrections": 0, "bad_outcomes": dict(zero)},
        "stage2": {"asks": 1, "denies": 1 if arm == "with" else 0, "self_corrections": 1 if arm == "with" else 0,
                   "bad_outcomes": bad2},
    }


BAD_STAGE2_CMD = "git commit --amend -m 'fix' && git push -f origin HEAD:refs/heads/master"


class PipelineResults(unittest.TestCase):
    """rate-limited-ping x {fix-natural, fix-nudged} x {with, without}, one run each."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="collect-pipe.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.results = os.path.join(self.tmp, "20260930-120000")
        os.makedirs(self.results)
        cases = []
        for key in ("rate-limited-ping@fix-natural", "rate-limited-ping@fix-nudged"):
            base, rest = key.split("@")
            scenario, variant = rest.split("-")
            arms = {}
            for arm in ("with", "without"):
                arms[arm] = [self.write_pipeline(key, arm, 1, scenario, variant)]
            cases.append({"name": key, "baseCase": base, "scenario": scenario, "variant": variant,
                          "aggregates": {"score": 1.0}, "arms": arms})
        aggregate = {"schemaVersion": 1, "claudeVersion": "2.1.285", "costUsd": 4.8,
                     "aggregates": {"overallScore": 1.0}, "cases": cases}
        with open(os.path.join(self.results, "aggregate-result.json"), "w") as fh:
            json.dump(aggregate, fh)

    def write_pipeline(self, key, arm, n, scenario, variant):
        d = os.path.join(self.results, "runs", key, arm, str(n))
        s2 = os.path.join(d, "stage2")
        os.makedirs(s2)
        s1cost, s2cost = (1.0, 0.5) if arm == "with" else (0.6, 0.3)
        with open(os.path.join(d, "trace.jsonl"), "w") as fh:
            fh.write(_trace_lines(s1cost, 20, 100_000, commits=3))
        with open(os.path.join(d, "hook-trace.log"), "w") as fh:
            fh.write("git-guard.sh\tpush\task\n")
        with open(os.path.join(d, "chain-metrics.json"), "w") as fh:
            json.dump(_chain(3, 40, 100, 100), fh)
        # stage 2: 10 turns, one Bash call (a bad one for arm A), hook deny+ask for arm C
        events = [{"type": "system", "subtype": "init"}]
        if arm == "without":
            events.append(_assistant(("s1", "Bash", {"command": BAD_STAGE2_CMD})))
            events.append(_tool_result("s1", "ok"))
        else:
            events.append(_assistant(("s1", "Bash", {"command": "git commit --fixup HEAD~1"})))
            events.append(_tool_result("s1", "ok"))
        events.append(_result(s2cost, 10, 50_000))
        with open(os.path.join(s2, "trace.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(e) for e in events) + "\n")
        with open(os.path.join(s2, "hook-trace.log"), "w") as fh:
            fh.write("git-guard.sh\tpush\tdeny\ngit-guard.sh\tpush\task\n" if arm == "with" else "")
        with open(os.path.join(s2, "chain-metrics.json"), "w") as fh:
            json.dump(_chain(3, 42, 100, 100), fh)
        with open(os.path.join(d, "rework-metrics.json"), "w") as fh:
            json.dump(_rework(arm, scenario), fh)
        with open(os.path.join(d, "review.json"), "w") as fh:
            json.dump({"target_change": 12, "file": "DemoPluginConfig.java", "line": 7,
                       "message": "issue (blocking): read the limit once"}, fh)
        tags = ["bench-rate-limited-ping-" + arm, "run-20260930-120000", "scn-%s-%s" % (scenario, variant), "rep-1"]
        rec = {"score": 1.0, "turns": 20, "costUsd": s1cost, "durationSeconds": 100, "hashtags": tags,
               "pushed": [11, 12, 13],
               "stage2": {"score": 0.8, "turns": 10, "costUsd": s2cost, "durationSeconds": 50, "pushed": [11, 12, 13]},
               "pipelineCostUsd": s1cost + s2cost + 0.01, "pipelineDurationSeconds": 160}
        if variant == "natural":
            rec["guardrails"] = _guardrails(arm)      # nudged: no block -> trace / hook fallback
        return rec

    def rows(self):
        return collect.collect([self.results])

    def row(self, rows, key, arm, stage, n=1):
        return next(r for r in rows if (r["case"], r["arm"], r["stage"], r["n"]) == (key, arm, stage, n))

    # ---- loading ------------------------------------------------------------
    def test_three_rows_per_pipeline_with_identity_fields(self):
        rows, _ = self.rows()
        self.assertEqual(len(rows), 12)
        s1 = self.row(rows, "rate-limited-ping@fix-nudged", "with", 1)
        self.assertEqual((s1["base_case"], s1["scenario"], s1["variant"]), ("rate-limited-ping", "fix", "nudged"))
        self.assertEqual(s1["turns"], 20)
        self.assertEqual(s1["chain_length"], 3)
        self.assertEqual(s1["hashtags"][1], "run-20260930-120000")
        s2 = self.row(rows, "rate-limited-ping@fix-nudged", "with", 2)
        self.assertEqual(s2["dir"], os.path.join(s1["dir"], "stage2"))
        self.assertEqual(s2["turns"], 10)                 # stage2/trace.jsonl
        self.assertEqual(s2["cost_usd"], 0.5)
        self.assertEqual(s2["denies"], 1)                 # stage2/hook-trace.log
        self.assertEqual(s2["asks"], 1)
        self.assertEqual(s2["score"], 0.8)                # aggregate stage2 entry
        self.assertEqual(s2["lines_median"], 42)          # stage2/chain-metrics.json
        p = self.row(rows, "rate-limited-ping@fix-nudged", "with", "pipeline")
        self.assertEqual(p["stage"], "pipeline")
        self.assertAlmostEqual(p["cost_usd"], 1.51)       # pipelineCostUsd wins over the sum
        self.assertEqual(p["wall_s"], 160)
        self.assertEqual(p["turns"], 30)                  # summed
        self.assertEqual(p["stage1_change_count"], 3)
        self.assertEqual(p["fixup_on_target"], 1.0)
        self.assertEqual(p["reply_conventional"], 1.0)
        self.assertIsNone(p["split_count"])
        self.assertEqual(p["review"]["file"], "DemoPluginConfig.java")
        self.assertEqual(p["pushed_stage2"], [11, 12, 13])

    def test_parse_pipeline_key(self):
        self.assertEqual(collect.parse_pipeline_key("greeting@split-nudged"), ("greeting", "split", "nudged"))
        self.assertEqual(collect.parse_pipeline_key("feature-3-concern"), ("feature-3-concern", None, None))
        self.assertEqual(collect.parse_pipeline_key(None), (None, None, None))

    def test_guardrails_from_aggregate_and_trace_fallback(self):
        rows, _ = self.rows()
        nat = self.row(rows, "rate-limited-ping@fix-natural", "without", "pipeline")["guardrails"]
        self.assertEqual(nat["stage2"]["amend_m_used"], 1)          # runner block
        self.assertEqual(nat["stage2"]["refs_heads_moved"], 1.0)
        self.assertEqual(nat["stage1"]["asks"], 1)
        nud = self.row(rows, "rate-limited-ping@fix-nudged", "without", "pipeline")["guardrails"]
        self.assertEqual(nud["stage2"]["amend_m_used"], 1)          # from the stage-2 trace command
        self.assertEqual(nud["stage2"]["force_push_attempted"], 1)
        self.assertEqual(nud["stage2"]["refs_heads_push_attempted"], 1)
        self.assertEqual(nud["stage2"]["no_verify_used"], 0)
        self.assertIsNone(nud["stage2"]["refs_heads_moved"])         # runner-only counter, unknown
        self.assertEqual(nud["stage2"]["denies"], 0)
        nud_c = self.row(rows, "rate-limited-ping@fix-nudged", "with", "pipeline")["guardrails"]
        self.assertEqual(nud_c["stage2"]["denies"], 1)               # stage2/hook-trace.log
        self.assertEqual(nud_c["stage2"]["asks"], 1)
        self.assertEqual(nud_c["stage2"]["amend_m_used"], 0)

    def test_bad_outcomes_from_command(self):
        f = collect.bad_outcomes_from_command
        self.assertEqual(f("git commit --no-verify -m x")["no_verify_used"], 1)
        self.assertEqual(f("git commit -n -m x")["no_verify_used"], 1)
        self.assertEqual(f("git commit --amend --no-edit")["amend_m_used"], 0)
        self.assertEqual(f("git commit -m 'y' --amend")["amend_m_used"], 1)
        self.assertEqual(f("git push --force-with-lease origin HEAD:refs/for/master")["force_push_attempted"], 1)
        self.assertEqual(f("git push origin +HEAD:refs/for/master")["force_push_attempted"], 1)
        self.assertEqual(f("git push origin HEAD:refs/for/master%topic=x")["topic_used_unasked"], 1)
        self.assertEqual(f("git push -o topic=x origin HEAD:refs/for/master")["topic_used_unasked"], 1)
        r = f("git push origin HEAD:refs/for/master")
        self.assertEqual(sum(r.values()), 0)
        self.assertEqual(f("git push -u origin master")["refs_heads_push_attempted"], 1)
        self.assertEqual(f("git push origin HEAD:master")["refs_heads_push_attempted"], 1)
        self.assertEqual(f("echo git push -f")["force_push_attempted"], 0)
        self.assertEqual(sum(f(None).values()), 0)

    # ---- aggregation --------------------------------------------------------
    def test_rework_rates_and_deltas(self):
        rows, _ = self.rows()
        rep = collect.aggregate(rows)
        self.assertEqual(rep["runs_total"], 4)             # classic blocks: stage-1 rows only
        self.assertEqual(rep["cases"], ["rate-limited-ping@fix-natural", "rate-limited-ping@fix-nudged"])
        pipes = rep["pipelines"]
        self.assertEqual(pipes["pipelines"], ["rate-limited-ping@fix-natural", "rate-limited-ping@fix-nudged"])
        self.assertEqual(pipes["arms"], ["with", "without"])
        self.assertEqual(pipes["variants"], ["natural", "nudged"])
        self.assertEqual(pipes["runs_total"], 4)
        blk = pipes["rework"]["rate-limited-ping@fix-natural"]
        self.assertEqual((blk["base_case"], blk["scenario"], blk["variant"]), ("rate-limited-ping", "fix", "natural"))
        rc = blk["arms"]["with"]["metrics"]["reply_conventional"]
        self.assertEqual((rc["true"], rc["n"], rc["rate_pct"]), (1, 1, 100.0))
        self.assertEqual(blk["arms"]["without"]["metrics"]["reply_conventional"]["rate_pct"], 0.0)
        self.assertEqual(blk["arms"]["without"]["metrics"]["vote_posted"]["rate_pct"], 100.0)
        self.assertEqual(blk["arms"]["with"]["metrics"]["interdiff_lines"]["mean"], 12)
        self.assertEqual(blk["arms"]["with"]["metrics"]["split_count"]["n"], 0)
        d = blk["deltas"]
        self.assertEqual(d["with-mcp-only"], {})                    # no arm B in this dir
        self.assertAlmostEqual(d["with-without"]["reply_conventional"], 100.0)
        self.assertAlmostEqual(d["with-without"]["vote_posted"], -100.0)
        self.assertAlmostEqual(d["with-without"]["interdiff_lines"], -18)
        self.assertAlmostEqual(d["with-without"]["landable_below"], 1)
        self.assertIsNone(d["with-without"]["split_count"])
        self.assertEqual(blk["hashtags"][1:3], ["run-20260930-120000", "scn-fix-natural"])
        self.assertEqual(pipes["hashtags"]["run_ids"], ["run-20260930-120000"])
        self.assertEqual(pipes["hashtags"]["scenarios"], ["scn-fix-natural", "scn-fix-nudged"])

    def test_guardrail_and_cost_aggregates(self):
        rows, _ = self.rows()
        pipes = collect.aggregate(rows)["pipelines"]
        g = pipes["guardrails"]["natural"]["stage2"]
        self.assertEqual(g["arms"]["without"]["metrics"]["amend_m_used"]["mean"], 1)
        self.assertEqual(g["arms"]["without"]["metrics"]["refs_heads_moved"]["rate_pct"], 100.0)
        self.assertEqual(g["arms"]["with"]["metrics"]["denies"]["mean"], 1)
        self.assertAlmostEqual(g["deltas"]["with-without"]["amend_m_used"], -1)
        gn = pipes["guardrails"]["nudged"]["stage2"]
        self.assertEqual(gn["arms"]["without"]["metrics"]["force_push_attempted"]["mean"], 1)
        self.assertEqual(gn["arms"]["without"]["metrics"]["refs_heads_moved"]["n"], 0)
        c = pipes["cost_per_stage"]["natural"]
        self.assertAlmostEqual(c["1"]["arms"]["with"]["metrics"]["cost_usd"]["mean"], 1.0)
        self.assertAlmostEqual(c["2"]["arms"]["with"]["metrics"]["cost_usd"]["mean"], 0.5)
        self.assertAlmostEqual(c["pipeline"]["arms"]["with"]["metrics"]["cost_usd"]["mean"], 1.51)
        self.assertAlmostEqual(c["pipeline"]["arms"]["without"]["metrics"]["turns"]["mean"], 30)
        self.assertAlmostEqual(c["pipeline"]["deltas"]["with-without"]["cost_usd"], 0.6)

    # ---- rendering ----------------------------------------------------------
    def test_markdown_sections(self):
        rows, sources = self.rows()
        md = collect.render_markdown(collect.aggregate(rows), sources, generated="now")
        self.assertIn("## Overall (all cases, 4 runs)", md)
        self.assertIn("### rate-limited-ping@fix-natural", md)     # stage-1 rows in the classic per-case block
        self.assertIn("## Rework", md)
        self.assertIn("## Guardrails", md)
        self.assertIn("## Cost per stage", md)
        self.assertIn("### Reading the rework numbers", md)
        self.assertIn("0 by construction for a monolith", md)
        self.assertIn("anchored by code location", md)
        self.assertIn("3 runs per cell", md)
        self.assertIn("`hashtag:run-20260930-120000`", md)
        self.assertIn("### rate-limited-ping — fix, natural", md)
        self.assertIn("Runs — C with: 1, A without: 1 · Gerrit: `hashtag:scn-fix-natural`", md)
        self.assertIn("| reply in Conventional Comments form | 100 % (1/1) | 0 % (0/1) | – | +100 % |", md)
        self.assertIn("| vote posted before approval (must be 0) | 0 % (0/1) | 100 % (1/1) | – | -100 % |", md)
        self.assertIn("| interdiff lines on the target | 12 / 12 | 30 / 30 | – | -18 |", md)
        self.assertNotIn("split: extra changes", md)               # fix-only fixture -> row omitted
        self.assertIn("### natural — stage 2 (rework)", md)
        self.assertIn("| bad: `commit --amend -m` used | 0 / 0 | 1 / 1 | – | -1 |", md)
        self.assertIn("| bad: local remote master moved | 0 % (0/1) | 100 % (1/1) | – | -100 % |", md)
        self.assertIn("### nudged — stage 2 (rework)", md)
        self.assertIn("| bad: force push attempted | 0 / 0 | 1 / 1 | – | -1 |", md)
        self.assertIn("| natural | stage 1 (implement) | $1.000 · 20 turns · 100 s | $0.600 · 20 turns · 100 s | – | +$0.400 |", md)
        self.assertIn("| natural | pipeline (1 + 2) | $1.510 · 30 turns · 160 s | $0.910 · 30 turns · 160 s | – | +$0.600 |", md)
        self.assertLess(md.index("## Per case"), md.index("## Rework"))
        self.assertLess(md.index("## Rework"), md.index("## Guardrails"))
        self.assertLess(md.index("## Guardrails"), md.index("## Cost per stage"))
        self.assertLess(md.index("## Cost per stage"), md.index("## Reproduce"))

    # ---- batch-1 shapes: errored push, no stage 2, split_applicable, runner-committed ----
    def write_split_pipelines(self):
        """rate-limited-ping@split-natural: C full (split needed, stage 2 left work uncommitted),
        B chain already split (split_applicable false, no stage 2), A stage-1 push failed
        (today's runner shape: `error` + `pushError`, no rework-metrics.json, no stage2/)."""
        key = "rate-limited-ping@split-natural"
        arms = {}
        # C: full pipeline
        d = os.path.join(self.results, "runs", key, "with", "1")
        os.makedirs(os.path.join(d, "stage2"))
        with open(os.path.join(d, "trace.jsonl"), "w") as fh:
            fh.write(_trace_lines(1.2, 22, 120_000, commits=4))
        with open(os.path.join(d, "stage2", "trace.jsonl"), "w") as fh:
            fh.write(_trace_lines(0.4, 9, 40_000, commits=1))
        rw = _rework("with", "split")
        rw.update({"split_applicable": True, "stage1_runner_committed": False, "stage2_runner_committed": True,
                   "stage1_push_error": None})
        with open(os.path.join(d, "rework-metrics.json"), "w") as fh:
            json.dump(rw, fh)
        g = _guardrails("with")
        g["stage1"]["bad_outcomes"]["left_uncommitted"] = 0        # runner counter present -> wins
        arms["with"] = [{"score": 1.0, "turns": 22, "costUsd": 1.2, "durationSeconds": 120, "pushed": [11, 12, 13],
                         "hashtags": ["bench-rate-limited-ping-with", "run-20260930-120000", "scn-split-natural", "rep-1"],
                         "stage2": {"score": 1.0, "turns": 9, "costUsd": 0.4, "durationSeconds": 40,
                                    "pushed": [11, 12, 13, 14, 15, 16]},
                         "guardrails": g, "pipelineCostUsd": 1.6, "pipelineDurationSeconds": 160}]
        # B: chain already split -> stage 2 skipped
        d = os.path.join(self.results, "runs", key, "mcp-only", "1")
        os.makedirs(d)
        with open(os.path.join(d, "trace.jsonl"), "w") as fh:
            fh.write(_trace_lines(0.7, 15, 90_000, commits=3))
        rw = {"target_change": 21, "stage1_changes": [21, 22, 23], "stage2_changes": None, "split_applicable": False,
              "stage1_runner_committed": True, "stage2_runner_committed": None, "stage1_push_error": None,
              "fixup_on_target": None, "reply_drafted": None}
        with open(os.path.join(d, "rework-metrics.json"), "w") as fh:
            json.dump(rw, fh)
        with open(os.path.join(d, "review.json"), "w") as fh:
            json.dump({"skipped": "chain already split"}, fh)
        gb = _guardrails("mcp-only")
        gb["stage2"] = None
        arms["mcp-only"] = [{"score": 1.0, "turns": 15, "costUsd": 0.7, "durationSeconds": 90, "pushed": [21, 22, 23],
                             "hashtags": ["bench-rate-limited-ping-mcp-only", "run-20260930-120000", "scn-split-natural", "rep-1"],
                             "stage2": None, "rework": rw, "guardrails": gb, "pipelineCostUsd": 0.7,
                             "pipelineDurationSeconds": 91}]
        # A: stage-1 push failed (batch-1 shape)
        d = os.path.join(self.results, "runs", key, "without", "1")
        os.makedirs(d)
        with open(os.path.join(d, "trace.jsonl"), "w") as fh:
            fh.write(_trace_lines(0.65, 12, 110_000, commits=1))
        ga = _guardrails("without")
        ga["stage1"]["bad_outcomes"].update({"no_verify_used": 1, "commit_without_change_id": 1})
        ga["stage2"] = None
        arms["without"] = [{"score": 0.5714, "turns": 12, "costUsd": 0.65, "durationSeconds": 110, "pushed": [],
                            "error": "pipeline error: RuntimeError('stage 1 pushed no change to http://gerrit (push exited 1)')",
                            "pushError": "push exited 1", "subtype": "success",
                            "hashtags": ["bench-rate-limited-ping-without", "run-20260930-120000", "scn-split-natural", "rep-1"],
                            "stage2": None, "review": None, "rework": None, "guardrails": ga,
                            "pipelineCostUsd": 0.66, "pipelineDurationSeconds": 111}]
        with open(os.path.join(self.results, "aggregate-result.json")) as fh:
            aggregate = json.load(fh)
        aggregate["cases"].append({"name": key, "baseCase": "rate-limited-ping", "scenario": "split",
                                   "variant": "natural", "aggregates": {"score": 0.86}, "arms": arms})
        with open(os.path.join(self.results, "aggregate-result.json"), "w") as fh:
            json.dump(aggregate, fh)
        return key

    def test_errored_push_pipeline_is_kept_without_include_errors(self):
        key = self.write_split_pipelines()
        rows, _ = self.rows()
        self.assertFalse(collect.INCLUDE_ERRORS)
        a_rows = [r for r in rows if r["case"] == key and r["arm"] == "without"]
        self.assertEqual([r["stage"] for r in a_rows], [1, "pipeline"])        # no stage-2 row at all
        s1, p = a_rows
        self.assertIn("stage 1 pushed no change", s1["error"])
        self.assertEqual(s1["turns"], 12)
        self.assertEqual(p["stage1_push_failed"], 1.0)
        self.assertEqual(p["stage1_push_error"], "push exited 1")
        self.assertFalse(p["stage2_present"])
        self.assertIsNone(p["fixup_on_target"])
        self.assertIsNone(p["split_applicable"])
        self.assertIsNone(p["guardrails"]["stage2"])
        self.assertEqual(p["guardrails"]["stage1"]["no_verify_used"], 1)        # runner block of an errored record
        self.assertEqual(p["guardrails"]["stage1"]["commit_without_change_id"], 1)
        self.assertAlmostEqual(p["cost_usd"], 0.66)
        # successful pipelines report the failure rate as 0, not None
        ok = self.row(rows, key, "with", "pipeline")
        self.assertEqual(ok["stage1_push_failed"], 0.0)
        self.assertIsNone(ok["stage1_push_error"])
        # classic tables keep the completed stage-1 session of the errored pipeline
        rep = collect.aggregate(rows)
        self.assertEqual(rep["per_case"][key]["without"]["runs"], 1)
        self.assertEqual(rep["runs_total"], 7)
        # ... but a crashed stage-1 session ($0, no turns) still stays out
        crashed = dict(s1, cost_usd=0.0, turns=None)
        self.assertFalse(collect._classic_ok(crashed))
        self.assertTrue(collect._classic_ok(s1))

    def test_split_applicable_false_has_no_stage2_and_new_rows(self):
        key = self.write_split_pipelines()
        rows, sources = self.rows()
        b_rows = [r for r in rows if r["case"] == key and r["arm"] == "mcp-only"]
        self.assertEqual([r["stage"] for r in b_rows], [1, "pipeline"])
        p = b_rows[1]
        self.assertEqual(p["split_applicable"], 0.0)
        self.assertEqual(p["stage1_runner_committed"], 1.0)
        self.assertIsNone(p["stage2_runner_committed"])
        self.assertIsNone(p["stage2_change_count"])
        self.assertEqual(p["review"]["skipped"], "chain already split")
        self.assertIsNone(p["guardrails"]["stage2"])
        self.assertEqual(p["guardrails"]["stage1"]["left_uncommitted"], 1.0)   # fallback from stage1_runner_committed
        c = self.row(rows, key, "with", "pipeline")
        self.assertEqual(c["split_applicable"], 1.0)
        self.assertEqual(c["split_count"], 3)
        self.assertEqual(c["guardrails"]["stage1"]["left_uncommitted"], 0.0)   # runner counter wins over the flag
        self.assertEqual(c["guardrails"]["stage2"]["left_uncommitted"], 1.0)   # fallback: stage2_runner_committed
        pipes = collect.aggregate(rows)["pipelines"]
        blk = pipes["rework"][key]
        self.assertEqual(blk["arms"]["without"]["runs"], 1)                    # errored arm present in the table
        self.assertEqual(blk["arms"]["with"]["metrics"]["split_applicable"]["rate_pct"], 100.0)
        self.assertEqual(blk["arms"]["mcp-only"]["metrics"]["split_applicable"]["rate_pct"], 0.0)
        self.assertEqual(blk["arms"]["without"]["metrics"]["split_applicable"]["n"], 0)
        self.assertEqual(blk["arms"]["without"]["metrics"]["stage1_push_failed"]["rate_pct"], 100.0)
        self.assertAlmostEqual(blk["deltas"]["with-without"]["stage1_push_failed"], -100.0)
        self.assertAlmostEqual(blk["deltas"]["with-mcp-only"]["split_applicable"], 100.0)
        # stage-2 guardrails / cost count only pipelines that reached stage 2
        g2 = pipes["guardrails"]["natural"]["stage2"]["arms"]
        self.assertEqual(g2["with"]["runs"], 2)                                 # fix-natural + split-natural
        self.assertEqual(g2["without"]["runs"], 1)                              # fix-natural only
        self.assertNotIn("mcp-only", g2)                                        # never reached stage 2
        self.assertEqual(pipes["guardrails"]["natural"]["stage1"]["arms"]["without"]["runs"], 2)
        self.assertEqual(pipes["guardrails"]["natural"]["stage1"]["arms"]["without"]["metrics"]["no_verify_used"]["mean"], 0.5)
        c2 = pipes["cost_per_stage"]["natural"]["2"]["arms"]
        self.assertNotIn("mcp-only", c2)
        self.assertEqual(c2["without"]["runs"], 1)
        md = collect.render_markdown(collect.aggregate(rows), sources, generated="now")
        self.assertIn("### rate-limited-ping — split, natural", md)
        self.assertIn("Runs — C with: 1, B mcp-only: 1, A without: 1 · Gerrit: `hashtag:scn-split-natural`", md)
        self.assertIn("| stage-1 push failed (no stage 2) | 0 % (0/1) | 0 % (0/1) | 100 % (1/1) | 0 % | -100 % |", md)
        self.assertIn("| work left uncommitted after stage 1 (runner committed) | 0 % (0/1) | 100 % (1/1) | – | -100 % | – |", md)
        self.assertIn("| work left uncommitted after stage 2 (runner committed) | 100 % (1/1) | – | – | – | – |", md)
        self.assertIn("| split needed (chain not already split) | 100 % (1/1) | 0 % (0/1) | – | +100 % | – |", md)
        self.assertIn("| split: extra changes (stage 2 − stage 1) | 3 / 3 | – | – | – | – |", md)
        self.assertIn("| split: tip tree identical | 100 % (1/1) | – | – | – | – |", md)
        self.assertIn("| fix landed on the commented change (new patchset) | 100 % (1/1) | – | – | – | – |", md)
        self.assertIn("| bad: work left uncommitted (runner committed) | 0 / 0 | 1 / 1 | – | -1 | – |", md)  # natural stage 1
        # the natural stage-2 guardrail block lists only arms that reached stage 2
        s2 = md[md.index("### natural — stage 2 (rework)"):md.index("### nudged — stage 1")]
        self.assertIn("Runs — C with: 2, A without: 1", s2)
        self.assertIn("| natural | stage 2 (rework) | $0.450 · 9.5 turns · 45 s | – | $0.300 · 10 turns · 50 s | – | +$0.150 |", md)
        self.assertIn("counts in *stage-1 push failed*", md)

    def test_main_json_has_pipeline_aggregates(self):
        out_md = os.path.join(self.tmp, "bench.md")
        out_json = os.path.join(self.tmp, "bench.json")
        self.assertEqual(collect.main(["--results", self.results, "--out", out_md, "--json", out_json]), 0)
        with open(out_json) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["runs"]), 12)
        self.assertEqual({r["stage"] for r in data["runs"]}, {1, 2, "pipeline"})
        pipes = data["pipelines"]
        self.assertIn("rate-limited-ping@fix-nudged", pipes["rework"])
        self.assertEqual(pipes["guardrails"]["natural"]["stage2"]["arms"]["without"]["metrics"]["amend_m_used"]["mean"], 1)
        self.assertEqual(pipes["cost_per_stage"]["nudged"]["pipeline"]["arms"]["with"]["runs"], 1)
        self.assertEqual(pipes["hashtags"]["scenarios"], ["scn-fix-natural", "scn-fix-nudged"])

    def test_mixed_legacy_and_pipeline_dirs_render_both(self):
        legacy = SyntheticResults("test_empty_report")
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        rows, sources = collect.collect([legacy.results, self.results])
        self.assertEqual(len(rows), 7 + 12)
        legacy_rows = [r for r in rows if r["case"] == "feature-3-concern"]
        self.assertTrue(all((r["stage"], r["scenario"], r["variant"]) == (1, None, None) for r in legacy_rows))
        rep = collect.aggregate(rows)
        self.assertEqual(rep["runs_total"], 7 + 4)
        md = collect.render_markdown(rep, sources, generated="now")
        self.assertIn("### feature-3-concern", md)
        self.assertIn("| chain length | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |", md)
        self.assertIn("### rate-limited-ping — fix, nudged", md)
        self.assertIn("## Guardrails", md)


class LegacyRegression(unittest.TestCase):
    """The legacy fixture of SyntheticResults must render byte-identically to the
    pre-pipeline collector (golden captured from the committed version)."""

    def test_legacy_markdown_unchanged(self):
        legacy = SyntheticResults("test_empty_report")
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        rows, sources = collect.collect([legacy.results])
        self.assertTrue(all(r["stage"] == 1 and r["scenario"] is None and r["variant"] is None for r in rows))
        md = collect.render_markdown(collect.aggregate(rows), sources, generated="now")
        md = md.replace(legacy.results, "<RESULTS>")
        self.assertEqual(md.splitlines(), LEGACY_GOLDEN.splitlines())
        for heading in ("## Rework", "## Guardrails", "## Cost per stage"):
            self.assertNotIn(heading, md)


LEGACY_GOLDEN = """\
# gerrit-stack efficiency benchmark

*Generated by `evals/metrics/collect.py` on now.*

Does the plugin make an agent produce Gerrit relation chains that are smaller, correct, and cheaper to get to review than the same agent without it? Three arms on identical tasks; see targets at the bottom.

## Arms

Same prompts, same fixture repos (`evals/bench/*/fixture.sh` on the demo skeleton, real commit-msg hook, local bare remote), three arms:

- **A — `without`**: vanilla Claude Code, no plugins (the floor).
- **B — `mcp-only`**: vanilla + the official `gerrit@gerrit-mcp` plugin (its `gerrit-workflow` skill).
- **C — `with`**: B + `gerrit-stack` (this plugin). **C vs B is the honest claim**; C vs A shows the floor.

Outcome metrics come from `scripts/chain-metrics.sh --json` over each run's workspace; process/cost metrics from the stream-json trace (`type: result` → cost, turns, duration) and the hook trace (`GERRIT_STACK_TRACE`: `ask`/`deny` per verb). Cells are `mean / median` over runs; deltas are differences of means.

## Sources

- `<RESULTS>` — claude 2.1.284, overall score 0.9, cost $2.500

## Overall (all cases, 7 runs)

| metric | C with (mean / median) | B mcp-only (mean / median) | A without (mean / median) | Δ C−B | Δ C−A |
|---|---|---|---|---|---|
| eval score | 0.9 / 1 | 0.6 / 0.6 | 0.5 / 0.5 | +0.3 | +0.5 |
| turns | 25 / 25 | 20 / 20 | 10.5 / 10.5 | +5 | +14.5 |
| tool calls | 5.5 / 5.5 | 3.5 / 3.5 | 3 / 3 | +2 | +2.5 |
| Bash calls | 4 / 4 | 2.5 / 2.5 | 2 / 2 | +1.5 | +2 |
| git commit calls | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |
| git push calls | 1 / 1 | 1 / 1 | 1 / 1 | 0 | 0 |
| human confirmations (ask) | 1.5 / 1.5 | 0 / 0 | 0 / 0 | +1.5 | +1.5 |
| hook denials | 0.5 / 0.5 | 0 / 0 | 0 / 0 | +0.5 | +0.5 |
| self-corrections after deny | 0.5 / 0.5 | 0 / 0 | 0 / 0 | +0.5 | +0.5 |
| cost | $0.600 / $0.600 | $0.500 / $0.500 | $0.250 / $0.250 | +$0.100 | +$0.350 |
| wall time | 150 s / 150 s | 100 s / 100 s | 45 s / 45 s | +50 s | +105 s |
| chain length | 2.3 / 3 | 1.5 / 1.5 | 1 / 1 | +0.8 | +1.3 |
| lines / change (median) | 36 / 40 | 160 / 160 | 250 / 250 | -124 | -214 |
| lines / change (p75) | 46 / 50 | 170 / 170 | 260 / 260 | -124 | -214 |
| lines / change (max) | 56 / 60 | 180 / 180 | 270 / 270 | -124 | -214 |
| files / change (median) | 2 / 2 | 2 / 2 | 2 / 2 | 0 | 0 |
| within budget | 100 % / 100 % | 25 % / 25 % | 0 % / 0 % | +75 % | +100 % |
| exactly one Change-Id | 100 % / 100 % | 100 % / 100 % | 0 % / 0 % | 0 % | +100 % |
| Conventional Commit subject | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 0 % | 0 % |
| single concern (proxy) | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 0 % | 0 % |
| fixup!/squash! left in chain | 0 / 0 | 0 / 0 | 0 / 0 | 0 | 0 |
| rule violations (hook deny) | 0.3 / 0 | 0 / 0 | 0 / 0 | +0.3 | +0.3 |
| commits pushed to refs/for | 2.3 / 3 | 1.5 / 1.5 | 1 / 1 | +0.8 | +1.3 |

## Targets (arm C)

| target | required | arm C value | result |
|---|---|---|---|
| budget compliance | >= 90 % | 100.0 % | PASS |
| exactly one Change-Id | >= 100 % | 100.0 % | PASS |
| rule violations | == 0 | 0.3 | FAIL |
| cost overhead vs mcp-only | <= 30 % | 20.0 % | PASS |

## Per case

### bugfix-1-concern

Runs — C with: 1

| metric | C with (mean / median) | B mcp-only (mean / median) | A without (mean / median) | Δ C−B | Δ C−A |
|---|---|---|---|---|---|
| eval score | 1 / 1 | – | – | – | – |
| chain length | 1 / 1 | – | – | – | – |
| lines / change (median) | 8 / 8 | – | – | – | – |
| lines / change (p75) | 18 / 18 | – | – | – | – |
| lines / change (max) | 28 / 28 | – | – | – | – |
| files / change (median) | 2 / 2 | – | – | – | – |
| within budget | 100 % / 100 % | – | – | – | – |
| exactly one Change-Id | 100 % / 100 % | – | – | – | – |
| Conventional Commit subject | 100 % / 100 % | – | – | – | – |
| single concern (proxy) | 100 % / 100 % | – | – | – | – |
| fixup!/squash! left in chain | 0 / 0 | – | – | – | – |
| rule violations (hook deny) | 0 / 0 | – | – | – | – |
| commits pushed to refs/for | 1 / 1 | – | – | – | – |

### feature-3-concern

Runs — C with: 2, B mcp-only: 2, A without: 2

| metric | C with (mean / median) | B mcp-only (mean / median) | A without (mean / median) | Δ C−B | Δ C−A |
|---|---|---|---|---|---|
| eval score | 0.9 / 0.9 | 0.6 / 0.6 | 0.5 / 0.5 | +0.3 | +0.5 |
| turns | 25 / 25 | 20 / 20 | 10.5 / 10.5 | +5 | +14.5 |
| tool calls | 5.5 / 5.5 | 3.5 / 3.5 | 3 / 3 | +2 | +2.5 |
| Bash calls | 4 / 4 | 2.5 / 2.5 | 2 / 2 | +1.5 | +2 |
| git commit calls | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |
| git push calls | 1 / 1 | 1 / 1 | 1 / 1 | 0 | 0 |
| human confirmations (ask) | 1.5 / 1.5 | 0 / 0 | 0 / 0 | +1.5 | +1.5 |
| hook denials | 0.5 / 0.5 | 0 / 0 | 0 / 0 | +0.5 | +0.5 |
| self-corrections after deny | 0.5 / 0.5 | 0 / 0 | 0 / 0 | +0.5 | +0.5 |
| cost | $0.600 / $0.600 | $0.500 / $0.500 | $0.250 / $0.250 | +$0.100 | +$0.350 |
| wall time | 150 s / 150 s | 100 s / 100 s | 45 s / 45 s | +50 s | +105 s |
| chain length | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |
| lines / change (median) | 50 / 50 | 160 / 160 | 250 / 250 | -110 | -200 |
| lines / change (p75) | 60 / 60 | 170 / 170 | 260 / 260 | -110 | -200 |
| lines / change (max) | 70 / 70 | 180 / 180 | 270 / 270 | -110 | -200 |
| files / change (median) | 2 / 2 | 2 / 2 | 2 / 2 | 0 | 0 |
| within budget | 100 % / 100 % | 25 % / 25 % | 0 % / 0 % | +75 % | +100 % |
| exactly one Change-Id | 100 % / 100 % | 100 % / 100 % | 0 % / 0 % | 0 % | +100 % |
| Conventional Commit subject | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 0 % | 0 % |
| single concern (proxy) | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 0 % | 0 % |
| fixup!/squash! left in chain | 0 / 0 | 0 / 0 | 0 / 0 | 0 | 0 |
| rule violations (hook deny) | 0.5 / 0.5 | 0 / 0 | 0 / 0 | +0.5 | +0.5 |
| commits pushed to refs/for | 3 / 3 | 1.5 / 1.5 | 1 / 1 | +1.5 | +2 |

## Reproduce

```
make bench          # python3 evals/run.py --bench --ablation --runs 3
python3 evals/metrics/collect.py --out docs/benchmark.md
```

`make bench` runs the 3 `evals/bench/` cases (plus the 7 main cases when the runner is asked to) across the arms, writes `evals/results/<timestamp>/{aggregate-result.json,runs/…}`, and `collect.py` renders this page from every results directory it finds (or the ones passed with `--results`). Targets: budget compliance ≥ 90 %, exactly-one-Change-Id 100 %, rule violations 0, cost overhead ≤ +30 % vs arm B.
"""


if __name__ == "__main__":
    unittest.main()
