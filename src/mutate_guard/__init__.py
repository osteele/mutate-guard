"""Apply one mutation, run a check, restore, and report a verdict that cannot lie.

A guard is tested when breaking it breaks a check. Running that by hand is two
edits and a test command, and it has five ways to report success while
measuring nothing:

  1. The mutation does not apply -- a pattern that matches nothing, or a
     conditional that skips -- and the untouched file passes.
  2. The mutation does not compile, and the toolchain's failure is
     indistinguishable from the guard firing.
  3. The check runs a target that excludes the mutated file, so nothing
     relevant executes and the suite passes.
  4. The status is read through a pipe, so the last command in the pipeline
     reports instead of the check. Checks run under `bash -o pipefail`.
  5. Python runs stale bytecode: it trusts a cached .pyc whose recorded source
     mtime (whole seconds) and size match, so a same-size mutation applied or
     restored within one second executes the other version. Each run gets its
     own empty PYTHONPYCACHEPREFIX, and nothing is written into the tree.

Each produces a false statement about the code: a present guard called missing,
or a missing guard called present. This runs the sequence so none of them is
reachable.

Usage:

    mutate-guard --file src/x.rs --old 'a && b' --new 'a' --test 'cargo test -q'
                 [--expect-test NAME] [--cwd DIR] [--count N]

Verdicts, and the exit status of each:

    KILLED (0)    the check fails with the mutation and passes without it
    SURVIVED (1)  the check passes with the mutation applied
    INVALID (2)   the mutation did not build, so nothing was measured
    REFUSED (3)   the mutation does not apply exactly --count times
    BASELINE (4)  the check was already failing before the mutation
    ERROR (5)     the file or the tree did not return to its original state

SURVIVED means the guard is absent or the check does not reach it. When the
mutated run reports the same tests as the baseline, the second is likely, and
the verdict says so.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: Substrings meaning the toolchain rejected the mutated source. A build
#: failure is not a kill: it says the mutation was malformed, not that a guard
#: caught it.
BUILD_FAILURE_MARKERS = (
    "[build failed]",
    "build failed",
    "error[E",
    "cannot find",
    "undefined:",
    "not enough arguments",
    "too many arguments",
    "SyntaxError",
    "IndentationError",
    "unresolved import",
    "no such module",
)

#: Lines reporting how much ran, compared between the two runs to detect a
#: mutation outside the check's scope.
SUMMARY_PATTERNS = (
    re.compile(r"^ok\s+\S+", re.M),
    re.compile(r"^test result:\s+.*?(?=;\s*finished in|$)", re.M),
    re.compile(r"^Ran \d+ tests?", re.M),
    re.compile(r"^\d+ passed", re.M),
)


def find_bash() -> str:
    """Locate the bash executable, supporting standard Git for Windows locations."""
    found = shutil.which("bash")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if Path(candidate).is_file():
                return candidate
    return "bash"


def run(command: str, cwd: Path) -> tuple[int, str]:
    """Run a check and return its own status, whichever pipeline stage failed."""
    with tempfile.TemporaryDirectory(prefix="mutate-guard-pycache-") as cache:
        finished = subprocess.run(
            [find_bash(), "-o", "pipefail", "-c", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPYCACHEPREFIX": cache},
        )
    return finished.returncode, finished.stdout + finished.stderr


def looks_like_build_failure(output: str) -> bool:
    return any(marker in output for marker in BUILD_FAILURE_MARKERS)


def scope_fingerprint(output: str) -> tuple[str, ...]:
    """What the check reported running, for comparison across runs."""
    found: list[str] = []
    for pattern in SUMMARY_PATTERNS:
        found.extend(pattern.findall(output))
    return tuple(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="file to mutate")
    parser.add_argument("--old", required=True, help="exact text to replace")
    parser.add_argument("--new", required=True, help="replacement text")
    parser.add_argument("--test", required=True, help="check to run")
    parser.add_argument("--cwd", default=".", help="directory to run the check in")
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="occurrences --old must have in the file (default 1)",
    )
    parser.add_argument(
        "--expect-test",
        default=None,
        help="test expected to fail; a warning is printed when it does not",
    )
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        print(f"REFUSED  directory does not exist: {cwd}")
        return 3

    path = Path(args.file)
    if not path.is_absolute():
        path = cwd / path

    if not path.is_file():
        print(f"REFUSED  file does not exist: {path}")
        return 3

    if args.count <= 0:
        print(f"REFUSED  --count must be positive, got {args.count}")
        return 3

    if not args.old:
        print("REFUSED  --old cannot be empty.")
        return 3

    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        print(f"REFUSED  cannot read {path}: {err}")
        return 3

    occurrences = original.count(args.old)
    if occurrences != args.count:
        print(f"REFUSED  --old occurs {occurrences} times, expected {args.count}: {path}")
        return 3

    mutated = original.replace(args.old, args.new)
    if mutated == original:
        print("REFUSED  the replacement leaves the file unchanged.")
        return 3

    print(f"$ {args.test}   (baseline)")
    baseline_status, baseline_output = run(args.test, cwd)
    if baseline_status != 0:
        print(f"BASELINE the check fails before any mutation (exit {baseline_status}).")
        print("         A red check cannot show whether a guard fires.")
        print(baseline_output[-2000:])
        return 4
    baseline_scope = scope_fingerprint(baseline_output)

    verdict = 1
    try:
        path.write_text(mutated, encoding="utf-8")
        print(f"$ {args.test}   (mutated)")
        status, output = run(args.test, cwd)

        if status == 0:
            print("SURVIVED the check passes with the mutation applied.")
            if baseline_scope and scope_fingerprint(output) == baseline_scope:
                print("WARNING  the mutated run reported the same tests as the baseline.")
                print("         Check that the test target includes the mutated file.")
            verdict = 1
        elif looks_like_build_failure(output):
            print("INVALID  the mutated source does not build; nothing was measured.")
            print(output[-1500:])
            verdict = 2
        else:
            print(f"KILLED   the check fails with the mutation (exit {status}).")
            if args.expect_test and args.expect_test not in output:
                print(f"WARNING  {args.expect_test!r} is absent from the failures.")
                print("         A different guard caught it; the named one may not.")
            failures = [
                line
                for line in output.splitlines()
                if "FAIL" in line or "FAILED" in line or line.startswith("---- ")
            ]
            for line in failures[:8]:
                print(f"         {line.strip()}")
            verdict = 0
    finally:
        path.write_text(original, encoding="utf-8")
        if path.read_text(encoding="utf-8") != original:
            print("ERROR    the file was not restored; inspect it by hand.")
            return 5
        restored_status, restored_output = run(args.test, cwd)
        if restored_status != 0:
            print(f"ERROR    the check fails after restoring (exit {restored_status}).")
            print(restored_output[-2000:])
            return 5
        print("restored, and the check passes again.")

    return verdict


def entry() -> None:
    """Console-script entry point: exit with the verdict's status."""
    sys.exit(main())


if __name__ == "__main__":
    entry()
