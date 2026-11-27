import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_test_linkage", ROOT / "Scripts/intel/verify_test_linkage.py")
LINKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINKAGE)

BUNDLE = "OmniWMTests.xctest/Contents/MacOS/OmniWMTests"
CONCURRENCY = "/usr/lib/swift/libswift_Concurrency.dylib"
CORE = "/usr/lib/swift/libswiftCore.dylib"


def product(concurrency_weak, target=True):
    imports = [["libswiftCore", "_swift_retain", False], ["libSystem", "____chkstk_darwin", True],
               ["libswift_Concurrency", "_swift_task_isCancelled", concurrency_weak]]
    if target:
        imports.append(["libswift_Concurrency", LINKAGE.TARGET, concurrency_weak])
    return {"imports": imports, "dylibs": {
        CONCURRENCY: "LC_LOAD_WEAK_DYLIB" if concurrency_weak else "LC_LOAD_DYLIB", CORE: "LC_LOAD_DYLIB"}}


class IntelTestLinkageTests(unittest.TestCase):
    def setUp(self):
        self.before = {BUNDLE: product(False), "omniwmctl": product(False, target=False)}
        self.after = {BUNDLE: product(True), "omniwmctl": product(True, target=False)}

    def test_only_the_concurrency_library_becoming_weak_passes(self):
        report = LINKAGE.compare(self.before, self.after)
        self.assertEqual((report["target_before"], report["target_after"], report["workaround_needed"]),
                         ("strong", "weak", True))
        self.assertEqual(report["products"][BUNDLE]["newly_weak"], 2)
        self.assertEqual(report["products"][BUNDLE]["reloaded"], [CONCURRENCY])

    def test_toolchain_that_no_longer_imports_the_symbol_strongly_makes_the_workaround_unneeded(self):
        self.before[BUNDLE]["imports"][-1][2] = True
        self.assertFalse(LINKAGE.compare(self.before, self.after)["workaround_needed"])

    def test_any_other_change_fails(self):
        def other_weak(found):
            found["imports"][0][2] = True

        def no_longer_weak(found):
            found["imports"][1][2] = False

        def added(found):
            found["imports"].append(["libswiftCore", "_new", False])

        def partly_strong(found):
            found["imports"][2][2] = False

        def reloaded(found):
            found["dylibs"][CORE] = "LC_LOAD_WEAK_DYLIB"

        def lost_library(found):
            del found["dylibs"][CORE]

        for change, message in ((other_weak, "other libraries became weak"), (no_longer_weak, "no_longer_weak"),
                                (added, "only_in_variant"), (partly_strong, "still imported strongly"),
                                (reloaded, "other load commands"), (lost_library, "loaded libraries changed")):
            after = copy.deepcopy(self.after)
            change(after[BUNDLE])
            with self.assertRaisesRegex(RuntimeError, message, msg=change.__name__):
                LINKAGE.compare(self.before, after)
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
        for product_file in ("Package.swift", "Scripts/package-app.sh", "Makefile"):
            self.assertNotIn("weak-lswift", (ROOT / product_file).read_text())


if __name__ == "__main__":
    unittest.main()
