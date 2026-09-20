"""Load versioned prompt files from ``prompts/``.

Prompts are never inline strings in Python. Each file is
``prompts/<name>.<version>.md`` with a ``---`` front-matter header (purpose,
inputs, output) followed by the prompt body. This loader returns the body as the
prompt text and exposes the header metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    purpose: str
    inputs: str
    output: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.name}.{self.version}"


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw.strip()
    header: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
    return header, parts[2].strip()


def load_prompt(name: str, version: str) -> Prompt:
    """Load and parse ``prompts/<name>.<version>.md``."""
    path = PROMPTS_DIR / f"{name}.{version}.md"
    header, body = _split_front_matter(path.read_text(encoding="utf-8"))
    return Prompt(
        name=name,
        version=version,
        purpose=header.get("purpose", ""),
        inputs=header.get("inputs", ""),
        output=header.get("output", ""),
        text=body,
    )
