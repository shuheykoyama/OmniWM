import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("intel_collect_dsyms", ROOT / "Scripts/intel/collect_dsyms.py")
SYMBOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYMBOLS)


@unittest.skipUnless(sys.platform == "darwin", "Uses Apple's clang, dsymutil and dwarfdump")
class IntelDebugSymbolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="omniwm-intel-symbols-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.products = self.root / "Release"
        self.app = self.root / "OmniWM.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        self.products.mkdir()

    def build(self, name, value=0):
        source = self.root / f"{name}.c"
        source.write_text(f"int main(void) {{ return {value}; }}\n")
        object_file = self.root / f"{name}.o"
        subprocess.run(["xcrun", "clang", "-g", "-arch", "x86_64", "-c", source, "-o", object_file], check=True)
        product = self.products / name
        subprocess.run(["xcrun", "clang", "-arch", "x86_64", object_file, "-o", product], check=True)
        subprocess.run(["xcrun", "dsymutil", product, "-o", self.products / f"{name}.dSYM"], check=True)
        return product

    def ship(self, product):
        shipped = self.app / "Contents/MacOS" / product.name
        shipped.write_bytes(product.read_bytes())
        # Signing the shipped copy, as packaging does, must not change what identifies it.
        subprocess.run(["codesign", "--force", "--sign", "-", shipped], check=True, capture_output=True)

    def collect(self):
        work = self.root / "work"
        work.mkdir()
        return SYMBOLS.collect(self.app, self.products, self.root / "out", work)

    def test_every_shipped_binary_is_archived_with_its_matching_symbols(self):
        for name, value in (("OmniWM", 0), ("omniwmctl", 1)):
            self.ship(self.build(name, value))
        self.build("unshipped", 2)
        report = self.collect()
        self.assertEqual(sorted(report["binaries"]), ["Contents/MacOS/OmniWM", "Contents/MacOS/omniwmctl"])
        self.assertNotEqual(*[found["uuid"] for found in report["binaries"].values()])
        listing = subprocess.run(["zipinfo", "-1", self.root / "out" / report["archive"]],
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn("dSYMs/OmniWM.dSYM/Contents/Resources/DWARF/OmniWM", listing)
        self.assertNotIn("unshipped", listing)

    def test_stale_or_missing_symbols_are_rejected(self):
        self.ship(self.build("OmniWM", 0))
        self.build("OmniWM", 7)
        with self.assertRaisesRegex(RuntimeError, "no dSYM .* has the UUID of \\['Contents/MacOS/OmniWM'\\]"):
            SYMBOLS.match(self.app, self.products)

    def test_symbols_inside_the_app_are_rejected(self):
        self.ship(self.build("OmniWM"))
        (self.app / "Contents/MacOS/OmniWM.dSYM").mkdir()
        with self.assertRaisesRegex(RuntimeError, "must not contain debug symbols"):
            SYMBOLS.match(self.app, self.products)


if __name__ == "__main__":
    unittest.main()
