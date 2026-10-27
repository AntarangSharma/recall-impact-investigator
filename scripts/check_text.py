"""Check authored text and generated reports without embedding the forbidden character."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {
    ".git",
    ".venv",
    "runtime",
    "work",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
TEXT = {
    ".py",
    ".md",
    ".json",
    ".csv",
    ".html",
    ".css",
    ".js",
    ".toml",
    ".yml",
    ".yaml",
    ".txt",
    ".lock",
}
failures = []
for path in ROOT.rglob("*"):
    if any(part in SKIP for part in path.relative_to(ROOT).parts) or not path.is_file():
        continue
    if path.suffix in TEXT or path.name in {"Dockerfile", "Makefile", "LICENSE", ".env.example"}:
        if chr(0x2014) in path.read_text():
            failures.append(str(path.relative_to(ROOT)))
if failures:
    raise SystemExit("Forbidden character found in: " + ", ".join(failures))
print("First-party text and generated reports pass the forbidden-character check.")
