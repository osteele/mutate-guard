# mutate-guard

Apply one exact mutation to a source file, run a check, restore the file, and
report a verdict that cannot lie.

A guard is tested when breaking it breaks a check. mutate-guard runs that
experiment for one mutation you choose — delete a call site, flip a
comparison, drop a condition — in any language whose check is a shell command.
It complements generated-operator tools such as mutmut, which sweep many
mutants but cannot target a specific semantic change.

## Install

```sh
uv tool install --force --reinstall .   # or: just install
```

## Usage

```sh
mutate-guard --file src/x.rs --old 'a && b' --new 'a' --test 'cargo test -q'
             [--expect-test NAME] [--cwd DIR] [--count N]
```

| Verdict | Exit | Meaning |
|---|---|---|
| KILLED | 0 | The check fails with the mutation and passes without it |
| SURVIVED | 1 | The check passes with the mutation applied |
| INVALID | 2 | The mutated run's output contains a known build-error string, so nothing was measured |
| REFUSED | 3 | `--old` does not occur exactly `--count` times, the file/dir does not exist, or the replacement leaves the file unchanged |
| BASELINE | 4 | The check was already failing before the mutation |
| ERROR | 5 | The file did not return to its original state, or the check fails after restoring |

Read the printed verdict line, not the exit status alone: an argparse usage
error also exits 2.

## What it guards against

Each of these would otherwise report a verdict about code that never ran:

- **A pattern that matches nothing.** The mutation must apply exactly
  `--count` times, or the run is refused.
- **A mutation that does not compile — for recognized toolchains.** INVALID is
  decided by substring: the mutated run's output must contain one of
  `SyntaxError`, `IndentationError`, `error[E`, `cannot find`, `undefined:`,
  `build failed`, `not enough arguments`, `too many arguments`,
  `unresolved import`, or `no such module`. Another toolchain's compile error
  (TypeScript's `error TS2322`, a `NameError` at import) is reported as KILLED,
  and a test failure whose message contains a marker is reported as INVALID.
  Check the failure lines printed under KILLED before trusting it.
- **A check that never reaches the mutated file.** SURVIVED warns when the
  mutated run reports the same tests as the baseline.
- **Pipelines.** The check runs under `bash -o pipefail`, so
  `pytest | tee log` reports the test's failure, not `tee`'s success.
- **Stale Python bytecode.** Python trusts a cached `.pyc` whose recorded
  source mtime (whole seconds) and size match the file, so a same-size
  mutation applied or restored within one second can run the wrong code. Each
  of the three runs (baseline, mutated, restored) gets its own empty
  `PYTHONPYCACHEPREFIX`, so no run reads bytecode another wrote, and nothing is
  written into the tree.

## Limits

There is no per-run timeout, process-group cleanup, or concurrent-edit
protection. Restoration runs in a Python `finally` block, which does not survive
SIGKILL or the default SIGTERM action. Run it in a disposable or otherwise
quiet tree, with a bounded check command.
