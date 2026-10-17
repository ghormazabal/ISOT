"""`isot init`, `isot init --scaffold` y `isot demo`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot import bootstrap
from isot import config as isot_config
from isot.indexer import check_index
from isot.validator import validate
from isot.web import create_app


@pytest.fixture
def git_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Identidad git «del usuario» (en un gitconfig aislado)."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Persona Ejemplo\n\temail = persona@example.net\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_init_writes_minimal_config(tmp_path: Path, repo_root: Path) -> None:
    out = tmp_path / "isot.yaml"
    assert bootstrap.init_main(["--path", str(repo_root), "--output", str(out)]) == 0
    resolved = isot_config.resolve(config_path=out)
    assert resolved.repo == repo_root.resolve()
    assert resolved.config.edit.enabled is False
    assert bootstrap.init_main(["--path", str(repo_root), "--output", str(out)]) == 1  # ya existe
    assert bootstrap.init_main([]) == 2


def test_display_path_uses_tilde() -> None:
    assert bootstrap.display_path(Path.home() / "repos" / "inv") == "~/repos/inv"


def test_scaffold_creates_empty_data_repo(tmp_path: Path, git_identity: None) -> None:
    target = tmp_path / "nuevo"
    out = tmp_path / "isot.yaml"
    assert bootstrap.init_main(["--scaffold", str(target), "--output", str(out)]) == 0
    for rel in ("sites.yaml", "sites/_sites_index.json", "cluster-templates/_template_index.json", "README.md", ".gitignore", "site-templates/edge/topology/index.yaml", ".github/workflows/isot-validate.yml"):
        assert (target / rel).is_file(), rel
    assert check_index(target)[0]
    assert validate(target) == 0
    assert _git(target, "log", "--format=%an") == "Persona Ejemplo"
    assert _git(target, "status", "--porcelain") == ""
    resolved = isot_config.resolve(config_path=out)
    assert resolved.repo == target.resolve() and resolved.config.edit.enabled is True
    with pytest.raises(FileExistsError):
        bootstrap.scaffold(target)


def test_scaffold_without_identity_leaves_files_staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "vacio"))
    result = bootstrap.scaffold(tmp_path / "nuevo")
    assert result["sha"] is None
    assert (tmp_path / "nuevo" / ".git").is_dir()


def test_site_wizard_creates_first_site_on_empty_repo(tmp_path: Path, git_identity: None) -> None:
    target = bootstrap.scaffold(tmp_path / "nuevo")["path"]
    client = TestClient(create_app(target, edit=True))
    assert client.get("/api/sites").json() == []
    status = client.get("/api/wizard/status").json()
    template_ids = {item["id"]: item["source"] for item in status["site_templates"]}
    assert template_ids["plantilla:edge"] == "repo" and template_ids["plantilla:cloud"] == "isot"
    body = {
        "id": "norte",
        "name": "NORTE",
        "template_site": "plantilla:edge",
        "infra_range": "10.90.0.0/20",
        "clusters": [{"name": "norte-edge", "template_ref": "rke2-rancher-hybrid-calico", "workers": 1}],
    }
    preview = client.post("/api/wizard/site/preview", json=body)
    assert preview.status_code == 200, preview.text
    paths = {item["path"] for item in preview.json()["files"]}
    assert "sites/norte/topology/index.yaml" in paths
    assert "cluster-templates/rke2-rancher-hybrid-calico/template.yaml" in paths
    assert "cluster-templates/rke2-rancher-hybrid-calico/values/rke2-rancher-hybrid-calico.yaml" in paths
    commit = client.post("/api/wizard/site/commit", json={**body, "accept_warnings": True})
    assert commit.status_code == 200, commit.text
    branch = commit.json()["branch"]
    assert branch.startswith("wizard/")
    assert _git(target, "log", "-1", "--format=%an", branch) == "Persona Ejemplo"
    assert "norte" in _git(target, "show", f"{branch}:sites.yaml")
    dci = client.post("/api/wizard/site/preview", json={**body, "dci": True, "dci_peer": "site1"})
    assert dci.status_code == 400 and "no existe" in dci.json()["detail"]


def test_wizard_push_uses_configured_remote(tmp_path: Path, git_identity: None) -> None:
    target = bootstrap.scaffold(tmp_path / "nuevo")["path"]
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(target, "remote", "add", "origin", str(bare))
    cfg = tmp_path / "isot.yaml"
    cfg.write_text(f"data_repo:\n  path: {target}\nedit:\n  enabled: true\n  push: true\n", encoding="utf-8")
    client = TestClient(create_app(config_path=cfg))
    body = {"id": "sur", "name": "SUR", "template_site": "plantilla:edge", "clusters": []}
    commit = client.post("/api/wizard/site/commit", json={**body, "accept_warnings": True})
    assert commit.status_code == 200, commit.text
    pushed = commit.json()["push"]
    assert pushed["ok"] is True, pushed
    assert pushed["branch"] in subprocess.run(["git", "-C", str(bare), "branch"], check=True, capture_output=True, text=True).stdout


def test_demo_copies_to_separate_git_repo(tmp_path: Path) -> None:
    target = bootstrap.create_demo(tmp_path / "demo")
    assert (target / ".git").is_dir()
    branches = _git(target, "branch", "--format=%(refname:short)").split()
    assert set(branches) == {"main", bootstrap.DIFF_EXAMPLE_BRANCH}
    assert _git(target, "status", "--porcelain") == ""
    client = TestClient(create_app(target))
    diff = client.get("/api/diff", params={"base": "main", "head": bootstrap.DIFF_EXAMPLE_BRANCH})
    assert diff.status_code == 200, diff.text
    text = str(diff.json())
    assert "site1-jump001" in text and "LAB" in text
    assert bootstrap.create_demo(target) == target  # reutiliza una demo existente
