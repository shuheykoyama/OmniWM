import importlib.util
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = load("verify_intel_app", "Scripts/intel/verify_app.py")
LOCALIZATION = load("localization_arch", "Scripts/localization.py")


def script(path, source):
    path.write_text("#!/bin/bash\nset -eu\n" + source)
    path.chmod(0o755)


def environment(arch, **extra):
    values = {key: value for key, value in os.environ.items() if not key.startswith("OMNIWM_") and key != "MAKEFLAGS"}
    if arch:
        values["OMNIWM_ARCH"] = arch
    return {**values, **extra}


class IntelLocalizationArchitectureTests(unittest.TestCase):
    def test_compiler_metadata_build_follows_the_selected_architecture(self):
        for selected, expected in ((None, "arm64"), ("x86_64", "x86_64")):
            commands = []

            def record(command, **_):
                commands.append(command)
                if "build" in command:
                    raise StopIteration
                return subprocess.CompletedProcess(command, 0, stdout="/ghostty\n")

            with patch.object(LOCALIZATION.subprocess, "run", side_effect=record), \
                    patch.dict(os.environ, environment(selected), clear=True), self.assertRaises(StopIteration):
                LOCALIZATION.compiler_stringsdata()
            self.assertEqual(commands[-1][commands[-1].index("--arch") + 1], expected)


@unittest.skipUnless(sys.platform == "darwin", "Uses Apple's build tools")
class IntelBuildScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-scripts-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / "Scripts"
        self.scripts.mkdir()

    def invoke(self, *command, arch=None, **extra):
        return subprocess.run(command, cwd=self.root, env=environment(arch, **extra), capture_output=True, text=True)

    def archive(self, arch):
        directory = self.root / f"Frameworks/GhosttyKit.xcframework/macos-{arch}"
        directory.mkdir(parents=True)
        (self.root / "probe.c").write_text("int probe(void) { return 0; }\n")
        for command in (("clang", "-arch", arch, "-c", "probe.c", "-o", f"{arch}.o"),
                        ("libtool", "-static", "-o", str(directory / "libghostty-internal.a"), f"{arch}.o")):
            subprocess.run(["xcrun", *command], cwd=self.root, check=True)
        return VERIFIER.sha256(directory / "libghostty-internal.a")

    def test_preflight_defaults_to_the_pinned_arm64_archive_and_needs_a_digest_for_intel(self):
        shutil.copy2(ROOT / "Scripts/ghostty-preflight.sh", self.scripts)
        arm, intel = self.archive("arm64"), self.archive("x86_64")
        (self.scripts / "build-metadata.env").write_text(
            "OMNIWM_GHOSTTY_ARCHIVE_RELATIVE_PATH=Frameworks/GhosttyKit.xcframework/macos-arm64/libghostty-internal.a\n"
            f"OMNIWM_GHOSTTY_ARCHIVE_SHA256={arm}\n")
        preflight = ("bash", "Scripts/ghostty-preflight.sh", "print-library-dir")

        for arch, extra, directory in ((None, {}, "macos-arm64"),
                                       ("x86_64", {"OMNIWM_GHOSTTY_X86_64_ARCHIVE_SHA256": intel}, "macos-x86_64")):
            result = self.invoke(*preflight, arch=arch, **extra)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip().endswith(directory), result.stdout)

        for arch, extra, message in (
                ("x86_64", {}, "x86_64 builds need"),
                ("x86_64", {"OMNIWM_GHOSTTY_X86_64_ARCHIVE_SHA256": arm}, "digest mismatch"),
                ("riscv64", {}, "unsupported OMNIWM_ARCH")):
            result = self.invoke(*preflight, arch=arch, **extra)
            self.assertNotEqual(result.returncode, 0, message)
            self.assertIn(message, result.stderr)

        shutil.copyfile(self.root / "Frameworks/GhosttyKit.xcframework/macos-arm64/libghostty-internal.a",
                        self.root / "Frameworks/GhosttyKit.xcframework/macos-x86_64/libghostty-internal.a")
        result = self.invoke(*preflight, arch="x86_64", OMNIWM_GHOSTTY_X86_64_ARCHIVE_SHA256=arm)
        self.assertIn("must be x86_64-only", result.stderr)

    def test_make_build_follows_the_selected_architecture(self):
        shutil.copy2(ROOT / "Makefile", self.root)
        shutil.copy2(ROOT / "Scripts/dev-tools.env", self.scripts)
        script(self.scripts / "ghostty-preflight.sh", 'echo /ghostty\n')
        swift = self.root / "swift"
        script(swift, 'printf "%s\\n" "$*"\n')
        for arch, expected in ((None, "arm64"), ("x86_64", "x86_64")):
            result = self.invoke("make", "build", f"SWIFT={swift}", arch=arch)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"build --arch {expected}", result.stdout)

    def test_packaging_builds_the_selected_architecture(self):
        shutil.copy2(ROOT / "Scripts/package-app.sh", self.scripts)
        script(self.scripts / "ghostty-preflight.sh", 'echo /ghostty\n')
        tools = self.root / "tools"
        tools.mkdir()
        for name in ("make", "lipo"):
            script(tools / name, "exit 0\n")
        # Stop right after the build arguments are known.
        script(tools / "swift", 'printf "%s\\n" "$*"\nexit 3\n')
        for arch, expected in ((None, "arm64"), ("x86_64", "x86_64")):
            result = self.invoke("bash", "Scripts/package-app.sh", "release", "dev", arch=arch,
                                 PATH=f"{tools}:{os.environ['PATH']}")
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn(f"build -c release --arch {expected}", result.stdout)


