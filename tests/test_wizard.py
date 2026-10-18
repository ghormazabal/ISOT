"""Preview y commit de wizards sobre un repo git temporal."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from isot.indexer import write_indexes
from isot.inventory import load_yaml
from isot.web import create_app
from isot.wizard import device_preview
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _prepared(tmp_path: Path) -> Path:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    return root


def _client(root: Path, *, edit: bool) -> TestClient:
    return TestClient(create_app(root, edit=edit))


def test_wizard_disabled_without_flag(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=False)
    assert client.get("/api/wizard/status").json()["edit"] is False
    denied = client.post("/api/wizard/device/preview", json={"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker"})
    assert denied.status_code == 403


def test_device_preview_allocates_consecutive_worker(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    response = client.post(
        "/api/wizard/device/preview",
        json={"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker", "count": 1},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["hostnames"] == ["server-02"]
    created = next(item for item in payload["files"] if item["action"] == "create")
    assert created["path"] == "sites/site-a/devices/baremetal/server-02.yaml"
    assert "10.0.1.16/24" in created["content"]
    assert any(field["auto"] and "hostname" in field["path"] for field in payload["fields"])
    cluster = next(item for item in payload["files"] if "k8s-prod.yaml" in item["path"])
    assert cluster["action"] == "modify"
    assert "server-02" in cluster["content"]
    graph = payload["graphs"]["network"]
    nodes = {item["id"]: item for item in graph["nodes"]}
    assert nodes["site-a:server-02"]["diff_status"] == "added"
    assert nodes["site-a:switch-core"]["diff_status"] == "modified"
    added_edge = next(item for item in graph["edges"] if "server-02" in item["id"] and item["diff_status"] == "added")
    assert "switch-core" in added_edge["id"]
    assert "site-a:server-02" in payload["focus_ids"]


def test_device_save_writes_worktree_without_commit(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    body = {"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker", "count": 2}
    preview = client.post("/api/wizard/device/preview", json=body).json()
    assert preview["worktree"] is True and preview["token"] and preview["pending"] == []
    response = client.post("/api/wizard/device/save", json={**body, "token": preview["token"]})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True and payload["saved"] is True
    assert "branch" in payload and payload["branch"] in {"main", "master"}
    # en el working tree, sin commit ni ramas nuevas
    assert "hostname: server-02" in (root / "sites/site-a/devices/baremetal/server-02.yaml").read_text()
    assert "hostname: server-03" in (root / "sites/site-a/devices/baremetal/server-03.yaml").read_text()
    assert all(not name.startswith("wizard/") for name in subprocess_branches(root))
    status = client.get("/api/worktree").json()
    paths = {f["path"]: f for f in status["files"]}
    assert paths["sites/site-a/devices/baremetal/server-02.yaml"]["status"] == "untracked"
    assert paths["sites/site-a/devices/baremetal/server-02.yaml"]["area"] == "sites"


def test_save_rolls_back_on_duplicate_hostname(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    response = client.post(
        "/api/wizard/device/save",
        json={
            "site": "site-a",
            "kind": "baremetal",
            "cluster": "k8s-prod",
            "role": "k8s-worker",
            "overrides": {"hostname": "server-01"},
        },
    )
    assert response.status_code == 400
    assert "hostname" in response.json()["detail"]
    assert client.get("/api/worktree").json()["count"] == 0


def test_save_rolls_back_on_ip_collision(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    response = client.post(
        "/api/wizard/device/save",
        json={
            "site": "site-a",
            "kind": "baremetal",
            "cluster": "k8s-prod",
            "role": "k8s-worker",
            "overrides": {"ips": {"eno1": "10.0.1.15/24"}},
        },
    )
    assert response.status_code == 400
    assert "duplicada" in response.json()["detail"] or "IP" in response.json()["detail"]
    assert not (root / "sites" / "site-a" / "devices" / "baremetal" / "server-02.yaml").exists()
    assert client.get("/api/worktree").json()["count"] == 0


def test_site_preview_and_save(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    body = {
        "id": "site-c",
        "name": "SITE-C",
        "template_site": "site-b",
        "infra_range": "10.90.0.0/20",
        "vlan_offset": 0,
        "dci": True,
        "dci_peer": "site-a",
        "clusters": [{"name": "site-c-edge", "template_ref": "rke2-lab", "workers": 2}],
    }
    preview = client.post("/api/wizard/site/preview", json=body)
    assert preview.status_code == 200, preview.text
    paths = {item["path"] for item in preview.json()["files"]}
    assert "sites.yaml" in paths
    assert "sites/site-c/topology/index.yaml" in paths
    assert any(path.endswith("SITE-C-NODE001.yaml") for path in paths)
    saved = client.post("/api/wizard/site/save", json={**body, "accept_warnings": True})
    assert saved.status_code == 200, saved.text
    catalog = load_yaml(root / "sites.yaml")
    assert "site-c" in {item["id"] for item in catalog["sites"]}
    node = root / "sites/site-c/devices/baremetal/SITE-C-NODE001.yaml"
    assert node.is_file() and ("10.90." in node.read_text() or "ip:" in node.read_text())
    graph = preview.json()["graphs"]
    global_nodes = {item["id"]: item for item in graph["global"]["nodes"]}
    assert global_nodes["site:site-c"]["diff_status"] == "added"
    assert any(item["diff_status"] == "added" for item in graph["global"]["edges"])
    net_nodes = {item["id"]: item for item in graph["network"]["nodes"]}
    assert any(key.startswith("site-c:") and item["diff_status"] == "added" for key, item in net_nodes.items())
    # Commit aparte: rama wizard/ nueva con todos los archivos + índice.
    done = client.post("/api/worktree/commit", json={"message": "sitio site-c"})
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["branch"].startswith("wizard/") and result["created"] is True
    assert any(path.endswith("_index_manifest.json") for path in result["index_files"])
    assert client.get("/api/worktree").json()["count"] == 0


def test_device_preview_matches_parent_bond_port(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "kind": "baremetal",
            "cluster_ref": "k8s-prod",
            "bonds": [
                {"name": "bondC", "leaf_pc": {"switch-core": "PortChannel10"}},
                {"name": "bondS", "leaf_pc": {"switch-core": "PortChannel12"}},
            ],
            "networking": [
                {
                    "interface": "MGMT_SW",
                    "parent": "bondC",
                    "vlan": 100,
                    "ip": "10.0.1.15/24",
                    "connected_to": {"device": "switch-core", "port": "PortChannel10"},
                },
                {
                    "interface": "STOR",
                    "parent": "bondS",
                    "vlan": 100,
                    "ip": "10.0.1.40/24",
                    "connected_to": {"device": "switch-core", "port": "PortChannel12"},
                },
            ],
        },
    )
    write_indexes(root)
    git_init_and_commit(root, "base")
    client = _client(root, edit=True)
    response = client.post(
        "/api/wizard/device/preview",
        json={"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker", "count": 1},
    )
    assert response.status_code == 200, response.text
    fields = {item["path"]: item["value"] for item in response.json()["fields"]}
    assert fields["networking.STOR.connected_to.port"] == fields["bonds.bondS.leaf_pc"]
    assert fields["networking.MGMT_SW.connected_to.port"] == fields["bonds.bondC.leaf_pc"]
    assert fields["bonds.bondC.leaf_pc"] != fields["bonds.bondS.leaf_pc"]
    assert fields["bonds.bondC.leaf_pc"] == "PortChannel11"
    assert fields["bonds.bondS.leaf_pc"] == "PortChannel13"


def test_site1_apps_worker_fills_hostname_gap(repo_root: Path) -> None:
    payload = device_preview(
        repo_root,
        {"site": "site1", "kind": "baremetal", "cluster": "site1-apps", "role": "apps-worker", "count": 1},
    )
    assert payload["hostnames"] == ["SITE1-NODE003"]
    assert payload["sibling"] == "SITE1-NODE007"
    fields = {item["path"]: item["value"] for item in payload["fields"]}
    assert fields["networking.APP_DATA.connected_to.port"] == fields["bonds.bondD01.leaf_pc"]
    assert fields["networking.CLOUD_STOR.connected_to.port"] == fields["bonds.bondS.leaf_pc"]
    assert fields["networking.MGMT_SW.connected_to.port"] == fields["bonds.bondC.leaf_pc"]
    assert fields["hostname"] == "SITE1-NODE003"
    assert str(fields["networking.APP_DATA.ip"]).startswith("10.10.20.")
    assert payload.get("pattern_note") in (None, "")


def test_site2_apps_worker_uses_node_convention(repo_root: Path) -> None:
    payload = device_preview(
        repo_root,
        {"site": "site2", "kind": "baremetal", "cluster": "site2-edge", "role": "apps-worker", "count": 1},
    )
    assert payload["hostnames"] == ["SITE2-NODE001"]
    assert payload["sibling"] in (None, "")
    assert payload["pattern_note"] == "sin hermanos del mismo rol: patrón nuevo"
    created = next(item for item in payload["files"] if item["action"] == "create")
    assert created["path"] == "sites/site2/devices/baremetal/SITE2-NODE001.yaml"
    assert "hostname: SITE2-NODE001" in created["content"]
    assert "role: apps-worker" in created["content"]
    assert "role: edge-server" not in created["content"]
    assert all("CTRL" not in host for host in payload["hostnames"])


def test_site2_edge_server_keeps_ctrl_siblings(repo_root: Path) -> None:
    payload = device_preview(
        repo_root,
        {"site": "site2", "kind": "baremetal", "cluster": "site2-edge", "role": "edge-server", "count": 1},
    )
    assert payload["hostnames"] == ["SITE2-CTRL003"]
    assert payload["sibling"] == "SITE2-CTRL002"
    assert payload.get("pattern_note") in (None, "")


def test_mixed_hostname_prefix_warns(repo_root: Path) -> None:
    payload = device_preview(
        repo_root,
        {
            "site": "site2",
            "kind": "baremetal",
            "cluster": "site2-edge",
            "role": "apps-worker",
            "fields": {"hostname": "SITE2-CTRL009"},
        },
    )
    warnings = payload.get("warnings") or []
    assert any("CTRL" in item and "apps-worker" in item for item in warnings)
    assert payload["hostnames"] == ["SITE2-CTRL009"]


def test_preview_graph_recalculates_on_ip_edit(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    client = _client(root, edit=True)
    response = client.post(
        "/api/wizard/device/preview",
        json={
            "site": "site-a",
            "kind": "baremetal",
            "cluster": "k8s-prod",
            "role": "k8s-worker",
            "fields": {"networking.eno1.ip": "10.0.1.22/24"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    created = next(item for item in payload["files"] if item["action"] == "create")
    assert "10.0.1.22/24" in created["content"]
    node = next(item for item in payload["graphs"]["network"]["nodes"] if item["id"] == "site-a:server-02")
    assert "10.0.1.22" in str(node.get("ip") or "")
    field = next(item for item in payload["fields"] if item["path"] == "networking.eno1.ip")
    assert field["auto"] is False


def test_save_warns_about_uncommitted_and_detects_external_change(tmp_path: Path) -> None:
    root = _prepared(tmp_path)
    (root / "scratch.txt").write_text("dirty", encoding="utf-8")  # cambios ajenos: ya no bloquean
    client = _client(root, edit=True)
    body = {"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker"}
    preview = client.post("/api/wizard/device/preview", json=body).json()
    cluster = next(f["path"] for f in preview["files"] if f["path"].endswith("k8s-prod.yaml"))
    # alguien edita el archivo del clúster por fuera: aviso en la preview y conflicto con el token viejo
    target = root / cluster
    target.write_text(target.read_text() + "# editado a mano\n", encoding="utf-8")
    again = client.post("/api/wizard/device/preview", json=body).json()
    assert [p["path"] for p in again["pending"]] == [cluster]
    assert "editado a mano" in again["pending"][0]["diff"]
    stale = client.post("/api/wizard/device/save", json={**body, "token": preview["token"]})
    assert stale.status_code == 409
    ok = client.post("/api/wizard/device/save", json={**body, "token": again["token"]})
    assert ok.status_code == 200, ok.text


def subprocess_branches(root: Path) -> list[str]:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(root), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
