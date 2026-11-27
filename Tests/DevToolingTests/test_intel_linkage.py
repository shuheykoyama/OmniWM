import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_test_linkage", ROOT / "Scripts/intel/verify_test_linkage.py")
LINKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINKAGE)

BUNDLE = "OmniWMTests.xctest/Contents/MacOS/OmniWMTests"
PLUGIN = "libOmniWMNowPlaying.dylib"
CONCURRENCY = "/usr/lib/swift/libswift_Concurrency.dylib"
CORE = "/usr/lib/swift/libswiftCore.dylib"
OTHER = "/usr/lib/swift/libswiftDispatch.dylib"


def product(weak, target=True):
    """A product that imports from the concurrency library, as linked without or with the flag."""
    imports = [["libswiftCore", "_swift_retain", False], ["libSystem", "____chkstk_darwin", True],
               ["libswift_Concurrency", "_swift_task_isCancelled", weak]]
    if target:
        imports.append(["libswift_Concurrency", LINKAGE.TARGET, weak])
    return {"imports": imports, "dylibs": {
        CONCURRENCY: "LC_LOAD_WEAK_DYLIB" if weak else "LC_LOAD_DYLIB", CORE: "LC_LOAD_DYLIB"}}


def plugin(weak):
    """A product that imports nothing from it; the flag adds a weak load all the same."""
    dylibs = {CORE: "LC_LOAD_DYLIB"}
    if weak:
        dylibs[CONCURRENCY] = "LC_LOAD_WEAK_DYLIB"
    return {"imports": [["libswiftCore", "_swift_retain", False]], "dylibs": dylibs}


class IntelTestLinkageTests(unittest.TestCase):
    def setUp(self):
        self.before = {BUNDLE: product(False), "omniwmctl": product(False, target=False), PLUGIN: plugin(False)}
        self.after = {BUNDLE: product(True), "omniwmctl": product(True, target=False), PLUGIN: plugin(True)}

    def violations(self, name, change, before=None):
        before, after = copy.deepcopy(before or self.before), copy.deepcopy(self.after)
        change(after[name])
        return LINKAGE.compare(before, after)["violations"]

    def test_weakened_imports_and_a_weak_load_in_a_product_without_imports_pass_and_are_recorded(self):
        report = LINKAGE.compare(self.before, self.after)
        self.assertEqual(report["violations"], [])
        self.assertEqual((report["target_before"], report["target_after"], report["workaround_needed"]),
                         ("strong", "weak", True))
        self.assertEqual(report["gained_weak_load"], [PLUGIN])
        added = report["products"][PLUGIN]
        self.assertEqual((added["libraries_before"], added["libraries_after"], added["newly_weak"]), (1, 2, 0))
        self.assertEqual(added["load_command_changes"], {CONCURRENCY: [None, "LC_LOAD_WEAK_DYLIB"]})
        bundle = report["products"][BUNDLE]
        self.assertEqual(bundle["load_command_changes"], {CONCURRENCY: ["LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB"]})
        self.assertFalse(bundle["added_weak_load"])

    def test_toolchain_that_no_longer_imports_the_symbol_strongly_makes_the_workaround_unneeded(self):
        self.before[BUNDLE]["imports"][-1][2] = True
        self.assertFalse(LINKAGE.compare(self.before, self.after)["workaround_needed"])

    def test_any_other_import_change_is_rejected(self):
        def other_weak(found):
            found["imports"][0][2] = True

        def no_longer_weak(found):
            found["imports"][1][2] = False

        def new_import(found):
            found["imports"].append(["libswiftCore", "_new", False])

        def lost_import(found):
            del found["imports"][0]

        def partly_strong(found):
            found["imports"][2][2] = False

        for change, message in ((other_weak, "other libraries became weak"), (no_longer_weak, "no_longer_weak"),
                                (new_import, "only_in_variant"), (lost_import, "only_in_baseline"),
                                (partly_strong, "still imported strongly")):
            self.assertIn(message, "; ".join(self.violations(BUNDLE, change)), change.__name__)

    def test_new_import_beside_the_added_weak_load_is_rejected(self):
        for imported in (["libswift_Concurrency", "_swift_task_create", True], ["libswiftCore", "_new", False]):
            found = self.violations(PLUGIN, lambda product: product["imports"].append(imported))
            self.assertIn(f"{PLUGIN}: imports changed (only_in_variant)", "; ".join(found))
        # A weak load only counts as an addition where nothing is imported from the library.
        found = self.violations(PLUGIN, lambda product: product["imports"].append(
            ["libswift_Concurrency", "_swift_task_create", True]))
        self.assertIn(f"load command changed: {CONCURRENCY} None -> LC_LOAD_WEAK_DYLIB", "; ".join(found))

    def test_any_other_load_change_is_rejected(self):
        def load(path, command):
            return lambda product: product["dylibs"].__setitem__(path, command)

        for name, change, message in (
                (PLUGIN, load(CONCURRENCY, "LC_LOAD_DYLIB"), "None -> LC_LOAD_DYLIB"),
                (PLUGIN, load(CONCURRENCY, "LC_REEXPORT_DYLIB"), "None -> LC_REEXPORT_DYLIB"),
                (PLUGIN, load(OTHER, "LC_LOAD_WEAK_DYLIB"), f"{OTHER} None -> LC_LOAD_WEAK_DYLIB"),
                (PLUGIN, load(CORE, "LC_LOAD_WEAK_DYLIB"), f"{CORE} LC_LOAD_DYLIB -> LC_LOAD_WEAK_DYLIB"),
                (BUNDLE, load(OTHER, "LC_LOAD_DYLIB"), f"{OTHER} None -> LC_LOAD_DYLIB"),
                (BUNDLE, lambda product: product["dylibs"].pop(CORE), f"{CORE} LC_LOAD_DYLIB -> None")):
            self.assertIn(message, "; ".join(self.violations(name, change)))

    def test_every_product_is_checked_and_reported(self):
        self.after["omniwmctl"]["imports"][0][2] = True
        self.after[PLUGIN]["dylibs"][OTHER] = "LC_LOAD_DYLIB"
        report = LINKAGE.compare(self.before, self.after)
        self.assertEqual(sorted({violation.split(":")[0] for violation in report["violations"]}),
                         [PLUGIN, "omniwmctl"])
        self.assertEqual(sorted(report["products"]), sorted(self.before))
        with self.assertRaisesRegex(RuntimeError, "products differ"):
            LINKAGE.compare(self.before, {BUNDLE: self.after[BUNDLE]})

    def test_missing_symbols_list_swift_libraries_only_with_their_strength(self):
        exports = {"libswiftCore": {"_swift_retain"}, "libswift_Concurrency": {"_swift_task_isCancelled"}}
        report = LINKAGE.missing_symbols(self.after, exports=exports.get)
        self.assertEqual(report["libraries"], ["libswiftCore", "libswift_Concurrency"])
        self.assertEqual(report["missing"], {
            f"libswift_Concurrency {LINKAGE.TARGET}": {"weak": True, "products": [BUNDLE]}})
        strong = LINKAGE.missing_symbols(self.before, exports={"libswiftCore": set()}.get)
        self.assertEqual(strong["absent_libraries"], ["libswift_Concurrency"])
        self.assertFalse(strong["missing"]["libswiftCore _swift_retain"]["weak"])

    def test_workflow_applies_the_flag_to_test_builds_only(self):
        workflow = (ROOT / ".github/workflows/intel.yml").read_text()
        flag = "-Xlinker -weak-lswift_Concurrency"
        test_build = workflow[workflow.index("\n  test-build:\n"):workflow.index("\n  test:\n")]
        # The plain build that is recorded, then the relink and the live check compile with the flag.
        self.assertEqual(test_build.count("swift build --build-tests"), 3)
        self.assertEqual(test_build.count(flag), 2)
        self.assertEqual(workflow.count("weak-lswift"), 2)
        self.assertNotIn("dead_strip_dylibs", workflow)
        for product_file in ("Package.swift", "Scripts/package-app.sh", "Makefile"):
            self.assertNotIn("weak-lswift", (ROOT / product_file).read_text())


