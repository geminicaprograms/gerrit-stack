#!/usr/bin/env python3
"""collect.py — merge benchmark results into docs/benchmark.md.

Usage:
    python3 evals/metrics/collect.py [--results DIR ...] [--include-errors]
                                     [--out docs/benchmark.md] [--json PATH]

Reads any number of result directories (default: every ``evals/results/*``
that holds an ``aggregate-result.json`` or a ``runs/`` tree), each laid out as

    <results>/aggregate-result.json                    official eval schema
    <results>/runs/<case>[@<variant>]/<arm>/<n>/       one run:
        trace.jsonl            claude stream-json
        hook-trace.log         GERRIT_STACK_TRACE
        chain-metrics.json     chain-metrics.sh --json
        isolation.json         sandbox startup check + fingerprint
        conventions.json       commit subjects / comment labels
        rework-metrics.json    kind rework
        review-metrics.json    kind review

One run is one row: ``suite`` (basename of the eval dir, e.g.
``bench-unprompted``), ``kind`` (implement | rework | review), ``case``,
``variant`` (natural | nudged), ``arm``, ``n``, the process metrics from the
trace, the chain metrics, and the per-run JSON files (the run record of the
aggregate fills in whatever file is missing: ``isolation``, ``capability``,
``conventions``, ``rework``, ``reviewMetrics``, ``guardrails``, ``model``).

Sections of the report: Arms, Reading the numbers, Sources, then per implement
suite Overall / Targets / Per case, ``Split quality``, ``Rework (seeded
chain)``, ``Reviewer``, ``Conventions``, ``Guardrails``, ``Isolation``,
``Reproduce``. A section is rendered only when a run feeds it. ``--json``
mirrors the sections.

Errored runs (run record ``error``, or ``isolation.ok == false``) stay out of
the metric means unless ``--include-errors`` is given, but are always counted
in Isolation and in the "Runs / errors" line of every section.

Arms: ``with`` (gerrit-mcp + gerrit-stack + team config), ``mcp-only``
(gerrit-mcp only), ``without`` (no plugins). Stdlib only; tolerant of missing
fields.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import statistics
import sys

ARMS = ["with", "mcp-only", "without"]
ARM_SHORT = {"with": "C with", "mcp-only": "B mcp-only", "without": "A without"}
KINDS = ["implement", "rework", "review"]
SUITE_ORDER = ["bench-unprompted", "bench-split", "bench-rework", "bench-review"]
SUITE_LABEL = {
    "bench-unprompted": "S1 unprompted: does the agent split on its own",
    "bench-split": "S2 prompted split: the prompt asks for one concern per change",
    "bench-rework": "S3 rework on a seeded chain",
    "bench-review": "S4 reviewer",
}
KIND_SUITE = {"rework": "bench-rework", "review": "bench-review", "implement": "bench"}

# (key, label, unit, lower-is-better?) — order = table order
PROCESS_METRICS = [
    ("score", "eval score", "", False),
    ("turns", "turns", "", True),
    ("tool_calls", "tool calls", "", True),
    ("bash_calls", "Bash calls", "", True),
    ("git_commit_calls", "git commit calls", "", True),
    ("git_push_calls", "git push calls", "", True),
    ("asks", "human confirmations (ask)", "", True),
    ("denies", "hook denials", "", True),
    ("self_corrections", "self-corrections after deny", "", False),
    ("cost_usd", "cost", "USD", True),
    ("wall_s", "wall time", "s", True),
]
CHAIN_METRICS = [
    ("chain_length", "chain length", "", None),
    ("lines_median", "lines / change (median)", "", True),
    ("lines_p75", "lines / change (p75)", "", True),
    ("lines_max", "lines / change (max)", "", True),
    ("files_median", "files / change (median)", "", True),
    ("within_budget_pct", "within budget", "%", False),
    ("one_change_id_pct", "exactly one Change-Id", "%", False),
    ("conventional_pct", "Conventional Commit subject", "%", False),
    ("single_concern_pct", "single concern (proxy)", "%", False),
    ("builds_alone_pct", "builds alone", "%", False),
    ("fixups_present", "fixup!/squash! left in chain", "", True),
    ("violations", "rule violations (hook deny)", "", True),
    ("refs_for_pushed", "commits pushed to refs/for", "", None),
]
ALL_METRICS = PROCESS_METRICS + CHAIN_METRICS
METRIC_KEYS = [m[0] for m in ALL_METRICS]
# chain-metrics keys read into a row beyond the classic table
EXTRA_CHAIN_KEYS = ["purity_pct", "completeness_pct", "tests_travel_pct"]

# Section specs: (key, label, kind[, (numerator key, denominator key)]).
# kind: bool -> rate over runs · count -> mean / median · pct / usd / s -> mean /
# median with a unit · ratio -> pooled numerator / denominator.
SPLIT_METRICS = [
    ("chain_length", "chain length", "count"),
    ("purity_pct", "purity (changes with exactly one concern)", "pct"),
    ("completeness_pct", "completeness (concerns living in one change)", "pct"),
    ("builds_alone_pct", "builds alone", "pct"),
    ("tests_travel_pct", "tests travel with the code", "pct"),
    ("within_budget_pct", "within budget", "pct"),
    ("one_change_id_pct", "exactly one Change-Id", "pct"),
]
SPLIT_SIGNAL_KEYS = ["purity_pct", "completeness_pct", "builds_alone_pct", "tests_travel_pct"]
SESSION_METRICS = [
    ("cost_usd", "cost", "usd"),
    ("turns", "turns", "count"),
    ("wall_s", "wall time", "s"),
]
REWORK_METRICS = [
    ("seeded_changes", "seeded changes", "count"),
    ("final_changes", "changes after the rework", "count"),
    ("change_id_set_preserved", "Change-Id set preserved", "bool"),
    ("order_preserved", "chain order preserved", "bool"),
    ("fix_on_target", "fix landed on the commented change", "bool"),
    ("untouched", "untouched changes patch-identical", "ratio", ("untouched_identical", "untouched_total")),
    ("may_change_changed", "changes touched by conflict resolution (allowed)", "count"),
    ("new_changes_opened", "new changes opened", "count"),
    ("fixups_left", "fixup commits left in the chain", "count"),
    ("builds_alone_pct", "every commit builds", "pct"),
    ("conflict_markers_left", "conflict markers left in the tip tree", "count"),
    ("interdiff_lines", "interdiff lines", "count"),
    ("reply_drafted", "reply drafted", "bool"),
    ("reply_labelled", "reply labelled (Conventional Comments)", "bool"),
    ("reply_posted", "reply posted before approval (must be 0)", "bool"),
    ("vote_posted", "vote posted before approval (must be 0)", "bool"),
    ("runner_committed", "work left uncommitted (runner committed)", "bool"),
] + SESSION_METRICS
REVIEW_METRICS = [
    ("planted", "planted defects found", "ratio", ("planted_found", "planted_total")),
    ("labelled", "comments labelled", "ratio", ("comments_labelled", "comments_total")),
    ("blocking_marked_correct", "blocking defects marked blocking", "count"),
    ("comments_total", "comments drafted", "count"),
    ("drafts_created", "Gerrit drafts created", "count"),
    ("published_comments", "comments published (must be 0)", "count"),
    ("votes_posted", "votes posted (must be 0)", "count"),
] + SESSION_METRICS
CONVENTION_METRICS = [
    ("subjects", "conforming commit subjects", "ratio", ("commit_subjects_conforming", "commit_subjects_total")),
    ("comments", "labelled comments", "ratio", ("comments_labelled", "comments_total")),
]
GUARDRAIL_METRICS = [
    ("asks", "human confirmations (ask)", "count"),
    ("denies", "hook denials", "count"),
    ("self_corrections", "self-corrections after deny", "count"),
    ("no_verify_used", "bad: `--no-verify` used", "count"),
    ("amend_m_used", "bad: `commit --amend -m` used", "count"),
    ("force_push_attempted", "bad: force push attempted", "count"),
    ("topic_used_unasked", "bad: topic set unasked", "count"),
    ("refs_heads_push_attempted", "bad: push to refs/heads attempted", "count"),
    ("commit_without_change_id", "bad: commit without Change-Id", "count"),
    ("left_uncommitted", "bad: work left uncommitted (runner committed)", "count"),
    ("refs_heads_moved", "bad: local remote master moved", "bool"),
    ("gerrit_master_moved", "bad: Gerrit master moved", "bool"),
]
GUARDRAIL_BASE_KEYS = ["asks", "denies", "self_corrections"]
GUARDRAIL_KNOWN = {m[0] for m in GUARDRAIL_METRICS}
ISOLATION_GROUPS = ["plugins", "mcp_servers", "skills", "agents"]
CAPABILITY_KEYS = ["mcp_calls", "mcp_denied", "skill_calls", "hook_lines"]

# Targets (execution plan, "Proposal: measuring the efficiency of gerrit-stack")
TARGETS = [
    ("budget compliance", "within_budget_pct", ">=", 90.0, "%"),
    ("exactly one Change-Id", "one_change_id_pct", ">=", 100.0, "%"),
    ("rule violations", "violations", "==", 0.0, ""),
    ("cost overhead vs mcp-only", "cost_overhead_pct", "<=", 30.0, "%"),
]

GIT_VERB_RE = re.compile(r"(?:^|[;&|]\s*|\n\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--git-dir=\S+\s+|--work-tree=\S+\s+)*([a-z][a-z-]*)")
DENY_HINT_RE = re.compile(r"hook|denied|blocked|not allowed|refused", re.IGNORECASE)
# bad-outcome heuristics over one shell command segment (fallback when the run
# record carries no ``guardrails`` block; the runner's own counters win)
_SEG_SPLIT_RE = re.compile(r"[;&|]+|\n")
_NO_VERIFY_RE = re.compile(r"(?:^|\s)(?:--no-verify|-n)(?:\s|$)")
_AMEND_RE = re.compile(r"(?:^|\s)--amend(?:\s|$)")
_MSG_FLAG_RE = re.compile(r"(?:^|\s)(?:-m|--message(?:=|\s))")
_FORCE_RE = re.compile(
    r"(?:^|\s)(?:--force(?:-with-lease)?(?:=\S*)?|-f)(?:\s|$)|\s\+\S+:\S+")
_TOPIC_RE = re.compile(r"%(?:[^ ]*,)?topic=|(?:^|\s)-o\s+topic=")
_REFS_HEADS_RE = re.compile(
    r"refs/heads/|(?:^|\s)HEAD:(?:master|main)(?:\s|$)"
    r"|(?:^|\s)(?:--set-upstream\s+|-u\s+)?\S+\s+(?:master|main)(?:\s|$)")


def bad_outcomes_from_command(command):
    """Guardrail bad-outcome counters found in one Bash command (trace fallback)."""
    counts = {k: 0 for k in ("no_verify_used", "amend_m_used", "force_push_attempted",
                             "topic_used_unasked", "refs_heads_push_attempted")}
    if not isinstance(command, str):
        return counts
    for seg in _SEG_SPLIT_RE.split(command):
        verbs = git_verbs(seg.strip())
        if not verbs:
            continue
        if "commit" in verbs:
            if _NO_VERIFY_RE.search(seg):
                counts["no_verify_used"] += 1
            if _AMEND_RE.search(seg) and _MSG_FLAG_RE.search(seg):
                counts["amend_m_used"] += 1
        if "push" in verbs:
            if _FORCE_RE.search(seg):
                counts["force_push_attempted"] += 1
            if _TOPIC_RE.search(seg):
                counts["topic_used_unasked"] += 1
            if "refs/for/" not in seg and _REFS_HEADS_RE.search(seg):
                counts["refs_heads_push_attempted"] += 1
    return counts


# ----------------------------------------------------------------- helpers ----
def _num(value):
    """Return value as float when it is a finite number (bool -> 0/1), else None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def git_verbs(command):
    """All git sub-command verbs found in a shell command string."""
    if not isinstance(command, str):
        return []
    return GIT_VERB_RE.findall(command)


