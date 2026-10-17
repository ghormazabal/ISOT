"""Detalle de conexiones (aristas del grafo): GET /api/link y POST /api/link/preview."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from isot.gitdiff import clear_caches
from isot.links import diff_details, link_details, overlay_files, view_from_tree
from isot.web import create_app

from tests.conftest import git_commit_all, git_init_and_commit, seed_mini_repo, write_yaml


def _by_label(detail: dict) -> dict[str, dict]:
    return {link["label"]: link for link in detail["links"]}


def test_bond_link_lists_every_bond_with_vlans_and_sources(repo_root: Path) -> None:
    view = view_from_tree(repo_root)
    detail = link_details(view, "site1:SITE1-NODE007", "site1:SITE1-LEAF003")
    assert detail is not None and detail["kind"] == "network"
    assert detail["source"]["hostname"] == "SITE1-NODE007"
    assert detail["source"]["role"] == "apps-worker"
    assert detail["source"]["cluster_ref"] == "site1-apps"
    links = _by_label(detail)
    assert set(links) >= {"bondC → PortChannel39", "bondD01 → PortChannel38", "bondS → PortChannel42"}
    bond_c = links["bondC → PortChannel39"]
    assert bond_c["type"] == "bond"
    assert bond_c["source"]["bond"] == "bondC"
    assert bond_c["target"]["port"] == "PortChannel39"
    assert bond_c["target"]["port_channel"] == "PortChannel39"
    # No hay modo LACP modelado en este bond: queda vacío, no se inventa.
    assert bond_c["source"]["lacp"] is None
    vlans = {vlan["id"]: vlan for vlan in bond_c["vlans"]}
    assert vlans[2003]["name"] == "CLOUD_MGMT_SW"
    assert vlans[2003]["cidr"] == "10.10.17.0/24"
    assert vlans[2003]["ip"] == "10.10.17.24/24"
    assert vlans[2005]["interface"] == "APP_CTRL"
    keys = {(src["path"], src["key"]) for src in bond_c["sources"]}
    assert ("sites/site1/devices/baremetal/SITE1-NODE007.yaml", "bonds[0]") in keys
    assert any(src["yaml"].startswith("name: bondC") for src in bond_c["sources"])
    assert {v["id"] for v in detail["vlans"]} == {2000, 2003, 2005, 2006}
    assert "PortChannel39" in detail["ports"]


def test_lacp_members_and_mtu_come_from_the_model(repo_root: Path) -> None:
    view = view_from_tree(repo_root)
    detail = link_details(view, "site1:SITE1-CTRL001", "site1:SITE1-LEAF001")
    bond = _by_label(detail)["bondC → PortChannel22"]
    assert bond["source"]["lacp"] == "802.3ad"
    assert bond["source"]["members"] == ["ens1f1np1", "ens3f0np0"]
    assert bond["target"]["device_mtu"] == 9216
    node = link_details(view, "site1:SITE1-NODE002", "site1:SITE1-LEAF001")
    assert _by_label(node)["bondC → PortChannel43"]["source"]["mtu"] == 4470
    assert _by_label(node)["bondD00 → PortChannel41"]["source"]["extra"]["note"].startswith("PortChannel mode on")


def test_order_of_endpoints_does_not_matter(repo_root: Path) -> None:
    view = view_from_tree(repo_root)
    forward = link_details(view, "site1:SITE1-NODE007", "site1:SITE1-LEAF003")
    backward = link_details(view, "site1:SITE1-LEAF003", "site1:SITE1-NODE007")
    assert sorted(_by_label(forward)) == sorted(_by_label(backward))
    assert _by_label(backward)["bondC → PortChannel39"]["declared_by"] == "target"


def test_vm_interfaces_hypervisor_and_oob(repo_root: Path) -> None:
    view = view_from_tree(repo_root)
    vm = _by_label(link_details(view, "site1:site1-appvm002", "site1:SITE1-CTRL002"))
    eth0 = vm["eth0 → br-app2005"]
    assert eth0["type"] == "interface"
    assert eth0["source"]["ip"] == "10.10.19.3/24"
    assert eth0["source"]["extra"]["nad"] == "apps-control-plane/app-ctrl"
    assert eth0["vlans"][0]["name"] == "APP_CTRL"
    assert vm["VM sobre hypervisor"]["source"]["extra"]["namespace"] == "apps-control-plane"
    oob = _by_label(link_details(view, "site1:SITE1-CTRL001", "site1:SITE1-MGMT001"))["OOB bmc → Ethernet0"]
    assert oob["source"]["ip"] == "10.10.16.11/24"
    assert oob["target"]["port"] == "Ethernet0"
    assert oob["vlans"][0]["id"] == 2002 and oob["vlans"][0]["implicit"] is True


def test_dci_and_kubernetes_references(repo_root: Path) -> None:
    view = view_from_tree(repo_root)
    dci = link_details(view, "site:site1", "site:site2")
    assert dci["kind"] == "dci"
    assert dci["links"] and all(link.get("pair") for link in dci["links"])
    assert any(vlan["name"] == "DCI" for vlan in dci["vlans"])
    ref = link_details(view, "cluster:site1:site1-apps", "site1:SITE1-NODE007")
    assert ref["kind"] == "kubernetes"
    assert ref["links"][0]["attributes"]["field"] == "cluster_ref"
    assert ref["links"][0]["attributes"]["k8s_roles"] == ["worker"]
    assert ref["links"][0]["sources"][0]["key"] == "cluster_ref"
    assert link_details(view, "site1:SITE1-NODE007", "site1:SITE1-SPNE001") is None


def test_api_link_status_codes(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    ok = client.get("/api/link", params={"source": "site1:SITE1-NODE007", "target": "site1:SITE1-LEAF003"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["diff"] is None and body["detail"]["links"]
    missing = client.get("/api/link", params={"source": "site1:NOPE", "target": "site1:SITE1-LEAF003"})
    assert missing.status_code == 404
    bad = client.get("/api/link", params={"source": "../x", "target": "site1:SITE1-LEAF003"})
    assert bad.status_code == 400


def test_api_link_preview_overlays_proposed_device(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    content = (
        "hostname: SITE1-NODE099\nrole: apps-worker\ncluster_ref: site1-apps\n"
        "bonds:\n  - name: bondC\n    mode: 802.3ad\n    leaf_pc: {SITE1-LEAF003: PortChannel90, SITE1-LEAF004: PortChannel90}\n"
        "    allowed_vlans: [2003]\n"
        "networking:\n  - interface: MGMT_SW\n    parent: bondC\n    vlan: 2003\n    ip: 10.10.17.99/24\n"
    )
    files = [{"path": "sites/site1/devices/baremetal/SITE1-NODE099.yaml", "content": content}]
    res = client.post("/api/link/preview", json={"source": "site1:SITE1-NODE099", "target": "site1:SITE1-LEAF003", "files": files})
    assert res.status_code == 200
    detail = res.json()["detail"]
    assert detail["source"]["proposed"] is True
    bond = _by_label(detail)["bondC → PortChannel90"]
    assert bond["source"]["lacp"] == "802.3ad"
    assert bond["vlans"][0]["ip"] == "10.10.17.99/24"
    # Rutas fuera de devices/topology se ignoran.
    view = overlay_files(view_from_tree(repo_root), [{"path": "../../etc/passwd", "content": "hostname: x"}])
    assert "site1:x" not in view.devices


def test_link_diff_between_refs(tmp_path: Path) -> None:
    clear_caches()
    root = seed_mini_repo(tmp_path / "inv")
    base = git_init_and_commit(root, "base")
    server = root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml"
    write_yaml(
        server,
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-prod",
            "networking": [
                {
                    "interface": "eno1",
                    "ip": "10.0.1.16/24",
                    "vlan": 100,
                    "mtu": 9000,
                    "connected_to": {"device": "switch-core", "port": "GigabitEthernet1/0/6"},
                }
            ],
        },
    )
    head = git_commit_all(root, "cambia puerto, ip y mtu")
    client = TestClient(create_app(root))
    res = client.get(
        "/api/link",
        params={"source": "site-a:server-01", "target": "site-a:switch-core", "base": base, "head": head},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["diff"]["status"] == "modified"
    changes = {item["path"]: item for item in body["diff"]["changes"]}
    assert any(item["status"] == "removed" and "GigabitEthernet1/0/5" in path for path, item in changes.items())
    assert any(item["status"] == "added" and "GigabitEthernet1/0/6" in path for path, item in changes.items())
    assert any(path.endswith("source.mtu") and item["after"] == 9000 for path, item in changes.items())
    assert body["base"] and body["head"]


def test_diff_details_statuses() -> None:
    assert diff_details(None, None)["status"] == "missing"
    detail = {"links": [{"type": "interface", "label": "a → b", "declared_by": "source", "source": {"ip": "1"}, "target": {}, "vlans": [], "attributes": {}}]}
    assert diff_details(None, detail)["status"] == "added"
    assert diff_details(detail, None)["status"] == "removed"
    assert diff_details(detail, detail) == {"status": "unchanged", "changes": []}
