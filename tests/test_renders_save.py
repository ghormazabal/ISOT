"""«Guardar en el repo» desde la preview de Plantillas: destino, estado, instancia nueva y commit en wizard/…"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from isot import indexer
from isot.refs import tree_for
from isot.web import create_app
from tests.test_index_fresh import _demo, _git


def _client(root: Path, edit: bool = True) -> TestClient:
    return TestClient(create_app(root, edit=edit))


def test_status_of_an_existing_instance_and_read_only(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    body = {"template": "chrony-conf", "site": "site1", "instance": "site1-chrony"}
    status = _client(root, edit=False).post("/api/renders/save/status", json=body)
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["instance"] == "site1-chrony" and not data["needs_instance"] and data["edit"] is False
    assert data["dir"] == "rendered/site1/site/chrony-conf"
    assert data["state"] in {"no_existe", "al_dia", "desfasado"}
    # Sin edición no se puede previsualizar ni guardar.
    assert _client(root, edit=False).post("/api/renders/save/preview", json=body).status_code == 403
    assert _client(root, edit=False).post("/api/renders/save/commit", json=body).status_code == 403


def test_save_existing_instance_commits_output_and_index_on_wizard_branch(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    body = {"template": "chrony-conf", "site": "site1", "instance": "site1-chrony"}
    before = client.post("/api/renders/save/status", json=body).json()["state"]
    preview = client.post("/api/renders/save/preview", json=body)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["dir"] == "rendered/site1/site/chrony-conf"
    assert any(f["path"] == "rendered/site1/site/chrony-conf/chrony.conf" for f in plan["files"])
    assert all(f["status"] in {"new", "modified", "unchanged", "orphaned"} for f in plan["files"])
    result = client.post("/api/renders/save/commit", json={**body, "message": "chrony de site1"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["branch"].startswith("wizard/") and data["instance"] == "site1-chrony" and not data["created_instance"]
    assert data["message"] == "wizard: chrony de site1"
    assert _git(root, "status", "--porcelain") == ""
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    with tree_for(root, data["branch"]) as tree:
        assert (tree / "rendered/site1/site/chrony-conf/chrony.conf").is_file()
        assert json.loads((tree / "rendered/site1/site/chrony-conf/.isot-render.json").read_text())["instance"] == "site1-chrony"
        ok, message = indexer.check_index(tree)
        assert ok, message
    # En la rama ya está al día; en Local (main) sigue como antes.
    on_branch = client.post("/api/renders/save/status", json={**body, "branch": data["branch"]}).json()
    assert on_branch["state"] == "al_dia"
    assert client.post("/api/renders/save/status", json=body).json()["state"] == before
    # Continuar la misma rama sin cambios: nada que guardar.
    again = client.post("/api/renders/save/commit", json={**body, "branch": data["branch"]})
    assert again.status_code == 400 and "al día" in again.json()["detail"]


def test_params_differ_or_no_instance_requires_creating_one(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    body = {"template": "chrony-conf", "site": "site1", "instance": "site1-chrony", "params": {"minsources": 3}}
    status = client.post("/api/renders/save/status", json=body).json()
    assert status["needs_instance"] and "params" in status["reason"]
    assert status["new_instance"]["params"]["minsources"] == 3
    assert status["new_instance"]["id"] == "site1-site-chrony-conf"
    refused = client.post("/api/renders/save/preview", json=body)
    assert refused.status_code == 400 and "instancia" in refused.json()["detail"]

    # Template incluido sin instancia para site2: se propone una.
    bare = {"template": "chrony-conf", "site": "site2"}
    status = client.post("/api/renders/save/status", json=bare).json()
    assert status["needs_instance"] and status["instance"] is None
    assert status["dir"] == "rendered/site2/site/chrony-conf" and status["state"] == "no_existe"

    create = {**body, "create_instance": True, "new_instance_id": "site1-chrony-strict"}
    preview = client.post("/api/renders/save/preview", json=create)
    assert preview.status_code == 200, preview.text
    assert any(item["path"] == "renders.yaml" for item in preview.json()["extra"])
    result = client.post("/api/renders/save/commit", json=create)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["created_instance"] and data["instance"] == "site1-chrony-strict"
    with tree_for(root, data["branch"]) as tree:
        config = yaml.safe_load((tree / "renders.yaml").read_text())
        added = [i for i in config["instances"] if i["id"] == "site1-chrony-strict"]
        assert added and added[0]["params"]["minsources"] == 3 and added[0]["template"] == "chrony-conf"
        folder = tree / data["dir"]
        assert folder.is_dir() and any(p.name != ".isot-render.json" for p in folder.iterdir())
        ok, message = indexer.check_index(tree)
        assert ok, message
    assert _git(root, "status", "--porcelain") == ""


def test_save_rejects_bad_input(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = _client(root)
    assert client.post("/api/renders/save/status", json={"template": "../x", "site": "site1"}).status_code == 400
    assert client.post("/api/renders/save/status", json={"template": "no-existe", "site": "site1"}).status_code == 400
    clash = {"template": "chrony-conf", "site": "site2", "create_instance": True, "new_instance_id": "site1-chrony"}
    assert client.post("/api/renders/save/preview", json=clash).status_code == 400
    bad_id = {**clash, "new_instance_id": "Mal Id"}
    assert client.post("/api/renders/save/preview", json=bad_id).status_code == 400
    (root / "sites.yaml").write_text((root / "sites.yaml").read_text() + "\n# sucio\n")
    dirty = client.post("/api/renders/save/commit", json={"template": "chrony-conf", "site": "site1", "instance": "site1-chrony"})
    assert dirty.status_code == 400 and "sucio" in dirty.json()["detail"]
    subprocess.run(["git", "checkout", "--", "sites.yaml"], cwd=root, check=True)