# ------------------------------------------------------------- trace parse ----
def parse_trace(path):
    """Process metrics from a claude ``--output-format stream-json`` trace.

    Returns a dict (all keys present; None when the trace lacks the info) plus
    ``tool_uses`` (list of {id, name, input}) for callers that need detail.
    """
    out = {
        "turns": None, "tool_calls": 0, "bash_calls": 0, "git_commit_calls": 0,
        "git_push_calls": 0, "cost_usd": None, "wall_s": None, "asks_tool": 0,
        "denies_trace": 0, "self_corrections": 0, "usage": None,
        "bad_outcomes": {k: 0 for k in ("no_verify_used", "amend_m_used", "force_push_attempted",
                                        "topic_used_unasked", "refs_heads_push_attempted")},
    }
    tool_uses = []          # in order: {id, name, input}
    results = {}            # tool_use_id -> {"is_error": bool, "text": str}
    if not os.path.isfile(path):
        out["tool_uses"] = tool_uses
        out["present"] = False
        return out
    out["present"] = True
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            content = msg.get("content")
            if etype == "assistant" and isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    name = item.get("name") or ""
                    inp = item.get("input") if isinstance(item.get("input"), dict) else {}
                    tool_uses.append({"id": item.get("id"), "name": name, "input": inp})
                    out["tool_calls"] += 1
                    if name == "AskUserQuestion":
                        out["asks_tool"] += 1
                    if name == "Bash":
                        out["bash_calls"] += 1
                        verbs = git_verbs(inp.get("command"))
                        if "commit" in verbs:
                            out["git_commit_calls"] += 1
                        if "push" in verbs:
                            out["git_push_calls"] += 1
                        for k, v in bad_outcomes_from_command(inp.get("command")).items():
                            out["bad_outcomes"][k] += v
            elif etype == "user" and isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_result":
                        continue
                    text = item.get("content")
                    if isinstance(text, list):
                        text = " ".join(
                            str(t.get("text", "")) for t in text if isinstance(t, dict))
                    results[item.get("tool_use_id")] = {
                        "is_error": bool(item.get("is_error")),
                        "text": text if isinstance(text, str) else "",
                    }
            elif etype == "result":
                out["turns"] = _num(ev.get("num_turns"))
                out["cost_usd"] = _num(ev.get("total_cost_usd"))
                ms = _num(ev.get("duration_ms"))
                out["wall_s"] = ms / 1000.0 if ms is not None else None
                if isinstance(ev.get("usage"), dict):
                    out["usage"] = ev["usage"]
    # denials seen in the trace: a Bash call whose result is an error that
    # mentions a hook / denial; self-correction = a later Bash call with the
    # same git verb that succeeded.
    denied = []
    for idx, tu in enumerate(tool_uses):
        if tu["name"] != "Bash":
            continue
        res = results.get(tu["id"])
        if res and res["is_error"] and DENY_HINT_RE.search(res["text"] or ""):
            denied.append((idx, set(git_verbs(tu["input"].get("command")))))
    out["denies_trace"] = len(denied)
    for idx, verbs in denied:
        fixed = False
        for later in tool_uses[idx + 1:]:
            if later["name"] != "Bash":
                continue
            res = results.get(later["id"])
            if res is None or res["is_error"]:
                continue
            if not verbs or verbs & set(git_verbs(later["input"].get("command"))):
                fixed = True
                break
        if fixed:
            out["self_corrections"] += 1
    out["tool_uses"] = tool_uses
    return out


