"""Guardar (working tree) vs Commit (rama): varios guardados → un commit, descartar, nunca en main, rutas."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot import indexer, worktree
from isot.refs import tree_for
from isot.web import create_app
from tests.test_index_fresh import _demo, _git


def _client(root: Path, edit: bool = True) -> TestClient:
    return TestClient(create_app(root, edit=edit))


def _save_two_renders_and_a_template(client: TestClient) -> None:
    for body in (
        {"template": "chrony-conf", "site": "site1", "instance": "site1-chrony"},
        {"template": "multus-nad", "site": "site1", "cluster": "site1-platform", "instance": "site1-platform-nads"},
    ):
        result = client.post("/api/renders/save/write", json=body)
        assert result.status_code == 200, result.text
    spec = client.get("/api/renders/templates/chrony-conf").json()
    body = {"id": "chrony-conf", "spec_text": spec["spec_text"], "files": {**spec["file_contents"], "chrony.conf.j2": spec["file_contents"]["chrony.conf.j2"] + "# extra\n"}}
    assert client.post("/api/renders/templates/save/write", json=body).status_code == 200


def test_multiple_saves_then_one_commit_with_all_files_and_index(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    _save_two_renders_and_a_template(client)
    status = client.get("/api/worktree").json()
    assert status["branch"] == "main" and status["default_target"] == "new" and status["edit"] is True
    paths = [f["path"] for f in status["files"]]
    assert "manifest-templates/chrony-conf/chrony.conf.j2" in paths
    assert any(p.startswith("rendered/site1/site/chrony-conf/") for p in paths)
    assert any(p.startswith("rendered/site1/site1-platform/multus-nad/") for p in paths)
    assert status["counts"]["output"] >= 4 and status["counts"]["templates"] == 1
    # En main no se hace commit en la rama actual.
    refused = client.post("/api/worktree/commit", json={"message": "x", "target": "current"})
    assert refused.status_code == 400 and "main" in refused.json()["detail"]
    done = client.post("/api/worktree/commit", json={"message": "dos renders y un template", "branch_name": "renders site1"})
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["branch"] == "wizard/renders-site1" and result["created"] and result["base"] == "main"
    assert result["current_branch"] == "wizard/renders-site1"  # se sigue trabajando en la rama
    assert sorted(result["files"]) == sorted(paths)
    assert "_index_manifest.json" in result["index_files"]
    assert _git(root, "rev-list", "--count", "main..wizard/renders-site1") == "1"
    committed = _git(root, "show", "--name-only", "--format=", "wizard/renders-site1").splitlines()
    assert set(paths) <= set(committed) and "_index_manifest.json" in committed
    assert _git(root, "rev-parse", "main") == _git(root, "rev-parse", "wizard/renders-site1~1")  # main intacto
    assert _git(root, "status", "--porcelain") == ""
    with tree_for(root, "wizard/renders-site1") as tree:
        ok, message = indexer.check_index(tree)
        assert ok, message
    # Más cambios: por defecto continúan la rama wizard/ actual.
    assert client.get("/api/worktree").json()["default_target"] == "current"
    (root / "sites.yaml").write_text((root / "sites.yaml").read_text() + "# nota\n")
    more = client.post("/api/worktree/commit", json={"message": "nota"})
    assert more.status_code == 200 and more.json()["branch"] == "wizard/renders-site1" and not more.json()["created"]


def test_commit_subset_keeps_the_rest_and_index_matches_commit(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    _save_two_renders_and_a_template(client)
    chrony = [f["path"] for f in client.get("/api/worktree").json()["files"] if f["path"].startswith("rendered/site1/site/chrony-conf/")]
    done = client.post("/api/worktree/commit", json={"message": "solo chrony", "paths": chrony, "stay": False})
    assert done.status_code == 200, done.text
    branch = done.json()["branch"]
    assert done.json()["current_branch"] == "main"  # vuelve a main; el resto sigue sin commit
    committed = set(_git(root, "show", "--name-only", "--format=", branch).splitlines())
    assert set(chrony) <= committed
    assert not any(p.startswith("manifest-templates/") or "multus-nad" in p for p in committed)
    with tree_for(root, branch) as tree:
        ok, message = indexer.check_index(tree)  # el índice describe exactamente el commit
        assert ok, message
    left = [f["path"] for f in client.get("/api/worktree").json()["files"] if f["area"] != "index"]
    assert "manifest-templates/chrony-conf/chrony.conf.j2" in left
    assert not any(p in left for p in chrony)  # en main ya no están (están en la rama)
    bad = client.post("/api/worktree/commit", json={"message": "x", "paths": ["no/esta.txt"]})
    assert bad.status_code == 400


def test_discard_restores_head_and_removes_new_files(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    _save_two_renders_and_a_template(client)
    template = "manifest-templates/chrony-conf/chrony.conf.j2"
    original = _git(root, "show", f"HEAD:{template}")
    diff = client.get("/api/worktree/diff", params={"path": template}).json()
    assert "+# extra" in diff["diff"] and diff["token"]
    # Si cambia después de ver el diff, descartar pide revisarlo otra vez.
    (root / template).write_text((root / template).read_text() + "# otra\n")
    stale = client.post("/api/worktree/discard", json={"paths": [template], "tokens": {template: diff["token"]}})
    assert stale.status_code == 409
    fresh = client.get("/api/worktree/diff", params={"path": template}).json()
    ok = client.post("/api/worktree/discard", json={"paths": [template], "tokens": {template: fresh["token"]}})
    assert ok.status_code == 200, ok.text
    assert (root / template).read_text().rstrip("\n") == original.rstrip("\n")
    new_files = [f["path"] for f in client.get("/api/worktree").json()["files"] if f["status"] == "untracked"]
    assert new_files
    assert client.post("/api/worktree/discard", json={"paths": new_files}).status_code == 200
    assert all(not (root / p).exists() for p in new_files)
    assert not (root / "rendered/site1/site/chrony-conf").exists()  # carpetas vacías fuera
    assert _git(root, "status", "--porcelain") == ""
    assert client.post("/api/worktree/discard", json={"paths": ["sites.yaml"]}).status_code == 400  # sin cambios
    assert _client(root, edit=False).post("/api/worktree/discard", json={"paths": [template]}).status_code == 403


def test_path_protections(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    for rel in ("../fuera.txt", "/etc/x", ".git/config", "a/../b", "rendered//x"):
        with pytest.raises(worktree.WorktreeError):
            worktree.save_files(root, {rel: "x"}, [])
    with pytest.raises(worktree.WorktreeError):
        worktree.save_files(root, {"sites.yaml": "x"}, [], allowed_prefix="rendered")
    outside = tmp_path / "otro"
    outside.mkdir()
    os.symlink(outside, root / "escape")
    with pytest.raises(worktree.WorktreeError):
        worktree.save_files(root, {"escape/x.txt": "x"}, [])
    assert not (outside / "x.txt").exists()
    os.unlink(root / "escape")
    client = _client(root)
    assert client.get("/api/worktree/diff", params={"path": "../x"}).status_code == 400
    assert client.post("/api/worktree/discard", json={"paths": ["../x"]}).status_code == 400
    assert client.post("/api/worktree/commit", json={"paths": [".git/config"]}).status_code == 400
    assert client.post("/api/worktree/commit", json={"message": "nada"}).status_code == 400  # sin cambios
    assert "wizard/" not in _git(root, "branch")


def test_never_commits_to_main_even_if_checked_out_branch_is_protected(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    _git(root, "checkout", "-q", "-b", "master")
    (root / "sites.yaml").write_text((root / "sites.yaml").read_text() + "# x\n")
    assert client.post("/api/worktree/commit", json={"target": "current", "message": "x"}).status_code == 400
    done = client.post("/api/worktree/commit", json={"message": "x", "branch_name": "main"}).json()
    assert done["branch"] == "wizard/main" and done["base"] == "master"
    assert _git(root, "rev-parse", "master") == _git(root, "rev-parse", "main")
