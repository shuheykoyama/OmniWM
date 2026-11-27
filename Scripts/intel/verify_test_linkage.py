#!/usr/bin/env python3
"""Check what weak-linking libswift_Concurrency changes in the x86_64 test products.

A workaround for the pinned toolchain, whose _Concurrency interface makes a macOS 27 runtime function a
strong import. `record` the products linked without the flag, `compare` them after relinking with it, and
list the Swift runtime symbols that the macOS of this machine does not export (`missing`).
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("intel_cancellation_probe", "Scripts/intel/cancellation-probe/probe.py")
products = load("intel_verify_products", "Scripts/intel/verify_products.py")

LIBRARY = probe.LIBRARY
LIBRARY_PATH = f"/usr/lib/swift/{LIBRARY}.dylib"
TARGET = probe.TARGET
BUNDLE = ".xctest/"
# Every load command that makes a Mach-O depend on a library.
LOAD = re.compile(r"cmd (LC_(?:LOAD\w*|LAZY_LOAD|REEXPORT)_DYLIB)\n\s*cmdsize \d+\n\s*name (\S+)")


class LinkageError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise LinkageError(message)


def run(*command):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, check=True,
                          timeout=900).stdout


def describe(binary):
    """What a Mach-O imports, and how it loads each library."""
    imports = probe.imports(run("xcrun", "dyld_info", "-imports", binary))
    require(imports, f"could not read the imports of {binary.name}")
    return {
        "imports": sorted([library, symbol, weak] for (library, symbol), weak in imports.items()),
        "dylibs": {name: command for command, name in LOAD.findall(run("xcrun", "otool", "-l", binary))},
    }


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): describe(path) for path in products.products(directory)}


def as_map(imports):
    return {(library, symbol): weak for library, symbol, weak in imports}


def check_product(before, after):
    """What relinking changed in one product, and every change the workaround does not allow.

    Allowed: imports of the concurrency library turning weak, its load command turning weak, and a product
    that imports nothing from it gaining a weak load of it, which `-weak-l` adds to every link.
    """
    old, new = as_map(before["imports"]), as_map(after["imports"])
    changed = probe.compare(old, new)
    violations = [f"imports changed ({key}): {changed[key][:5]}"
                  for key in ("only_in_baseline", "only_in_variant", "no_longer_weak") if changed[key]]
    foreign = [entry for entry in changed["newly_weak"] if not entry.startswith(LIBRARY + " ")]
    if foreign:
        violations.append(f"imports of other libraries became weak: {foreign[:5]}")
    concurrency = {symbol: weak for (library, symbol), weak in new.items() if library == LIBRARY}
    strong = sorted(symbol for symbol, weak in concurrency.items() if not weak)
    if strong:
        violations.append(f"{LIBRARY} is still imported strongly: {strong[:5]}")
    uses = any(library == LIBRARY for library, _ in [*old, *new])
    loads = {path: [before["dylibs"].get(path), after["dylibs"].get(path)]
             for path in sorted(before["dylibs"].keys() | after["dylibs"].keys())}
    loads = {path: [was, now] for path, (was, now) in loads.items() if was != now}
    added = False
    for path, (was, now) in loads.items():
        weakened = path == LIBRARY_PATH and was == "LC_LOAD_DYLIB" and now == "LC_LOAD_WEAK_DYLIB"
        gained = path == LIBRARY_PATH and was is None and now == "LC_LOAD_WEAK_DYLIB" and not uses
        added = added or gained
        if not (weakened or gained):
            violations.append(f"load command changed: {path} {was} -> {now}")
    record = {"imports": len(new), "concurrency_imports": len(concurrency),
              "newly_weak": len(changed["newly_weak"]), "libraries_before": len(before["dylibs"]),
              "libraries_after": len(after["dylibs"]), "load_command_changes": loads, "added_weak_load": added}
    return record, violations


def compare(before, after):
    """Check every product, so that one report shows every difference."""
    require(before.keys() == after.keys(), f"products differ: {sorted(before.keys() ^ after.keys())}")
    bundles = [name for name in before if BUNDLE in name]
    require(bundles, "no XCTest bundle among the products")
    report = {"products": {}, "violations": []}
    for name in sorted(before):
        report["products"][name], violations = check_product(before[name], after[name])
        report["violations"] += [f"{name}: {violation}" for violation in violations]
    report["gained_weak_load"] = [name for name, found in report["products"].items() if found["added_weak_load"]]
    for key, found in (("target_before", before), ("target_after", after)):
        states = [weak for name in bundles
                  for (library, symbol), weak in as_map(found[name]["imports"]).items() if symbol == TARGET]
        report[key] = "absent" if not states else "weak" if all(states) else "strong"
    if report["target_after"] == "strong":
        report["violations"].append(f"{TARGET} is still imported strongly")
    # Without the flag a fixed toolchain would no longer import it strongly.
    report["workaround_needed"] = report["target_before"] == "strong"
    return report


def exported(library):
    """Symbols a Swift runtime library of this macOS exports, or None if it is not there."""
    listed = subprocess.run(["xcrun", "dyld_info", "-exports", f"/usr/lib/swift/{library}.dylib"],
                            capture_output=True, text=True, timeout=300)
    names = set()
    for line in listed.stdout.splitlines():
        words = line.split()
        if len(words) > 1 and (words[0].startswith("0x") or words[0] == "[re-export]"):
            names.add(words[1])
    return names or None


def missing_symbols(described, exports=exported):
    cache, found = {}, {}
    for name, product in described.items():
        for library, symbol, weak in product["imports"]:
            if not library.startswith("libswift"):
                continue
            if library not in cache:
                cache[library] = exports(library)
            if cache[library] is None or symbol not in cache[library]:
                found.setdefault(f"{library} {symbol}", {"weak": True, "products": []})
                found[f"{library} {symbol}"]["weak"] &= weak
                found[f"{library} {symbol}"]["products"].append(name)
    return {"libraries": sorted(cache), "absent_libraries": sorted(k for k, v in cache.items() if v is None),
            "missing": found}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "compare", "missing"))
    parser.add_argument("directory", type=Path, help="the build's products")
    parser.add_argument("--before", type=Path, help="compare: what `record` wrote")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    found = snapshot(args.directory.resolve())
    if args.command == "record":
        write(args.report, found)
        print(f"recorded {len(found)} products")
        return 0
    if args.command == "compare":
        require(args.before, "compare needs --before")
        report = compare(json.loads(args.before.read_text()), found)
        if not report["workaround_needed"]:
            print(f"::warning::{TARGET} is not a strong import without the flag; the workaround can be removed")
        for name in report["gained_weak_load"]:
            print(f"::notice::{name} imports nothing from {LIBRARY} and now loads it weakly")
        for violation in report["violations"]:
            print(f"::error::{violation}")
    else:
        # Reported only: a strong import that is missing already stops the bundle from loading.
        report = missing_symbols(found)
        for entry, details in report["missing"].items():
            print(f"::notice::not exported by this macOS ({'weak' if details['weak'] else 'STRONG'} import): {entry}")
    write(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("violations") else 0


if __name__ == "__main__":
    sys.exit(main())
