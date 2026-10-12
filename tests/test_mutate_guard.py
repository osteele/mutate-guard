"""Verdicts measured by running the real CLI against disposable trees."""

from __future__ import annotations

import importlib.util
import marshal
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIBRARY = "def allowed(value):\n    return value > 0\n"
MUTANT = "def allowed(value):\n    return value < 0\n"  # same size as LIBRARY
CHECK = """\
import unittest

from library import allowed


class AllowedTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(allowed(1))

    def test_zero(self):
        self.assertFalse(allowed(0))


unittest.main()
"""


class VerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name)
        (self.tree / "library.py").write_text(LIBRARY)
        (self.tree / "check.py").write_text(CHECK)
        self.python = shlex.quote(sys.executable.replace("\\", "/"))

    def guard(
        self,
        old: str,
        new: str,
        test: str,
        file: str = "library.py",
        cwd: str | None = None,
        count: int = 1,
        expect_test: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {key: value for key, value in os.environ.items()}
        # The check's own interpreter must decide whether to trust bytecode.
        for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
            environment.pop(name, None)
        cmd = [
            sys.executable,
            "-m",
            "mutate_guard",
            "--file",
            file,
            "--old",
            old,
            "--new",
            new,
            "--test",
            test,
            "--cwd",
            str(self.tree) if cwd is None else cwd,
            "--count",
            str(count),
        ]
        if expect_test is not None:
            cmd.extend(["--expect-test", expect_test])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_a_failing_check_piped_into_a_succeeding_command_is_killed(self) -> None:
        for suffix in ("", " | cat"):
            with self.subTest(suffix=suffix):
                result = self.guard("value > 0", "value >= 0", f"{self.python} -B check.py{suffix}")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("KILLED", result.stdout)
                self.assertEqual((self.tree / "library.py").read_text(), LIBRARY)

    def test_bytecode_matching_the_source_stamp_is_never_trusted(self) -> None:
        # What a same-second, same-size mutation leaves behind: bytecode whose
        # recorded mtime and size match the source while its code does not.
        source = self.tree / "library.py"
        stat = source.stat()
        code = compile(MUTANT, str(source), "exec")
        # The in-tree location, even when this test itself runs under a prefix.
        cached = self.tree / "__pycache__" / f"library.{sys.implementation.cache_tag}.pyc"
        cached.parent.mkdir()
        # PEP 552 timestamp layout: magic, flags=0, source mtime, source size, code.
        header = importlib.util.MAGIC_NUMBER + struct.pack(
            "<III", 0, int(stat.st_mtime), stat.st_size
        )
        cached.write_bytes(header + marshal.dumps(code))

        result = self.guard("value > 0", "value >= 0", f"{self.python} check.py")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KILLED", result.stdout)
        self.assertEqual(source.read_text(), LIBRARY)

    def test_survived_when_check_passes_with_mutation(self) -> None:
        # A check that only tests positive values; changing > 0 to != 0 survives because
        # 1 != 0 is True
        partial_check = """\
import unittest
from library import allowed

class AllowedTests(unittest.TestCase):
    def test_positive(self):
        self.assertTrue(allowed(1))

unittest.main()
"""
        (self.tree / "partial_check.py").write_text(partial_check)
        result = self.guard("value > 0", "value != 0", f"{self.python} partial_check.py")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SURVIVED", result.stdout)
        self.assertIn(
            "WARNING  the mutated run reported the same tests as the baseline.", result.stdout
        )
        self.assertEqual((self.tree / "library.py").read_text(), LIBRARY)

    def test_passing_check_with_build_failure_marker_is_survived_not_invalid(self) -> None:
        # Passing test that logs or prints a substring that looks like a build failure marker
        marker_check = """\
import unittest
from library import allowed

class MarkerTests(unittest.TestCase):
    def test_with_log(self):
        print("cannot find cached config: using defaults")
        self.assertTrue(allowed(1))

unittest.main()
"""
        (self.tree / "marker_check.py").write_text(marker_check)
        result = self.guard("value > 0", "value != 0", f"{self.python} marker_check.py")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SURVIVED", result.stdout)
        self.assertNotIn("INVALID", result.stdout)

    def test_mutated_syntax_error_is_invalid(self) -> None:
        result = self.guard("value > 0", "value > > 0", f"{self.python} check.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("INVALID", result.stdout)
        self.assertEqual((self.tree / "library.py").read_text(), LIBRARY)

    def test_refused_when_old_not_found(self) -> None:
        result = self.guard("nonexistent", "replacement", f"{self.python} check.py")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)

    def test_refused_when_count_mismatches(self) -> None:
        result = self.guard("value > 0", "value >= 0", f"{self.python} check.py", count=2)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)

    def test_refused_when_replacement_leaves_file_unchanged(self) -> None:
        result = self.guard("value > 0", "value > 0", f"{self.python} check.py")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)

    def test_refused_when_file_not_found(self) -> None:
        result = self.guard("old", "new", f"{self.python} check.py", file="nonexistent.py")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)
        self.assertIn("file does not exist", result.stdout)

    def test_refused_when_cwd_not_found(self) -> None:
        result = self.guard(
            "old", "new", f"{self.python} check.py", cwd=str(self.tree / "nonexistent_dir")
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)
        self.assertIn("directory does not exist", result.stdout)

    def test_refused_when_old_is_empty(self) -> None:
        result = self.guard("", "something", f"{self.python} check.py")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)
        self.assertIn("--old cannot be empty", result.stdout)

    def test_refused_when_count_is_invalid(self) -> None:
        result = self.guard("value > 0", "value >= 0", f"{self.python} check.py", count=0)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stdout)
        self.assertIn("--count must be positive", result.stdout)

    def test_baseline_failure_reports_baseline(self) -> None:
        failing_check = """\
import sys
sys.exit(1)
"""
        (self.tree / "failing_check.py").write_text(failing_check)
        result = self.guard("value > 0", "value >= 0", f"{self.python} failing_check.py")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("BASELINE", result.stdout)
        self.assertEqual((self.tree / "library.py").read_text(), LIBRARY)

    def test_check_failing_after_restore_reports_error(self) -> None:
        # A check that fails on its second invocation (after restore)
        state_file = self.tree / "run_count.txt"
        state_file.write_text("0")
        state_path = repr(str(state_file).replace("\\", "/"))
        flaky_check = f"""\
from pathlib import Path
state = Path({state_path})
count = int(state.read_text()) + 1
state.write_text(str(count))
if count >= 3:
    import sys
    print("Post-restore check failed intentionally")
    sys.exit(1)
"""
        (self.tree / "state_check.py").write_text(flaky_check)
        result = self.guard("value > 0", "value >= 0", f"{self.python} state_check.py")
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn("ERROR", result.stdout)
        self.assertIn("Post-restore check failed intentionally", result.stdout)
        self.assertEqual((self.tree / "library.py").read_text(), LIBRARY)

    def test_scope_fingerprint_cargo_test_timing_independence(self) -> None:
        from mutate_guard import scope_fingerprint

        out1 = "test result: ok. 5 passed; 0 failed; finished in 0.01s"
        out2 = "test result: ok. 5 passed; 0 failed; finished in 0.05s"
        self.assertEqual(scope_fingerprint(out1), scope_fingerprint(out2))

    def test_empty_scope_does_not_warn_on_survived(self) -> None:
        # Check command output produces no test summary patterns (e.g. exit 0 with arbitrary output)
        result = self.guard("value > 0", "value != 0", "true")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SURVIVED", result.stdout)
        self.assertNotIn(
            "WARNING  the mutated run reported the same tests as the baseline.", result.stdout
        )

    def test_expect_test_warning_behavior(self) -> None:
        # When expect_test matches failure, no warning
        res_matching = self.guard(
            "value > 0", "value >= 0", f"{self.python} check.py", expect_test="test_zero"
        )
        self.assertEqual(res_matching.returncode, 0)
        self.assertIn("KILLED", res_matching.stdout)
        self.assertNotIn("WARNING", res_matching.stdout)

        # When expect_test does not match failure, warning is printed
        res_missing = self.guard(
            "value > 0", "value >= 0", f"{self.python} check.py", expect_test="test_nonexistent"
        )
        self.assertEqual(res_missing.returncode, 0)
        self.assertIn("KILLED", res_missing.stdout)
        self.assertIn(
            "WARNING  'test_nonexistent' is absent from the failures.", res_missing.stdout
        )


if __name__ == "__main__":
    unittest.main()
