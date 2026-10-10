"""Run every tests/test_*.py module in its own isolated interpreter and fail if any of them fails.

    python -I tests/run_all.py [--tests-dir DIR]

Each module runs as ``<this interpreter> -I <module>`` from the repository root, so it sees exactly what CI sees.
The exit status is nonzero when any module exits nonzero (failure, error, crash at import, or unittest's
"no tests ran" status 5) and when no test module is discovered at all, so an emptied or misplaced test directory
cannot pass vacuously. Under GitHub Actions each module's output is grouped and every failure is annotated.
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


def discover(tests_dir: Path) -> list[Path]:
    """Sorted test modules in ``tests_dir`` (not recursive: fixtures may contain Python that is not a test)."""
    return sorted(path for path in tests_dir.glob(PATTERN) if path.is_file())


def run(tests: list[Path], *, cwd: Path, annotate: bool) -> list[tuple[Path, int, float]]:
    """Run each module; returns ``(module, exit status, seconds)`` per module in order."""
    results = []
    for test in tests:
        label = test.relative_to(cwd).as_posix() if test.is_relative_to(cwd) else str(test)
        print(f"::group::{label}" if annotate else f"=== {label}", flush=True)
        started = time.monotonic()
        status = subprocess.run([sys.executable, "-I", str(test)], cwd=cwd).returncode
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
        if annotate:
            print(f"::error file={label}::{label} failed (exit {status})", flush=True)
        print(f"FAILED: {label} (exit {status})", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
