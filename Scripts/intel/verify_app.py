#!/usr/bin/env python3
"""Check a packaged x86_64 OmniWM.app against the published arm64 one, then archive it with provenance."""

import argparse
import importlib.util
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("verify_intel_ghostty", ROOT / "Scripts/intel/verify_ghostty.py")
ghostty = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ghostty)

require = ghostty.require
run = ghostty.run
sha256 = ghostty.sha256
ARCH = "x86_64"
# Published OmniWM-v<version>.zip digests; the official app is the reference for contents and linkage.
OFFICIAL_SHA256 = {"0.8.0": "6f7fb3e211de3a83e772b131800680cb3d20d4c638811a1f02120fd225f03cc7"}
OFFICIAL_URL = "https://github.com/OmniNull/OmniWM/releases/download/v{0}/OmniWM-v{0}.zip"
SIGNATURE_FILES = ("Contents/_CodeSignature/", "Contents/CodeResources")
MACHO_MAGIC = {bytes.fromhex(magic) for magic in ("cffaedfe", "cefaedfe", "cafebabe", "cafebabf", "feedfacf", "feedface")}
SYSTEM_PREFIXES = ("/usr/lib/", "/System/Library/")
DEPENDENCY = re.compile(r"^\t(.+?) \(compatibility version [^,]+, current version [^,)]+((?:, [\w-]+)*)\)$", re.MULTILINE)
RPATH = re.compile(r"cmd LC_RPATH\n\s*cmdsize \d+\n\s*path (.+?) \(offset \d+\)")


def bundle_files(app):
    names = {path.relative_to(app).as_posix() for path in app.rglob("*") if path.is_file() or path.is_symlink()}
    return {name for name in names if not name.startswith(SIGNATURE_FILES)}


def macho_files(app):
    found = []
    for name in sorted(bundle_files(app)):
        with (app / name).open("rb") as stream:
            if stream.read(4) in MACHO_MAGIC:
                found.append(name)
    return found


def check_layout(app, official):
    ours, theirs = bundle_files(app), bundle_files(official)
    require(ours == theirs, "bundle contents differ from the published app: "
            f"missing {sorted(theirs - ours)}, unexpected {sorted(ours - theirs)}")
    require(not any(path.is_symlink() for path in app.rglob("*")), "app bundle must not contain symlinks")
    binaries = macho_files(app)
    require(binaries == macho_files(official), f"Mach-O files differ from the published app: {binaries}")
    return {"files": len(ours), "mach_o": binaries}


def check_info_plist(app, official, source=ROOT / "Info.plist"):
    ours, theirs, repository = (plistlib.loads(path.read_bytes()) for path in (
        app / "Contents/Info.plist", official / "Contents/Info.plist", source))
    ours.pop("OMNIWMGitHash", None)
    theirs.pop("OMNIWMGitHash", None)
    differing = sorted(key for key in ours.keys() | theirs.keys() if ours.get(key) != theirs.get(key))
    require(not differing, f"Info.plist differs from the published app in {differing}")
    require(ours["CFBundleShortVersionString"] == repository["CFBundleShortVersionString"],
            "app version differs from this checkout")
    require(ours["LSMinimumSystemVersion"] == ghostty.DEPLOYMENT_TARGET, "unexpected minimum system version")
    return {key: ours[key] for key in (
        "CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion", "LSMinimumSystemVersion")}


def linkage(binary):
    """Return the libraries a Mach-O loads (path to "is weak") and its run path search list."""
    identity = run("otool", "-D", binary, capture=True).splitlines()[1:]
    loaded = {path: "weak" in attributes.split(", ")
              for path, attributes in DEPENDENCY.findall(run("otool", "-L", binary, capture=True))
              if path not in identity}
    return loaded, RPATH.findall(run("otool", "-l", binary, capture=True))


def check_binary(binary, reference, arch=ARCH):
    require(run("lipo", "-archs", binary, capture=True) == arch, f"{binary.name} is not {arch}-only")
    target = ghostty.check_deployment_target(binary)
    require(not target["unversioned"], f"{binary.name} declares no minimum macOS version")
    loaded, rpaths = linkage(binary)
    expected, expected_rpaths = linkage(reference)
    require(loaded, f"could not read the libraries of {binary.name}")
    outside = sorted(path for path in loaded if not path.startswith(SYSTEM_PREFIXES))
    require(not outside, f"{binary.name} loads libraries that macOS does not provide: {outside}")
    added = sorted(loaded.keys() - expected.keys())
    require(not added, f"{binary.name} loads libraries the published build does not: {added}")
    hardened = sorted(path for path, weak in loaded.items() if expected[path] and not weak)
    require(not hardened, f"{binary.name} requires libraries the published build links weakly: {hardened}")
    foreign = sorted(set(rpaths) - set(expected_rpaths))
    require(not foreign, f"{binary.name} has run paths the published build does not: {foreign}")
    return {
        "architecture": arch,
        "minimum_versions": target["minimum_versions"],
        "libraries": len(loaded),
        "weak": sorted(path for path, weak in loaded.items() if weak),
        "absent_here": sorted(expected.keys() - loaded.keys()),
        "rpaths": rpaths,
        "sha256": sha256(binary),
    }


