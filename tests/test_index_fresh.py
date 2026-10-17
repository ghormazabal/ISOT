"""El índice siempre al día: escrituras, detección de índice viejo, artefactos, IDX-001 e incremental."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from isot import indexer
from isot.gitdiff import clear_caches
from isot.paths import demo_repo_dir
from isot.refs import tree_for
from isot.rules.evaluate import evaluate, evaluate_ref
from isot.web import create_app
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _demo(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    shutil.copytree(demo_repo_dir(), root)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "config", "user.name", "ISoT Test")
    _git(root, "config", "user.email", "test@example.net")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    clear_caches()
    return root


def _fresh_on(root: Path, branch: str) -> bool:
    with tree_for(root, branch) as tree:
        ok, message = indexer.check_index(tree)
    assert ok, message
    return ok


def test_bundled_demo_index_is_fresh() -> None:
    ok, message = indexer.check_index(demo_repo_dir())
    assert ok, message
    assert indexer.index_status(demo_repo_dir())["fresh"]


def test_every_write_path_commits_a_fresh_index(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = TestClient(create_app(root, edit=True))
    wizard = client.post(
        "/api/wizard/device/commit",
        json={"site": "site1", "kind": "baremetal", "cluster": "site1-apps", "role": "k8s-worker", "count": 1, "accept_warnings": True},
    )
    assert wizard.status_code == 200, wizard.text
    _fresh_on(root, wizard.json()["branch"])

    clone = client.get("/api/renders/templates/netplan-node/clone", params={"new_id": "netplan-mine"}).json()
    saved = client.post("/api/renders/templates/save/commit", json={"id": "netplan-mine", "spec_text": clone["spec_text"], "files": clone["file_contents"]})
    assert saved.status_code == 200, saved.text
    branch = saved.json()["branch"]
    _fresh_on(root, branch)
    with tree_for(root, branch) as tree:
        ids = [t["id"] for t in indexer.build_artifacts_index(tree)["manifest_templates"]]
    assert "netplan-mine" in ids

    upsert = {"upsert_instance": {"id": "site1-mine", "template": "netplan-mine", "site": "site1", "cluster": "site1-apps"}, "branch": branch}
    assert client.post("/api/renders/config/commit", json=upsert).status_code == 200
    _fresh_on(root, branch)

    applied = client.post("/api/renders/apply/commit", json={"instances": ["site1-chrony"]})
    assert applied.status_code == 200, applied.text
    _fresh_on(root, applied.json()["branch"])
    with tree_for(root, applied.json()["branch"]) as tree:
        rendered = indexer.build_artifacts_index(tree)["rendered"]
    assert any(item["instance"] == "site1-chrony" and item["files"] == ["chrony.conf"] for item in rendered)
    assert _git(root, "status", "--porcelain") == ""


def test_stale_index_is_detected_served_live_and_reindexed_on_a_branch(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    client = TestClient(create_app(root, edit=True))
    assert client.get("/api/index/status").json()["fresh"] is True
    # Alguien añade un device a mano sin reindexar.
    src = next((root / "sites" / "site1" / "devices").rglob("*.yaml"))
    doc = yaml.safe_load(src.read_text())
    doc["hostname"] = "SITE1-HANDMADE01"
    write_yaml(src.parent / "SITE1-HANDMADE01.yaml", doc)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "device a mano sin índice")
    clear_caches()
    status = client.get("/api/index/status").json()
    assert status["fresh"] is False and status["live"] is True and status["can_reindex"] is True
    assert "sites/site1/devices" in status["stale"]
    # La web no sirve el índice viejo: el device nuevo aparece igual.
    hosts = [d["hostname"] for d in client.get("/api/sites/site1/devices").json()]
    assert "SITE1-HANDMADE01" in hosts
    assert any(f["id"] == "IDX-001" for f in client.get("/api/findings").json()["findings"])
    assert any(f.id == "IDX-001" for f in evaluate_ref(root, "HEAD"))  # índice commiteado viejo
    result = client.post("/api/index/reindex")
    assert result.status_code == 200, result.text
    branch = result.json()["branch"]
    assert branch.startswith("wizard/")
    _fresh_on(root, branch)
    assert _git(root, "status", "--porcelain") == ""  # el working tree no se toca
    assert not any(f.id == "IDX-001" for f in evaluate_ref(root, branch))
    assert TestClient(create_app(root, edit=False)).post("/api/index/reindex").status_code == 403


def test_artifacts_index_covers_templates_renders_output_and_ingest(tmp_path: Path) -> None:
    root = _demo(tmp_path)
    (root / "rendered" / "site1" / "site" / "chrony-conf").mkdir(parents=True)
    (root / "rendered" / "site1" / "site" / "chrony-conf" / "chrony.conf").write_text("x\n")
    write_yaml(root / "_ingest" / "state.yaml", {"version": 1})
    data = indexer.build_artifacts_index(root)
    assert [t["id"] for t in data["manifest_templates"]] == ["chrony-conf"]
    assert any(i["id"] == "site1-chrony" for i in data["renders"]["instances"])
    assert {"dir": "rendered/site1/site/chrony-conf", "template": None, "version": None, "instance": None, "files": ["chrony.conf"]} in data["rendered"]
    assert data["ingest"] == [{"path": "_ingest/state.yaml", "size": (root / "_ingest" / "state.yaml").stat().st_size}]
    status = indexer.index_status(root)
    assert "rendered:rendered" in status["stale"] and "_ingest" in status["stale"]


def _big_repo(root: Path, sites: int = 24, devices: int = 30) -> Path:
    seed_mini_repo(root)
    catalog = yaml.safe_load((root / "sites.yaml").read_text())
    template = yaml.safe_load(next((root / "sites" / "site-a" / "devices").rglob("server-01.yaml")).read_text())
    for n in range(sites):
        sid = f"s{n:02d}"
        catalog["sites"].append({"id": sid, "name": sid.upper(), "path": f"sites/{sid}"})
        for d in range(devices):
            doc = dict(template, hostname=f"{sid}-srv{d:03d}")
            doc.pop("cluster_ref", None)
            write_yaml(root / "sites" / sid / "devices" / "baremetal" / f"{sid}-srv{d:03d}.yaml", doc)
    write_yaml(root / "sites.yaml", catalog)
    return root


def test_incremental_reindex_is_consistent_and_faster(tmp_path: Path) -> None:
    root = _big_repo(tmp_path / "big")
    indexer.write_indexes(root, incremental=False)
    assert indexer.index_status(root)["fresh"]
    target = root / "sites" / "s07" / "devices" / "baremetal" / "s07-srv003.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["role"] = "k8s-master"
    write_yaml(target, doc)
    assert indexer.index_status(root)["stale"] == ["sites/s07/devices"]

    started = time.perf_counter()
    written = indexer.write_indexes(root)  # incremental
    incremental = time.perf_counter() - started
    builder = indexer.write_indexes.last_builder  # type: ignore[attr-defined]
    assert builder.parsed == {"sites/s07/devices"}, builder.parsed
    rel = sorted(p.relative_to(root).as_posix() for p in written)
    assert "sites/s07/devices/_device_index.json" in rel and "_index_manifest.json" in rel
    ok, message = indexer.check_index(root)  # mismo resultado que un reindexado completo
    assert ok, message

    started = time.perf_counter()
    indexer.write_indexes(root, incremental=False)
    full = time.perf_counter() - started
    assert incremental < full, (incremental, full)
    assert indexer.write_indexes(root) == []  # nada que reescribir


def test_compare_snapshots_reuse_the_committed_index_of_each_ref(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    indexer.write_indexes(root)
    sha = git_init_and_commit(root, "base")
    with tree_for(root, sha) as tree:
        builder = indexer.plan_builder(tree)
        for site in builder.sites:
            builder.devices(site)
            builder.clusters(site)
        assert builder.parsed == set()  # índice del commit al día → no se reparsea
    assert not any(f.id == "IDX-001" for f in evaluate(root))
