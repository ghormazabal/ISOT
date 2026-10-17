"""Convención `_ingest/` (state, gaps, provenance), secretos y el ejemplo messy-source."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from isot import ingest, indexer, validator
from isot.__main__ import main as isot_main
from isot.paths import tool_root
from isot.rules.evaluate import evaluate
from tests.conftest import seed_mini_repo, write_yaml

EXAMPLE = tool_root() / "examples" / "messy-source"
EXPECTED = EXAMPLE / "expected-repo"
SOURCE = EXAMPLE / "source"


def _ids(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in evaluate(root):
        out.setdefault(item.id, []).append(item.severity)
    return out


def test_expected_repo_validates_and_indexes_are_current() -> None:
    assert validator.main(["--repo", str(EXPECTED)]) == 0
    ok, message = indexer.check_index(EXPECTED)
    assert ok, message
    found = _ids(EXPECTED)
    assert "ING-FMT-001" not in found and "ING-REF-001" not in found and "ING-SEC-001" not in found
    # 2 huecos que esperan a una persona (warning) y 5 desconocidos abiertos (info).
    assert sorted(found["ING-GAP-001"]) == ["info"] * 5 + ["warning"] * 2
    # El aviso real del clúster se queda: ninguna fuente justifica un override.
    assert "K8S-CP-001" in found


def test_expected_ingest_files_have_valid_format() -> None:
    assert ingest.format_issues(EXPECTED) == []
    state = yaml.safe_load((EXPECTED / "_ingest" / "state.yaml").read_text(encoding="utf-8"))
    assert state["isot_version"]
    assert {item["id"] for item in state["sources"]} == {"messy"}


def test_state_matches_source_so_rerun_is_a_noop(capsys: pytest.CaptureFixture[str]) -> None:
    """Idempotencia: sin cambios en la fuente, el estado registrado coincide (nada que reingestar)."""
    status = ingest.source_status(EXPECTED, SOURCE)
    assert status["changed"] is False, status
    assert isot_main(["ingest", "status", "--repo", str(EXPECTED), "--source", str(SOURCE)]) == 0
    assert "sin cambios" in capsys.readouterr().out


def test_status_reports_changed_files(tmp_path: Path) -> None:
    source = tmp_path / "fuente"
    shutil.copytree(SOURCE, source)
    with (source / "notes.md").open("a", encoding="utf-8") as handle:
        handle.write("- VLAN 400 nueva\n")
    (source / "extra.csv").write_text("a,b\n", encoding="utf-8")
    (source / "hosts.csv").unlink()
    status = ingest.source_status(EXPECTED, source)
    assert status == {
        "source": "messy",
        "method": "sha256",
        "added": ["extra.csv"],
        "modified": ["notes.md"],
        "removed": ["hosts.csv"],
        "changed": True,
    }
    assert ingest.main(["status", "--repo", str(EXPECTED), "--source", str(source)]) == 1


def test_status_with_git_commit(tmp_path: Path) -> None:
    import subprocess

    source = tmp_path / "fuente"
    shutil.copytree(SOURCE, source)
    git = ["git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@example.net"]
    subprocess.run([*git[:3], "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    sha = subprocess.run([*git[:3], "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    repo = tmp_path / "datos"
    write_yaml(
        repo / "_ingest" / "state.yaml",
        {"version": 1, "isot_version": "0.0.0", "processed_at": "2026-01-01", "sources": [{"id": "s", "repo": "fuente", "commit": sha}]},
    )
    assert ingest.source_status(repo, source)["changed"] is False
    (source / "notes.md").write_text("cambio\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "cambio"], check=True)
    assert ingest.source_status(repo, source)["modified"] == ["notes.md"]


def test_hash_command_prints_state_block(capsys: pytest.CaptureFixture[str]) -> None:
    assert ingest.main(["hash", "--source", str(SOURCE)]) == 0
    block = yaml.safe_load(capsys.readouterr().out)
    state = yaml.safe_load((EXPECTED / "_ingest" / "state.yaml").read_text(encoding="utf-8"))
    assert block["files"] == state["sources"][0]["files"]


def _with_ingest(tmp_path: Path, **files: object) -> Path:
    root = seed_mini_repo(tmp_path / "inv")
    for name, data in files.items():
        path = root / "_ingest" / f"{name}.yaml"
        if isinstance(data, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        else:
            write_yaml(path, data)
    return root


def test_bad_ingest_format_is_an_error(tmp_path: Path) -> None:
    root = _with_ingest(
        tmp_path,
        state={"version": 2, "sources": [{"id": "a b", "repo": "https://user:pw@example.net/x.git"}]},
        gaps={"version": 1, "gaps": [{"id": "G1", "entity": "host-sin-tipo", "kind": "conflict", "status": "resolved", "detail": "x"}]},
        provenance="entities: [esto no es un mapa",
        otro={"x": 1},
    )
    messages = [issue.message for issue in ingest.format_issues(root)]
    joined = "\n".join(messages)
    for text in (
        "version debe ser 1",
        "falta isot_version",
        "id inválido",
        "credenciales",
        "indica commit",
        "no sigue la convención",
        "al menos 2 valores",
        "necesita resolution",
        "YAML inválido",
        "archivo desconocido",
    ):
        assert text in joined, text
    found = _ids(root)
    assert "error" in found["ING-FMT-001"]
    assert validator.main(["--repo", str(root)]) == 1


def test_dangling_refs_are_warnings(tmp_path: Path) -> None:
    root = _with_ingest(
        tmp_path,
        state={"version": 1, "isot_version": "0.0.0", "processed_at": "2026-01-01", "sources": [{"id": "src", "repo": "x", "commit": "abcdef1"}]},
        provenance={
            "version": 1,
            "entities": {
                "site:site-a": [{"source": "src", "file": "a.csv", "locator": "línea 2"}],
                "device:site-a/NO-EXISTE": [{"source": "otra", "file": "a.csv"}],
            },
        },
    )
    issues = ingest.format_issues(root)
    assert issues == []
    found = _ids(root)
    assert found["ING-REF-001"] == ["warning", "warning"]
    assert validator.main(["--repo", str(root)]) == 0


def test_ingest_dir_is_not_inventory(tmp_path: Path) -> None:
    """Un YAML con forma de device dentro de _ingest/ no aparece en el inventario."""
    root = seed_mini_repo(tmp_path / "inv")
    before = indexer.index_targets(root)
    write_yaml(root / "_ingest" / "devices" / "X.yaml", {"hostname": "X", "role": "worker"})
    assert indexer.index_targets(root) == before


@pytest.mark.parametrize(
    "data, hit",
    [
        ({"snmp": {"community": "lab-ro"}}, "snmp.community"),
        ({"users": [{"name": "admin", "password": "hunter2"}]}, "users[0].password"),
        ({"key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"}, "PEM"),
    ],
)
def test_secrets_are_errors(tmp_path: Path, data: dict, hit: str) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    device = next((root / "sites").rglob("devices/**/*.yaml"))
    doc = yaml.safe_load(device.read_text(encoding="utf-8"))
    doc.update(data)
    write_yaml(device, doc)
    messages = [issue.message for issue in ingest.secret_issues(root)]
    assert any(hit in item for item in messages), messages
    assert "error" in _ids(root)["ING-SEC-001"]


def test_secret_placeholders_are_allowed(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    device = next((root / "sites").rglob("devices/**/*.yaml"))
    doc = yaml.safe_load(device.read_text(encoding="utf-8"))
    doc["snmp"] = {"community": "${SNMP_COMMUNITY}", "token": "<secreto>", "password": "vault:infra/lab", "secret": ""}
    write_yaml(device, doc)
    assert ingest.secret_issues(root) == []


def test_bundled_examples_have_no_secrets() -> None:
    for repo in (tool_root() / "examples" / "demo-repo", EXPECTED):
        assert ingest.secret_issues(repo) == []


def test_docs_cover_rules_and_conventions() -> None:
    """La guía y el catálogo nombran todas las reglas, claves de entidad y claves de secreto."""
    from isot.rules.evaluate import catalog

    rules_doc = (tool_root() / "docs" / "rules.md").read_text(encoding="utf-8")
    for item in catalog():
        assert f"| {item['id']} |" in rules_doc, item["id"]
    guide = (tool_root() / "docs" / "llm" / "INGEST.md").read_text(encoding="utf-8")
    for kind in ingest.ENTITY_KINDS:
        assert f"`{kind}:" in guide, kind
    for key in ingest.SECRET_KEYS:
        assert f"`{key}`" in guide, key
    for value in ingest.GAP_KINDS + ingest.GAP_STATUSES:
        assert value in guide, value
    assert "Eres un agente de ingesta para ISoT" in guide
