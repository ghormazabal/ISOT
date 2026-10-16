"""Detecta reaparición de identificadores prohibidos (hashes, no texto en claro).

La lista vive en isot/forbidden_patterns.json: SHA-256 salado de cadenas
normalizadas (NFKC, minúsculas, sin acentos). El árbol de trabajo se recorre
y cada candidato (token, IP, prefijo, bigrama, nombre de archivo) se hashea
con la misma receta. Si coincide, CI/pre-commit fallan.

No recorre .git: el historial puede contener datos reales a propósito.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable

from isot.paths import find_repo_root

OK_EXIT = 0
FAIL_EXIT = 1

CONFIG_NAME = "forbidden_patterns.json"
SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "isot.egg-info",
    ".eggs",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pyc",
    ".so",
    ".bin",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
WORD_RE = re.compile(r"[A-Za-záéíóúñÁÉÍÓÚÑ]+")


def strip_accents(text: str) -> str:
    nfd = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text))
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def digest(value: str, salt: str) -> str:
    norm = strip_accents(value).lower()
    return hashlib.sha256(f"{salt}\n{norm}".encode("utf-8")).hexdigest()


def load_config(repo_root: Path) -> dict:
    path = Path(__file__).resolve().parent / CONFIG_NAME
    if not path.is_file():
        path = repo_root / "isot" / CONFIG_NAME
    if not path.is_file():
        raise FileNotFoundError(f"No existe {CONFIG_NAME}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("hashes"):
        raise ValueError(f"{CONFIG_NAME} no tiene hashes")
    return data


def iter_candidates(text: str, rel: str) -> Iterable[str]:
    yield rel
    yield Path(rel).name
    yield Path(rel).stem
    for part in Path(rel).parts:
        yield part
    lowered = text
    for match in TOKEN_RE.finditer(lowered):
        token = match.group(0)
        yield token
        for piece in re.split(r"[_\-./]+", token):
            if piece:
                yield piece
        for piece in re.findall(r"[A-Za-z]{3,}", token):
            yield piece
    for match in IP_RE.finditer(lowered):
        ip = match.group(1)
        yield ip
        octets = ip.split(".")
        if len(octets) == 4:
            yield f"{octets[0]}.{octets[1]}."
            yield f"{octets[0]}.{octets[1]}.{octets[2]}."
            yield f"{octets[0]}.{octets[1]}.{octets[2]}"
            yield ".".join(octets[:3])
    words = [strip_accents(w).lower() for w in WORD_RE.findall(lowered)]
    for i, word in enumerate(words):
        yield word
        if i + 1 < len(words):
            yield f"{word} {words[i + 1]}"


def iter_files(repo_root: Path) -> Iterable[Path]:
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def scan(repo_root: Path) -> list[str]:
    config = load_config(repo_root)
    salt = str(config.get("salt") or "")
    forbidden = {str(item) for item in config["hashes"]}
    hits: list[str] = []
    for path in iter_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        seen: set[str] = set()
        for candidate in iter_candidates(text, rel):
            if candidate in seen:
                continue
            seen.add(candidate)
            if digest(candidate, salt) in forbidden:
                hits.append(f"{rel}: patrón prohibido (candidato {candidate!r})")
                break
    hits.sort()
    return hits


def format_report(repo_root: Path, hits: list[str]) -> str:
    header = [
        "ISoT leakcheck",
        "==============",
        f"Raíz: {repo_root}",
        "",
    ]
    if not hits:
        header.append("Resultado: OK (sin patrones prohibidos en el árbol de trabajo)")
        return "\n".join(header) + "\n"
    body = [f"[ERROR] {hit}" for hit in hits]
    body.append("")
    body.append(f"Resultado: FALLÓ ({len(hits)} archivo(s))")
    return "\n".join(header + body) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Falla si reaparecen identificadores prohibidos (hashes)."
    )
    parser.add_argument("--root", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_repo_root()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return FAIL_EXIT
    hits = scan(root)
    sys.stdout.write(format_report(root, hits))
    return FAIL_EXIT if hits else OK_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
