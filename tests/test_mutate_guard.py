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
        self.python = shlex.quote(sys.executable)

    def guard(self, old: str, new: str, test: str) -> subprocess.CompletedProcess[str]:
        environment = {key: value for key, value in os.environ.items()}
        # The check's own interpreter must decide whether to trust bytecode.
        for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
            environment.pop(name, None)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "mutate_guard",
                "--file",
                "library.py",
                "--old",
                old,
                "--new",
                new,
                "--test",
                test,
                "--cwd",
                str(self.tree),
            ],
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


if __name__ == "__main__":
    unittest.main()
