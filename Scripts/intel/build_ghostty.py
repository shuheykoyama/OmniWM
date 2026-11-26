#!/usr/bin/env python3
"""Build the pinned Ghostty revision as an x86_64-only GhosttyKit.xcframework on an arm64 macOS host."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import sys


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("build_ghosttykit", ROOT / "Scripts/build-ghosttykit.py")
upstream = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(upstream)

ARCH = "x86_64"
LIBRARY = Path(f"macos-{ARCH}") / upstream.LIBRARY.name
HEADERS = Path(f"macos-{ARCH}/Headers")
HEADER_NAMES = ("ghostty.h", "module.modulemap")
ZIG_SHA256 = {"0.17.0": "b607e9b9234790a008116ae5bdb71c6243b84b9fb42a53a9e70fde41c06c536a"}
require = upstream.require
run = upstream.run


def pins(path=ROOT / "Scripts/dev-tools.env"):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and line[0] != "#")


def build_flags():
    flags = [flag for flag in upstream.build_flags() if not flag.startswith("-Dxcframework-target=")]
    # Ghostty only cross-builds x86_64 as half of its universal macOS library.
    return [*flags, "-Dxcframework-target=universal"]


def thin_framework(universal, output):
    libraries = list(universal.glob("*/*.a"))
    require(len(libraries) == 1, "universal framework must contain exactly one static library")
    thin = output / "thin"
    thin.mkdir()
    library = thin / upstream.LIBRARY.name
    run("lipo", libraries[0], "-thin", ARCH, "-output", library)
    framework = output / upstream.FRAMEWORK
    run("xcodebuild", "-create-xcframework", "-library", library,
        "-headers", libraries[0].parent / "Headers", "-output", framework)
    return framework


def verify_layout(framework):
    with (framework / "Info.plist").open("rb") as stream:
        libraries = plistlib.load(stream).get("AvailableLibraries", [])
    require(len(libraries) == 1, "framework must contain exactly one library")
    library = libraries[0]
    require(
        library.get("LibraryIdentifier") == f"macos-{ARCH}"
        and library.get("LibraryPath") == LIBRARY.name
        and library.get("HeadersPath") == "Headers"
        and library.get("SupportedArchitectures") == [ARCH]
        and library.get("SupportedPlatform") == "macos",
        f"framework must contain the {ARCH} macOS static library",
    )
    expected = {Path("Info.plist"), LIBRARY, *(HEADERS / name for name in HEADER_NAMES)}
    actual = {path.relative_to(framework) for path in framework.rglob("*") if path.is_file()}
    require(actual == expected, "unexpected framework contents")
    require(not any(path.is_symlink() for path in framework.rglob("*")), "framework must not contain symlinks")
    require(run("lipo", "-archs", framework / LIBRARY, capture=True) == ARCH, f"archive must be {ARCH}-only")
    return {str(path): upstream.sha256(framework / path) for path in sorted(expected)}


def verify_framework(framework, source):
    hashes = verify_layout(framework)
    for name in HEADER_NAMES:
        require(
            (framework / HEADERS / name).read_bytes() == (source / "include" / name).read_bytes(),
            f"framework header differs from pinned source: {name}",
        )
    return hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build/intel-ghostty", help="new directory for source, tools and artifacts")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".build/ghosttykit-cache")
    args = parser.parse_args(argv)
    require(platform.system() == "Darwin" and platform.machine() == "arm64", "an arm64 macOS host is required")
    require(shutil.which("msgfmt"), "gettext's msgfmt must be on PATH")
    run("xcrun", "-sdk", "macosx", "-find", "metal", capture=True)
    run("xcrun", "-sdk", "macosx", "-find", "metallib", capture=True)
    output = args.output_dir.resolve()
    require(not output.exists(), f"output directory already exists; choose a new path: {output}")
    output.mkdir(parents=True)
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)

    pinned = pins()
    revision = pinned["OMNIWM_GHOSTTY_SOURCE_COMMIT"]
    source = output / "source"
    run("git", "init", "--quiet", source)
    run("git", "remote", "add", "origin", upstream.REPOSITORY, cwd=source)
    run("git", "fetch", "--depth=1", "--no-tags", "origin", revision, cwd=source)
    run("git", "checkout", "--quiet", "-b", "main", "FETCH_HEAD", cwd=source)
    upstream.verify_source(source, revision)

    version, zig_version = upstream.source_versions(source)
    require(zig_version == pinned["OMNIWM_GHOSTTY_ZIG_VERSION"], f"pinned source requires Zig {zig_version}")
    distribution = upstream.zig_distribution(zig_version)
    require(distribution["sha256"] == ZIG_SHA256.get(zig_version), f"no matching SHA-256 pin for Zig {zig_version}")
    zig = upstream.install_zig(distribution, output, cache)
    flags = build_flags()
    environment = {
        **os.environ,
        "ZIG_LIB_DIR": str(zig.parent / "lib"),
        "ZIG_LOCAL_CACHE_DIR": str(cache / "local"),
        "ZIG_GLOBAL_CACHE_DIR": str(cache / "global"),
    }
    print(f"Building Ghostty {revision} with Zig {zig_version}", flush=True)
    run(zig, "build", *flags, cwd=source, env=environment)
    upstream.verify_source(source, revision)

    framework = thin_framework(source / "macos" / upstream.FRAMEWORK, output)
    hashes = verify_framework(framework, source)
    short_revision = run("git", "log", "--format=%h", "-1", cwd=source, capture=True)
    embedded = upstream.embedded_version(framework / LIBRARY, f"{version}-main+{short_revision}")

    archive = output / "GhosttyKit.xcframework.zip"
    archive_hash = upstream.package_framework(framework, archive, hashes)
    provenance = {
        "schema_version": 1,
        "architecture": ARCH,
        "source_repository": upstream.REPOSITORY,
        "source_revision": revision,
        "source_version": embedded,
        "zig_distribution": distribution,
        "xcode": run("xcodebuild", "-version", capture=True),
        "sdk_version": run("xcrun", "-sdk", "macosx", "--show-sdk-version", capture=True),
        "macos": run("sw_vers", "-productVersion", capture=True),
        "host_architecture": platform.machine(),
        "metal": run("xcrun", "-sdk", "macosx", "metal", "--version", capture=True).splitlines()[0],
        "gettext": run("msgfmt", "--version", capture=True).splitlines()[0],
        "build_arguments": ["build", *flags],
        "framework_files": hashes,
        "zip": archive.name,
        "zip_sha256": archive_hash,
        "builder_sha256": upstream.sha256(Path(__file__)),
    }
    manifest = output / "provenance.json"
    manifest.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(f"ZIP: {archive}\nSHA-256: {archive_hash}\nProvenance: {manifest}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, upstream.subprocess.CalledProcessError) as error:
        print(f"intel-ghostty: {error}", file=sys.stderr)
        sys.exit(1)
