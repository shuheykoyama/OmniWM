#!/usr/bin/env python3
"""Summarize XCTest logs: which tests passed, failed, were skipped, never finished or never ran."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


EVENT = re.compile(r"Test Case '-\[([\w.]+) (\w+)\]' (started|passed|failed|skipped)")
LISTED = re.compile(r"^[\w.]+/\w+$", re.MULTILINE)
# Worst first: a test reported twice counts as its worst result.
STATES = ("failed", "unfinished", "skipped", "passed")
SHOWN = 40


def read_log(text):
    """Map "Module.Suite/test" to every result it reported; empty means it started and never reported."""
    results = {}
    for suite, test, event in EVENT.findall(text):
        reported = results.setdefault(f"{suite}/{test}", [])
        if event != "started":
            reported.append(event)
    return results


def state(reported):
    return min(reported, key=STATES.index) if reported else "unfinished"


def merge(logs):
    """One state per test, and the tests that more than one log or run reported."""
    reported, repeated = {}, set()
    for results in logs.values():
        for name, found in results.items():
            if name in reported or len(found) > 1:
                repeated.add(name)
            reported.setdefault(name, []).extend(found)
    return {name: state(found) for name, found in reported.items()}, sorted(repeated)


def counts(states):
    return {wanted: sum(1 for found in states if found == wanted) for wanted in STATES}


def summarize(logs, expected=None, baseline=None, required=()):
    """expected is the test list in its original order, so that duplicates in it show."""
    results, repeated = merge(logs)
    report = {
        "logs": {label: counts([state(found) for found in log.values()]) for label, log in logs.items()},
        "total": {"tests": len(results), **counts(results.values())},
        "repeated": repeated,
        **{wanted: sorted(name for name, found in results.items() if found == wanted) for wanted in STATES[:-1]},
        "required": {suite: sum(1 for name, found in results.items()
                                if found == "passed" and name.startswith(suite + "/")) for suite in required},
    }
    if expected is not None:
        listed = set(expected)
        report["listed"] = len(listed)
        report["listed_twice"] = sorted(name for name, times in Counter(expected).items() if times > 1)
        report["not_run"] = sorted(listed - results.keys())
        report["unlisted"] = sorted(results.keys() - listed)
    if baseline is not None:
        reference, _ = merge(baseline)
        report["baseline"] = {
            "tests": len(reference),
            "only_in_baseline": sorted(reference.keys() - results.keys()),
            "only_here": sorted(results.keys() - reference.keys()),
        }
        if expected is not None:
            report["baseline"]["not_listed"] = sorted(reference.keys() - set(expected))
            report["baseline"]["listed_only"] = sorted(set(expected) - reference.keys())
    problems = [key for key in (*STATES[:-1], "not_run", "unlisted", "repeated", "listed_twice") if report.get(key)]
    if not results:
        problems.append("no test results")
    problems += [f"no passing test in {suite}" for suite, passed in report["required"].items() if not passed]
    report["problems"] = problems
    report["passed_gate"] = not problems
    return report


def markdown(report):
    lines = [f"## XCTest results: {'passed' if report['passed_gate'] else 'NOT passed'}", "",
             "| log | passed | failed | unfinished | skipped |", "|---|---|---|---|---|"]
    rows = [*report["logs"].items(), ("total", report["total"])]
    lines += [f"| {label} | {c['passed']} | {c['failed']} | {c['unfinished']} | {c['skipped']} |" for label, c in rows]
    if "listed" in report:
        lines += ["", f"Listed in the test bundle: {report['listed']}. Not run: {len(report['not_run'])}."]
    for suite, passed in report["required"].items():
        lines.append(f"- {suite}: {passed} passed")
    for key in ("failed", "unfinished", "skipped", "not_run", "unlisted", "repeated", "listed_twice"):
        if report.get(key):
            lines += ["", f"### {key} ({len(report[key])})", *[f"- `{name}`" for name in report[key][:SHOWN]]]
    return "\n".join(lines) + "\n"


def read_logs(paths):
    return {path.stem: read_log(path.read_text(encoding="utf-8", errors="replace")) for path in paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--expected", type=Path, help="output of `swift test list`; every listed test must report")
    parser.add_argument("--baseline", nargs="+", type=Path, help="logs of another run to compare test names with")
    parser.add_argument("--require", action="append", default=[], help="suite that must have a passing test")
    parser.add_argument("--output", type=Path, help="write the full report as JSON")
    parser.add_argument("--markdown", type=Path, help="append a readable summary to this file")
    args = parser.parse_args(argv)
    expected = None
    if args.expected:
        expected = LISTED.findall(args.expected.read_text(encoding="utf-8"))
        if not expected:
            parser.error(f"{args.expected} lists no tests")
    report = summarize(read_logs(args.logs), expected,
                       read_logs(args.baseline) if args.baseline else None, args.require)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    text = markdown(report)
    print(text)
    if args.markdown:
        with args.markdown.open("a", encoding="utf-8") as stream:
            stream.write(text)
    return 0 if report["passed_gate"] else 1


if __name__ == "__main__":
    sys.exit(main())