def parse_hook_trace(path):
    """Count decisions in a GERRIT_STACK_TRACE file (``script<TAB>verb<TAB>decision``)."""
    counts = {"deny": 0, "ask": 0, "feedback": 0, "lines": 0, "present": False}
    if not os.path.isfile(path):
        return counts
    counts["present"] = True
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.rstrip("\r\n")
            if not raw.strip():
                continue
            parts = raw.split("\t") if "\t" in raw else raw.split()
            counts["lines"] += 1
            decision = parts[2].strip() if len(parts) >= 3 else ""
            if decision in counts:
                counts[decision] += 1
    return counts


# ----------------------------------------------------------------- loading ----
def _count_of(value):
    """Lists count their items; bools/numbers pass through _num; else None."""
    if isinstance(value, (list, tuple, dict)):
        return float(len(value))
    return _num(value)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _spec_keys(spec):
    keys = []
    for item in spec:
        keys.extend(item[3] if item[2] == "ratio" else [item[0]])
    return keys


def split_case_name(name):
    """``<case>@<variant>`` -> (case, variant); ``<case>`` -> (case, None)."""
    base, _sep, variant = (name or "").partition("@")
    return base, (variant or None)


def _basename(path):
    return os.path.basename(os.path.normpath(path)) if isinstance(path, str) and path else None


def _file_or_record(run_dir, filename, entry, key):
    """A per-run JSON file wins; the run record's block fills in when it is missing."""
    data = _read_json(os.path.join(run_dir, filename)) if run_dir else None
    if isinstance(data, dict):
        return data
    return entry.get(key) if isinstance(entry.get(key), dict) else None


def _guardrail_counters(block, row, runner_committed):
    """Flat guardrail counters of one run: the runner's block wins, the trace fills gaps.

    Unknown counters under ``bad_outcomes`` are kept, so a new bad outcome the
    runner starts recording shows up without a collector change.
    """
    block = _dict(block)
    bad = _dict(block.get("bad_outcomes"))
    trace_bad = row.get("bad_outcomes") or {}
    out = {}
    for key in GUARDRAIL_BASE_KEYS:
        val = _num(block.get(key))
        out[key] = val if val is not None else row.get(key)
    keys = [m[0] for m in GUARDRAIL_METRICS if m[0] not in GUARDRAIL_BASE_KEYS]
    keys += sorted(k for k in bad if k not in GUARDRAIL_KNOWN)
    for key in keys:
        val = _count_of(bad.get(key))
        if val is None:
            val = _count_of(block.get(key))
        if val is None and key in trace_bad:
            val = float(trace_bad[key])
        if val is None and key == "left_uncommitted" and runner_committed is not None:
            val = _num(runner_committed)
        out[key] = val
    return out


def load_run(run_dir, name, arm, n, agg_entry=None, case_meta=None):
    """One run's row from its directory and run record (any of them may be missing)."""
    entry = _dict(agg_entry)
    meta = _dict(case_meta)
    trace = parse_trace(os.path.join(run_dir, "trace.jsonl")) if run_dir else parse_trace("")
    hook = parse_hook_trace(os.path.join(run_dir, "hook-trace.log")) if run_dir else parse_hook_trace("")
    chain = _dict(_read_json(os.path.join(run_dir, "chain-metrics.json")) if run_dir else None)
    rework = _file_or_record(run_dir, "rework-metrics.json", entry, "rework")
    review = _file_or_record(run_dir, "review-metrics.json", entry, "reviewMetrics")
    isolation = _file_or_record(run_dir, "isolation.json", entry, "isolation")
    conventions = _file_or_record(run_dir, "conventions.json", entry, "conventions")
    capability = entry.get("capability") if isinstance(entry.get("capability"), dict) else None
    if capability is None and isinstance(_dict(isolation).get("capability"), dict):
        capability = isolation["capability"]
    if capability is None:
        capability = _file_or_record(run_dir, "capability.json", {}, "capability")
    hashtags = [t for t in entry.get("hashtags") or [] if isinstance(t, str)] \
        if isinstance(entry.get("hashtags"), list) else None

    case, name_variant = split_case_name(name)
    kind = entry.get("kind") or meta.get("kind")
    if kind not in KINDS:
        kind = "rework" if rework is not None else "review" if review is not None else "implement"
    variant = entry.get("variant") or meta.get("variant") or name_variant
    if not isinstance(variant, str) or not variant:
        variant = next((t[4:] for t in hashtags or [] if t.startswith("var-")), "natural")
    suite = meta.get("suite") or KIND_SUITE[kind]

    row = {"suite": suite, "kind": kind, "case": meta.get("case") or case, "variant": variant,
           "arm": arm, "n": n, "dir": run_dir, "name": name}
    row["case_key"] = row["case"] if variant == "natural" else "%s@%s" % (row["case"], variant)
    row["score"] = _num(entry.get("score"))
    if row["score"] is None and entry.get("passed") is not None:
        row["score"] = 1.0 if entry.get("passed") else 0.0
    row["turns"] = trace["turns"] if trace["turns"] is not None else _num(entry.get("turns"))
    for key in ("tool_calls", "bash_calls", "git_commit_calls", "git_push_calls", "self_corrections"):
        row[key] = float(trace[key]) if trace["present"] else None
    row["cost_usd"] = trace["cost_usd"] if trace["cost_usd"] is not None else _num(entry.get("costUsd"))
    row["wall_s"] = trace["wall_s"] if trace["wall_s"] is not None else _num(entry.get("durationSeconds"))
    # asks: hook `ask` decisions + AskUserQuestion tool calls
    asks = (hook["ask"] if hook["present"] else 0) + (trace["asks_tool"] if trace["present"] else 0)
    row["asks"] = float(asks) if (hook["present"] or trace["present"]) else None
    if hook["present"]:
        row["denies"] = float(hook["deny"])
    elif trace["present"]:
        row["denies"] = float(trace["denies_trace"])
    else:
        row["denies"] = None
    for key, _label, _unit, _lower in CHAIN_METRICS:
        val = chain.get(key)
        if key == "violations" and val is None and hook["present"]:
            val = hook["deny"]
        row[key] = _num(val)
    for key in EXTRA_CHAIN_KEYS:
        row[key] = _num(chain.get(key))
    if row.get("chain_length") is None and chain:
        row["chain_length"] = 0.0
    row["change_id_set"] = chain.get("change_id_set") if isinstance(chain.get("change_id_set"), list) else None
    row["usage"] = trace.get("usage")
    row["bad_outcomes"] = dict(trace["bad_outcomes"]) if trace["present"] else None
    row["hashtags"] = hashtags
    row["pushed"] = entry.get("pushed") if isinstance(entry.get("pushed"), list) else None

    row["rework"] = None
    if kind == "rework":
        src = _dict(rework)
        row["rework"] = {k: _count_of(src.get(k)) for k in _spec_keys(REWORK_METRICS)
                         if k not in ("cost_usd", "turns", "wall_s")}
        row["rework"]["target_change"] = src.get("target_change")
        if row["rework"]["builds_alone_pct"] is None:
            row["rework"]["builds_alone_pct"] = row.get("builds_alone_pct")
    row["review"] = None
    if kind == "review":
        src = _dict(review)
        row["review"] = {k: _count_of(src.get(k)) for k in _spec_keys(REVIEW_METRICS)
                         if k not in ("cost_usd", "turns", "wall_s")}
        row["review"]["found_ids"] = [i for i in src.get("found_ids") or [] if isinstance(i, str)] \
            if isinstance(src.get("found_ids"), list) else []
    row["conventions"] = None
    if conventions is not None:
        row["conventions"] = {k: _num(conventions.get(k)) for k in _spec_keys(CONVENTION_METRICS)}
        avail = conventions.get("commitlint_available")
        row["conventions"]["commitlint_available"] = avail if isinstance(avail, bool) else None
    row["guardrails"] = _guardrail_counters(entry.get("guardrails"), row,
                                            _dict(rework).get("runner_committed"))

    iso = None
    if isolation is not None:
        unexpected = _dict(isolation.get("unexpected"))
        finger = _dict(isolation.get("fingerprint"))
        iso = {
            "ok": isolation.get("ok") if isinstance(isolation.get("ok"), bool) else None,
            "unexpected": {g: [str(i) for i in unexpected.get(g) or []] if isinstance(unexpected.get(g), list) else []
                           for g in ISOLATION_GROUPS},
            "fingerprint": {g: finger.get(g) for g in ISOLATION_GROUPS + ["model", "claude_code_version"]},
        }
    row["isolation"] = iso
    row["capability"] = {k: _num(capability.get(k)) for k in CAPABILITY_KEYS} if capability is not None else None
    finger = (iso or {}).get("fingerprint") or {}
    model = entry.get("model") or finger.get("model")
    row["model"] = model if isinstance(model, str) else None
    version = finger.get("claude_code_version") or meta.get("claudeVersion")
    row["claude_version"] = version if isinstance(version, str) else None

    error = entry.get("error") if isinstance(entry.get("error"), str) and entry.get("error") else None
    if error is None and iso is not None and iso["ok"] is False:
        error = "isolation check failed"
    row["error"] = error
    row["errored"] = error is not None
    return row


