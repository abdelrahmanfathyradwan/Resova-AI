"""Cross-platform task runner for the Resova project.

Standard library only. Run it with any Python 3.12+ interpreter::

    python scripts/tasks.py <command>

Commands:
    install   create .venv (if missing) and install the pinned dependencies
    test      run pytest with coverage
    lint      run ruff lint and format checks
    run       start the local API server on 127.0.0.1
    migrate   apply alembic migrations
    check     sanity-check the local checkout before committing
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
LOCK_FILE = ROOT / "requirements.lock"
ALEMBIC_INI = ROOT / "alembic.ini"
REQUIRED_PYTHON = "3.12"
APP_MODULE = "resova.api:app"

# Importable top-level modules that must be present after a successful install.
DEPENDENCY_MODULES = (
    "sqlalchemy",
    "alembic",
    "pydantic",
    "pydantic_settings",
    "fastapi",
    "uvicorn",
    "yaml",
)


def venv_python() -> Path:
    """Path of the interpreter inside the project virtualenv."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(cmd: list[str | Path], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    """Echo and run a command. Output is streamed unless ``quiet`` is set."""
    printable = " ".join(str(part) for part in cmd)
    print(f"$ {printable}", flush=True)
    result = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(ROOT),
        text=True,
        capture_output=quiet,
        check=False,
    )
    if quiet and result.returncode != 0:
        print((result.stdout or "") + (result.stderr or ""), flush=True)
    return result


def capture(cmd: list[str | Path]) -> str:
    """Run a command and return its combined output, stripped."""
    result = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    return ((result.stdout or "") + (result.stderr or "")).strip()


def is_ignored(path: str) -> bool:
    """True when git would ignore ``path`` in this repository."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=str(ROOT),
            check=False,
        ).returncode
        == 0
    )


def python_launcher() -> list[str]:
    """Best-effort locator for a Python 3.12 interpreter used to create the venv."""
    if sys.platform == "win32" and shutil.which("py"):
        return ["py", f"-{REQUIRED_PYTHON}"]
    for name in (f"python{REQUIRED_PYTHON}",):
        found = shutil.which(name)
        if found:
            return [found]
    if sys.version_info[:2] == (3, 12):
        return [sys.executable]
    raise SystemExit(
        f"No Python {REQUIRED_PYTHON} interpreter found. Install Python {REQUIRED_PYTHON} first "
        f"(see README) or create {VENV_DIR} manually with a {REQUIRED_PYTHON} interpreter."
    )


def cmd_install(_args: argparse.Namespace) -> int:
    if not venv_python().exists():
        launcher = python_launcher()
        print(f"Creating virtualenv at {VENV_DIR} using: {' '.join(launcher)}", flush=True)
        created = run([*launcher, "-m", "venv", VENV_DIR])
        if created.returncode != 0:
            return created.returncode

    python = venv_python()
    if LOCK_FILE.exists():
        code = run(
            [python, "-m", "pip", "install", "--only-binary=:all:", "-r", LOCK_FILE]
        ).returncode
        if code == 0:
            # The project itself is installed editable without re-resolving the locked set.
            code = run([python, "-m", "pip", "install", "--no-deps", "-e", "."]).returncode
    else:
        print(f"{LOCK_FILE.name} not found - falling back to pyproject (dev extras).", flush=True)
        code = run([python, "-m", "pip", "install", "-e", ".[dev]"]).returncode

    if code != 0:
        return code

    print(f"\nInstall complete. Interpreter: {python}", flush=True)
    run([python, "--version"])
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    return run(
        [venv_python(), "-m", "pytest", "--cov=resova", "--cov-report=term-missing"]
    ).returncode


def cmd_lint(_args: argparse.Namespace) -> int:
    lint_code = run([venv_python(), "-m", "ruff", "check", "."]).returncode
    format_code = run([venv_python(), "-m", "ruff", "format", "--check", "."]).returncode
    return lint_code or format_code


def cmd_run(args: argparse.Namespace) -> int:
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"Refusing to bind to {args.host!r}: Resova is a local, single-user tool and "
            "may only listen on the loopback interface (127.0.0.1).",
            flush=True,
        )
        return 2
    return run(
        [
            venv_python(),
            "-m",
            "uvicorn",
            APP_MODULE,
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
    ).returncode


def cmd_migrate(_args: argparse.Namespace) -> int:
    if not ALEMBIC_INI.exists():
        print(
            "\n[NOT IMPLEMENTED - Phase 1 / Step 2.4] alembic.ini and the migrations tree "
            "do not exist yet.",
            flush=True,
        )
        print("Nothing was applied. This command becomes functional in Step 2.4.", flush=True)
        return 2
    return run([venv_python(), "-m", "alembic", "upgrade", "head"]).returncode


def cmd_check(_args: argparse.Namespace) -> int:
    python = venv_python()
    results: list[tuple[str, bool, str]] = []

    if python.exists():
        version = capture([python, "--version"])
        results.append(
            (
                f"venv interpreter is Python {REQUIRED_PYTHON}.x",
                version.startswith(f"Python {REQUIRED_PYTHON}."),
                version,
            )
        )
    else:
        results.append(
            (f"venv interpreter is Python {REQUIRED_PYTHON}.x", False, f"missing {python}")
        )

    toplevel = capture(["git", "rev-parse", "--show-toplevel"])
    results.append(
        (
            "git repository root is the project root",
            bool(toplevel) and Path(toplevel).resolve() == ROOT,
            toplevel or "not a git repository",
        )
    )

    results.append(("requirements.lock exists", LOCK_FILE.exists(), str(LOCK_FILE)))
    results.append((".env is git-ignored", is_ignored(".env"), "git check-ignore .env"))
    results.append(("*.db is git-ignored", is_ignored("probe.db"), "git check-ignore probe.db"))

    if python.exists():
        import_code = "import " + ", ".join(DEPENDENCY_MODULES)
        deps = run([python, "-c", import_code], quiet=True)
        results.append(("runtime dependencies importable", deps.returncode == 0, import_code))
    else:
        results.append(("runtime dependencies importable", False, "no interpreter"))

    width = max(len(name) for name, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        failed += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}", flush=True)

    print("\n--- git status --short (review before staging) ---", flush=True)
    print(capture(["git", "status", "--short"]) or "(working tree clean)", flush=True)

    if failed:
        print(f"\n{failed} check(s) failed.", flush=True)
        return 1
    print("\nAll checks passed.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tasks.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install", help="create .venv and install pinned dependencies")
    subparsers.add_parser("test", help="run pytest with coverage")
    subparsers.add_parser("lint", help="run ruff lint and format checks")
    run_parser = subparsers.add_parser("run", help="start the local API server")
    run_parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    run_parser.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    subparsers.add_parser("migrate", help="apply alembic migrations")
    subparsers.add_parser("check", help="sanity-check the local checkout")

    handlers = {
        "install": cmd_install,
        "test": cmd_test,
        "lint": cmd_lint,
        "run": cmd_run,
        "migrate": cmd_migrate,
        "check": cmd_check,
    }
    args = parser.parse_args(argv)
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