@unittest.skipUnless(sys.platform == "darwin", "Links real Mach-O files with Apple's clang")
class IntelRealLinkageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-linkage-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.products = self.root / "Debug"
        self.bundle = self.products / "Probe.xctest/Contents/MacOS/Probe"
        self.bundle.parent.mkdir(parents=True)
        self.sdk = subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], capture_output=True, text=True,
                                  check=True).stdout.strip()
        (self.root / "uses.c").write_text(
            "extern _Bool swift_task_isCancelled(void *);\nint main(void) { return swift_task_isCancelled(0); }\n")
        (self.root / "plain.c").write_text("#include <stdlib.h>\nvoid *take(void) { return malloc(1); }\n")

    def link(self, *flags, plain_flags=()):
        common = ["xcrun", "clang", "-arch", "x86_64", "-mmacosx-version-min=26.0", f"-L{self.sdk}/usr/lib/swift"]
        subprocess.run([*common, self.root / "uses.c", "-lswift_Concurrency", *flags, "-o", self.bundle], check=True)
        subprocess.run([*common, "-dynamiclib", self.root / "plain.c", *flags, *plain_flags,
                        "-o", self.products / "libPlain.dylib"], check=True)

    def run_check(self, **relink):
        before, report = self.root / "before.json", self.root / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.link()
            self.assertEqual(LINKAGE.main(["record", str(self.products), "--report", str(before)]), 0)
            self.link("-Wl,-weak-lswift_Concurrency", **relink)
            status = LINKAGE.main(["compare", str(self.products), "--before", str(before), "--report", str(report)])
        return status, json.loads(report.read_text())

    def test_relinking_with_the_flag_adds_a_weak_load_to_the_library_that_imports_nothing(self):
        status, report = self.run_check()
        self.assertEqual((status, report["violations"]), (0, []))
        self.assertEqual(report["gained_weak_load"], ["libPlain.dylib"])
        self.assertEqual(report["products"]["libPlain.dylib"]["newly_weak"], 0)
        bundle = report["products"]["Probe.xctest/Contents/MacOS/Probe"]
        self.assertEqual(bundle["newly_weak"], 1)
        self.assertEqual(bundle["load_command_changes"][LINKAGE.LIBRARY_PATH],
                         ["LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB"])

    def test_a_second_added_library_in_the_real_link_is_rejected_and_reported(self):
        status, report = self.run_check(plain_flags=("-Wl,-weak-lswiftDispatch",))
        self.assertEqual(status, 1)
        self.assertIn("libswiftDispatch.dylib None -> LC_LOAD_WEAK_DYLIB", "; ".join(report["violations"]))


if __name__ == "__main__":
    unittest.main()
