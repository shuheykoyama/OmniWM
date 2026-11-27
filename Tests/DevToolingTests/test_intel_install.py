import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Scripts/intel/install.sh"


@unittest.skipUnless(sys.platform == "darwin", "Uses ditto and the macOS shell tools")
class IntelInstallTests(unittest.TestCase):
    """Runs the installer against temporary directories; signing, the keychain and processes are faked."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-install-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.applications = self.root / "Applications"
        self.backups = self.root / "backups"
        self.tools = self.root / "tools"
        for directory in (self.home, self.applications, self.tools):
            directory.mkdir()
        self.signed = self.root / "codesign.log"
        self.fake("codesign", f'echo "$*" >> "{self.signed}"')
        self.fake("security", 'echo \'  1) 0000 "OmniWM Intel"\'')
        self.fake("pgrep", "exit 1")
        self.fake("lipo", "echo x86_64")

    def fake(self, name, body):
        path = self.tools / name
        path.write_text(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)

    def artifact(self, version):
        artifact = self.root / f"artifact-{version}/intel-app"
        app = self.root / f"build-{version}/OmniWM.app"
        for name in ("Contents/MacOS/OmniWM", "Contents/MacOS/omniwmctl",
                     "Contents/Frameworks/libOmniWMNowPlaying.dylib"):
            (app / name).parent.mkdir(parents=True, exist_ok=True)
            (app / name).write_text(version)
        artifact.mkdir(parents=True)
        archive = artifact / "OmniWM-x86_64.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", app, archive], check=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (artifact / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
        (artifact / "provenance.json").write_text(f'{{"source_commit": "{version}"}}\n')
        return artifact.parent

    def run_script(self, *arguments):
        environment = {**os.environ, "HOME": str(self.home), "PATH": f"{self.tools}:{os.environ['PATH']}",
                       "OMNIWM_APPLICATIONS_DIR": str(self.applications), "OMNIWM_BACKUP_DIR": str(self.backups)}
        environment.pop("OMNIWM_SIGNING_IDENTITY", None)
        return subprocess.run(["bash", str(SCRIPT), *map(str, arguments)], capture_output=True, text=True,
                              env=environment)

    def installed(self):
        return (self.applications / "OmniWM.app/Contents/MacOS/OmniWM").read_text()

    def settings(self, text=None):
        path = self.home / ".config/omniwm/settings.toml"
        if text is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return path.read_text()

    def replaced(self, version):
        found = [directory for directory in self.backups.iterdir()
                 if (directory / "OmniWM.app/Contents/MacOS/OmniWM").is_file()
                 and (directory / "OmniWM.app/Contents/MacOS/OmniWM").read_text() == version]
        self.assertEqual(len(found), 1, f"one backup of {version}")
        return found[0]

    def test_install_signs_inside_out_with_the_hardened_runtime(self):
        result = self.run_script(self.artifact("one"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed(), "one")
        signing = [line for line in self.signed.read_text().splitlines() if line.startswith("--force")]
        self.assertEqual([line.split("/")[-1] for line in signing],
                         ["libOmniWMNowPlaying.dylib", "omniwmctl", "OmniWM", "OmniWM.app"])
        self.assertTrue(all("--options runtime" in line and "--sign OmniWM Intel" in line for line in signing))
        self.assertEqual(["--entitlements" in line for line in signing], [False, False, True, True])
        self.assertFalse((self.applications / ".OmniWM.installing.app").exists())

    def test_update_keeps_the_old_app_and_settings_and_rollback_restores_both(self):
        self.assertEqual(self.run_script(self.artifact("one")).returncode, 0)
        self.settings("old")
        result = self.run_script(self.artifact("two"))
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = self.replaced("one")
        self.assertEqual((previous / ".config/omniwm/settings.toml").read_text(), "old")
        self.assertEqual(self.settings(), "old")

        self.settings("changed by the new version")
        result = self.run_script("--rollback", previous)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.installed(), self.settings()), ("one", "old"))
        newer = self.replaced("two")
        self.assertEqual((newer / ".config/omniwm/settings.toml").read_text(), "changed by the new version")
        self.assertEqual((previous / "OmniWM.app/Contents/MacOS/OmniWM").read_text(), "one")

    def test_nothing_changes_when_a_check_fails(self):
        self.assertEqual(self.run_script(self.artifact("one")).returncode, 0)
        damaged = self.artifact("two")
        with (damaged / "intel-app/OmniWM-x86_64.zip").open("ab") as stream:
            stream.write(b"x")
        good = self.artifact("three")
        for arguments, fake, message in (
                ((damaged,), None, "does not match its checksum"),
                ((good,), ("security", "echo '  0 valid identities found'"), "no code signing identity"),
                ((good,), ("pgrep", "echo 123"), "OmniWM is running"),
                ((good,), ("lipo", "echo arm64"), "not x86_64"),
                (("--rollback", self.root), None, "holds no OmniWM.app"),
                (("nonsense",), None, "expected a run id")):
            originals = {path: path.read_text() for path in self.tools.iterdir()}
            if fake:
                self.fake(*fake)
            result = self.run_script(*arguments)
            for path, body in originals.items():
                path.write_text(body)
            self.assertNotEqual(result.returncode, 0, message)
            self.assertIn(message, result.stderr)
            self.assertEqual(self.installed(), "one", message)


if __name__ == "__main__":
    unittest.main()
