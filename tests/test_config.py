"""Configuración (isot.yaml), repo de datos externo y onboarding sin configuración."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot import config as isot_config
from isot.indexer import check_index, write_indexes
from isot.inventory import load_templates, set_template_library
from isot.web import create_app
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _write_config(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _data_repo(tmp_path: Path, name: str = "inv") -> Path:
    root = seed_mini_repo(tmp_path / name)
    write_indexes(root)
    return root


def test_resolution_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_a = _data_repo(tmp_path, "a")
    repo_b = _data_repo(tmp_path, "b")
    repo_c = _data_repo(tmp_path, "c")
    repo_d = _data_repo(tmp_path, "d")
    user = _write_config(Path.home() / ".config" / "isot" / "config.yaml", f"data_repo:\n  path: {repo_d}\n")
    assert isot_config.resolve().repo == repo_d.resolve()
    _write_config(Path.cwd() / "isot.yaml", f"data_repo:\n  path: {repo_c}\n")
    assert isot_config.resolve().repo == repo_c.resolve()
    env_cfg = _write_config(tmp_path / "env.yaml", f"data_repo:\n  path: {repo_b}\n")
    monkeypatch.setenv("ISOT_CONFIG", str(env_cfg))
    assert isot_config.resolve().repo == repo_b.resolve()
    cli_cfg = _write_config(tmp_path / "cli.yaml", f"data_repo:\n  path: {repo_a}\n")
    resolved = isot_config.resolve(config_path=cli_cfg)
    assert resolved.repo == repo_a.resolve()
    assert resolved.config.source == cli_cfg.resolve()
    assert isot_config.resolve(repo=repo_d, config_path=cli_cfg).origin == "--repo"
    assert user.is_file()


def test_relative_and_home_paths(tmp_path: Path) -> None:
    repo = _data_repo(Path.home(), "mi-inventario")
    cfg = _write_config(tmp_path / "cfg" / "isot.yaml", "data_repo:\n  path: ~/mi-inventario\n")
    assert isot_config.resolve(config_path=cfg).repo == repo.resolve()
    nested = _data_repo(tmp_path / "cfg", "rel")
    cfg2 = _write_config(tmp_path / "cfg" / "rel.yaml", "data_repo:\n  path: rel\n")
    assert isot_config.resolve(config_path=cfg2).repo == nested.resolve()


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("data_repo:\n  remote: origin\n", "falta data_repo.path"),
        ("data_repo:\n  path: /no/existe/isot\n", "no existe"),
        ("datarepo:\n  path: x\n", "clave(s) desconocida(s) datarepo"),
        ("data_repo:\n  path: {repo}\nedit:\n  enabled: quizás\n", "edit.enabled debe ser true o false"),
        ("data_repo:\n  path: {repo}\nedit:\n  pussh: true\n", "pussh"),
        ("data_repo:\n  path: {repo}\ntemplates: /tmp\n", "templates debe ser una lista"),
        ("data_repo:\n  path: {repo}\ntemplates: [/no/existe/tpl]\n", "templates[0]"),
        ("version: 2\n", "version=2"),
        ("- a\n- b\n", "mapa YAML"),
        ("data_repo:\n  path: {empty}\n", "isot init --scaffold"),
    ],
)
def test_invalid_config_messages(tmp_path: Path, body: str, fragment: str) -> None:
    repo = _data_repo(tmp_path)
    empty = tmp_path / "vacio"
    empty.mkdir()
    cfg = _write_config(tmp_path / "isot.yaml", body.format(repo=repo, empty=empty))
    with pytest.raises(isot_config.ConfigError) as info:
        isot_config.resolve(config_path=cfg)
    assert fragment in str(info.value)


def test_missing_config_message() -> None:
    with pytest.raises(isot_config.ConfigMissing) as info:
        isot_config.resolve()
    assert "isot.example.yaml" in str(info.value)
    assert "isot init" in isot_config.missing_message()


def test_explicit_config_must_exist(tmp_path: Path) -> None:
    with pytest.raises(isot_config.ConfigError, match="--config apunta"):
        isot_config.resolve(config_path=tmp_path / "nada.yaml")


def test_cli_prints_instructions_without_config(capsys: pytest.CaptureFixture[str]) -> None:
    from isot import indexer, validator

    assert validator.main([]) == 2
    assert "No hay repositorio de datos configurado" in capsys.readouterr().err
    assert indexer.main(["--check"]) == 2
    assert "isot init" in capsys.readouterr().err


def test_cli_uses_config_and_repo_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from isot import indexer, validator

    repo = _data_repo(tmp_path)
    cfg = _write_config(tmp_path / "isot.yaml", f"data_repo:\n  path: {repo}\n")
    assert indexer.main(["--check", "--config", str(cfg)]) == 0
    assert validator.main(["--repo", str(repo)]) == 0
    capsys.readouterr()


def test_sanitize_remote_url() -> None:
    assert isot_config.sanitize_remote_url("https://user:tok@git.example.net/org/inv.git") == "https://git.example.net/org/inv.git"
    assert isot_config.sanitize_remote_url("git@git.example.net:org/inv.git") == "git@git.example.net:org/inv.git"
    assert isot_config.sanitize_remote_url(None) is None


def test_api_onboarding_without_config() -> None:
    client = TestClient(create_app())
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["configured"] is False
    info = client.get("/api/config").json()
    assert info["configured"] is False
    assert "isot init" in info["message"]
    assert any("isot demo" in hint for hint in info["hints"])
    sites = client.get("/api/sites")
    assert sites.status_code == 503
    assert "isot.example.yaml" in sites.json()["detail"]


def test_api_picks_up_config_created_later(tmp_path: Path) -> None:
    client = TestClient(create_app())
    assert client.get("/api/config").json()["configured"] is False
    repo = _data_repo(tmp_path)
    _write_config(Path.cwd() / "isot.yaml", f"data_repo:\n  path: {repo}\n")
    assert client.get("/api/config").json()["configured"] is True
    assert client.get("/api/sites").status_code == 200


def test_api_config_reports_git_state(tmp_path: Path) -> None:
    repo = _data_repo(tmp_path)
    git_init_and_commit(repo, "base")
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://user:secret@git.example.net/org/inv.git"],
        check=True,
    )
    cfg = _write_config(
        tmp_path / "isot.yaml",
        f"data_repo:\n  path: {repo}\n  branch: main\nedit:\n  enabled: true\nui:\n  title: Inventario\n",
    )
    client = TestClient(create_app(config_path=cfg))
    info = client.get("/api/config").json()
    data = info["data_repo"]
    assert info["configured"] and info["edit"] is True
    assert data["name"] == "inv" and data["git"] is True
    assert len(data["short"]) == 7 and data["dirty"] is False
    assert data["remote_url"] == "https://git.example.net/org/inv.git"
    assert "secret" not in str(info)
    assert info["ui"]["title"] == "Inventario"
    (repo / "nota.txt").write_text("x", encoding="utf-8")
    assert client.get("/api/config").json()["data_repo"]["dirty"] is True
    assert client.get("/api/wizard/status").json()["edit"] is True


def test_nested_data_repo_does_not_use_enclosing_git(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    repo = _data_repo(outer)
    git_init_and_commit(outer, "outer")
    client = TestClient(create_app(repo))
    refs = client.get("/api/refs")
    assert refs.status_code == 503
    assert "raíz de un repo git" in refs.json()["detail"]


def test_library_template_fallback_and_override(tmp_path: Path) -> None:
    repo = _data_repo(tmp_path)
    write_yaml(
        repo / "sites" / "site-b" / "clusters" / "k8s-lib.yaml",
        {"cluster_name": "k8s-lib", "kind": "kubernetes", "template_ref": "rke2-rancher-hybrid-calico", "nodes": []},
    )
    write_indexes(repo)
    ok, _ = check_index(repo)
    assert ok
    docs = load_templates(repo)
    library = [doc for doc in docs if doc.get("_library")]
    assert [doc["_path"] for doc in library] == ["@isot/rke2-rancher-hybrid-calico/template.yaml"]
    # El índice commiteado del repo de datos no incluye la biblioteca.
    index = (repo / "cluster-templates" / "_template_index.json").read_text(encoding="utf-8")
    assert "rke2-rancher-hybrid-calico" not in index
    client = TestClient(create_app(repo))
    names = {item["name"]: item for item in client.get("/api/templates").json()}
    assert names["rke2-rancher-hybrid-calico"]["library"] == "@isot"
    assert "rke2-openstack-cilium" not in names  # solo los referenciados
    detail = client.get("/api/templates/rke2-rancher-hybrid-calico")
    assert detail.status_code == 200, detail.text
    assert detail.json()["path"].startswith("@isot/")
    values = client.get("/api/templates/rke2-rancher-hybrid-calico/values/rke2-rancher-hybrid-calico")
    assert values.status_code == 200, values.text
    cluster = client.get("/api/sites/site-b/clusters/k8s-lib").json()
    assert cluster["template_site"] == "global" and cluster["template_path"].startswith("@isot/")
    catalog = {item["name"] for item in client.get("/api/wizard/status").json()["template_library"]}
    assert {"rke2-openstack-cilium", "rke2-rancher-hybrid-calico"} <= catalog
    # Un template del mismo id en el repo de datos tiene prioridad.
    write_yaml(
        repo / "cluster-templates" / "rke2-rancher-hybrid-calico" / "template.yaml",
        {"template_name": "rke2-rancher-hybrid-calico", "distro": "rke2", "charts": []},
    )
    write_indexes(repo)
    docs = load_templates(repo)
    assert not [doc for doc in docs if doc.get("_library")]
    assert any(doc["_path"] == "cluster-templates/rke2-rancher-hybrid-calico/template.yaml" for doc in docs)


def test_extra_template_dirs_take_priority_over_bundled(tmp_path: Path) -> None:
    repo = _data_repo(tmp_path)
    extra = tmp_path / "extra"
    write_yaml(extra / "cluster-templates" / "rke2-openstack-cilium" / "template.yaml", {"template_name": "rke2-openstack-cilium", "distro": "custom"})
    write_yaml(
        repo / "sites" / "site-b" / "clusters" / "k8s-x.yaml",
        {"cluster_name": "k8s-x", "kind": "kubernetes", "template_ref": "rke2-openstack-cilium", "nodes": []},
    )
    cfg = _write_config(tmp_path / "isot.yaml", f"data_repo:\n  path: {repo}\ntemplates:\n  - {extra}\n")
    isot_config.apply(isot_config.resolve(config_path=cfg).config)
    try:
        library = [doc for doc in load_templates(repo) if doc.get("_library")]
        assert library[0]["_library"] == "@templates1"
        assert library[0]["distro"] == "custom"
    finally:
        set_template_library()


def test_repos_list_and_default_repo(tmp_path: Path) -> None:
    repo_a = _data_repo(tmp_path, "a")
    repo_b = _data_repo(tmp_path, "b")
    cfg = _write_config(
        tmp_path / "isot.yaml",
        f"repos:\n  - {{id: norte, name: Norte, path: {repo_a}}}\n  - {{id: sur, path: {repo_b}, branch: main}}\n"
        "default_repo: sur\n",
    )
    resolved = isot_config.resolve(config_path=cfg)
    assert resolved.repo == repo_b.resolve() and resolved.repo_id == "sur"
    assert [item.id for item in resolved.config.repos] == ["norte", "sur"]
    info = TestClient(create_app(config_path=cfg)).get("/api/config").json()
    assert info["active_repo"] == "sur"
    assert [(item["id"], item["active"]) for item in info["repos"]] == [("norte", False), ("sur", True)]
    assert info["data_repo"]["id"] == "sur" and info["data_repo"]["expected_branch"] == "main"


def test_data_repo_is_shorthand_for_one_repo(tmp_path: Path) -> None:
    repo = _data_repo(tmp_path, "Mi Inventario")
    cfg = _write_config(tmp_path / "isot.yaml", f"data_repo:\n  path: {repo}\n")
    config = isot_config.resolve(config_path=cfg).config
    assert [item.id for item in config.repos] == ["mi-inventario"]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("data_repo:\n  path: {repo}\nrepos:\n  - {{id: x, path: {repo}}}\n", "no ambos"),
        ("repos:\n  - {{path: {repo}}}\n", "falta repos[0].id"),
        ("repos:\n  - {{id: x, path: {repo}}}\n  - {{id: x, path: {repo}}}\n", "duplicados: x"),
        ("repos:\n  - {{id: x, path: {repo}}}\ndefault_repo: y\n", "default_repo='y'"),
        ("repos: []\n", "lista no vacía"),
        ("repos:\n  - {{id: X Y, path: {repo}}}\n", "inválido"),
    ],
)
def test_invalid_repos(tmp_path: Path, body: str, fragment: str) -> None:
    repo = _data_repo(tmp_path)
    cfg = _write_config(tmp_path / "isot.yaml", body.format(repo=repo))
    with pytest.raises(isot_config.ConfigError) as info:
        isot_config.resolve(config_path=cfg)
    assert fragment in str(info.value)
