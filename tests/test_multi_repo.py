"""Varios repositorios de datos: registro por id, API con prefijo /api/repos/{id}/ y CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot import bootstrap
from isot import config as isot_config
from isot import indexer, validator
from isot.__main__ import main as isot_main
from isot.web import build_parser, create_app


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def demos(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """demo-norte (site1+site2, con demo/diff-example) y demo-sur (site3+site4), más isot.yaml."""
    parent = tmp_path_factory.mktemp("demos")
    bootstrap.create_multi_demo(parent)
    return parent


def _config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def three_repos(tmp_path: Path, demos: Path) -> Path:
    """Config con los dos demos y un tercer repo inválido (ruta inexistente)."""
    return _config(
        tmp_path / "isot.yaml",
        f"""version: 1
repos:
  - id: norte
    name: Norte
    path: {demos / 'demo-norte'}
    branch: main
  - id: sur
    path: {demos / 'demo-sur'}
    edit: false
  - id: perdido
    path: {tmp_path / 'no-existe'}
default_repo: norte
edit:
  enabled: true
""",
    )


def test_multi_demo_layout(demos: Path) -> None:
    cfg = isot_config.load_config_file(demos / "isot.yaml")
    assert [item.id for item in cfg.repos] == ["demo-norte", "demo-sur"]
    assert cfg.default_repo == "demo-norte" and cfg.edit.enabled
    sur = demos / "demo-sur"
    assert {p.name for p in (sur / "sites").iterdir() if p.is_dir()} == {"site3", "site4"}
    assert _git(sur, "status", "--porcelain") == ""
    assert validator.validate(sur) == 0
    assert bootstrap.DIFF_EXAMPLE_BRANCH in _git(demos / "demo-norte", "branch")
    # Las copias renombradas no arrastran ids de la demo original.
    for path in (sur / "sites").rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "site1" not in text and "site2" not in text, path


def test_api_repos_lists_status_and_invalid_repo(three_repos: Path) -> None:
    client = TestClient(create_app(config_path=three_repos))
    body = client.get("/api/repos").json()
    assert body["configured"] is True and body["default_repo"] == "norte"
    repos = {item["id"]: item for item in body["repos"]}
    assert list(repos) == ["norte", "sur", "perdido"]
    norte = repos["norte"]
    assert norte["available"] and norte["error"] is None and norte["default"]
    assert norte["name"] == "Norte" and norte["path"] == "demo-norte"
    assert norte["branch"] == "main" and len(norte["short"]) == 7 and norte["dirty"] is False
    assert norte["edit"] is True and repos["sur"]["edit"] is False
    lost = repos["perdido"]
    assert lost["available"] is False and "no existe" in lost["error"]
    assert lost["path"] == "no-existe"  # solo el nombre de la carpeta
    health = client.get("/api/health").json()
    assert health == {
        "status": "ok",
        "configured": True,
        "data_repo": "Norte",
        "default_repo": "norte",
        "repos": 3,
        "available": 2,
    }


def test_data_routes_are_scoped_by_repo_prefix(three_repos: Path) -> None:
    client = TestClient(create_app(config_path=three_repos))
    ids = lambda url: sorted(item["id"] for item in client.get(url).json())  # noqa: E731
    assert ids("/api/repos/norte/sites") == ["site1", "site2"]
    assert ids("/api/repos/sur/sites") == ["site3", "site4"]
    assert ids("/api/sites") == ["site1", "site2"]  # compatibilidad: repo por defecto
    devices = client.get("/api/repos/sur/sites/site3/devices").json()
    assert {item["hostname"] for item in devices} >= {"SITE3-DCGW001", "SITE3-CTRL001"}
    assert client.get("/api/repos/norte/sites/site3/devices").status_code == 404
    cfg = client.get("/api/repos/sur/config").json()
    assert cfg["active_repo"] == "sur" and cfg["data_repo"]["id"] == "sur" and cfg["edit"] is False
    assert [item["active"] for item in cfg["repos"]] == [False, True, False]
    findings = client.get("/api/repos/sur/findings").json()
    assert set(findings["by_site"]) == {"site3", "site4"}


def test_unknown_and_invalid_repo_errors(three_repos: Path) -> None:
    client = TestClient(create_app(config_path=three_repos))
    missing = client.get("/api/repos/nada/sites")
    assert missing.status_code == 404 and "repositorio desconocido" in missing.json()["detail"]
    broken = client.get("/api/repos/perdido/sites")
    assert broken.status_code == 503 and "perdido" in broken.json()["detail"]
    # El inválido no rompe a los demás ni su estado.
    assert client.get("/api/repos/norte/sites").status_code == 200
    detail = client.get("/api/repos/perdido").json()
    assert detail["available"] is False
    cfg = client.get("/api/repos/perdido/config").json()
    assert cfg["configured"] is True and cfg["data_repo"]["available"] is False


def test_git_state_is_per_repo(three_repos: Path) -> None:
    client = TestClient(create_app(config_path=three_repos))
    norte = client.get("/api/repos/norte/refs").json()
    sur = client.get("/api/repos/sur/refs").json()
    norte_branches = {item["name"] for item in norte["branches"]}
    sur_branches = {item["name"] for item in sur["branches"]}
    assert bootstrap.DIFF_EXAMPLE_BRANCH in norte_branches
    assert bootstrap.DIFF_EXAMPLE_BRANCH not in sur_branches
    diff = client.get(
        "/api/repos/norte/diff", params={"base": "main", "head": bootstrap.DIFF_EXAMPLE_BRANCH}
    )
    assert diff.status_code == 200
    bad = client.get("/api/repos/sur/diff", params={"base": "main", "head": bootstrap.DIFF_EXAMPLE_BRANCH})
    assert bad.status_code == 400


def test_edit_flag_per_repo(three_repos: Path) -> None:
    client = TestClient(create_app(config_path=three_repos))
    assert client.get("/api/repos/norte/wizard/status").json()["edit"] is True
    status = client.get("/api/repos/sur/wizard/status").json()
    assert status["edit"] is False and status["repo"] == "sur"
    denied = client.post("/api/repos/sur/wizard/device/preview", json={"site": "site3"})
    assert denied.status_code == 403 and "solo lectura" in denied.json()["detail"]


def test_wizard_branch_lands_in_selected_repo(tmp_path: Path) -> None:
    parent = tmp_path / "demos"
    bootstrap.create_multi_demo(parent, diff_example=False)
    client = TestClient(create_app(config_path=parent / "isot.yaml"))
    body = {"site": "site3", "cluster": "site3-edge", "role": "edge-server", "count": 1, "accept_warnings": True}
    commit = client.post("/api/repos/demo-sur/wizard/device/commit", json=body)
    assert commit.status_code == 200, commit.text
    branch = commit.json()["branch"]
    assert branch in _git(parent / "demo-sur", "branch")
    assert branch not in _git(parent / "demo-norte", "branch")
    assert _git(parent / "demo-sur", "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_repo_flags_use_folder_name_ids(tmp_path: Path, demos: Path) -> None:
    norte, sur = demos / "demo-norte", demos / "demo-sur"
    client = TestClient(create_app([norte, sur]))
    body = client.get("/api/repos").json()
    assert [item["id"] for item in body["repos"]] == ["demo-norte", "demo-sur"]
    assert body["default_repo"] == "demo-norte"
    assert sorted(i["id"] for i in client.get("/api/repos/demo-sur/sites").json()) == ["site3", "site4"]
    resolved = isot_config.resolve(repo=[sur])
    assert resolved.repo_id == "demo-sur"
    dupes = isot_config.repos_from_paths([norte, sur / "..", sur])
    assert [item.id for item in dupes] == ["demo-norte", demos.name, "demo-sur"]
    same = tmp_path / "otro" / "demo-sur"
    same.mkdir(parents=True)
    assert [item.id for item in isot_config.repos_from_paths([sur, same])] == ["demo-sur", "demo-sur-2"]


def test_isot_root_env_accepts_several_paths(monkeypatch: pytest.MonkeyPatch, demos: Path) -> None:
    import os

    monkeypatch.setenv("ISOT_ROOT", os.pathsep.join([str(demos / "demo-sur"), str(demos / "demo-norte")]))
    _cfg, repos, default_id, origin = isot_config.resolve_repos()
    assert [item.id for item in repos] == ["demo-sur", "demo-norte"]
    assert default_id == "demo-sur" and origin == "ISOT_ROOT"


def test_config_reload_on_change(tmp_path: Path, demos: Path) -> None:
    cfg = _config(tmp_path / "isot.yaml", f"repos:\n  - id: norte\n    path: {demos / 'demo-norte'}\n")
    client = TestClient(create_app(config_path=cfg))
    assert [i["id"] for i in client.get("/api/repos").json()["repos"]] == ["norte"]
    import os
    import time

    _config(
        cfg,
        f"repos:\n  - id: norte\n    path: {demos / 'demo-norte'}\n  - id: sur\n    path: {demos / 'demo-sur'}\n"
        "default_repo: sur\n",
    )
    later = time.time() + 5
    os.utime(cfg, (later, later))
    body = client.get("/api/repos").json()
    assert [i["id"] for i in body["repos"]] == ["norte", "sur"] and body["default_repo"] == "sur"


def test_invalid_repo_does_not_break_config(tmp_path: Path, demos: Path) -> None:
    cfg = isot_config.parse_config(
        {"repos": [{"id": "ok", "path": str(demos / "demo-norte")}, {"id": "roto", "path": str(tmp_path / "x")}]},
        tmp_path / "isot.yaml",
    )
    assert cfg.repo("ok").error() is None
    assert "no existe" in cfg.repo("roto").error()
    with pytest.raises(isot_config.ConfigError, match="no existe"):
        isot_config.resolve(config_path=_config(tmp_path / "c.yaml", f"repos:\n  - id: roto\n    path: {tmp_path / 'x'}\n"))
    with pytest.raises(isot_config.ConfigError, match="edit"):
        isot_config.parse_config({"repos": [{"id": "a", "path": ".", "edit": "no"}]}, None)


def test_validator_and_indexer_repo_id_and_all(three_repos: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert validator.main(["--config", str(three_repos), "--repo-id", "sur"]) == 0
    assert "site3" in capsys.readouterr().out
    assert validator.main(["--config", str(three_repos), "--repo-id", "nada"]) == 2
    code = validator.main(["--config", str(three_repos), "--all"])
    out = capsys.readouterr().out
    assert code == 1  # el repo perdido cuenta como fallo
    assert "=== repositorio norte" in out and "=== repositorio sur" in out and "no disponible" in out
    assert indexer.main(["--config", str(three_repos), "--all", "--check"]) == 1
    out = capsys.readouterr().out
    assert "[norte] Índices al día" in out and "[sur] Índices al día" in out and "[perdido] no disponible" in out
    assert indexer.main(["--config", str(three_repos), "--repo-id", "norte", "--check"]) == 0
    assert validator.main(["--config", str(three_repos), "--all", "--repo-id", "sur"]) == 2


def test_isot_config_lists_all_repos(three_repos: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert isot_main(["config", "--config", str(three_repos)]) == 0
    out = capsys.readouterr().out
    assert "repositorios: 3 (por defecto: norte)" in out
    assert "* norte" in out and "sur" in out and "NO DISPONIBLE" in out and "solo lectura" in out


def test_web_binds_localhost_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISOT_HOST", raising=False)
    args = build_parser().parse_args(["--repo", "a", "--repo", "b"])
    assert args.host == "127.0.0.1"
    assert [p.name for p in args.repo] == ["a", "b"]
    assert build_parser().parse_args(["--host", "0.0.0.0"]).host == "0.0.0.0"
