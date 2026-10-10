"""tests/run_all.py (the CI unit-test step) propagates every failure and refuses to pass with no tests."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/run_all.py"

PASSING = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\nunittest.main()\n"
FAILING = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail('boom')\nunittest.main()\n"
ERRORING = "import unittest\nclass T(unittest.TestCase):\n    def test_err(self):\n        raise RuntimeError\nunittest.main()\n"
BROKEN_IMPORT = "import module_that_does_not_exist\n"
NO_TESTS = "import unittest\nunittest.main()\n"
SKIPPED = textwrap.dedent("""\
    import unittest
    @unittest.skip("not applicable")
    class T(unittest.TestCase):
        def test_x(self):
            pass
    unittest.main()
""")


class RunAllTests(unittest.TestCase):
    def run_all(self, modules: dict[str, str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as temporary:
            tests_dir = Path(temporary) / "tests"
            tests_dir.mkdir()
            for name, source in modules.items():
                (tests_dir / name).write_text(source, encoding="utf-8")
            return subprocess.run([sys.executable, "-I", str(RUNNER), "--tests-dir", str(tests_dir)],
                                  capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})

    def test_all_passing_modules_exit_zero(self):
        result = self.run_all({"test_a.py": PASSING, "test_b.py": PASSING, "test_c.py": SKIPPED})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("3 of 3 test modules passed", result.stdout)

    def test_any_failure_error_or_crash_is_nonzero_and_named(self):
        for label, source in (("failure", FAILING), ("error", ERRORING), ("import crash", BROKEN_IMPORT),
                              ("no tests ran", NO_TESTS)):
            with self.subTest(case=label):
                result = self.run_all({"test_a.py": PASSING, "test_z.py": source})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("FAILED: ", result.stderr)
                self.assertIn("test_z.py", result.stderr)
                self.assertNotIn("test_a.py", result.stderr)

    def test_failure_does_not_stop_later_modules(self):
        result = self.run_all({"test_a.py": FAILING, "test_b.py": PASSING})
        self.assertEqual(result.returncode, 1)
        self.assertIn("ok ", result.stdout)
        self.assertIn("1 of 2 test modules passed", result.stdout)

    def test_no_discovered_tests_is_a_failure(self):
        result = self.run_all({"helper.py": PASSING, "check_x.py": PASSING})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No tests discovered", result.stderr)

    def test_repository_suite_is_discovered(self):
        modules = sorted(path.name for path in (ROOT / "tests").glob("test_*.py"))
        self.assertIn("test_workflows_run_all.py", modules)
        self.assertGreater(len(modules), 5)


if __name__ == "__main__":
    unittest.main()
