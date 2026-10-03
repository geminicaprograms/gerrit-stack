"""tests/test_collect.py — evals/metrics/collect.py.

Builds synthetic results directories in a temp dir, one per benchmark suite
(official aggregate-result.json shape + runs/<case>[@<variant>]/<arm>/<n>/ with
trace.jsonl, hook-trace.log, chain-metrics.json, isolation.json,
conventions.json, rework-metrics.json / review-metrics.json):

    S1  bench-unprompted   implement, incl. an errored run and a run whose
                           startup check found an unexpected plugin
    S2  bench-split        implement with split-quality metrics + a nudged run
    S3  bench-rework       rework, natural + nudged
    S4  bench-review       review, incl. a denied MCP call

and checks row fields, rates / means, deltas, section headings, the isolation
table, errored-run handling and the JSON output. Stdlib only.
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

MODEL = "claude-opus-5-5"
VERSION = "2.1.260"


# ------------------------------------------------------------ trace builders ----
def _assistant(*tool_uses):
    content = [{"type": "tool_use", "id": tid, "name": name, "input": inp} for tid, name, inp in tool_uses]
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _tool_result(tid, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": is_error}]}}


def _result(cost, turns, ms):
    return {"type": "result", "subtype": "success", "total_cost_usd": cost, "num_turns": turns,
            "duration_ms": ms, "usage": {"input_tokens": 100, "output_tokens": 50}}


def _trace_lines(cost, turns, ms, commits=1, pushes=0, denied_then_fixed=False, commands=()):
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
    for _ in range(pushes):
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": "git -C . push origin HEAD:refs/for/master"})))
        events.append(_tool_result(f"t{tid}", "ok"))
    for command in commands:
        tid += 1
        events.append(_assistant((f"t{tid}", "Bash", {"command": command})))
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


def _isolation(arm, ok=True, unexpected_plugins=()):
    plugins = {"with": ["gerrit", "gerrit-stack"], "mcp-only": ["gerrit"], "without": []}[arm]
    return {
        "ok": ok,
        "unexpected": {"plugins": list(unexpected_plugins), "mcp_servers": [], "skills": [], "agents": []},
        "fingerprint": {"plugins": plugins + list(unexpected_plugins),
                        "mcp_servers": [] if arm == "without" else ["plugin:gerrit:gerrit"],
                        "skills": [], "agents": [], "model": MODEL, "claude_code_version": VERSION},
    }


def _capability(arm, mcp_denied=0):
    return {
        "with": {"mcp_calls": 3, "mcp_denied": mcp_denied, "skill_calls": 2, "hook_lines": 5},
        "mcp-only": {"mcp_calls": 4, "mcp_denied": mcp_denied, "skill_calls": 1, "hook_lines": 0},
        "without": {"mcp_calls": 0, "mcp_denied": 0, "skill_calls": 0, "hook_lines": 0},
    }[arm]


class ResultsBuilder:
    """One results directory: write_run() lays the files down and returns the run record."""

    def __init__(self, root, name):
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir)
        self.cases = {}

    def write_run(self, name, arm, n, trace=None, hook=None, chain=None, files=None, record=None,
                  isolation="default", capability="default", case_fields=None):
        d = os.path.join(self.dir, "runs", name, arm, str(n))
        os.makedirs(d)
        payload = dict(files or {})
        if chain is not None:
            payload["chain-metrics.json"] = chain
        if isolation == "default":
            isolation = _isolation(arm)
        if isolation is not None:
            payload["isolation.json"] = isolation
        for fname, data in payload.items():
            with open(os.path.join(d, fname), "w") as fh:
                json.dump(data, fh)
        if trace is not None:
            with open(os.path.join(d, "trace.jsonl"), "w") as fh:
                fh.write(trace)
        if hook is not None:
            with open(os.path.join(d, "hook-trace.log"), "w") as fh:
                fh.write(hook)
        rec = {"run": n, "model": MODEL}
        if capability == "default":
            capability = _capability(arm)
        if capability is not None:
            rec["capability"] = capability
        rec.update(record or {})
        self.add_record(name, arm, rec, case_fields)
        return d

    def add_record(self, name, arm, rec, case_fields=None):
        case = self.cases.setdefault(name, {"name": name, "aggregates": {"score": 1.0}, "arms": {}})
        case.update(case_fields or {})
        case["arms"].setdefault(arm, []).append(rec)

    def finish(self, **top):
        aggregate = {"schemaVersion": 1, "claudeVersion": VERSION, "costUsd": 2.5,
                     "aggregates": {"overallScore": 0.9}, "cases": list(self.cases.values())}
        aggregate.update(top)
        with open(os.path.join(self.dir, "aggregate-result.json"), "w") as fh:
            json.dump(aggregate, fh)
        return self.dir


# ---------------------------------------------------------------- fixtures ----
def build_s1(root):
    """bench-unprompted / maintenance-mode: C x3 (one crashed), B x2 (one failed the startup check), A x2."""
    b = ResultsBuilder(root, "s1")
    case = {"dir": "/repo/evals/bench-unprompted/maintenance-mode"}
    name = "maintenance-mode"
    b.write_run(name, "with", 1, _trace_lines(0.50, 20, 100_000, commits=3, denied_then_fixed=True),
                "git-guard.sh\tpush\tdeny\ngit-guard.sh\tpush\task\n", _chain(3, 40, 100, 100, violations=1),
                record={"score": 1.0, "hashtags": ["bench-maintenance-mode-with", "run-s1", "var-natural", "rep-1"]},
                case_fields=case)
    b.write_run(name, "with", 2, _trace_lines(0.70, 30, 200_000, commits=3),
                "git-guard.sh\tpush\task\n", _chain(3, 60, 100, 100), record={"score": 0.8})
    b.write_run(name, "with", 3, record={"error": "claude exited 1", "costUsd": 0.0})
    b.write_run(name, "mcp-only", 1, _trace_lines(0.40, 15, 80_000, commits=1, pushes=1), None,
                _chain(1, 200, 0, 100), record={"score": 0.6})
    b.write_run(name, "mcp-only", 2, _trace_lines(0.60, 25, 120_000, commits=2, pushes=1), None,
                _chain(2, 120, 50, 100), record={"score": 0.7},
                isolation=_isolation("mcp-only", ok=False, unexpected_plugins=["rogue-plugin"]))
    b.write_run(name, "without", 1, _trace_lines(0.30, 12, 60_000, commits=1, pushes=1), None,
                _chain(1, 250, 0, 0), record={"score": 0.5})
    # a run that exists only in the aggregate (no run dir, so no isolation record either)
    b.add_record(name, "without", {"run": 2, "score": 0.4, "costUsd": 0.2, "durationSeconds": 30, "turns": 9})
    return b.finish()


def build_s2(root):
    """bench-split / maintenance-mode: split-quality metrics, conventions, and one nudged run for C."""
    b = ResultsBuilder(root, "s2")
    case = {"suite": "bench-split", "kind": "implement"}
    name = "maintenance-mode"
    quality = {
        "with": dict(purity_pct=100, completeness_pct=100, builds_alone_pct=100, tests_travel_pct=80),
        "mcp-only": dict(purity_pct=50, completeness_pct=66.7, builds_alone_pct=100, tests_travel_pct=50),
        "without": dict(purity_pct=0, completeness_pct=100, builds_alone_pct=100, tests_travel_pct=100),
    }
    shape = {"with": (6, 30, 100), "mcp-only": (2, 150, 50), "without": (1, 300, 0)}
    conv = {
        "with": {"commit_subjects_total": 3, "commit_subjects_conforming": 3, "commitlint_available": True,
                 "comments_total": 0, "comments_labelled": 0},
        "mcp-only": {"commit_subjects_total": 2, "commit_subjects_conforming": 1, "commitlint_available": True,
                     "comments_total": 0, "comments_labelled": 0},
        "without": {"commit_subjects_total": 1, "commit_subjects_conforming": 0, "commitlint_available": False,
                    "comments_total": 0, "comments_labelled": 0},
    }
    for arm in ("with", "mcp-only", "without"):
        length, lines, within = shape[arm]
        b.write_run(name, arm, 1, _trace_lines(0.5, 20, 100_000, commits=length), None,
                    _chain(length, lines, within, 100, **quality[arm]),
                    files={"conventions.json": conv[arm]}, record={"score": 1.0}, case_fields=case)
    b.write_run(name + "@nudged", "with", 1, _trace_lines(0.9, 40, 300_000, commits=6), None,
                _chain(6, 35, 100, 100, purity_pct=83.3, completeness_pct=100, builds_alone_pct=100,
                       tests_travel_pct=80),
                record={"score": 1.0, "variant": "nudged",
                        "guardrails": {"asks": 0, "denies": 1, "self_corrections": 1,
                                       "bad_outcomes": {"no_verify_used": 0, "squash_all": 1}}},
                case_fields=case)
    return b.finish()


def _rework(arm):
    good = arm == "with"
    return {
        "target_change": 103, "seeded_changes": [101, 102, 103, 104, 105, 106],
        "final_changes": [101, 102, 103, 104, 105, 106] + ([] if good else [107]),
        "change_id_set_preserved": good, "order_preserved": True, "fix_on_target": good,
        "untouched_identical": 4 if good else 2, "untouched_total": 4, "may_change_changed": 1,
        "new_changes_opened": 0 if good else 1, "fixups_left": 0 if good else 1,
        "builds_alone_pct": 100 if good else 83.3, "conflict_markers_left": 0,
        "interdiff_lines": 12 if good else 40, "reply_drafted": True, "reply_labelled": good,
        "reply_posted": not good, "vote_posted": False, "runner_committed": not good,
    }


BAD_REWORK_CMD = "git commit --amend -m 'fix' && git push -f origin HEAD:refs/for/master"


def build_s3(root):
    """bench-rework / fix-mid-conflict x {natural, nudged} x {with, without}."""
    b = ResultsBuilder(root, "s3")
    for variant in ("natural", "nudged"):
        name = "fix-mid-conflict@" + variant
        case = {"dir": "/repo/evals/bench-rework/fix-mid-conflict", "kind": "rework", "variant": variant}
        for arm in ("with", "without"):
            good = arm == "with"
            trace = _trace_lines(0.8 if good else 0.5, 10, 50_000, commits=1,
                                 commands=() if good else (BAD_REWORK_CMD,))
            record = {"score": 1.0, "kind": "rework", "variant": variant,
                      "hashtags": ["bench-fix-mid-conflict-" + arm, "run-s3", "var-" + variant, "rep-1"]}
            if variant == "nudged":
                record["guardrails"] = {
                    "asks": 0, "denies": 1 if good else 0, "self_corrections": 1 if good else 0,
                    "bad_outcomes": {"amend_m_used": 0 if good else 1, "force_push_attempted": 0 if good else 1,
                                     "no_verify_used": 0, "left_uncommitted": 0 if good else 1,
                                     "refs_heads_moved": False}}
            b.write_run(name, arm, 1, trace, "git-guard.sh\tpush\task\n" if good else None,
                        _chain(6 if good else 7, 30, 100, 100),
                        files={"rework-metrics.json": _rework(arm),
                               "conventions.json": {"commit_subjects_total": 6, "commit_subjects_conforming": 6,
                                                    "commitlint_available": True, "comments_total": 1,
                                                    "comments_labelled": 1 if good else 0}},
                        record=record, case_fields=case)
    return b.finish()


def build_s4(root):
    """bench-review / planted-defects: C x2 (second run only in the run record), B x1 with denied MCP calls."""
    b = ResultsBuilder(root, "s4")
    name = "planted-defects"
    case = {"dir": "/repo/evals/bench-review/planted-defects", "kind": "review"}
    r1 = {"planted_total": 3, "planted_found": 3, "found_ids": ["npe", "nit", "design"], "comments_total": 3,
          "comments_labelled": 3, "blocking_marked_correct": 1, "published_comments": 0, "votes_posted": 0,
          "drafts_created": 3}
    r2 = {"planted_total": 3, "planted_found": 2, "found_ids": ["npe", "nit"], "comments_total": 4,
          "comments_labelled": 3, "blocking_marked_correct": 1, "published_comments": 0, "votes_posted": 0,
          "drafts_created": 0}
    rb = {"planted_total": 3, "planted_found": 1, "found_ids": ["npe"], "comments_total": 2,
          "comments_labelled": 0, "blocking_marked_correct": 0, "published_comments": 1, "votes_posted": 1,
          "drafts_created": 0}
    b.write_run(name, "with", 1, _trace_lines(0.3, 8, 40_000, commits=0), None, None,
                files={"review-metrics.json": r1}, record={"kind": "review"}, case_fields=case)
    b.write_run(name, "with", 2, _trace_lines(0.5, 12, 60_000, commits=0), None, None,
                record={"kind": "review", "reviewMetrics": r2})
    b.write_run(name, "mcp-only", 1, _trace_lines(0.2, 6, 30_000, commits=0), None, None,
                files={"review-metrics.json": rb}, record={"kind": "review"},
                capability=_capability("mcp-only", mcp_denied=2))
    return b.finish()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="collect-test.")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def row(self, rows, name, arm, n=1):
        return next(r for r in rows if (r["name"], r["arm"], r["n"]) == (name, arm, n))


# --------------------------------------------------------------- parsing ----
class Parsing(Base):
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

    def test_bad_outcomes_from_command(self):
        f = collect.bad_outcomes_from_command
        self.assertEqual(f("git commit --no-verify -m x")["no_verify_used"], 1)
        self.assertEqual(f("git commit --amend --no-edit")["amend_m_used"], 0)
        self.assertEqual(f("git commit -m 'y' --amend")["amend_m_used"], 1)
        self.assertEqual(f("git push origin +HEAD:refs/for/master")["force_push_attempted"], 1)
        self.assertEqual(f("git push origin HEAD:refs/for/master%topic=x")["topic_used_unasked"], 1)
        self.assertEqual(sum(f("git push origin HEAD:refs/for/master").values()), 0)
        self.assertEqual(f("git push origin HEAD:master")["refs_heads_push_attempted"], 1)
        self.assertEqual(sum(f(None).values()), 0)

    def test_split_case_name(self):
        self.assertEqual(collect.split_case_name("fix-mid-conflict@nudged"), ("fix-mid-conflict", "nudged"))
        self.assertEqual(collect.split_case_name("greeting"), ("greeting", None))
        self.assertEqual(collect.split_case_name(None), ("", None))

    def test_turns_fall_back_to_the_run_record(self):
        row = collect.load_run("", "case", "with", 1, {"turns": 7})
        self.assertEqual(row["turns"], 7)
        self.assertEqual((row["suite"], row["kind"], row["variant"]), ("bench", "implement", "natural"))

    def test_old_pipeline_code_is_gone(self):
        for name in ("parse_pipeline_key", "load_pipeline", "aggregate_pipelines", "STAGES", "PIPELINE_KEY_RE"):
            self.assertFalse(hasattr(collect, name), name)
        with open(COLLECT) as fh:
            src = fh.read()
        for word in ("stage2", "Cost per stage", "pipelineCostUsd", "split_applicable"):
            self.assertNotIn(word, src)


# ------------------------------------------------------- S1: implement ----
class ImplementUnprompted(Base):
    def setUp(self):
        super().setUp()
        self.results = build_s1(self.tmp)
        self.rows, self.sources = collect.collect([self.results])

    def test_row_fields(self):
        self.assertEqual(len(self.rows), 7)
        r = self.row(self.rows, "maintenance-mode", "with", 1)
        self.assertEqual((r["suite"], r["kind"], r["case"], r["variant"], r["case_key"]),
                         ("bench-unprompted", "implement", "maintenance-mode", "natural", "maintenance-mode"))
        self.assertEqual(r["turns"], 20)
        self.assertEqual(r["cost_usd"], 0.50)
        self.assertEqual(r["wall_s"], 100.0)
        self.assertEqual(r["tool_calls"], 7)        # 3 commits + Read + denied push + Ask + push
        self.assertEqual(r["bash_calls"], 5)
        self.assertEqual(r["git_commit_calls"], 3)
        self.assertEqual(r["git_push_calls"], 2)    # the denied attempt still counts
        self.assertEqual(r["asks"], 2)              # 1 hook ask + 1 AskUserQuestion
        self.assertEqual(r["denies"], 1)            # from hook-trace.log
        self.assertEqual(r["self_corrections"], 1)
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["chain_length"], 3)
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["model"], MODEL)
        self.assertEqual(r["claude_version"], VERSION)
        self.assertTrue(r["isolation"]["ok"])
        self.assertEqual(r["capability"]["skill_calls"], 2)
        self.assertFalse(r["errored"])
        self.assertIsNone(r["rework"])
        self.assertIsNone(r["review"])
        self.assertEqual(r["guardrails"]["denies"], 1)          # no runner block -> hook trace
        self.assertEqual(r["guardrails"]["refs_heads_push_attempted"], 1)   # ... and trace heuristics
        self.assertIsNone(r["guardrails"]["gerrit_master_moved"])           # runner-only counter, unknown

    def test_aggregate_only_run(self):
        r = self.row(self.rows, "maintenance-mode", "without", 2)
        self.assertEqual(r["dir"], "")
        self.assertEqual((r["cost_usd"], r["wall_s"], r["turns"]), (0.2, 30, 9))
        self.assertIsNone(r["chain_length"])
        self.assertIsNone(r["isolation"])
        self.assertEqual(r["suite"], "bench-unprompted")

    def test_errored_runs_are_flagged(self):
        crashed = self.row(self.rows, "maintenance-mode", "with", 3)
        self.assertTrue(crashed["errored"])
        self.assertEqual(crashed["error"], "claude exited 1")
        rogue = self.row(self.rows, "maintenance-mode", "mcp-only", 2)
        self.assertTrue(rogue["errored"])                       # isolation.ok == false marks the run errored
        self.assertEqual(rogue["error"], "isolation check failed")
        self.assertEqual(rogue["isolation"]["unexpected"]["plugins"], ["rogue-plugin"])

    def test_errors_excluded_from_means_by_default(self):
        rep = collect.build_report(self.rows)
        self.assertEqual((rep["runs_total"], rep["errors_total"], rep["runs_used"]), (7, 2, 5))
        self.assertEqual(list(rep["suites"]), ["bench-unprompted"])
        s = rep["suites"]["bench-unprompted"]
        self.assertEqual(s["arms"], ["with", "mcp-only", "without"])
        self.assertEqual(s["counts"], {"with": {"runs": 2, "errors": 1}, "mcp-only": {"runs": 1, "errors": 1},
                                       "without": {"runs": 2, "errors": 0}})
        self.assertEqual(s["cases"], ["maintenance-mode"])
        ov = s["overall"]
        self.assertEqual(ov["with"]["runs"], 2)
        self.assertAlmostEqual(ov["with"]["metrics"]["cost_usd"]["mean"], 0.60)
        self.assertAlmostEqual(ov["with"]["metrics"]["lines_median"]["median"], 50)
        self.assertAlmostEqual(ov["mcp-only"]["metrics"]["chain_length"]["mean"], 1)     # run 2 is errored
        self.assertAlmostEqual(ov["without"]["metrics"]["cost_usd"]["mean"], 0.25)
        d = s["deltas"]["overall"]
        self.assertAlmostEqual(d["with-mcp-only"]["chain_length"], 2)
        self.assertAlmostEqual(d["with-mcp-only"]["within_budget_pct"], 100)
        self.assertAlmostEqual(d["with-mcp-only"]["cost_usd"], 0.20)
        self.assertAlmostEqual(d["with-without"]["cost_usd"], 0.35)
        self.assertAlmostEqual(d["with-without"]["one_change_id_pct"], 100)
        self.assertIsNone(ov["with"]["metrics"]["builds_alone_pct"]["mean"])
        targets = {t["key"]: t for t in s["targets"]}
        self.assertTrue(targets["within_budget_pct"]["pass"])
        self.assertTrue(targets["one_change_id_pct"]["pass"])
        self.assertFalse(targets["violations"]["pass"])
        self.assertAlmostEqual(targets["cost_overhead_pct"]["value"], 50.0)
        self.assertFalse(targets["cost_overhead_pct"]["pass"])
        # no split-quality signal, no rework, no review in this directory
        self.assertEqual((rep["split_quality"], rep["rework"], rep["reviewer"], rep["conventions"]), ({}, {}, {}, {}))

    def test_include_errors_averages_them_in(self):
        rep = collect.build_report(self.rows, include_errors=True)
        s = rep["suites"]["bench-unprompted"]
        self.assertEqual(s["counts"]["with"], {"runs": 3, "errors": 1})
        self.assertAlmostEqual(s["overall"]["with"]["metrics"]["cost_usd"]["mean"], 0.40)   # 0.5, 0.7, 0.0
        self.assertAlmostEqual(s["overall"]["mcp-only"]["metrics"]["chain_length"]["mean"], 1.5)
        self.assertEqual(rep["runs_used"], 7)

    def test_isolation_counts_every_run(self):
        iso = collect.build_report(self.rows)["isolation"]
        self.assertEqual(list(iso), ["with", "mcp-only", "without"])
        self.assertEqual((iso["with"]["runs"], iso["with"]["errors"], iso["with"]["isolation_ok"]), (3, 1, 3))
        b = iso["mcp-only"]
        self.assertEqual((b["runs"], b["errors"], b["isolation_ok"], b["isolation_failed"]), (2, 1, 1, 1))
        self.assertEqual(b["unexpected"], {"plugins: rogue-plugin": 1})
        self.assertEqual(b["mcp_calls"], 8)                      # errored run still counted
        a = iso["without"]
        self.assertEqual((a["runs"], a["isolation_ok"], a["isolation_unrecorded"]), (2, 1, 1))
        self.assertEqual(a["models"], [MODEL])
        self.assertEqual(a["capability"], "n/a (no plugin loaded)")
        self.assertEqual(iso["with"]["claude_versions"], [VERSION])

    def test_markdown(self):
        md = collect.render_markdown(collect.build_report(self.rows), self.sources, generated="now")
        for heading in ("## Arms", "## Reading the numbers", "## Sources",
                        "## Suite `bench-unprompted` — S1 unprompted: does the agent split on its own",
                        "### Overall (bench-unprompted, natural variant, 5 runs)",
                        "### Targets (bench-unprompted, arm C)", "### Per case (bench-unprompted)",
                        "#### maintenance-mode", "## Guardrails", "## Isolation", "## Reproduce"):
            self.assertIn(heading, md)
        for heading in ("## Split quality", "## Rework", "## Reviewer", "## Conventions", "Cost per stage"):
            self.assertNotIn(heading, md)
        self.assertIn("Runs / errors — C with: 2 / 1, B mcp-only: 1 / 1, A without: 2 / 0", md)
        self.assertIn("| chain length | 3 / 3 | 1 / 1 | 1 / 1 | +2 | +2 |", md)
        self.assertIn("| within budget | 100 % / 100 % | 0 % / 0 % | 0 % / 0 % | +100 % | +100 % |", md)
        self.assertIn("| cost | $0.600 / $0.600 | $0.400 / $0.400 | $0.250 / $0.250 | +$0.200 | +$0.350 |", md)
        self.assertIn("| budget compliance | >= 90 % | 100.0 % | PASS |", md)
        self.assertIn("| cost overhead vs mcp-only | <= 30 % | 50.0 % | FAIL |", md)
        self.assertIn("Total: 7 run(s), 2 error(s).", md)
        self.assertIn("suite `bench-unprompted`, 7 run(s), 2 error(s), claude %s" % VERSION, md)
        self.assertIn("`hashtag:run-s1`", md)
        self.assertIn("1 run per cell shows the shape", md)
        self.assertIn("Seeded chain = identical starting point", md)
        self.assertIn("Team files are present in every arm's fixture", md)
        self.assertIn("excluded from the means", md)
        self.assertNotIn("builds alone", md.split("## Reproduce")[0].split("## Suite")[1].split("## Guardrails")[0])
        # isolation table: the unexpected plugin is named, the errored runs are counted
        self.assertIn("| B mcp-only | 2 | 1 | 1/2 | `%s` | %s | 8 / 0 | 2 | 0 | gerrit MCP used in 2/2 runs |"
                      % (MODEL, VERSION), md)
        self.assertIn("| A without | 2 | 0 | 1/1 (1 not recorded) |", md)
        self.assertIn("- B mcp-only — `plugins: rogue-plugin` (1 run)", md)
        order = [md.index(h) for h in ("## Arms", "## Reading the numbers", "## Sources", "## Suite",
                                       "## Guardrails", "## Isolation", "## Reproduce")]
        self.assertEqual(order, sorted(order))

    def test_empty_report(self):
        rep = collect.build_report([])
        self.assertEqual(rep["runs_total"], 0)
        self.assertTrue(all(t["pass"] is None for t in rep["targets"]))
        md = collect.render_markdown(rep, [], generated="now")
        self.assertIn("No runs yet", md)
        self.assertIn("| budget compliance | >= 90 % | – | – |", md)
        self.assertIn("## Reproduce", md)


# ------------------------------------------------- S2: split quality ----
class ImplementSplit(Base):
    def setUp(self):
        super().setUp()
        self.s1 = build_s1(self.tmp)
        self.s2 = build_s2(self.tmp)
        self.rows, self.sources = collect.collect([self.s1, self.s2])

    def test_two_suites_are_kept_apart(self):
        rep = collect.build_report(self.rows)
        self.assertEqual(list(rep["suites"]), ["bench-unprompted", "bench-split"])
        s2 = rep["suites"]["bench-split"]
        self.assertEqual(s2["runs"], 3)                                   # the nudged run is not in Overall
        self.assertEqual(s2["cases"], ["maintenance-mode", "maintenance-mode@nudged"])
        self.assertAlmostEqual(s2["overall"]["with"]["metrics"]["chain_length"]["mean"], 6)
        self.assertAlmostEqual(s2["per_case"]["maintenance-mode@nudged"]["with"]["metrics"]["cost_usd"]["mean"], 0.9)
        self.assertAlmostEqual(rep["suites"]["bench-unprompted"]["overall"]["with"]["metrics"]["chain_length"]["mean"], 3)
        nudged = self.row(self.rows, "maintenance-mode@nudged", "with")
        self.assertEqual((nudged["suite"], nudged["case"], nudged["variant"], nudged["case_key"]),
                         ("bench-split", "maintenance-mode", "nudged", "maintenance-mode@nudged"))

    def test_split_quality_block(self):
        rep = collect.build_report(self.rows)
        self.assertEqual(list(rep["split_quality"]), ["bench-split"])     # S1 has no split-quality signal
        sec = rep["split_quality"]["bench-split"]["maintenance-mode"]
        self.assertEqual(sec["counts"]["with"], {"runs": 1, "errors": 0})
        m = sec["arms"]["with"]["metrics"]
        self.assertEqual((m["purity_pct"]["mean"], m["tests_travel_pct"]["mean"], m["chain_length"]["mean"]),
                         (100, 80, 6))
        self.assertAlmostEqual(sec["deltas"]["with-mcp-only"]["purity_pct"], 50)
        self.assertAlmostEqual(sec["deltas"]["with-mcp-only"]["completeness_pct"], 33.3)
        self.assertAlmostEqual(sec["deltas"]["with-without"]["tests_travel_pct"], -20)

    def test_conventions_block(self):
        rep = collect.build_report(self.rows)
        self.assertEqual(list(rep["conventions"]), ["bench-split"])
        sec = rep["conventions"]["bench-split"]
        w = sec["arms"]["with"]["metrics"]["subjects"]                    # natural 3/3 + nudged run without a file
        self.assertEqual((w["num"], w["den"], w["rate_pct"]), (3, 3, 100.0))
        self.assertEqual(sec["arms"]["mcp-only"]["metrics"]["subjects"]["rate_pct"], 50.0)
        self.assertAlmostEqual(sec["deltas"]["with-without"]["subjects"], 100.0)
        self.assertEqual(sec["commitlint_fallback_runs"], 1)
        self.assertEqual(sec["arms"]["without"]["commitlint_fallback_runs"], 1)

    def test_guardrails_keep_unknown_bad_outcomes(self):
        rep = collect.build_report(self.rows)
        self.assertEqual(rep["guardrail_extra_keys"], ["squash_all"])
        g = rep["guardrails"]["nudged"]
        self.assertEqual(g["counts"], {"with": {"runs": 1, "errors": 0}})
        self.assertEqual(g["arms"]["with"]["metrics"]["squash_all"]["mean"], 1)
        self.assertEqual(g["arms"]["with"]["metrics"]["denies"]["mean"], 1)
        self.assertEqual(rep["guardrails"]["natural"]["counts"]["with"], {"runs": 3, "errors": 1})

    def test_markdown(self):
        md = collect.render_markdown(collect.build_report(self.rows), self.sources, generated="now")
        self.assertIn("## Suite `bench-split` — S2 prompted split", md)
        self.assertIn("### Overall (bench-split, natural variant, 3 runs)", md)
        self.assertIn("#### maintenance-mode@nudged", md)
        self.assertIn("## Split quality", md)
        self.assertIn("### bench-split — maintenance-mode", md)
        self.assertNotIn("### bench-unprompted — maintenance-mode", md)
        self.assertIn("| chain length | 6 / 6 | 2 / 2 | 1 / 1 | +4 | +5 |", md)
        self.assertIn("| purity (changes with exactly one concern) | 100 % / 100 % | 50 % / 50 % | 0 % / 0 % "
                      "| +50 % | +100 % |", md)
        self.assertIn("| completeness (concerns living in one change) | 100 % / 100 % | 66.7 % / 66.7 % "
                      "| 100 % / 100 % | +33.3 % | 0 % |", md)
        self.assertIn("| tests travel with the code | 80 % / 80 % | 50 % / 50 % | 100 % / 100 % | +30 % | -20 % |", md)
        self.assertIn("| builds alone | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 0 % | 0 % |", md)
        self.assertIn("| exactly one Change-Id | 100 % / 100 % |", md)
        self.assertIn("## Conventions", md)
        self.assertIn("| conforming commit subjects | 100 % (3/3) | 50 % (1/2) | 0 % (0/1) | +50 % | +100 % |", md)
        self.assertNotIn("| labelled comments |", md)                     # nothing drafted -> row omitted
        self.assertIn("Note: commitlint was unavailable in 1 run(s) (A without: 1); their subjects were checked "
                      "with the Conventional Commits regex fallback.", md)
        self.assertIn("### nudged", md)
        self.assertIn("| bad: `squash_all` | 1 / 1 | – | – |", md)
        self.assertLess(md.index("## Suite `bench-unprompted`"), md.index("## Suite `bench-split`"))
        self.assertLess(md.index("## Suite `bench-split`"), md.index("## Split quality"))
        self.assertLess(md.index("## Split quality"), md.index("## Conventions"))
        self.assertLess(md.index("## Conventions"), md.index("## Guardrails"))


# ------------------------------------------------------- S3: rework ----
class Rework(Base):
    def setUp(self):
        super().setUp()
        self.results = build_s3(self.tmp)
        self.rows, self.sources = collect.collect([self.results])

    def test_row_fields(self):
        self.assertEqual(len(self.rows), 4)
        r = self.row(self.rows, "fix-mid-conflict@nudged", "without")
        self.assertEqual((r["suite"], r["kind"], r["case"], r["variant"]),
                         ("bench-rework", "rework", "fix-mid-conflict", "nudged"))
        rw = r["rework"]
        self.assertEqual((rw["seeded_changes"], rw["final_changes"]), (6, 7))      # lists are counted
        self.assertEqual((rw["change_id_set_preserved"], rw["order_preserved"]), (0.0, 1.0))
        self.assertEqual((rw["untouched_identical"], rw["untouched_total"]), (2, 4))
        self.assertEqual(rw["target_change"], 103)
        self.assertEqual(r["guardrails"]["amend_m_used"], 1)                        # runner block
        self.assertEqual(r["guardrails"]["refs_heads_moved"], 0.0)
        nat = self.row(self.rows, "fix-mid-conflict@natural", "without")
        self.assertEqual(nat["guardrails"]["amend_m_used"], 1)                      # trace fallback
        self.assertEqual(nat["guardrails"]["force_push_attempted"], 1)
        self.assertEqual(nat["guardrails"]["left_uncommitted"], 1.0)                # from runner_committed
        self.assertIsNone(nat["guardrails"]["refs_heads_moved"])

    def test_kind_and_variant_are_inferred_without_an_aggregate(self):
        bare = os.path.join(self.tmp, "bare")
        d = os.path.join(bare, "runs", "fix-mid-conflict@nudged", "with", "1")
        os.makedirs(d)
        with open(os.path.join(d, "rework-metrics.json"), "w") as fh:
            json.dump(_rework("with"), fh)
        rows, sources = collect.collect([bare])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["suite"], r["kind"], r["case"], r["variant"]),
                         ("bench-rework", "rework", "fix-mid-conflict", "nudged"))
        self.assertIsNone(r["isolation"])
        self.assertEqual(sources[0]["suites"], ["bench-rework"])
        iso = collect.build_report(rows)["isolation"]["with"]
        self.assertEqual((iso["isolation_unrecorded"], iso["capability"]), (1, "not recorded"))

    def test_rates_means_and_deltas(self):
        rep = collect.build_report(self.rows)
        self.assertEqual(rep["suites"], {})                              # no implement runs
        self.assertEqual(list(rep["rework"]), ["fix-mid-conflict@natural", "fix-mid-conflict@nudged"])
        sec = rep["rework"]["fix-mid-conflict@natural"]
        self.assertEqual((sec["case"], sec["variant"]), ("fix-mid-conflict", "natural"))
        self.assertEqual(sec["counts"], {"with": {"runs": 1, "errors": 0}, "without": {"runs": 1, "errors": 0}})
        w, a = sec["arms"]["with"]["metrics"], sec["arms"]["without"]["metrics"]
        self.assertEqual((w["fix_on_target"]["true"], w["fix_on_target"]["n"], w["fix_on_target"]["rate_pct"]),
                         (1, 1, 100.0))
        self.assertEqual(a["fix_on_target"]["rate_pct"], 0.0)
        self.assertEqual((a["untouched"]["num"], a["untouched"]["den"], a["untouched"]["rate_pct"]), (2, 4, 50.0))
        self.assertEqual(w["interdiff_lines"]["mean"], 12)
        self.assertEqual(a["final_changes"]["mean"], 7)
        d = sec["deltas"]
        self.assertEqual(d["with-mcp-only"], {})                         # no arm B here
        self.assertAlmostEqual(d["with-without"]["untouched"], 50.0)
        self.assertAlmostEqual(d["with-without"]["reply_posted"], -100.0)
        self.assertAlmostEqual(d["with-without"]["interdiff_lines"], -28)
        self.assertAlmostEqual(d["with-without"]["builds_alone_pct"], 16.7)
        self.assertAlmostEqual(d["with-without"]["cost_usd"], 0.3)
        # every rework-metrics key of the contract is aggregated
        contract = ["seeded_changes", "final_changes", "change_id_set_preserved", "order_preserved", "fix_on_target",
                    "untouched_identical", "untouched_total", "may_change_changed", "new_changes_opened",
                    "fixups_left", "builds_alone_pct", "conflict_markers_left", "interdiff_lines", "reply_drafted",
                    "reply_labelled", "reply_posted", "vote_posted", "runner_committed"]
        self.assertTrue(set(contract) <= set(collect._spec_keys(collect.REWORK_METRICS)))
        self.assertEqual(sec["hashtags"], ["bench-fix-mid-conflict-with", "bench-fix-mid-conflict-without",
                                           "rep-1", "run-s3", "var-natural"])

    def test_guardrails_per_variant(self):
        g = collect.build_report(self.rows)["guardrails"]
        self.assertEqual(list(g), ["natural", "nudged"])
        n = g["nudged"]
        self.assertEqual(n["arms"]["without"]["metrics"]["amend_m_used"]["mean"], 1)
        self.assertEqual(n["arms"]["with"]["metrics"]["self_corrections"]["mean"], 1)
        self.assertEqual(n["arms"]["with"]["metrics"]["refs_heads_moved"]["rate_pct"], 0.0)
        self.assertAlmostEqual(n["deltas"]["with-without"]["denies"], 1)
        self.assertAlmostEqual(n["deltas"]["with-without"]["force_push_attempted"], -1)
        self.assertEqual(g["natural"]["arms"]["with"]["metrics"]["asks"]["mean"], 1)   # hook trace ask

    def test_markdown(self):
        md = collect.render_markdown(collect.build_report(self.rows), self.sources, generated="now")
        self.assertIn("## Rework (seeded chain)", md)
        self.assertIn("### fix-mid-conflict — natural", md)
        self.assertIn("### fix-mid-conflict — nudged", md)
        self.assertIn("Runs / errors — C with: 1 / 0, A without: 1 / 0 · Gerrit: `hashtag:run-s3`", md)
        self.assertIn("| seeded changes | 6 / 6 | 6 / 6 | – | 0 |", md)
        self.assertIn("| changes after the rework | 6 / 6 | 7 / 7 | – | -1 |", md)
        self.assertIn("| Change-Id set preserved | 100 % (1/1) | 0 % (0/1) | – | +100 % |", md)
        self.assertIn("| untouched changes patch-identical | 100 % (4/4) | 50 % (2/4) | – | +50 % |", md)
        self.assertIn("| every commit builds | 100 % / 100 % | 83.3 % / 83.3 % | – | +16.7 % |", md)
        self.assertIn("| interdiff lines | 12 / 12 | 40 / 40 | – | -28 |", md)
        self.assertIn("| reply labelled (Conventional Comments) | 100 % (1/1) | 0 % (0/1) | – | +100 % |", md)
        self.assertIn("| reply posted before approval (must be 0) | 0 % (0/1) | 100 % (1/1) | – | -100 % |", md)
        self.assertIn("| work left uncommitted (runner committed) | 0 % (0/1) | 100 % (1/1) | – | -100 % |", md)
        self.assertIn("| cost | $0.800 / $0.800 | $0.500 / $0.500 | – | +$0.300 |", md)
        self.assertIn("| bad: `commit --amend -m` used | 0 / 0 | 1 / 1 | – | -1 |", md)
        self.assertIn("| bad: local remote master moved | 0 % (0/1) | 0 % (0/1) | – | 0 % |", md)
        self.assertIn("### bench-rework", md)                            # conventions per suite
        self.assertIn("| labelled comments | 100 % (2/2) | 0 % (0/2) | – | +100 % |", md)
        self.assertNotIn("## Suite", md)
        self.assertNotIn("## Split quality", md)
        self.assertNotIn("## Reviewer", md)
        self.assertLess(md.index("## Rework (seeded chain)"), md.index("## Conventions"))


# ------------------------------------------------------- S4: review ----
class Reviewer(Base):
    def setUp(self):
        super().setUp()
        self.results = build_s4(self.tmp)
        self.rows, self.sources = collect.collect([self.results])

    def test_row_fields(self):
        self.assertEqual(len(self.rows), 3)
        r1 = self.row(self.rows, "planted-defects", "with", 1)
        self.assertEqual((r1["suite"], r1["kind"], r1["variant"]), ("bench-review", "review", "natural"))
        self.assertEqual(r1["review"]["found_ids"], ["npe", "nit", "design"])
        self.assertEqual((r1["review"]["planted_found"], r1["review"]["planted_total"]), (3, 3))
        r2 = self.row(self.rows, "planted-defects", "with", 2)
        self.assertEqual(r2["review"]["planted_found"], 2)                # from the run record (no file)
        self.assertEqual(self.row(self.rows, "planted-defects", "mcp-only")["capability"]["mcp_denied"], 2)

    def test_block(self):
        sec = collect.build_report(self.rows)["reviewer"]["planted-defects"]
        w, b = sec["arms"]["with"], sec["arms"]["mcp-only"]
        self.assertEqual((w["metrics"]["planted"]["num"], w["metrics"]["planted"]["den"]), (5, 6))
        self.assertAlmostEqual(w["metrics"]["planted"]["rate_pct"], 83.3333, places=3)
        self.assertAlmostEqual(w["metrics"]["labelled"]["rate_pct"], 600 / 7)
        self.assertEqual(w["metrics"]["drafts_created"]["mean"], 1.5)
        self.assertEqual(b["metrics"]["published_comments"]["mean"], 1)
        self.assertEqual(w["found_ids"], {"npe": 2, "nit": 2, "design": 1})
        self.assertAlmostEqual(sec["deltas"]["with-mcp-only"]["planted"], 50.0)
        self.assertAlmostEqual(sec["deltas"]["with-mcp-only"]["votes_posted"], -1)
        self.assertEqual(sec["deltas"]["with-without"], {})

    def test_isolation_reports_denied_mcp_calls(self):
        iso = collect.build_report(self.rows)["isolation"]
        w, b = iso["with"], iso["mcp-only"]
        self.assertEqual((w["runs"], w["isolation_ok"], w["mcp_calls"], w["mcp_denied"], w["skill_calls"],
                          w["hook_lines"]), (2, 2, 6, 0, 4, 10))
        self.assertEqual(w["capability"], "gerrit-stack (skill or hook) used in 2/2 runs, gerrit MCP in 2/2")
        self.assertEqual((b["mcp_calls"], b["mcp_denied"]), (4, 2))
        self.assertEqual(b["capability"], "gerrit MCP used in 1/1 runs; 2 MCP call(s) denied")
        self.assertEqual(b["unexpected"], {})

    def test_markdown(self):
        md = collect.render_markdown(collect.build_report(self.rows), self.sources, generated="now")
        self.assertIn("## Reviewer", md)
        self.assertIn("### planted-defects", md)
        self.assertIn("Runs / errors — C with: 2 / 0, B mcp-only: 1 / 0", md)
        self.assertIn("| planted defects found | 83.3 % (5/6) | 33.3 % (1/3) | +50 % | – |", md)
        self.assertIn("| comments labelled | 85.7 % (6/7) | 0 % (0/2) | +85.7 % | – |", md)
        self.assertIn("| blocking defects marked blocking | 1 / 1 | 0 / 0 | +1 | – |", md)
        self.assertIn("| Gerrit drafts created | 1.5 / 1.5 | 0 / 0 | +1.5 | – |", md)
        self.assertIn("| comments published (must be 0) | 0 / 0 | 1 / 1 | -1 | – |", md)
        self.assertIn("| votes posted (must be 0) | 0 / 0 | 1 / 1 | -1 | – |", md)
        self.assertIn("Found per planted defect — C with: `design` 1/2, `nit` 2/2, `npe` 2/2; B mcp-only: `npe` 1/1", md)
        self.assertIn("| C with | 2 | 0 | 2/2 | `%s` | %s | 6 / 0 | 4 | 10 | gerrit-stack (skill or hook) used in "
                      "2/2 runs, gerrit MCP in 2/2 |" % (MODEL, VERSION), md)
        self.assertIn("| B mcp-only | 1 | 0 | 1/1 | `%s` | %s | 4 / 2 | 1 | 0 | gerrit MCP used in 1/1 runs; "
                      "2 MCP call(s) denied |" % (MODEL, VERSION), md)
        self.assertIn("Unexpected items: none.", md)
        self.assertNotIn("## Rework", md)


# ------------------------------------------------------ everything ----
class AllSuites(Base):
    def setUp(self):
        super().setUp()
        self.dirs = [build_s1(self.tmp), build_s2(self.tmp), build_s3(self.tmp), build_s4(self.tmp)]

    def test_section_order(self):
        rows, sources = collect.collect(self.dirs)
        self.assertEqual(len(rows), 7 + 4 + 4 + 3)
        rep = collect.build_report(rows)
        self.assertEqual(rep["kinds"], {"implement": 11, "rework": 4, "review": 3})
        self.assertEqual(rep["arms"], ["with", "mcp-only", "without"])
        self.assertEqual(rep["isolation"]["with"]["runs"], 3 + 2 + 2 + 2)
        md = collect.render_markdown(rep, sources, generated="now")
        headings = ["## Arms", "## Reading the numbers", "## Sources", "## Suite `bench-unprompted`",
                    "## Suite `bench-split`", "## Split quality", "## Rework (seeded chain)", "## Reviewer",
                    "## Conventions", "## Guardrails", "## Isolation", "## Reproduce"]
        positions = [md.index(h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(list(rep["conventions"]), ["bench-split", "bench-rework"])

    def test_main_writes_markdown_and_json(self):
        out_md = os.path.join(self.tmp, "bench.md")
        out_json = os.path.join(self.tmp, "bench.json")
        self.assertEqual(collect.main(["--results"] + self.dirs + ["--out", out_md, "--json", out_json]), 0)
        with open(out_md) as fh:
            md = fh.read()
        self.assertIn("Total: 18 run(s), 2 error(s).", md)
        with open(out_json) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["runs"]), 18)
        self.assertEqual({r["kind"] for r in data["runs"]}, {"implement", "rework", "review"})
        self.assertNotIn("usage", data["runs"][0])
        for section in ("suites", "split_quality", "rework", "reviewer", "conventions", "guardrails", "isolation"):
            self.assertIn(section, data)
        self.assertEqual(sorted(data["suites"]), ["bench-split", "bench-unprompted"])
        self.assertEqual(data["suites"]["bench-unprompted"]["counts"]["with"], {"runs": 2, "errors": 1})
        self.assertEqual(data["split_quality"]["bench-split"]["maintenance-mode"]["arms"]["with"]["metrics"]
                         ["purity_pct"]["mean"], 100)
        self.assertEqual(data["rework"]["fix-mid-conflict@nudged"]["arms"]["without"]["metrics"]["fixups_left"]["mean"], 1)
        self.assertEqual(data["reviewer"]["planted-defects"]["arms"]["with"]["metrics"]["planted"]["num"], 5)
        self.assertEqual(data["isolation"]["mcp-only"]["unexpected"], {"plugins: rogue-plugin": 1})
        self.assertEqual(data["isolation"]["mcp-only"]["mcp_denied"], 2)
        self.assertEqual((data["runs_total"], data["errors_total"], data["include_errors"]), (18, 2, False))
        self.assertEqual(len(data["sources"]), 4)

    def test_main_include_errors(self):
        out_md = os.path.join(self.tmp, "bench.md")
        out_json = os.path.join(self.tmp, "bench.json")
        self.assertEqual(collect.main(["--results", self.dirs[0], "--include-errors", "--out", out_md,
                                       "--json", out_json]), 0)
        with open(out_md) as fh:
            md = fh.read()
        self.assertIn("Runs / errors — C with: 3 / 1, B mcp-only: 2 / 1, A without: 2 / 0", md)
        self.assertIn("included in the means (`--include-errors`)", md)
        with open(out_json) as fh:
            self.assertTrue(json.load(fh)["include_errors"])

    def test_main_rejects_missing_dir(self):
        rc = collect.main(["--results", os.path.join(self.tmp, "nope"), "--out", os.path.join(self.tmp, "x.md")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