def _aggregate_index(aggregate):
    """((name, arm, n) -> run record, name -> case meta) from an aggregate-result.json."""
    index, case_meta = {}, {}
    if not isinstance(aggregate, dict):
        return index, case_meta
    top_suite = aggregate.get("suite") if isinstance(aggregate.get("suite"), str) else None
    if top_suite is None:
        top_suite = next((_basename(aggregate.get(k)) for k in ("evalDir", "evalsDir", "eval_dir")
                          if _basename(aggregate.get(k))), None)
    version = aggregate.get("claudeVersion") if isinstance(aggregate.get("claudeVersion"), str) else None
    for case in aggregate.get("cases") or []:
        if not isinstance(case, dict) or not isinstance(case.get("name"), str):
            continue
        name = case["name"]
        suite = case.get("suite") if isinstance(case.get("suite"), str) else None
        if suite is None and isinstance(case.get("dir"), str) and case["dir"]:
            suite = _basename(os.path.dirname(os.path.normpath(case["dir"])))
        base = case.get("baseCase") if isinstance(case.get("baseCase"), str) else None
        case_meta[name] = {
            "suite": suite or top_suite,
            "kind": case.get("kind") if case.get("kind") in KINDS else None,
            "variant": case.get("variant") if isinstance(case.get("variant"), str) else None,
            "case": base,
            "claudeVersion": version,
        }
        arms = case.get("arms") if isinstance(case.get("arms"), dict) else {}
        for arm, runs in arms.items():
            if not isinstance(runs, list):
                continue
            for i, entry in enumerate(runs):
                if isinstance(entry, dict):
                    n = entry.get("run") if isinstance(entry.get("run"), int) else i + 1
                    index[(name, arm, n)] = entry
    return index, case_meta


def load_results_dir(results_dir):
    """All run rows of one results directory (+ its aggregate metadata)."""
    aggregate = _read_json(os.path.join(results_dir, "aggregate-result.json"))
    index, case_meta = _aggregate_index(aggregate)
    default_meta = {}
    if isinstance(aggregate, dict):
        suite = aggregate.get("suite") if isinstance(aggregate.get("suite"), str) else None
        suite = suite or next((_basename(aggregate.get(k)) for k in ("evalDir", "evalsDir", "eval_dir")
                               if _basename(aggregate.get(k))), None)
        default_meta = {"suite": suite, "claudeVersion": aggregate.get("claudeVersion")}
    rows, seen = [], set()
    runs_root = os.path.join(results_dir, "runs")
    if os.path.isdir(runs_root):
        for name in sorted(os.listdir(runs_root)):
            case_dir = os.path.join(runs_root, name)
            if not os.path.isdir(case_dir):
                continue
            for arm in sorted(os.listdir(case_dir)):
                arm_dir = os.path.join(case_dir, arm)
                if not os.path.isdir(arm_dir):
                    continue
                for nname in sorted(os.listdir(arm_dir)):
                    run_dir = os.path.join(arm_dir, nname)
                    if not os.path.isdir(run_dir):
                        continue
                    try:
                        n = int(nname)
                    except ValueError:
                        continue
                    rows.append(load_run(run_dir, name, arm, n, index.get((name, arm, n)),
                                         case_meta.get(name) or default_meta))
                    seen.add((name, arm, n))
    # runs only present in the aggregate (no per-run directory)
    for key, entry in sorted(index.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])):
        if key in seen:
            continue
        name, arm, n = key
        rows.append(load_run("", name, arm, n, entry, case_meta.get(name) or default_meta))
    agg = aggregate if isinstance(aggregate, dict) else {}
    meta = {
        "dir": results_dir,
        "aggregate": bool(aggregate),
        "claudeVersion": agg.get("claudeVersion"),
        "overallScore": _dict(agg.get("aggregates")).get("overallScore"),
        "costUsd": agg.get("costUsd"),
        "suites": _suite_order({r["suite"] for r in rows}),
        "runs": len(rows),
        "errors": sum(1 for r in rows if r["errored"]),
        "runIds": sorted({t for r in rows for t in r.get("hashtags") or [] if t.startswith("run-")}),
    }
    if isinstance(agg.get("runId"), str):
        meta["runId"] = agg["runId"]
    return rows, meta


def default_results_dirs(repo_root):
    dirs = []
    for path in sorted(glob.glob(os.path.join(repo_root, "evals", "results", "*"))):
        if os.path.isdir(path) and (
            os.path.isfile(os.path.join(path, "aggregate-result.json"))
            or os.path.isdir(os.path.join(path, "runs"))
        ):
            dirs.append(path)
    return dirs


# ------------------------------------------------------------- aggregation ----
def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "values": []}
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "values": vals,
    }


