"""tests/run_all.py (the CI unit-test step) propagates every failure and refuses to pass with no tests."""

import ast
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
GUARDED = PASSING.replace("unittest.main()\n", 'if __name__ == "__main__":\n    unittest.main()\n')
# A module that defines tests but never runs them exits 0 on its own; the runner must still fail it.
NO_MAIN_FAILING = (
    "import unittest\nclass T(unittest.TestCase):\n    def test_fails(self):\n        self.assertEqual(1, 2)\n"
)
NO_MAIN_PASSING = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
EXITS_ZERO_FIRST = (
    "import sys, unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail()\nsys.exit(0)\n"
)
NO_EXIT_FAILING = FAILING.replace("unittest.main()", "unittest.main(exit=False)")
NO_EXIT_PASSING = PASSING.replace("unittest.main()", "unittest.main(exit=False)")


class RunAllTests(unittest.TestCase):
    def run_all(self, modules: dict[str, str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as temporary:
            tests_dir = Path(temporary) / "tests"
            tests_dir.mkdir()
            for name, source in modules.items():
                (tests_dir / name).write_text(source, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-I", str(RUNNER), "--tests-dir", str(tests_dir)],
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin"},
            )

    def test_all_passing_modules_exit_zero(self):
        result = self.run_all(
            {
                "test_a.py": PASSING,
                "test_b.py": PASSING,
                "test_c.py": SKIPPED,
                "test_d.py": GUARDED,
                "test_e.py": NO_EXIT_PASSING,
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("5 of 5 test modules passed", result.stdout)

    def test_modules_run_as_main_exactly_as_when_invoked_directly(self):
        probe = textwrap.dedent("""\
            import sys, unittest
            from pathlib import Path
            class T(unittest.TestCase):
                def test_context(self):
                    self.assertEqual(__name__, "__main__")
                    self.assertEqual(Path(__file__).name, "test_probe.py")
                    self.assertEqual(sys.argv, [__file__])
                    self.assertTrue(sys.flags.isolated)
                    self.assertNotIn(str(Path(__file__).parent), sys.path)
            if __name__ == "__main__":
                unittest.main()
        """)
        result = self.run_all({"test_probe.py": probe})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_any_failure_error_or_crash_is_nonzero_and_named(self):
        for label, source in (
            ("failure", FAILING),
            ("error", ERRORING),
            ("import crash", BROKEN_IMPORT),
            ("no tests ran", NO_TESTS),
            ("failing test, no unittest.main", NO_MAIN_FAILING),
            ("passing test, no unittest.main", NO_MAIN_PASSING),
            ("exits 0 before running tests", EXITS_ZERO_FIRST),
            ("unittest.main(exit=False) with a failure", NO_EXIT_FAILING),
        ):
            with self.subTest(case=label):
                result = self.run_all({"test_a.py": PASSING, "test_z.py": source})
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("FAILED: ", result.stderr)
                self.assertIn("test_z.py", result.stderr)
                self.assertNotIn("test_a.py", result.stderr)

    def test_a_module_that_never_runs_its_tests_is_named(self):
        result = self.run_all({"test_nomain.py": NO_MAIN_FAILING})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("0 of 1 test modules passed", result.stdout)
        self.assertIn("FAILED: test_nomain.py: no unittest suite ran (exit 4)", result.stderr)
        self.assertIn('if __name__ == "__main__": unittest.main()', result.stderr)

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

    def test_every_repository_module_runs_its_tests_when_invoked_directly(self):
        """``python -I tests/test_x.py`` (how workers run one module) must not pass silently either."""
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                self.assertTrue(
                    any(main_guard_runs_unittest(node) for node in tree.body),
                    'end the module with: if __name__ == "__main__": unittest.main()',
                )


def main_guard_runs_unittest(node: ast.stmt) -> bool:
    """Whether ``node`` is ``if __name__ == "__main__":`` whose body calls ``unittest.main(...)``."""
    if not (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    ):
        return False
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "main"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "unittest"
        for statement in node.body
        for call in ast.walk(statement)
    )


if __name__ == "__main__":
    unittest.main()
