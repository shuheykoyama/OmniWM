#!/usr/bin/env python3
"""Archive the release build's dSYM for every Mach-O in the packaged app, matched by UUID."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("verify_intel_app", ROOT / "Scripts/intel/verify_app.py")
app_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(app_tool)

require = app_tool.require
run = app_tool.run
sha256 = app_tool.sha256
ARCH = app_tool.ARCH
UUID = re.compile(r"^UUID: ([0-9A-F-]{36}) \((\w+)\)", re.MULTILINE)


def uuid(path):
    found = UUID.findall(run("xcrun", "dwarfdump", "--uuid", path, capture=True))
    require(len(found) == 1 and found[0][1] == ARCH, f"{path.name} does not carry one {ARCH} UUID: {found}")
    return found[0][0]


def match(app, products):
    """Pair each Mach-O of the app with the dSYM the build left next to its product."""
    require(not list(app.rglob("*.dSYM")), "the app bundle must not contain debug symbols")
    symbols = {}
    for dsym in sorted(products.glob("*.dSYM")):
        # A dSYM of another architecture or of a product the app does not ship is of no interest.
        for found, arch in UUID.findall(run("xcrun", "dwarfdump", "--uuid", dsym, capture=True)):
            if arch == ARCH:
                symbols[found] = dsym
    binaries = {name: uuid(app / name) for name in app_tool.macho_files(app)}
    require(binaries, f"no Mach-O file in {app}")
    missing = sorted(name for name, found in binaries.items() if found not in symbols)
    require(not missing, f"no dSYM in {products} has the UUID of {missing}")
    return {name: (found, symbols[found]) for name, found in binaries.items()}


def collect(app, products, output, work):
    matched = match(app, products)
    staging = work / "dSYMs"
    staging.mkdir()
    for _, dsym in matched.values():
        run("ditto", dsym, staging / dsym.name)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"OmniWM-{ARCH}.dSYMs.zip"
    run("ditto", "-c", "-k", "--keepParent", staging, archive)
    # The archive is what gets kept, so read the UUIDs back out of it.
    run("ditto", "-x", "-k", archive, work / "roundtrip")
    for name, (found, dsym) in matched.items():
        require(uuid(work / "roundtrip" / staging.name / dsym.name) == found,
                f"the archived dSYM does not match {name}")
    return {
        "architecture": ARCH,
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "binaries": {name: {"uuid": found, "dsym": dsym.name, "sha256": sha256(app / name)}
                     for name, (found, dsym) in matched.items()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "dist/OmniWM.app")
    parser.add_argument("--products", type=Path, default=ROOT / ".build/out/Products/Release")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build/intel-symbols")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="omniwm-intel-symbols-") as raw:
        report = collect(args.app.resolve(), args.products.resolve(), output, Path(raw))
    report["source_commit"] = run("git", "rev-parse", "HEAD", cwd=ROOT, capture=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (output / "symbols.json").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
