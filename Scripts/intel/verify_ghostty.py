#!/usr/bin/env python3
"""Check an x86_64 GhosttyKit artifact before it is used, and record the results as JSON."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("build_intel_ghostty", ROOT / "Scripts/intel/build_ghostty.py")
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

require = builder.require
run = builder.run
sha256 = builder.upstream.sha256
DEPLOYMENT_TARGET = "26.0"
# Package.swift's linker settings, plus IOSurface, which the app gets from Swift autolinking.
LINK_FLAGS = [
    flag
    for framework in ("AppKit", "ApplicationServices", "Carbon", "IOSurface", "Metal", "MetalKit", "QuartzCore")
    for flag in ("-framework", framework)
] + ["-lz", "-lc++"]
PROBE = """#include <ghostty.h>
#include <stdio.h>

int main(void) {
    ghostty_info_s info = ghostty_info();
    printf("%.*s\\n", (int)info.version_len, info.version);
    return 0;
}
"""


def version_tuple(text):
    parts = [int(part) for part in text.split(".")]
    return tuple((parts + [0, 0])[:3])


def minimum_versions(load_commands):
    """List each Mach-O in `otool -l` output as [name, (platform, minimum version) or None].

    A list rather than a mapping, because archives may hold several members with one name.
    """
    members = []
    command = platform = None
    for line in load_commands.splitlines():
        header = re.fullmatch(r"(\S.*):", line)
        if header:
            members.append([header[1], None])
            command = platform = None
            continue
        fields = line.split()
        if len(fields) < 2 or not members:
            continue
        key, value = fields[0], fields[1]
        if key == "cmd":
            command = value
        elif command == "LC_BUILD_VERSION" and key == "platform":
            platform = value
        elif command == "LC_BUILD_VERSION" and key == "minos":
            members[-1][1] = (platform, value)
        elif command == "LC_VERSION_MIN_MACOSX" and key == "version":
            members[-1][1] = ("1", value)
    return members


def check_deployment_target(binary, limit=DEPLOYMENT_TARGET):
    members = minimum_versions(run("otool", "-l", binary, capture=True))
    versioned = [(name, found) for name, found in members if found]
    require(versioned, f"no minimum OS version found in {binary.name}")
    for name, (platform, minimum) in versioned:
        require(platform in ("1", "macos", "MACOS"), f"{name} targets platform {platform}, not macOS")
        require(version_tuple(minimum) <= version_tuple(limit), f"{name} requires macOS {minimum}, above {limit}")
    return {
        "members": len(members),
        "unversioned": sorted(name for name, found in members if not found),
        "minimum_versions": sorted({found[1] for _, found in versioned}, key=version_tuple),
    }


def check_member_architectures(archive, arch):
    headers = run("otool", "-hv", archive, capture=True)
    found = re.findall(r"^\s*MH_MAGIC\S*\s+(\S+)", headers, re.MULTILINE)
    require(found, "archive has no Mach-O members")
    require(set(found) == {arch.upper()}, f"archive members must all be {arch}: found {sorted(set(found))}")
    return {"members": len(found), "architecture": arch}


def check_link(framework_library, headers, work, arch=builder.ARCH):
    source = work / "probe.c"
    source.write_text(PROBE)
    probe = work / "probe"
    run("xcrun", "--sdk", "macosx", "clang", "-target", f"{arch}-apple-macos{DEPLOYMENT_TARGET}",
        "-Werror", "-I", headers, source, framework_library, *LINK_FLAGS, "-o", probe)
    require(run("lipo", "-archs", probe, capture=True) == arch, f"link probe is not {arch}-only")
    return {**check_deployment_target(probe), "architecture": arch, "link_flags": LINK_FLAGS}


def check_official_headers(framework, official):
    """The Intel framework must expose the same ABI headers as the published arm64 one."""
    prefix = f"{builder.upstream.FRAMEWORK}/{builder.upstream.HEADERS}"
    with zipfile.ZipFile(official) as package:
        for name in builder.HEADER_NAMES:
            require((framework / builder.HEADERS / name).read_bytes() == package.read(f"{prefix}/{name}"),
                    f"header differs from the published GhosttyKit: {name}")
    return {name: sha256(framework / builder.HEADERS / name) for name in builder.HEADER_NAMES}


def check_provenance(provenance, archive, pinned):
    expected = {
        "architecture": builder.ARCH,
        "source_revision": pinned["OMNIWM_GHOSTTY_SOURCE_COMMIT"],
        "builder_sha256": sha256(ROOT / "Scripts/intel/build_ghostty.py"),
        "zip_sha256": sha256(archive),
    }
    for key, value in expected.items():
        require(provenance.get(key) == value, f"provenance {key} does not match this checkout and artifact")
    require(provenance["zig_distribution"]["version"] == pinned["OMNIWM_GHOSTTY_ZIG_VERSION"],
            "provenance records a different Zig version")
    return expected


def verify(directory, work, checks):
    pinned = builder.pins()
    archive = directory / "GhosttyKit.xcframework.zip"
    provenance = json.loads((directory / "provenance.json").read_text())
    checks["provenance"] = check_provenance(provenance, archive, pinned)

    run("ditto", "-x", "-k", archive, work)
    framework = work / builder.upstream.FRAMEWORK
    hashes = builder.verify_layout(framework)
    require(hashes == provenance["framework_files"], "framework files differ from the provenance")
    checks["xcframework_metadata"] = hashes

    official = work / "official-arm64.zip"
    with urllib.request.urlopen(pinned["OMNIWM_GHOSTTY_DOWNLOAD_URL"], timeout=120) as response, official.open("xb") as stream:
        shutil.copyfileobj(response, stream)
    builder.upstream.verify_hash(official, pinned["OMNIWM_GHOSTTY_ZIP_SHA256"])
    checks["headers_match_published"] = check_official_headers(framework, official)

    library = framework / builder.LIBRARY
    checks["member_architectures"] = check_member_architectures(library, builder.ARCH)
    checks["deployment_target"] = check_deployment_target(library)
    require(checks["deployment_target"]["members"] == checks["member_architectures"]["members"],
            "not every archive member was checked for its minimum OS version")
    checks["link_probe"] = check_link(library, framework / builder.HEADERS, work)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / ".build/intel-ghostty")
    parser.add_argument("--report", type=Path, default=ROOT / ".build/intel-reports/verification.json")
    args = parser.parse_args(argv)
    report = {"passed": False, "checks": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="omniwm-intel-verify-") as work:
            verify(args.artifact_dir.resolve(), Path(work), report["checks"])
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
