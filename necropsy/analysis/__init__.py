"""The analysis pipeline.

`AnalysisService.analyze()` is the single entry point for every surface (E8).
Nothing in this package may import `subprocess`, `os.system`, `pty`,
`multiprocessing`, or use `importlib` for dynamic loading; the import-guard
test in `tests/test_safety_guards.py` enforces it (E4).
"""
