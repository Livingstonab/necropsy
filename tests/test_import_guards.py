"""Import guard: no process spawning, no dynamic loading (R-16.1, R-16.2, R-16.5, E4).

NECROPSY reads GitHub's REST API and never executes anything derived from an
analyzed repository. That claim is only credible if it is machine-checked, so
this module does two independent checks over `necropsy/analysis/` and
`necropsy/scoring/`:

  1. A static AST scan of every module in the package, which catches a forbidden
     import even when the module is never imported at runtime.
  2. A fresh-interpreter import, which catches a *transitive* import: a module
     that pulls in `subprocess` through a dependency would pass the AST scan and
     fail this one.

Two anti-vacuity measures, because a guard that silently checks nothing is worse
than no guard. First, `test_detector_flags_known_violations` proves the scanner
actually fires on the patterns it claims to reject. Second, every scan asserts it
found at least one module, so an empty or renamed package fails instead of
quietly passing. `necropsy/analysis/` does not exist yet (it arrives with T-1.7 /
T-1.8); until it does, its tests skip with a named reason rather than pass.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Modules that can start a process or load code at runtime (R-16.2).
FORBIDDEN_MODULES = frozenset(
    {
        "subprocess",
        "pty",
        "multiprocessing",
        "importlib",
        "runpy",
        "ctypes",
        "popen2",
        "commands",
    }
)

#: The subset checked transitively, in a fresh interpreter. `importlib` is omitted
#: deliberately: it is part of CPython's own import machinery and is pulled into
#: `sys.modules` by Pydantic, so requiring its absence would test the interpreter
#: rather than NECROPSY. Dynamic loading *in our own code* is still banned, by the
#: static AST scan above, which is the check that can actually distinguish the two.
TRANSITIVELY_FORBIDDEN_MODULES = FORBIDDEN_MODULES - {"importlib"}

#: Dotted call targets that execute or dynamically load. `importlib.import_module`
#: is covered by the module rule above; these catch the builtins and the
#: `os`-level process spawners that need no import of their own.
FORBIDDEN_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "os.system",
        "os.popen",
        "os.spawnl",
        "os.spawnv",
        "os.spawnlp",
        "os.spawnvp",
        "os.execv",
        "os.execvp",
        "os.execl",
        "os.fork",
        "os.forkpty",
        "pickle.load",
        "pickle.loads",
        "marshal.loads",
    }
)

PACKAGES = {
    "necropsy.analysis": ROOT / "necropsy" / "analysis",
    "necropsy.scoring": ROOT / "necropsy" / "scoring",
}


def _dotted(node: ast.expr) -> str | None:
    """`os.system` for an Attribute chain, `eval` for a bare Name, else None."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def scan_source(source: str, label: str) -> list[str]:
    """Every forbidden import or call in `source`, as human-readable findings."""
    findings: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    findings.append(f"{label}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                findings.append(f"{label}:{node.lineno} imports from {node.module}")
        elif isinstance(node, ast.Call):
            target = _dotted(node.func)
            if target in FORBIDDEN_CALLS:
                findings.append(f"{label}:{node.lineno} calls {target}()")
    return findings


def python_files(package_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in package_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def scan_package(package_dir: Path) -> tuple[list[Path], list[str]]:
    files = python_files(package_dir)
    findings: list[str] = []
    for path in files:
        findings.extend(
            scan_source(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
        )
    return files, findings


def require_package(name: str) -> Path:
    directory = PACKAGES[name]
    if not directory.is_dir():
        pytest.skip(
            f"{name} does not exist yet (arrives with T-1.7/T-1.8). "
            "This guard is wired and will cover it as soon as the package is created; "
            "it is reported as skipped rather than passed so the gap stays visible."
        )
    return directory


# -- the guard itself is tested first (anti-vacuity) -----------------------
def test_detector_flags_known_violations() -> None:
    """If this fails, every other test in this file is meaningless."""
    offending = "\n".join(
        [
            "import subprocess",
            "import multiprocessing.pool",
            "from importlib import import_module",
            "import pty",
            "import os, pickle",
            "os.system('whoami')",
            "eval('1+1')",
            "exec('x = 1')",
            "pickle.loads(b'')",
            "__import__('subprocess')",
        ]
    )
    findings = scan_source(offending, "sample.py")
    joined = " | ".join(findings)
    for expected in (
        "imports subprocess",
        "imports multiprocessing.pool",
        "imports from importlib",
        "imports pty",
        "calls os.system()",
        "calls eval()",
        "calls exec()",
        "calls pickle.loads()",
        "calls __import__()",
    ):
        assert expected in joined, f"detector missed {expected}: {findings}"


def test_detector_accepts_clean_source() -> None:
    clean = "\n".join(
        [
            "import json",
            "from necropsy.models.scorecard import Scorecard",
            "def f(x: int) -> int:",
            "    return round(x / 2)",
        ]
    )
    assert scan_source(clean, "clean.py") == []


# -- static scan -----------------------------------------------------------
def test_scoring_package_imports_no_process_spawners() -> None:
    files, findings = scan_package(require_package("necropsy.scoring"))
    assert files, "necropsy/scoring/ has no modules; this guard would pass vacuously"
    assert findings == [], f"E4 violation in necropsy/scoring/: {findings}"


def test_analysis_package_imports_no_process_spawners() -> None:
    files, findings = scan_package(require_package("necropsy.analysis"))
    assert files, "necropsy/analysis/ has no modules; this guard would pass vacuously"
    assert findings == [], f"E4 violation in necropsy/analysis/: {findings}"


# -- transitive check in a fresh interpreter -------------------------------
def _imported_forbidden_modules(package: str) -> list[str]:
    """Import `package` in a clean interpreter; report forbidden modules pulled in.

    The subprocess here runs *our* code only. Nothing derived from an analyzed
    repository is ever executed, which is the rule this file exists to enforce.

    `-I` is what makes the interpreter clean: no inherited `PYTHONPATH`, no user
    site-packages, no implicit cwd. The project root is then put back on the path
    explicitly, so the check works on a plain checkout with nothing installed,
    which is the environment E11 requires the suite to pass in.
    """
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"import {package}\n"
        f"forbidden = {sorted(TRANSITIVELY_FORBIDDEN_MODULES)!r}\n"
        "print(json.dumps(sorted(m for m in forbidden if m in sys.modules)))"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no repository-derived input
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return list(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_importing_scoring_pulls_in_no_process_spawner() -> None:
    require_package("necropsy.scoring")
    assert _imported_forbidden_modules("necropsy.scoring") == []


def test_importing_analysis_pulls_in_no_process_spawner() -> None:
    require_package("necropsy.analysis")
    assert _imported_forbidden_modules("necropsy.analysis") == []