def codesign(*args):
    return subprocess.run(["codesign", *map(str, args)], check=True, capture_output=True)


def check_signature(app, entitlements=ROOT / "OmniWM.entitlements"):
    codesign("--verify", "--deep", "--strict", app)
    for name in macho_files(app):
        # Each nested binary on its own, in addition to the app seal that covers it.
        codesign("--verify", "--strict", app / name)
        details = codesign("--display", "--verbose=2", app / name).stderr.decode()
        require("Signature=adhoc" in details, f"{name} is not ad-hoc signed")
    expected = plistlib.loads(entitlements.read_bytes())
    for target in (app, app / "Contents/MacOS/OmniWM"):
        granted = codesign("--display", "--entitlements", "-", "--xml", target).stdout
        require(granted and plistlib.loads(granted) == expected, f"{target.name} has unexpected entitlements")
    return {"signature": "adhoc", "entitlements": sorted(expected)}


def verify(app, official, checks, arch=ARCH):
    checks["layout"] = check_layout(app, official)
    checks["info_plist"] = check_info_plist(app, official)
    checks["binaries"] = {name: check_binary(app / name, official / name, arch) for name in macho_files(app)}
    checks["signature"] = check_signature(app)


def download_official(version, work):
    require(version in OFFICIAL_SHA256, f"no published app digest pinned for {version}")
    archive = work / "official.zip"
    with urllib.request.urlopen(OFFICIAL_URL.format(version), timeout=120) as response, archive.open("xb") as stream:
        shutil.copyfileobj(response, stream)
    ghostty.builder.upstream.verify_hash(archive, OFFICIAL_SHA256[version])
    run("ditto", "-x", "-k", archive, work / "official")
    return work / "official/OmniWM.app"


def fingerprints(app):
    """Every file in the bundle, signature files included, with its content digest and permissions."""
    return {path.relative_to(app).as_posix(): (sha256(path), path.stat().st_mode & 0o777)
            for path in app.rglob("*") if path.is_file()}


def archive_app(app, output, work):
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"OmniWM-{ARCH}.zip"
    run("ditto", "-c", "-k", "--keepParent", app, archive)
    # The archive is what gets installed, so check what comes back out of it.
    run("ditto", "-x", "-k", archive, work / "roundtrip")
    restored = work / "roundtrip" / app.name
    check_signature(restored)
    require(fingerprints(restored) == fingerprints(app), "the archive does not restore the verified app")
    digest = sha256(archive)
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    return archive, digest


def toolchain():
    pin = (ROOT / ".swift-version").read_text().strip()
    resource_path = json.loads(run("swift", "-print-target-info", cwd=ROOT, capture=True))["paths"]["runtimeResourcePath"]
    date = pin.removeprefix("main-snapshot-")
    require(date in resource_path, f"swift on PATH is not the pinned {pin} toolchain: {resource_path}")
    return {
        "swift_pin": pin,
        "swift_version": run("swift", "--version", cwd=ROOT, capture=True),
        "swift_resource_path": resource_path,
        "xcode": run("xcodebuild", "-version", capture=True),
        "sdk_version": run("xcrun", "-sdk", "macosx", "--show-sdk-version", capture=True),
        "macos": run("sw_vers", "-productVersion", capture=True),
        "host_architecture": run("uname", "-m", capture=True),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "dist/OmniWM.app")
    parser.add_argument("--ghostty-provenance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build/intel-app")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "checks": {}}
    try:
        app = args.app.resolve()
        source = toolchain()
        version = plistlib.loads((ROOT / "Info.plist").read_bytes())["CFBundleShortVersionString"]
        with tempfile.TemporaryDirectory(prefix="omniwm-intel-app-") as raw:
            work = Path(raw)
            verify(app, download_official(version, work), report["checks"])
            archive, digest = archive_app(app, output, work)
        ghostty_source = json.loads(args.ghostty_provenance.read_text())
        provenance = {
            "schema_version": 1,
            "architecture": ARCH,
            "source_commit": run("git", "rev-parse", "HEAD", cwd=ROOT, capture=True),
            "version": version,
            "toolchain": source,
            "ghostty": {key: ghostty_source[key] for key in ("source_revision", "source_version", "zip_sha256", "framework_files")},
            "official_reference_sha256": OFFICIAL_SHA256[version],
            "zip": archive.name,
            "zip_sha256": digest,
        }
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