def _rate(values):
    """Share of truthy values over the non-None ones (bools rendered as rates over runs)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "values": [], "true": 0, "rate_pct": None}
    true = sum(1 for v in vals if v)
    st = _stats(vals)
    st.update({"true": true, "rate_pct": 100.0 * true / len(vals)})
    return st


def _ratio(dicts, num_key, den_key):
    """Pooled numerator / denominator over the runs that report a denominator."""
    pairs = [(d.get(num_key) or 0.0, d.get(den_key)) for d in dicts if d.get(den_key) is not None]
    num = sum(p[0] for p in pairs)
    den = sum(p[1] for p in pairs)
    return {"n": len(pairs), "num": num, "den": den, "rate_pct": (100.0 * num / den) if den else None}


def _arm_order(arms):
    return sorted(arms, key=lambda a: (ARMS.index(a) if a in ARMS else 99, a))


def _suite_order(suites):
    return sorted(suites, key=lambda s: (SUITE_ORDER.index(s) if s in SUITE_ORDER else 99, s))


def _metric_block(dicts_by_arm, spec):
    """{arm: {runs, metrics: {key: stats}}} for a section spec (see the spec comment)."""
    block = {}
    for arm, dicts in dicts_by_arm.items():
        if not dicts:
            continue
        metrics = {}
        for item in spec:
            key, kind = item[0], item[2]
            if kind == "ratio":
                metrics[key] = _ratio(dicts, item[3][0], item[3][1])
            elif kind == "bool":
                metrics[key] = _rate([d.get(key) for d in dicts])
            else:
                metrics[key] = _stats([d.get(key) for d in dicts])
        block[arm] = {"runs": len(dicts), "metrics": metrics}
    return block


def _block_deltas(block, spec):
    """C−B and C−A: difference of means, or of rates in percentage points (bool / ratio)."""
    def one(a, b):
        out = {}
        if a not in block or b not in block:
            return out
        for item in spec:
            key, kind = item[0], item[2]
            field = "rate_pct" if kind in ("bool", "ratio") else "mean"
            ma = block[a]["metrics"][key].get(field)
            mb = block[b]["metrics"][key].get(field)
            out[key] = (ma - mb) if (ma is not None and mb is not None) else None
        return out
    return {"with-mcp-only": one("with", "mcp-only"), "with-without": one("with", "without")}


def _counts(rows, include_errors):
    """{arm: {runs, errors}}: runs feeding the means, and errored runs (always counted)."""
    out = {}
    for arm in _arm_order({r["arm"] for r in rows}):
        subset = [r for r in rows if r["arm"] == arm]
        errors = sum(1 for r in subset if r["errored"])
        out[arm] = {"runs": len(subset) if include_errors else len(subset) - errors, "errors": errors}
    return out


def _section(rows, include_errors, spec, to_dict):
    """One table: counts over all rows, metrics over the rows that feed the means."""
    used = [r for r in rows if include_errors or not r["errored"]]
    arms = _arm_order({r["arm"] for r in rows})
    block = _metric_block({arm: [to_dict(r) for r in used if r["arm"] == arm] for arm in arms}, spec)
    return {"counts": _counts(rows, include_errors), "arms": block, "deltas": _block_deltas(block, spec)}


def _classic_block(rows, arms):
    block = {}
    for arm in arms:
        subset = [r for r in rows if r["arm"] == arm]
        if subset:
            block[arm] = {"runs": len(subset),
                          "metrics": {k: _stats([r.get(k) for r in subset]) for k in METRIC_KEYS}}
    return block


def _classic_deltas(block):
    def one(a, b):
        out = {}
        if a not in block or b not in block:
            return out
        for k in METRIC_KEYS:
            ma = block[a]["metrics"][k]["mean"]
            mb = block[b]["metrics"][k]["mean"]
            out[k] = (ma - mb) if (ma is not None and mb is not None) else None
        return out
    return {"with-mcp-only": one("with", "mcp-only"), "with-without": one("with", "without")}


def evaluate_targets(overall):
    """Target pass/fail for arm ``with`` (cost overhead relative to ``mcp-only``)."""
    with_m = overall.get("with", {}).get("metrics", {})
    mcp_m = overall.get("mcp-only", {}).get("metrics", {})
    results = []
    for label, key, op, threshold, unit in TARGETS:
        if key == "cost_overhead_pct":
            cw = (with_m.get("cost_usd") or {}).get("mean")
            cb = (mcp_m.get("cost_usd") or {}).get("mean")
            value = ((cw - cb) / cb * 100.0) if (cw is not None and cb not in (None, 0)) else None
        else:
            value = (with_m.get(key) or {}).get("mean")
        if value is None:
            status = None
        elif op == ">=":
            status = value >= threshold
        elif op == "<=":
            status = value <= threshold
        else:
            status = abs(value - threshold) < 1e-9
        results.append({
            "label": label, "key": key, "op": op, "threshold": threshold,
            "unit": unit, "value": value, "pass": status,
        })
    return results


def aggregate_suite(rows, include_errors):
    """Overall / Targets / Per case of one implement suite.

    Overall and the targets use the natural variant only (a nudged run carries a
    deliberately bad instruction); nudged runs show under Per case as
    ``<case>@nudged`` and in Guardrails.
    """
    used = [r for r in rows if include_errors or not r["errored"]]
    arms = _arm_order({r["arm"] for r in rows})
    natural = [r for r in used if r["variant"] == "natural"]
    overall = _classic_block(natural, arms)
    per_case, case_deltas, case_counts = {}, {}, {}
    cases = sorted({r["case_key"] for r in rows})
    for case in cases:
        per_case[case] = _classic_block([r for r in used if r["case_key"] == case], arms)
        case_deltas[case] = _classic_deltas(per_case[case])
        case_counts[case] = _counts([r for r in rows if r["case_key"] == case], include_errors)
    return {
        "cases": cases, "arms": arms,
        "counts": _counts([r for r in rows if r["variant"] == "natural"], include_errors),
        "runs": len(natural),
        "errors": sum(1 for r in rows if r["errored"]),
        "overall": overall, "per_case": per_case, "case_counts": case_counts,
        "deltas": {"overall": _classic_deltas(overall), "cases": case_deltas},
        "targets": evaluate_targets(overall),
    }


def aggregate_split_quality(rows, include_errors):
    """Per suite x case (natural variant): the split-quality metrics per arm."""
    out = {}
    rows = [r for r in rows if r["variant"] == "natural"]
    for suite in _suite_order({r["suite"] for r in rows}):
        for case in sorted({r["case"] for r in rows if r["suite"] == suite}):
            subset = [r for r in rows if r["suite"] == suite and r["case"] == case]
            if not any(r.get(k) is not None for r in subset for k in SPLIT_SIGNAL_KEYS):
                continue
            out.setdefault(suite, {})[case] = _section(subset, include_errors, SPLIT_METRICS, lambda r: r)
    return out


def _with_session(row, block):
    merged = dict(block or {})
    merged.update({k: row.get(k) for k in ("cost_usd", "turns", "wall_s")})
    return merged


def aggregate_rework(rows, include_errors):
    """Per case x variant: every rework metric per arm."""
    out = {}
    for case, variant in sorted({(r["case"], r["variant"]) for r in rows}):
        subset = [r for r in rows if (r["case"], r["variant"]) == (case, variant)]
        sec = _section(subset, include_errors, REWORK_METRICS, lambda r: _with_session(r, r["rework"]))
        sec.update({"case": case, "variant": variant,
                    "hashtags": sorted({t for r in subset for t in r.get("hashtags") or []})})
        out["%s@%s" % (case, variant)] = sec
    return out


def aggregate_review(rows, include_errors):
    """Per case: reviewer metrics per arm, plus how often each planted defect was found."""
    out = {}
    for case in sorted({r["case"] for r in rows}):
        subset = [r for r in rows if r["case"] == case]
        sec = _section(subset, include_errors, REVIEW_METRICS, lambda r: _with_session(r, r["review"]))
        for arm, blk in sec["arms"].items():
            found = {}
            for r in subset:
                if r["arm"] == arm and (include_errors or not r["errored"]):
                    for ident in (r["review"] or {}).get("found_ids") or []:
                        found[ident] = found.get(ident, 0) + 1
            blk["found_ids"] = found
        sec["case"] = case
        out[case] = sec
    return out


def aggregate_conventions(rows, include_errors):
    """Per suite x arm: pooled conforming subjects and labelled comments."""
    out = {}
    rows = [r for r in rows if r["conventions"] is not None]
    for suite in _suite_order({r["suite"] for r in rows}):
        subset = [r for r in rows if r["suite"] == suite]
        sec = _section(subset, include_errors, CONVENTION_METRICS, lambda r: r["conventions"])
        for arm, blk in sec["arms"].items():
            blk["commitlint_fallback_runs"] = sum(
                1 for r in subset if r["arm"] == arm and (include_errors or not r["errored"])
                and r["conventions"].get("commitlint_available") is False)
        sec["commitlint_fallback_runs"] = sum(b["commitlint_fallback_runs"] for b in sec["arms"].values())
        out[suite] = sec
    return out


def _guardrail_spec(rows):
    extra = sorted({k for r in rows for k in (r["guardrails"] or {}) if k not in GUARDRAIL_KNOWN})
    return GUARDRAIL_METRICS + [(k, "bad: `%s`" % k, "count") for k in extra]


def aggregate_guardrails(rows, include_errors):
    """Per variant x arm, pooled over suites and cases."""
    spec = _guardrail_spec(rows)
    out = {}
    for variant in sorted({r["variant"] for r in rows}):
        subset = [r for r in rows if r["variant"] == variant]
        out[variant] = _section(subset, include_errors, spec, lambda r: r["guardrails"] or {})
    return out


def _capability_verdict(arm, n, used_mcp, used_stack, denied, unflagged):
    """One sentence: was the arm's distinguishing capability exercised?"""
    if unflagged == n:
        return "not recorded"
    if arm == "without":
        text = "n/a (no plugin loaded)"
        if used_mcp or used_stack:
            text = "UNEXPECTED: plugin capability used in a no-plugin arm"
        return text
    if arm == "mcp-only":
        text = "gerrit MCP used in %d/%d runs" % (used_mcp, n)
        if not used_mcp:
            text += " (capability unused)"
    else:
        text = "gerrit-stack (skill or hook) used in %d/%d runs, gerrit MCP in %d/%d" % (used_stack, n, used_mcp, n)
        if not used_stack:
            text += " (capability unused)"
    if denied:
        text += "; %d MCP call(s) denied" % denied
    return text


