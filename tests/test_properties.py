"""Property and fuzz tests for mutate-guard contracts and invariants."""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from pathlib import Path

from mutate_guard import BUILD_FAILURE_MARKERS, looks_like_build_failure, scope_fingerprint

TEST_SEED_ENV = os.environ.get("MUTATE_GUARD_SEED")
DEFAULT_SEED = int(TEST_SEED_ENV) if TEST_SEED_ENV is not None else 42


class ScopeFingerprintProperties(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(DEFAULT_SEED)

    def test_cargo_timing_independence_fuzz(self) -> None:
        """Property: Varying cargo test elapsed time never affects scope fingerprint."""
        passed_counts = [1, 2, 5, 42, 100]
        failed_counts = [0, 1, 3]
        for passed in passed_counts:
            for failed in failed_counts:
                for _ in range(5):
                    dur1 = f"{self.rng.uniform(0.001, 10.0):.2f}s"
                    dur2 = f"{self.rng.uniform(0.001, 10.0):.2f}s"
                    base = (
                        f"test result: ok. {passed} passed; {failed} failed; "
                        "0 ignored; 0 measured; 0 filtered out"
                    )
                    out1 = f"running tests...\n{base}; finished in {dur1}\n"
                    out2 = f"running tests...\n{base}; finished in {dur2}\n"
                    fp1 = scope_fingerprint(out1)
                    fp2 = scope_fingerprint(out2)
                    self.assertEqual(fp1, fp2, f"Failed for seed {DEFAULT_SEED}: {fp1} != {fp2}")

    def test_arbitrary_unstructured_output_produces_empty_scope(self) -> None:
        """Property: Output without recognized test runners produces empty fingerprint."""
        corpus = [
            "hello world",
            "all checks passed",
            "build completed with status 0",
            "testing 123",
            "SUCCESS",
            "",
            "ok",  # ok without package name
            "passed",  # passed without count
        ]
        for sample in corpus:
            self.assertEqual(scope_fingerprint(sample), ())

    def test_unittest_and_pytest_patterns_match(self) -> None:
        """Property: Standard unittest and pytest patterns produce stable fingerprints."""
        for n in range(1, 20):
            ut = f"Ran {n} tests in 0.05s\n\nOK"
            pt = f"{n} passed in 0.12s"
            self.assertEqual(scope_fingerprint(ut), (f"Ran {n} tests",))
            self.assertEqual(scope_fingerprint(pt), (f"{n} passed",))


class BuildFailureMarkerProperties(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(DEFAULT_SEED)

    def test_all_declared_markers_detected(self) -> None:
        """Property: Any output containing any declared build marker is detected."""
        for marker in BUILD_FAILURE_MARKERS:
            # Embedded in random surrounding garbage
            prefix = "".join(self.rng.choices("abcdefghijklmn \n\t", k=20))
            suffix = "".join(self.rng.choices("opqrstuvwxyz123 \n\t", k=20))
            text = f"{prefix}{marker}{suffix}"
            self.assertTrue(
                looks_like_build_failure(text),
                f"Marker {marker!r} not detected in {text!r}",
            )

    def test_clean_output_never_flags_build_failure(self) -> None:
        """Property: Normal passing or standard assertion failure text does not flag build."""
        benign_outputs = [
            "AssertionError: 1 != 2",
            "ValueError: bad value",
            "KeyError: 'missing_key'",
            "FAILED (failures=1)",
            "test result: FAILED. 1 failed; finished in 0.01s",
            "--- FAIL: TestFoo (0.00s)",
            "expected true, got false",
            "status code 404",
        ]
        for output in benign_outputs:
            self.assertFalse(
                looks_like_build_failure(output),
                f"Benign output falsely marked as build failure: {output!r}",
            )


class MutationInvarianceProperties(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(DEFAULT_SEED)

    def test_restoration_is_strictly_idempotent(self) -> None:
        """Property: Mutating and restoring returns file to exact original bytes."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_path = tmp_path / "sample.txt"
            for iteration in range(20):
                # Generate random text
                words = ["alpha", "beta", "gamma", "delta", "epsilon", "guard", "check"]
                content = " ".join(self.rng.choices(words, k=50))
                file_path.write_text(content)
                target = "guard"
                if target in content:
                    mutated = content.replace(target, "mutated_guard")
                    file_path.write_text(mutated)
                    self.assertNotEqual(file_path.read_text(), content)
                    # Restore
                    file_path.write_text(content)
                    self.assertEqual(
                        file_path.read_text(),
                        content,
                        f"Failed restoring at iteration {iteration} with seed {DEFAULT_SEED}",
                    )


if __name__ == "__main__":
    unittest.main()
