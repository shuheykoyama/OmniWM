import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("verify_intel_ghostty", ROOT / "Scripts/intel/verify_ghostty.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
BUILDER = VERIFIER.builder


class IntelGhosttyPinTests(unittest.TestCase):
    def test_current_pins_have_a_compiler_hash_and_build_universally(self):
        pinned = BUILDER.pins()
        self.assertRegex(pinned["OMNIWM_GHOSTTY_SOURCE_COMMIT"], r"^[0-9a-f]{40}$")
        self.assertIn(pinned["OMNIWM_GHOSTTY_ZIG_VERSION"], BUILDER.ZIG_SHA256)
        targets = [flag for flag in BUILDER.build_flags() if "xcframework-target" in flag]
        self.assertEqual(targets, ["-Dxcframework-target=universal"])

    def test_members_sharing_a_name_are_each_checked(self):
        listing = "\n".join([
            "Archive : lib.a",
            "lib.a(ext.o):", "      cmd LC_BUILD_VERSION", " platform 1", "    minos 27.0",
            "lib.a(ext.o):", "      cmd LC_VERSION_MIN_MACOSX", "  version 13.0",
            "lib.a(bare.o):", "      cmd LC_SEGMENT_64",
        ])
        self.assertEqual(VERIFIER.minimum_versions(listing), [
            ["lib.a(ext.o)", ("1", "27.0")], ["lib.a(ext.o)", ("1", "13.0")], ["lib.a(bare.o)", None],
        ])
        with patch.object(VERIFIER, "run", return_value=listing):
            with self.assertRaisesRegex(RuntimeError, "requires macOS 27.0"):
                VERIFIER.check_deployment_target(Path("lib.a"))


@unittest.skipUnless(sys.platform == "darwin", "Uses Apple's clang, libtool, lipo, otool, ditto and xcodebuild")
class IntelGhosttyFrameworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-ghostty-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.include = self.root / "include"
        self.include.mkdir()
        (self.include / "ghostty.h").write_text(
            "#include <stdint.h>\n"
            "typedef struct { int build_mode; const char *version; uintptr_t version_len; } ghostty_info_s;\n"
            "ghostty_info_s ghostty_info(void);\n"
        )
        (self.include / "module.modulemap").write_text('module GhosttyKit { umbrella header "ghostty.h" }\n')

    def tool(self, *args):
        subprocess.run(["xcrun", *args], cwd=self.root, check=True, stdout=subprocess.DEVNULL)

    def framework(self, symbol="ghostty_info", minimum="13.0"):
        work = Path(tempfile.mkdtemp(dir=self.root))
        (work / "probe.c").write_text(
            f'#include <ghostty.h>\nghostty_info_s {symbol}(void) {{ return (ghostty_info_s){{0, "t", 1}}; }}\n'
        )
        slices = []
        for arch in ("arm64", "x86_64"):
            self.tool("clang", "-arch", arch, f"-mmacosx-version-min={minimum}", "-I", str(self.include),
                      "-c", str(work / "probe.c"), "-o", str(work / f"{arch}.o"))
            self.tool("libtool", "-static", "-o", str(work / f"{arch}.a"), str(work / f"{arch}.o"))
            slices.append(str(work / f"{arch}.a"))
        # Ghostty names its universal archive without the "lib" prefix.
        self.tool("lipo", "-create", *slices, "-output", str(work / "ghostty-internal.a"))
        universal = work / "universal/GhosttyKit.xcframework"
        self.tool("xcodebuild", "-create-xcframework", "-library", str(work / "ghostty-internal.a"),
                  "-headers", str(self.include), "-output", str(universal))
        output = work / "output"
        output.mkdir()
        return BUILDER.thin_framework(universal, output), work

    def verify(self, framework, **provenance_overrides):
        artifact = Path(tempfile.mkdtemp(dir=self.root))
        hashes = BUILDER.verify_framework(framework, self.root)
        archive = artifact / "GhosttyKit.xcframework.zip"
        archive_hash = BUILDER.upstream.package_framework(framework, archive, hashes)
        official = artifact / "official.zip"
        with zipfile.ZipFile(official, "w") as package:
            for name in BUILDER.HEADER_NAMES:
                package.write(self.include / name, f"GhosttyKit.xcframework/macos-arm64/Headers/{name}")
        pinned = {**BUILDER.pins(), "OMNIWM_GHOSTTY_DOWNLOAD_URL": official.as_uri(),
                  "OMNIWM_GHOSTTY_ZIP_SHA256": BUILDER.upstream.sha256(official)}
        (artifact / "provenance.json").write_text(json.dumps({
            "architecture": "x86_64",
            "source_revision": pinned["OMNIWM_GHOSTTY_SOURCE_COMMIT"],
            "builder_sha256": BUILDER.upstream.sha256(ROOT / "Scripts/intel/build_ghostty.py"),
            "zip_sha256": archive_hash,
            "zig_distribution": {"version": pinned["OMNIWM_GHOSTTY_ZIG_VERSION"]},
            "framework_files": hashes,
            **provenance_overrides,
        }))
        report = artifact / "verification.json"
        try:
            with patch.object(BUILDER, "pins", return_value=pinned), contextlib.redirect_stdout(io.StringIO()):
                VERIFIER.main(["--artifact-dir", str(artifact), "--report", str(report)])
        finally:
            self.report = json.loads(report.read_text())

    def test_universal_framework_is_thinned_and_passes_every_check(self):
        framework, _ = self.framework()
        self.verify(framework)
        self.assertTrue(self.report["passed"])
        self.assertEqual(set(self.report["checks"]), {
            "provenance", "xcframework_metadata", "headers_match_published", "member_architectures",
            "deployment_target", "link_probe",
        })
        self.assertEqual(self.report["checks"]["deployment_target"]["minimum_versions"], ["13.0"])

    def test_wrong_architecture_archive_is_rejected(self):
        framework, work = self.framework()
        shutil.copyfile(work / "arm64.a", framework / BUILDER.LIBRARY)
        with self.assertRaisesRegex(RuntimeError, "x86_64-only"):
            BUILDER.verify_framework(framework, self.root)

    def test_header_differing_from_source_or_published_framework_is_rejected(self):
        framework, _ = self.framework()
        official = self.root / "official.zip"
        with zipfile.ZipFile(official, "w") as package:
            for name in BUILDER.HEADER_NAMES:
                package.writestr(f"GhosttyKit.xcframework/macos-arm64/Headers/{name}", "different")
        with self.assertRaisesRegex(RuntimeError, "published GhosttyKit"):
            VERIFIER.check_official_headers(framework, official)
        (framework / BUILDER.HEADERS / "ghostty.h").write_text("int changed(void);\n")
        with self.assertRaisesRegex(RuntimeError, "pinned source"):
            BUILDER.verify_framework(framework, self.root)

    def test_artifact_built_from_another_revision_is_rejected(self):
        framework, _ = self.framework()
        with self.assertRaisesRegex(RuntimeError, "source_revision"):
            self.verify(framework, source_revision="0" * 40)
        self.assertFalse(self.report["passed"])

    def test_deployment_target_above_the_app_is_rejected_and_reported(self):
        framework, _ = self.framework(minimum="27.0")
        with self.assertRaisesRegex(RuntimeError, "above 26.0"):
            self.verify(framework)
        self.assertIn("above 26.0", self.report["error"])

    def test_unresolved_api_fails_the_link_probe(self):
        framework, _ = self.framework(symbol="not_ghostty_info")
        with self.assertRaises(subprocess.CalledProcessError), contextlib.redirect_stderr(io.StringIO()):
            self.verify(framework)
        self.assertNotIn("link_probe", self.report["checks"])
        self.assertFalse(self.report["passed"])


if __name__ == "__main__":
    unittest.main()