def aggregate_isolation(rows):
    """Per arm over ALL runs (errored ones included): what was loaded and what was used."""
    out = {}
    for arm in _arm_order({r["arm"] for r in rows}):
        subset = [r for r in rows if r["arm"] == arm]
        recorded = [r for r in subset if r["isolation"] is not None]
        unexpected = {}
        for r in recorded:
            for group in ISOLATION_GROUPS:
                for item in r["isolation"]["unexpected"].get(group) or []:
                    key = "%s: %s" % (group, item)
                    unexpected[key] = unexpected.get(key, 0) + 1
        caps = [r["capability"] for r in subset if r["capability"] is not None]
        totals = {k: sum((c.get(k) or 0.0) for c in caps) for k in CAPABILITY_KEYS}
        used_mcp = sum(1 for c in caps if (c.get("mcp_calls") or 0) > 0)
        used_stack = sum(1 for c in caps if (c.get("skill_calls") or 0) > 0 or (c.get("hook_lines") or 0) > 0)
        out[arm] = {
            "runs": len(subset),
            "errors": sum(1 for r in subset if r["errored"]),
            "isolation_ok": sum(1 for r in recorded if r["isolation"]["ok"] is True),
            "isolation_failed": sum(1 for r in recorded if r["isolation"]["ok"] is False),
            "isolation_unrecorded": len(subset) - len(recorded),
            "unexpected": unexpected,
            "models": sorted({r["model"] for r in subset if r["model"]}),
            "claude_versions": sorted({r["claude_version"] for r in subset if r["claude_version"]}),
            "capability_runs": len(caps),
            "mcp_calls": totals["mcp_calls"], "mcp_denied": totals["mcp_denied"],
            "skill_calls": totals["skill_calls"], "hook_lines": totals["hook_lines"],
            "runs_with_mcp_calls": used_mcp, "runs_with_stack_capability": used_stack,
            "capability": _capability_verdict(arm, len(subset), used_mcp, used_stack,
                                              int(totals["mcp_denied"]), len(subset) - len(caps)),
        }
    return out


def aggregate(rows, include_errors=False):
    """The whole report: one block per section (see the module docstring)."""
    implement = [r for r in rows if r["kind"] == "implement"]
    suites = {}
    for suite in _suite_order({r["suite"] for r in implement}):
        suites[suite] = aggregate_suite([r for r in implement if r["suite"] == suite], include_errors)
    errors = sum(1 for r in rows if r["errored"])
    return {
        "arms": _arm_order({r["arm"] for r in rows}),
        "include_errors": bool(include_errors),
        "runs_total": len(rows),
        "errors_total": errors,
        "runs_used": len(rows) if include_errors else len(rows) - errors,
        "kinds": {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS},
        "suites": suites,
        "split_quality": aggregate_split_quality(implement, include_errors),
        "rework": aggregate_rework([r for r in rows if r["kind"] == "rework"], include_errors),
        "reviewer": aggregate_review([r for r in rows if r["kind"] == "review"], include_errors),
        "conventions": aggregate_conventions(rows, include_errors),
        "guardrails": aggregate_guardrails(rows, include_errors) if rows else {},
        "isolation": aggregate_isolation(rows),
        "targets": evaluate_targets({}),
    }


# --------------------------------------------------------------- rendering ----
def fmt(value, unit=""):
    if value is None:
        return "–"
    if unit == "USD":
        return "$%.3f" % value
    if unit == "%":
        return "%.1f %%" % value if abs(value - round(value)) > 1e-9 else "%d %%" % round(value)
    if abs(value - round(value)) < 1e-9:
        return "%d%s" % (round(value), (" " + unit) if unit else "")
    return "%.1f%s" % (value, (" " + unit) if unit else "")


def fmt_delta(value, unit=""):
    if value is None:
        return "–"
    sign = "+" if value > 0 else ""
    return sign + fmt(value, unit)


KIND_UNIT = {"bool": "%", "ratio": "%", "pct": "%", "usd": "USD", "s": "s", "count": ""}


def _metric_table(block, arms, deltas):
    lines = ["| metric | " + " | ".join(ARM_SHORT.get(a, a) + " (mean / median)" for a in arms)
             + " | Δ C−B | Δ C−A |",
             "|---|" + "---|" * len(arms) + "---|---|"]
    for key, label, unit, _lower in ALL_METRICS:
        cells = []
        any_value = False
        for arm in arms:
            st = block.get(arm, {}).get("metrics", {}).get(key)
            if st and st["mean"] is not None:
                any_value = True
                cells.append("%s / %s" % (fmt(st["mean"], unit), fmt(st["median"], unit)))
            else:
                cells.append("–")
        if not any_value:
            continue
        d1 = fmt_delta((deltas.get("with-mcp-only") or {}).get(key), unit)
        d2 = fmt_delta((deltas.get("with-without") or {}).get(key), unit)
        lines.append("| %s | %s | %s | %s |" % (label, " | ".join(cells), d1, d2))
    return lines


def _kind_cell(st, kind):
    """One cell: ``100 % (k/n)`` for bools and ratios, ``mean / median`` otherwise."""
    if not st:
        return None
    if kind == "bool":
        if st.get("rate_pct") is None:
            return None
        return "%s (%d/%d)" % (fmt(st["rate_pct"], "%"), st["true"], st["n"])
    if kind == "ratio":
        if st.get("rate_pct") is None:
            return None
        return "%s (%s/%s)" % (fmt(st["rate_pct"], "%"), fmt(st["num"]), fmt(st["den"]))
    if st.get("mean") is None:
        return None
    unit = KIND_UNIT[kind]
    return "%s / %s" % (fmt(st["mean"], unit), fmt(st["median"], unit))


def _kind_table(section, spec, first_col="metric"):
    """Metric x arm table with C−B / C−A deltas; rows no arm has are omitted."""
    block, deltas = section["arms"], section["deltas"]
    arms = _arm_order(section["counts"])
    lines = ["| %s | " % first_col + " | ".join(ARM_SHORT.get(a, a) for a in arms) + " | Δ C−B | Δ C−A |",
             "|---|" + "---|" * len(arms) + "---|---|"]
    for item in spec:
        key, label, kind = item[0], item[1], item[2]
        cells, any_value = [], False
        for arm in arms:
            cell = _kind_cell(block.get(arm, {}).get("metrics", {}).get(key), kind)
            if cell is None:
                cells.append("–")
            else:
                any_value = True
                cells.append(cell)
        if not any_value:
            continue
        unit = KIND_UNIT[kind]
        d1 = fmt_delta((deltas.get("with-mcp-only") or {}).get(key), unit)
        d2 = fmt_delta((deltas.get("with-without") or {}).get(key), unit)
        lines.append("| %s | %s | %s | %s |" % (label, " | ".join(cells), d1, d2))
    return lines


def _runs_line(counts):
    """``Runs / errors — C with: 2 / 0, …`` (errors are never part of the means by default)."""
    return "Runs / errors — " + ", ".join(
        "%s: %d / %d" % (ARM_SHORT.get(a, a), counts[a]["runs"], counts[a]["errors"]) for a in _arm_order(counts))


