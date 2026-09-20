"""Static import scanning for the layering guard tests.

We parse source with :mod:`ast` rather than importing modules, so a forbidden
import is caught even if the offending line never executes at runtime.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def imported_modules(package_dir: pathlib.Path) -> dict[pathlib.Path, set[str]]:
    """Map each ``.py`` file under ``package_dir`` to the module names it imports.

    Relative imports (``from . import x``) are ignored: they can only reference
    the package itself, never another top-level package.
    """
    result: dict[pathlib.Path, set[str]] = {}
    for path in sorted(package_dir.rglob("*.py")):
        mods: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mods.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    mods.add(node.module)
        result[path] = mods
    return result


def forbidden_hits(package_dir: pathlib.Path, forbidden_tops: set[str]) -> dict[str, set[str]]:
    """Return ``{relative_file: {offending imports}}`` for imports whose top-level
    package is in ``forbidden_tops``.
    """
    offenders: dict[str, set[str]] = {}
    for path, mods in imported_modules(package_dir).items():
        hits = {m for m in mods if m.split(".")[0] in forbidden_tops}
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    return offenders
