#!/usr/bin/env python3
"""Check that built products are x86_64, and record their minimum macOS and what they load."""

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("verify_intel_app", ROOT / "Scripts/intel/verify_app.py")
app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(app)

require = app.require


def products(directory):
    """Linked Mach-O files; object files and debug symbol bundles are not loaded by a test run."""
    found = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix == ".o" or ".dSYM" in path.as_posix():
            continue
        with path.open("rb") as stream:
            if stream.read(4) in app.MACHO_MAGIC:
                found.append(path)
    return found


def verify(directory, arch=app.ARCH):
    binaries = {}
    for path in products(directory):
        name = path.relative_to(directory).as_posix()
        require(app.run("lipo", "-archs", path, capture=True) == arch, f"{name} is not {arch}-only")
        # Recorded, not required: whether the test bundle runs on the app's minimum macOS is investigated.
        target = app.ghostty.check_deployment_target(path, limit="999")
        newest = target["minimum_versions"][-1]
        loaded, rpaths = app.linkage(path)
        binaries[name] = {
            "architecture": arch,
            "minimum_versions": target["minimum_versions"],
            "newer_than_app_minimum": app.ghostty.version_tuple(newest)
            > app.ghostty.version_tuple(app.ghostty.DEPLOYMENT_TARGET),
            "outside_macos": sorted(p for p in loaded if not p.startswith(app.SYSTEM_PREFIXES)),
            "rpaths": rpaths,
            "sha256": app.sha256(path),
        }
    require(any(".xctest/" in name for name in binaries), f"no XCTest bundle executable in {directory}")
    return binaries


def unchanged(directory, binaries, expected, since=None):
    """A test run must use the products as built: same files, same content, not rewritten."""
    digests = {name: found["sha256"] for name, found in binaries.items()}
    built = {name: found["sha256"] for name, found in expected.items()}
    differing = sorted(name for name in digests.keys() | built.keys() if digests.get(name) != built.get(name))
    require(not differing, f"products differ from the build: {differing}")
    if since is not None:
        rewritten = sorted(name for name in digests if (directory / name).stat().st_mtime > since)
        require(not rewritten, f"products were written again during the run: {rewritten}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expect", type=Path, help="report of the build; the products must be identical")
    parser.add_argument("--unchanged-since", type=Path, help="marker file; no product may be newer")
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    binaries = verify(directory)
    if args.expect:
        since = args.unchanged_since.stat().st_mtime if args.unchanged_since else None
        unchanged(directory, binaries, json.loads(args.expect.read_text()), since)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(binaries, indent=2, sort_keys=True) + "\n")
    print(json.dumps(binaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