def _arms_section():
    return [
        "## Arms",
        "",
        "Same prompts, same fixture repos, same pinned model, three arms in a scrubbed sandbox "
        "(environment allowlist, account connectors off, startup check against a per-arm allowlist):",
        "",
        "- **A — `without`**: vanilla Claude Code, no plugins (the floor).",
        "- **B — `mcp-only`**: vanilla + the official `gerrit@gerrit-mcp` plugin (its MCP server and `gerrit-workflow` skill).",
        "- **C — `with`**: B + `gerrit-stack` (this plugin) honouring the team files. **C vs B is the honest claim**; "
        "C vs A shows the floor.",
        "",
        "Outcome metrics come from `scripts/chain-metrics.sh --json` over each run's workspace; process/cost metrics from the "
        "stream-json trace (`type: result` → cost, turns, duration) and the hook trace (`GERRIT_STACK_TRACE`: `ask`/`deny` per verb). "
        "Cells are `mean / median` over runs, rates are `share (count/total)`; deltas are differences of means "
        "(percentage points for rates).",
        "",
    ]


def _reading_section(report):
    mode = "included in the means (`--include-errors`)" if report.get("include_errors") else "excluded from the means"
    return [
        "## Reading the numbers",
        "",
        "- **1 run per cell shows the shape** of the behaviour (did it split, did the fix land on the right change, was "
        "anything posted); **3 runs per cell** are needed before reading differences between arms as numbers.",
        "- **Seeded chain = identical starting point**: in the rework and reviewer scenarios every arm starts from the "
        "same hand-written chain pushed by the runner, so differences come from the session, not from what an earlier "
        "session happened to produce.",
        "- **Team files are present in every arm's fixture** (`.gerrit-stack`, the commitlint config): they belong to "
        "the repo. Arms A and B simply have no tooling that honours them.",
        "- **Errored runs** (session crashed, or the sandbox startup check found something unexpected) are %s; they are "
        "always counted under Isolation and in each section's `Runs / errors` line." % mode,
        "- Paths that belong to no concern of a case's concern map do not count for purity or completeness.",
        "",
    ]


def _targets_table(targets):
    lines = ["| target | required | arm C value | result |", "|---|---|---|---|"]
    for t in targets:
        req = "%s %s" % (t["op"], fmt(t["threshold"], t["unit"]))
        val = fmt(t["value"], t["unit"]) if t["unit"] != "%" or t["value"] is None else "%.1f %%" % t["value"]
        res = "–" if t["pass"] is None else ("PASS" if t["pass"] else "FAIL")
        lines.append("| %s | %s | %s | %s |" % (t["label"], req, val, res))
    return lines


def _suite_title(suite):
    return "`%s`%s" % (suite, (" — " + SUITE_LABEL[suite]) if suite in SUITE_LABEL else "")


def _suite_section(suite, blk):
    lines = ["## Suite %s" % _suite_title(suite), ""]
    lines += ["### Overall (%s, natural variant, %d runs)" % (suite, blk["runs"]), "", _runs_line(blk["counts"]), ""]
    lines += _metric_table(blk["overall"], blk["arms"], blk["deltas"]["overall"]) + [""]
    lines += ["### Targets (%s, arm C)" % suite, ""] + _targets_table(blk["targets"]) + [""]
    lines += ["### Per case (%s)" % suite, ""]
    for case in blk["cases"]:
        lines += ["#### %s" % case, "", _runs_line(blk["case_counts"][case]), ""]
        lines += _metric_table(blk["per_case"][case], blk["arms"], blk["deltas"]["cases"][case]) + [""]
    return lines


def _split_section(split):
    lines = ["## Split quality", ""]
    lines.append(
        "Deterministic measures of how well a chain is cut, per suite and case (natural variant). *Purity*: share of "
        "changes that carry exactly one concern of the case's concern map; *completeness*: share of concerns that live "
        "in exactly one change; *builds alone*: share of changes on which the fixture's verify command passes when "
        "checked out alone; *tests travel*: share of changes touching `src/main` Java code that also touch a test."
    )
    lines.append("")
    for suite, cases in split.items():
        for case, sec in cases.items():
            lines += ["### %s — %s" % (suite, case), "", _runs_line(sec["counts"]), ""]
            lines += _kind_table(sec, SPLIT_METRICS) + [""]
    return lines


def _rework_section(rework):
    lines = ["## Rework (seeded chain)", ""]
    lines.append(
        "The runner pushes the fixture's hand-written chain, reviewer `rena` posts `Code-Review -1` with one blocking "
        "thread on a change in the middle, one agent session reworks the chain, the runner pushes again and reads "
        "Gerrit back. Every arm starts from the same chain. The nudged variant adds one plausible bad instruction."
    )
    lines.append("")
    for sec in rework.values():
        lines += ["### %s — %s" % (sec["case"], sec["variant"]), ""]
        runs = _runs_line(sec["counts"])
        tags = [t for t in sec.get("hashtags") or [] if t.startswith("run-")]
        if tags:
            runs += " · Gerrit: " + ", ".join("`hashtag:%s`" % t for t in tags)
        lines += [runs, ""]
        lines += _kind_table(sec, REWORK_METRICS) + [""]
    return lines


def _review_section(reviewer):
    lines = ["## Reviewer", ""]
    lines.append(
        "The agent reviews a seeded change with planted defects and drafts comments without posting. *Planted defects "
        "found* and *comments labelled* are pooled over runs (`share (found/total)`); a published comment or a vote is "
        "a violation, Gerrit drafts are allowed."
    )
    lines.append("")
    for case, sec in reviewer.items():
        lines += ["### %s" % case, "", _runs_line(sec["counts"]), ""]
        lines += _kind_table(sec, REVIEW_METRICS) + [""]
        found = []
        for arm in _arm_order(sec["arms"]):
            blk = sec["arms"][arm]
            if blk.get("found_ids"):
                found.append("%s: %s" % (ARM_SHORT.get(arm, arm), ", ".join(
                    "`%s` %d/%d" % (i, c, blk["runs"]) for i, c in sorted(blk["found_ids"].items()))))
        if found:
            lines += ["Found per planted defect — " + "; ".join(found), ""]
    return lines


def _conventions_section(conventions):
    lines = ["## Conventions", ""]
    lines.append(
        "Share of commit subjects that pass the repo's commitlint config and share of drafted comments that carry a "
        "Conventional Comments label, pooled per suite and arm (`share (conforming/total)`)."
    )
    lines.append("")
    for suite, sec in conventions.items():
        lines += ["### %s" % suite, "", _runs_line(sec["counts"]), ""]
        lines += _kind_table(sec, CONVENTION_METRICS, first_col="convention") + [""]
        if sec.get("commitlint_fallback_runs"):
            per_arm = ", ".join("%s: %d" % (ARM_SHORT.get(a, a), sec["arms"][a]["commitlint_fallback_runs"])
                                for a in _arm_order(sec["arms"]) if sec["arms"][a]["commitlint_fallback_runs"])
            lines += ["Note: commitlint was unavailable in %d run(s) (%s); their subjects were checked with the "
                      "Conventional Commits regex fallback." % (sec["commitlint_fallback_runs"], per_arm), ""]
    return lines


def _guardrails_section(guardrails, rows_spec):
    lines = ["## Guardrails", ""]
    lines.append(
        "Per variant, pooled over suites and cases: hook `ask` / `deny` decisions and self-corrections (hook trace when "
        "present, else trace heuristics) plus the bad outcomes the runner checks. Nudged runs add one plausible bad "
        "instruction; expected with the plugin: denies > 0, self-corrections > 0, bad outcomes 0."
    )
    lines.append("")
    for variant, sec in guardrails.items():
        lines += ["### %s" % variant, "", _runs_line(sec["counts"]), ""]
        lines += _kind_table(sec, rows_spec, first_col="counter") + [""]
    return lines


