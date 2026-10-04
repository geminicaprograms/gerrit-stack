#!/usr/bin/env python3
"""rescore_concerns.py — recompute split-quality metrics (purity, completeness, per-change
concerns) for implement runs whose chain-metrics.json predates a concern-map change.

The run workspaces are temporary, but every run pushed its chain to the demo Gerrit with the
hashtags bench-<case>-<arm>, run-<id>, var-<variant>, rep-<n>. This script reads each run's
record from aggregate-result.json, fetches the current-revision file list of every change it
pushed, applies the case's *current* `concerns:` map and rewrites the concern fields of
chain-metrics.json in place. The original is kept once as chain-metrics.orig.json.

Usage: python3 evals/metrics/rescore_concerns.py [--gerrit http://localhost:8080] RESULTS_DIR...
Auth: the ~/.netrc entry for the Gerrit host (read-only GETs). Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import netrc
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

_spec = importlib.util.spec_from_file_location("gs_eval_run", os.path.join(ROOT, "evals", "run.py"))
_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run)  # type: ignore[union-attr]


def gerrit_get(base: str, auth: str, path: str):
    req = urllib.request.Request(base + "/a" + path, headers={"Authorization": auth, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    if text.startswith(")]}'"):
        text = text[4:]
    return json.loads(text)


def load_concerns(case_yaml: str) -> list:
    with open(case_yaml, encoding="utf-8") as fh:
        cfg = _run.parse_yaml(fh.read()) or {}
    out = []
    for item in cfg.get("concerns") or []:
        if isinstance(item, dict) and item.get("name"):
            out.append((str(item["name"]), [re.compile(str(p)) for p in item.get("paths") or []]))
    return out


def score(chain_paths: list, concerns: list) -> dict:
    """Same definitions as scripts/chain-metrics.sh --concerns."""
    order = [n for n, _ in concerns]
    per_change, holders = [], {}
    for paths in chain_paths:
        names, unmapped = [], []
        for path in paths:
            hit = [n for n, rxs in concerns if any(r.search(path) for r in rxs)]
            if not hit:
                unmapped.append(path)
            for n in hit:
                if n not in names:
                    names.append(n)
        names.sort(key=order.index)
        for n in names:
            holders[n] = holders.get(n, 0) + 1
        per_change.append({"concerns": names, "unmapped_paths": unmapped, "paths": sorted(paths)})
    mapped = [c for c in per_change if c["concerns"]]
    seen = [n for n in order if n in holders]

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    return {
        "changes": per_change,
        "purity_pct": pct(sum(1 for c in mapped if len(c["concerns"]) == 1), len(mapped)),
        "completeness_pct": pct(sum(1 for n in seen if holders[n] == 1), len(seen)),
        "concerns_seen": seen,
        "concerns_defined": order,
    }


def ordered_chain(found: list) -> list:
    """ChangeInfos of one pushed chain, bottom to top (walk parents within the set)."""
    by_sha = {c["current_revision"]: c for c in found}
    parent = {}
    for sha, c in by_sha.items():
        parents = c["revisions"][sha]["commit"].get("parents") or []
        parent[sha] = parents[0]["commit"] if parents else None
    child = {p: s for s, p in parent.items() if p in by_sha}
    bottoms = [s for s, p in parent.items() if p not in by_sha]
    chain, cur = [], (bottoms[0] if bottoms else None)
    while cur and cur in by_sha and len(chain) < len(by_sha):
        chain.append(by_sha[cur])
        cur = child.get(cur)
    return chain


def rescore_dir(results_dir: str, base: str, auth: str) -> int:
    with open(os.path.join(results_dir, "aggregate-result.json"), encoding="utf-8") as fh:
        agg = json.load(fh)
    eval_dir = agg.get("evalDir") or ""
    if not os.path.isabs(eval_dir):
        eval_dir = os.path.join(ROOT, eval_dir)
    done = 0
    for case in agg.get("cases") or []:
        base_case = case.get("baseCase") or case.get("name", "").split("@")[0]
        case_yaml = os.path.join(eval_dir, base_case, "case.yaml")
        concerns = load_concerns(case_yaml) if os.path.exists(case_yaml) else []
        if not concerns:
            continue
        for arm, runs in (case.get("arms") or {}).items():
            for i, rec in enumerate(runs, 1):
                n = rec.get("run") if isinstance(rec.get("run"), int) else i
                run_dir = os.path.join(results_dir, "runs", case["name"], arm, str(n))
                cm_path = os.path.join(run_dir, "chain-metrics.json")
                tags = rec.get("hashtags") or []
                if not os.path.exists(cm_path) or not tags:
                    continue
                q = " ".join(["project:demo-plugin"] + [f"hashtag:{t}" for t in tags])
                found = gerrit_get(base, auth, "/changes/?n=100&o=CURRENT_REVISION&o=CURRENT_COMMIT&q="
                                   + urllib.parse.quote(q))
                chain = ordered_chain(found or [])
                if not chain:
                    print(f"  {case['name']}/{arm}/{n}: no changes found for {tags}", file=sys.stderr)
                    continue
                chain_paths = []
                for c in chain:
                    files = gerrit_get(base, auth, f"/changes/{c['_number']}/revisions/current/files")
                    chain_paths.append([p for p in files if not p.startswith("/")])
                new = score(chain_paths, concerns)
                with open(cm_path, encoding="utf-8") as fh:
                    cm = json.load(fh)
                orig = os.path.join(run_dir, "chain-metrics.orig.json")
                if not os.path.exists(orig):
                    shutil.copyfile(cm_path, orig)
                old = (cm.get("purity_pct"), cm.get("completeness_pct"))
                for k in ("purity_pct", "completeness_pct", "concerns_seen", "concerns_defined"):
                    cm[k] = new[k]
                cm["concerns_rescored_from"] = "gerrit"
                commits = [x for x in cm.get("changes") or [] if x.get("change_ids")]
                if len(commits) == len(new["changes"]):
                    for x, y in zip(commits, new["changes"]):
                        x.update(y)
                else:
                    cm["concerns_by_gerrit_change"] = new["changes"]
                with open(cm_path, "w", encoding="utf-8") as fh:
                    json.dump(cm, fh, indent=2)
                print(f"  {case['name']}/{arm}/{n}: purity {old[0]} -> {new['purity_pct']}, "
                      f"completeness {old[1]} -> {new['completeness_pct']} ({len(chain)} changes)")
                done += 1
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recompute split-quality concern metrics from the changes in Gerrit.")
    ap.add_argument("--gerrit", default="http://localhost:8080")
    ap.add_argument("results", nargs="+", metavar="RESULTS_DIR")
    opts = ap.parse_args(argv)
    host = urllib.parse.urlparse(opts.gerrit).hostname
    creds = netrc.netrc().authenticators(host)
    if not creds:
        print(f"no ~/.netrc entry for {host}", file=sys.stderr)
        return 2
    auth = "Basic " + base64.b64encode(f"{creds[0]}:{creds[2]}".encode()).decode()
    total = 0
    for d in opts.results:
        print(d)
        total += rescore_dir(d, opts.gerrit.rstrip("/"), auth)
    print(f"rescored {total} run(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