@unittest.skipUnless(sys.platform == "darwin", "Uses Apple's clang, codesign, otool, lipo and ditto")
class IntelAppVerificationTests(unittest.TestCase):
    NOW_PLAYING = "Contents/Frameworks/libOmniWMNowPlaying.dylib"
    MAIN = "Contents/MacOS/OmniWM"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-app-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.c").write_text("int main(void) { return 0; }\n")
        (self.root / "library.c").write_text("int now_playing(void) { return 0; }\n")
        self.official = self.app("official", "arm64", git_hash="upstream")
        self.intel = self.app("intel", "x86_64", git_hash="fork")

    def compile(self, output, arch, *flags, source="main.c", minimum="26.0"):
        subprocess.run(["xcrun", "clang", "-arch", arch, f"-mmacosx-version-min={minimum}", source, *flags,
                        "-o", str(output)], cwd=self.root, check=True)

    def app(self, name, arch, git_hash):
        app = self.root / name / "OmniWM.app"
        for directory in ("Contents/MacOS", "Contents/Frameworks", "Contents/Resources"):
            (app / directory).mkdir(parents=True)
        info = plistlib.loads((ROOT / "Info.plist").read_bytes())
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({**info, "OMNIWMGitHash": git_hash}))
        (app / "Contents/Resources/AppIcon.icns").write_text("icon")
        self.compile(app / self.MAIN, arch, "-weak_framework", "CoreFoundation")
        self.compile(app / "Contents/MacOS/omniwmctl", arch)
        self.compile(app / self.NOW_PLAYING, arch, "-dynamiclib",
                     "-install_name", "@rpath/libOmniWMNowPlaying.dylib", source="library.c")
        self.sign(app)
        return app

    def sign(self, app, entitlements=ROOT / "OmniWM.entitlements"):
        for target in (app / self.NOW_PLAYING, app / "Contents/MacOS/omniwmctl", app / self.MAIN, app):
            command = ["codesign", "--force", "--sign", "-"]
            if target in (app, app / self.MAIN) and entitlements:
                command += ["--entitlements", str(entitlements)]
            subprocess.run([*command, str(target)], check=True, capture_output=True)

    def assert_rejected(self, message):
        with self.assertRaisesRegex(RuntimeError, message):
            VERIFIER.verify(self.intel, self.official, {})

    def test_matching_intel_app_passes_and_survives_archiving(self):
        checks = {}
        VERIFIER.verify(self.intel, self.official, checks)
        self.assertEqual(set(checks), {"layout", "info_plist", "binaries", "signature"})
        self.assertEqual(len(checks["binaries"]), 3)
        self.assertTrue(any("CoreFoundation" in path for path in checks["binaries"][self.MAIN]["weak"]))
        output = self.root / "output"
        archive, digest = VERIFIER.archive_app(self.intel, output, self.root / "work")
        self.assertEqual((output / "SHA256SUMS").read_text(), f"{digest}  {archive.name}\n")

    def test_missing_resource_is_rejected(self):
        (self.intel / "Contents/Resources/AppIcon.icns").unlink()
        self.sign(self.intel)
        self.assert_rejected("missing \\['Contents/Resources/AppIcon.icns'\\]")

    def test_arm64_binary_in_the_intel_app_is_rejected(self):
        shutil.copy2(self.official / "Contents/MacOS/omniwmctl", self.intel / "Contents/MacOS/omniwmctl")
        self.sign(self.intel)
        self.assert_rejected("omniwmctl is not x86_64-only")

    def test_newer_minimum_macos_is_rejected(self):
        self.compile(self.intel / "Contents/MacOS/omniwmctl", "x86_64", minimum="27.0")
        self.sign(self.intel)
        self.assert_rejected("requires macOS 27.0")

    def test_linkage_beyond_the_published_build_is_rejected(self):
        cases = (
            (("-weak_framework", "CoreFoundation", "-framework", "CoreAudio"), "published build does not: .*CoreAudio"),
            (("-framework", "CoreFoundation"), "links weakly"),
            (("-weak_framework", "CoreFoundation", "-Wl,-rpath,/opt/toolchain/lib"), "run paths"),
        )
        for flags, message in cases:
            with self.subTest(message=message):
                self.compile(self.intel / self.MAIN, "x86_64", *flags)
                self.sign(self.intel)
                self.assert_rejected(message)

    def test_missing_entitlements_are_rejected(self):
        self.sign(self.intel, entitlements=None)
        self.assert_rejected("unexpected entitlements")

    def test_modified_nested_code_or_resource_breaks_the_signature(self):
        for index, relative in enumerate((self.NOW_PLAYING, "Contents/Resources/AppIcon.icns")):
            with self.subTest(relative=relative):
                app = self.app(f"tampered-{index}", "x86_64", git_hash="fork")
                VERIFIER.check_signature(app)
                with (app / relative).open("ab") as stream:
                    stream.write(b"tampered")
                with self.assertRaises(subprocess.CalledProcessError):
                    VERIFIER.check_signature(app)

    def test_archive_that_drops_a_permission_is_rejected(self):
        real = VERIFIER.run

        def archive_then_strip(*args, **options):
            result = real(*args, **options)
            if args[:2] == ("ditto", "-x"):
                (Path(args[-1]) / "OmniWM.app/Contents/Resources/AppIcon.icns").chmod(0o600)
            return result

        with patch.object(VERIFIER, "run", side_effect=archive_then_strip):
            with self.assertRaisesRegex(RuntimeError, "does not restore"):
                VERIFIER.archive_app(self.intel, self.root / "output", self.root / "work")

    def test_unpinned_swift_toolchain_is_rejected(self):
        with patch.object(VERIFIER, "run", return_value='{"paths": {"runtimeResourcePath": "/Xcode/usr/lib/swift"}}'):
            with self.assertRaisesRegex(RuntimeError, "not the pinned"):
                VERIFIER.toolchain()

    def test_version_without_a_published_digest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "no published app digest"):
            VERIFIER.download_official("9.9.9", self.root)


if __name__ == "__main__":
    unittest.main()
