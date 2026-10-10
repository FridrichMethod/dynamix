"""Run every tests/test_*.py module in its own isolated interpreter and fail if any of them fails.

    python -I tests/run_all.py [--tests-dir DIR]

Each module runs from the repository root in a fresh ``<this interpreter> -I`` process, as ``__main__``, exactly as
``python -I tests/test_x.py`` runs it (same ``sys.path``, ``__file__`` and ``__name__``), through a small loader that
also records every unittest run. A module passes only when it exits 0, ran at least one unittest suite, ran at least
one test (skipped tests count, as in unittest itself) and every suite it ran was successful. So it fails on a failure,
an error, a crash at import, unittest's "no tests ran" status 5, and also when it returns or exits 0 without running
any suite, e.g. a module whose tests never run because it lacks ``if __name__ == "__main__": unittest.main()``. The
runner also fails when no test module is discovered at all, so an emptied or misplaced test directory cannot pass
vacuously. Under GitHub Actions each module's output is grouped and every failure is annotated.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = "test_*.py"
NO_SUITE_STATUS = 4  # the module finished without running any unittest suite
NO_TESTS_STATUS = 5  # unittest's own status when a suite ran zero tests

# Runs one module as __main__ (runpy.run_path, as the interpreter runs a script) and checks that it really ran tests.
LOADER = f"""\
import runpy, sys, unittest
path = sys.argv[1]
sys.argv[:] = [path]
results = []
original_run = unittest.TextTestRunner.run
def recording_run(self, test):
    result = original_run(self, test)
    results.append(result)
    return result
unittest.TextTestRunner.run = recording_run
try:
    runpy.run_path(path, run_name="__main__")
except SystemExit as exit:
    if exit.code not in (0, None):
        raise
if not results:
    sys.stderr.write(path + ": no unittest suite ran; end the module with "
                     "'if __name__ == \\"__main__\\": unittest.main()'\\n")
    sys.exit({NO_SUITE_STATUS})
if sum(result.testsRun for result in results) == 0:
    sys.stderr.write(path + ": the unittest suite ran no tests\\n")
    sys.exit({NO_TESTS_STATUS})
if not all(result.wasSuccessful() for result in results):
    sys.exit(1)
"""


def discover(tests_dir: Path) -> list[Path]:
    """Sorted test modules in ``tests_dir`` (not recursive: fixtures may contain Python that is not a test)."""
    return sorted(path for path in tests_dir.glob(PATTERN) if path.is_file())


def command(test: Path) -> list[str]:
    """The isolated child process that runs ``test`` through the recording loader."""
    return [sys.executable, "-I", "-c", LOADER, str(test)]


def run(tests: list[Path], *, cwd: Path, annotate: bool) -> list[tuple[Path, int, float]]:
    """Run each module; returns ``(module, exit status, seconds)`` per module in order."""
    results = []
    for test in tests:
        label = test.relative_to(cwd).as_posix() if test.is_relative_to(cwd) else str(test)
        print(f"::group::{label}" if annotate else f"=== {label}", flush=True)
        started = time.monotonic()
        status = subprocess.run(command(test), cwd=cwd).returncode
        elapsed = time.monotonic() - started
        if annotate:
            print("::endgroup::", flush=True)
        print(f"{'ok' if status == 0 else 'FAILED'} {label} (exit {status}, {elapsed:.1f} s)", flush=True)
        results.append((test, status, elapsed))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tests-dir", type=Path, default=ROOT / "tests", help="directory holding test_*.py modules")
    args = parser.parse_args(argv)
    tests_dir = args.tests_dir.resolve()
    tests = discover(tests_dir)
    if not tests:
        print(f"No tests discovered: {tests_dir} has no {PATTERN} module", file=sys.stderr)
        return 2
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    cwd = ROOT if tests_dir.is_relative_to(ROOT) else tests_dir
    results = run(tests, cwd=cwd, annotate=annotate)
    failed = [(test, status) for test, status, _ in results if status != 0]
    total = sum(elapsed for _, _, elapsed in results)
    print(f"{len(results) - len(failed)} of {len(results)} test modules passed in {total:.1f} s", flush=True)
    for test, status in failed:
        label = test.relative_to(cwd).as_posix() if test.is_relative_to(cwd) else str(test)
        reason = {NO_SUITE_STATUS: "no unittest suite ran", NO_TESTS_STATUS: "no tests ran"}.get(status, "failed")
        if annotate:
            print(f"::error file={label}::{label}: {reason} (exit {status})", flush=True)
        print(f"FAILED: {label}: {reason} (exit {status})", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
