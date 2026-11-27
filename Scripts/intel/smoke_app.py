#!/usr/bin/env python3
"""Launch a packaged OmniWM.app for a short time on a disposable CI machine and record what happens.

Nothing is granted or changed on the machine; the app runs with whatever permissions it has.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


DYLD_FAILURES = ("dyld[", "Library not loaded", "Symbol not found")
CRASH_DIRECTORIES = (Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports"))
MODES = ("direct", "launch-services")


def process_table():
    listing = subprocess.run(["ps", "-axo", "pid=,command="], check=True, capture_output=True, text=True).stdout
    rows = (line.strip().partition(" ") for line in listing.splitlines())
    return {int(pid): command.strip() for pid, _, command in rows}


def processes(executable):
    """PIDs running the given executable."""
    return [pid for pid, command in process_table().items()
            if command == executable or command.startswith(executable + " ")]


def referencing(app):
    """Processes that run, or were handed, something inside the bundle, such as helpers the app started."""
    inside = f"{app}/Contents/"
    return {pid: command for pid, command in process_table().items() if inside in command and pid != os.getpid()}


def alive(pid):
    state = subprocess.run(["ps", "-o", "state=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def crash_reports(directories, since):
    found = []
    for directory in directories:
        try:
            found += [path for path in directory.iterdir()
                      if path.name.lower().startswith("omniwm") and path.stat().st_mtime >= since]
        except (FileNotFoundError, PermissionError) as error:
            print(f"crash reports not readable: {error}", file=sys.stderr)
    return sorted(found)


def check_help(cli, output):
    result = subprocess.run([str(cli), "--help"], capture_output=True, text=True, timeout=60)
    output.write_text(result.stdout + result.stderr)
    problems = []
    if result.returncode != 0:
        problems.append(f"omniwmctl --help exited with {result.returncode}")
    if "omniwmctl" not in result.stdout:
        problems.append("omniwmctl --help printed no usage text")
    return {"exit": result.returncode, "lines": len(result.stdout.splitlines()), "problems": problems}


def start(app, executable, mode, log, appear):
    """Return (pid, process); process is only available when this script is the parent."""
    if mode == "direct":
        with log.open("wb") as stream:
            process = subprocess.Popen([str(executable)], stdout=stream, stderr=subprocess.STDOUT)
        return process.pid, process
    # Through LaunchServices the app is responsible for itself, so it gets no permissions of the caller.
    log.touch()
    opened = subprocess.run(["open", "-n", "--stdout", str(log), "--stderr", str(log), str(app)],
                            capture_output=True, text=True, timeout=60)
    if opened.returncode != 0:
        log.write_text(f"open failed with {opened.returncode}: {opened.stderr}")
        return None, None
    deadline = time.monotonic() + appear
    while time.monotonic() < deadline:
        found = processes(str(executable))
        if found:
            return found[0], None
        time.sleep(0.5)
    return None, None


def stop(pid, process, grace):
    if not alive(pid):
        return "already exited"
    for name, number in (("terminated", signal.SIGTERM), ("killed", signal.SIGKILL)):
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            return name
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if process is not None:
                process.poll()
            if not alive(pid):
                return name
            time.sleep(0.2)
    return "still running"


def launch(app, mode, output, seconds, window_lister=None, appear=20, grace=10, crash_directories=CRASH_DIRECTORIES,
           report_delay=10):
    executable = app / "Contents/MacOS/OmniWM"
    log = output / f"{mode}.log"
    since = time.time()
    result = {"mode": mode, "problems": []}
    pid, process = start(app, executable, mode, log, appear)
    if pid is None:
        result["problems"].append("the app process did not appear")
    else:
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and alive(pid):
                if process is not None and process.poll() is not None:
                    break
                time.sleep(0.5)
            running = alive(pid) and (process is None or process.poll() is None)
            result["alive_after_seconds"] = seconds if running else None
            if not running:
                code = None if process is None else process.poll()
                result["problems"].append(f"the app exited within {seconds} seconds (status {code})")
            elif window_lister:
                listed = subprocess.run([str(window_lister), str(pid)], capture_output=True, text=True, timeout=60)
                result["windows"] = (listed.stdout + listed.stderr).splitlines()
        finally:
            result["stopped"] = stop(pid, process, grace)
            if process is not None:
                result["exit_status"] = process.poll()
        if result["stopped"] in ("killed", "still running"):
            result["problems"].append(f"the app did not end on SIGTERM: {result['stopped']}")
    text = log.read_text(errors="replace") if log.exists() else ""
    result["dyld_failures"] = [line for line in text.splitlines() if any(mark in line for mark in DYLD_FAILURES)]
    if result["dyld_failures"]:
        result["problems"].append("the dynamic loader reported a failure")
    # Crash reports are written a little after the process ends.
    if result["problems"]:
        time.sleep(report_delay)
    reports = crash_reports(crash_directories, since)
    for report in reports:
        try:
            (output / report.name).write_bytes(report.read_bytes())
        except OSError as error:
            result.setdefault("unreadable_reports", []).append(f"{report}: {error}")
    result["crash_reports"] = [report.name for report in reports]
    if reports:
        result["problems"].append("the system wrote a crash report")
    leftovers = referencing(app)
    result["leftover_processes"] = sorted(leftovers.values())
    if leftovers:
        result["problems"].append("processes from the bundle were still running afterwards")
        # Clean up so that the next launch starts from nothing.
        for leftover in leftovers:
            stop(leftover, None, grace)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=20, help="how long each launch has to stay running")
    parser.add_argument("--window-lister", type=Path, help="built from list_windows.swift")
    parser.add_argument("--mode", action="append", choices=MODES, help="default: both")
    args = parser.parse_args(argv)
    app, output = args.app.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {"help": check_help(app / "Contents/MacOS/omniwmctl", output / "omniwmctl-help.txt")}
    report["launches"] = [launch(app, mode, output, args.seconds, args.window_lister)
                          for mode in args.mode or MODES]
    problems = report["help"]["problems"] + [f"{found['mode']}: {problem}"
                                             for found in report["launches"] for problem in found["problems"]]
    report["problems"] = problems
    report["passed"] = not problems
    (output / "smoke.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
