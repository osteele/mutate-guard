# Changelog

## 0.1.0

- Packaged as a uv tool; previously an unversioned script in `~/.local/bin`.
- A check that pipes into another command reports the check's failure: checks
  run under `bash -o pipefail`. Before, `pytest | cat` reported a failing
  mutated test as SURVIVED.
- Python checks never run stale bytecode: each run gets an empty
  `PYTHONPYCACHEPREFIX`. Before, a same-size mutation applied or restored within
  one second could execute the other version, and projects had to clear
  `__pycache__` and set `PYTHONDONTWRITEBYTECODE` themselves.