def _isolation_section(isolation):
    lines = ["## Isolation", ""]
    lines.append(
        "What each arm's sessions actually loaded (startup check of the session's `init` record against the arm's "
        "allowlist) and whether the arm's distinguishing capability was exercised. All runs count here, errored ones "
        "included."
    )
    lines.append("")
    lines.append("| arm | runs | errors | isolation ok | model(s) | claude version(s) | MCP calls made / denied | "
                 "skill calls | hook lines | distinguishing capability |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for arm, blk in isolation.items():
        checked = blk["runs"] - blk["isolation_unrecorded"]
        ok = "%d/%d" % (blk["isolation_ok"], checked) if checked else "not recorded"
        if checked and blk["isolation_unrecorded"]:
            ok += " (%d not recorded)" % blk["isolation_unrecorded"]
        if blk["capability_runs"]:
            mcp = "%s / %s" % (fmt(blk["mcp_calls"]), fmt(blk["mcp_denied"]))
            skills, hooks = fmt(blk["skill_calls"]), fmt(blk["hook_lines"])
        else:
            mcp = skills = hooks = "–"
        lines.append("| %s | %d | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            ARM_SHORT.get(arm, arm), blk["runs"], blk["errors"], ok,
            ", ".join("`%s`" % m for m in blk["models"]) or "–",
            ", ".join(blk["claude_versions"]) or "–", mcp, skills, hooks, blk["capability"]))
    lines.append("")
    surprises = [(arm, blk["unexpected"]) for arm, blk in isolation.items() if blk["unexpected"]]
    if surprises:
        lines += ["Unexpected items (the run is marked errored):", ""]
        for arm, items in surprises:
            lines.append("- %s — %s" % (ARM_SHORT.get(arm, arm), ", ".join(
                "`%s` (%d run%s)" % (k, v, "" if v == 1 else "s") for k, v in sorted(items.items()))))
        lines.append("")
    else:
        lines += ["Unexpected items: none.", ""]
    return lines


def _footer():
    return [
        "## Reproduce",
        "",
        "```",
        "make demo-up demo-seed      # local Gerrit for the pushes",
        "python3 evals/run.py --eval-dir evals/bench-unprompted --arms with,mcp-only,without --runs 1",
        "python3 evals/run.py --eval-dir evals/bench-split      --arms with,mcp-only,without --runs 1",
        "python3 evals/run.py --eval-dir evals/bench-rework     --arms with,mcp-only,without --runs 1 \\",
        "    --variants natural,nudged --push-to <gerrit-project-url>",
        "python3 evals/run.py --eval-dir evals/bench-review     --arms with,mcp-only,without --runs 1 \\",
        "    --push-to <gerrit-project-url>",
        "python3 evals/metrics/collect.py --out docs/benchmark.md   # add --json PATH for the raw report",
        "```",
        "",
        "Each runner call writes `evals/results/<timestamp>/{aggregate-result.json,runs/<case>[@<variant>]/<arm>/<n>/…}`; "
        "`collect.py` renders this page from every results directory it finds (or the ones passed with `--results`). "
        "Use `--runs 3` for numbers. Targets: budget compliance ≥ 90 %, exactly-one-Change-Id 100 %, rule violations 0, "
        "cost overhead ≤ +30 % vs arm B.",
        "",
    ]


def render_markdown(report, sources, generated=None):
    generated = generated or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# gerrit-stack efficiency benchmark", ""]
    lines.append("*Generated by `evals/metrics/collect.py` on %s.*" % generated)
    lines.append("")
    lines.append(
        "Does the plugin make an agent produce Gerrit relation chains that are smaller, correct, and cheaper to get "
        "to review than the same agent without it, and does it keep a chain intact through rework and review? "
        "Three arms on identical tasks."
    )
    lines.append("")
    if report["runs_total"] == 0:
        lines += ["## Results", "", "**No runs yet.** Run the benchmark and re-render (see below).", ""]
        lines += _arms_section()
        lines += ["## Targets", ""] + _targets_table(report["targets"]) + [""]
        lines += _footer()
        return "\n".join(lines)

    lines += _arms_section()
    lines += _reading_section(report)
    lines += ["## Sources", ""]
    for meta in sources:
        extra = []
        if meta.get("suites"):
            extra.append("suite " + ", ".join("`%s`" % s for s in meta["suites"]))
        if meta.get("runs") is not None:
            extra.append("%d run(s), %d error(s)" % (meta["runs"], meta.get("errors") or 0))
        if meta.get("claudeVersion"):
            extra.append("claude %s" % meta["claudeVersion"])
        if meta.get("overallScore") is not None:
            extra.append("overall score %s" % fmt(_num(meta["overallScore"])))
        if meta.get("costUsd") is not None:
            extra.append("cost %s" % fmt(_num(meta["costUsd"]), "USD"))
        if meta.get("runIds"):
            extra.append("Gerrit " + ", ".join("`hashtag:%s`" % t for t in meta["runIds"]))
        lines.append("- `%s`%s" % (meta["dir"], (" — " + ", ".join(extra)) if extra else ""))
    lines.append("")
    lines.append("Total: %d run(s), %d error(s)." % (report["runs_total"], report["errors_total"]))
    lines.append("")
    for suite, blk in report["suites"].items():
        lines += _suite_section(suite, blk)
    if report["split_quality"]:
        lines += _split_section(report["split_quality"])
    if report["rework"]:
        lines += _rework_section(report["rework"])
    if report["reviewer"]:
        lines += _review_section(report["reviewer"])
    if report["conventions"]:
        lines += _conventions_section(report["conventions"])
    if report["guardrails"]:
        spec = GUARDRAIL_METRICS + [(k, "bad: `%s`" % k, "count") for k in report.get("guardrail_extra_keys") or []]
        lines += _guardrails_section(report["guardrails"], spec)
    lines += _isolation_section(report["isolation"])
    lines += _footer()
    return "\n".join(lines)


# -------------------------------------------------------------------- main ----
def collect(results_dirs):
    rows, sources = [], []
    for d in results_dirs:
        r, meta = load_results_dir(d)
        rows.extend(r)
        sources.append(meta)
    return rows, sources


def build_report(rows, include_errors=False):
    report = aggregate(rows, include_errors)
    report["guardrail_extra_keys"] = [m[0] for m in _guardrail_spec(rows)[len(GUARDRAIL_METRICS):]]
    return report


def _json_ready(report, rows, sources):
    def clean_row(r):
        return {k: v for k, v in r.items() if k not in ("usage",)}
    out = {"generated": _dt.datetime.now(_dt.timezone.utc).isoformat(), "sources": sources,
           "runs": [clean_row(r) for r in rows]}
    out.update(report)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render docs/benchmark.md from eval results.")
    parser.add_argument("--results", nargs="+", metavar="DIR", default=None,
                        help="results directories (default: evals/results/*)")
    parser.add_argument("--include-errors", action="store_true",
                        help="also average runs that errored (kept out of the means by default)")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="markdown output (default: docs/benchmark.md)")
    parser.add_argument("--json", default=None, metavar="PATH", help="also write the merged JSON report")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    results_dirs = args.results if args.results else default_results_dirs(repo_root)
    missing = [d for d in results_dirs if not os.path.isdir(d)]
    if missing:
        print("collect.py: not a directory: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    rows, sources = collect(results_dirs)
    report = build_report(rows, args.include_errors)
    out_path = args.out or os.path.join(repo_root, "docs", "benchmark.md")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report, sources))
        fh.write("\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(_json_ready(report, rows, sources), fh, indent=2, sort_keys=True)
            fh.write("\n")
    print("collect.py: %d run(s), %d error(s) from %d result dir(s) -> %s"
          % (report["runs_total"], report["errors_total"], len(results_dirs), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
