"""Tests de la API FastAPI: índices ligeros y carga perezosa del YAML."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from isot.inventory import load_yaml
from isot.web import create_app, graph_from_index, kubernetes_graph, load_device_index


def test_sites_indexes_and_graph_do_not_parse_yaml(repo_root: Path) -> None:
    app = create_app(repo_root)
    client = TestClient(app)
    with patch("isot.web.load_yaml", side_effect=AssertionError("no hay que parsear YAML aquí")):
        sites = client.get("/api/sites")
        devices = client.get("/api/sites/site1/devices")
        graph = client.get("/api/sites/site1/graph")
        clusters = client.get("/api/sites/site1/clusters")
        templates = client.get("/api/templates")
        k8s_graph = client.get("/api/sites/site1/graph", params={"view": "kubernetes"})
        global_graph = client.get("/api/graph", params={"view": "global"})
    assert sites.status_code == 200
    ids = {item["id"] for item in sites.json()}
    assert ids == {"site1", "site2"}
    assert devices.status_code == 200
    body = devices.json()
    assert len(body) == 22
    names = {item["hostname"] for item in body}
    assert "SITE1-CTRL001" in names
    assert "SITE1-MGMT001" in names
    assert graph.status_code == 200
    assert clusters.status_code == 200
    assert {item["name"] for item in clusters.json()} >= {"site1-apps", "site1-manager", "site1-platform"}
    assert templates.status_code == 200
    assert any(item["name"] == "rke2-rancher-hybrid-calico" for item in templates.json())
    assert k8s_graph.status_code == 200
    assert global_graph.status_code == 200
    payload = graph.json()
    assert len(payload["nodes"]) >= 22
    pairs = {(e["source"], e["target"]) for e in payload["edges"]}
    assert any("LEAF001" in (a + b) and "CTRL001" in (a + b) for a, b in pairs)
    global_ids = {node["id"] for node in global_graph.json()["nodes"]}
    assert "site:site1" in global_ids
    assert "site:site2" in global_ids


def test_device_detail_is_lazy_and_complete(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    listed = client.get("/api/sites/site1/devices").json()
    assert "os" not in listed[0]
    assert "networking" not in listed[0]

    response = client.get("/api/sites/site1/devices/SITE1-CTRL001")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["hostname"] == "SITE1-CTRL001"
    assert data["cluster_ref"] == "site1-platform"
    assert data["os"] == "Ubuntu 22.04 LTS"
    assert data["vendor"] == "generic"
    assert data["model"] == "compute-2u"
    assert isinstance(data["networking"], list)
    assert data["networking"][0]["ip"] == "10.10.17.11/24"


def test_cluster_template_and_topology_endpoints(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    cluster = client.get("/api/sites/site1/clusters/site1-apps")
    assert cluster.status_code == 200
    body = cluster.json()
    assert body["data"]["template_ref"] == "rke2-rancher-hybrid-calico"
    assert body["data"]["pod_cidr"] == "10.42.0.0/16"
    assert body.get("template_site") == "global"
    assert "SITE1-NODE002" in body["members"]

    template = client.get("/api/templates/rke2-rancher-hybrid-calico")
    assert template.status_code == 200
    tbody = template.json()
    assert tbody["data"]["distro"] == "rke2"
    assert tbody["path"] == "cluster-templates/rke2-rancher-hybrid-calico/template.yaml"
    assert "site1/site1-apps" in tbody["used_by"]
    assert "site2/site2-edge" in tbody["used_by"]
    assert any(item["name"] == "metallb" for item in tbody["charts"])
    assert all("values_inline" not in (item or {}) for item in tbody["data"].get("charts") or [])
    values = client.get("/api/templates/rke2-rancher-hybrid-calico/values/rke2-rancher-hybrid-calico")
    assert values.status_code == 200
    assert values.json()["values"]["cniConfig"]["podCIDR"] == "10.42.0.0/16"

    topology = client.get("/api/sites/site1/topology")
    assert topology.status_code == 200
    subnets = topology.json()["subnets"]
    assert any(item["vlan"] == 2003 for item in subnets)

    missing = client.get("/api/sites/site1/devices/no-existe")
    assert missing.status_code == 404
    bad = client.get("/api/sites/site1/devices/no@valido")
    assert bad.status_code == 400


def test_graph_from_index_matches_committed_index(repo_root: Path) -> None:
    entries = load_device_index(repo_root, "site1")
    graph = graph_from_index(entries)
    assert {node["hostname"] for node in graph["nodes"] if not node.get("remote")} == {
        e["hostname"] for e in entries
    }
    yaml_path = repo_root / "sites" / "site1" / "devices" / "baremetal" / "SITE1-CTRL001.yaml"
    assert yaml_path.is_file()
    raw = load_yaml(yaml_path)
    assert raw["os"] == "Ubuntu 22.04 LTS"


def test_kubernetes_graph_links_template_cluster_nodes(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    payload = client.get("/api/sites/site1/graph", params={"view": "kubernetes"}).json()
    ids = {node["id"] for node in payload["nodes"]}
    assert "template:global:rke2-rancher-hybrid-calico" in ids
    assert "cluster:site1:site1-apps" in ids
    assert "site1:SITE1-NODE002" in ids
    rels = {(e["source"], e["target"]) for e in payload["edges"]}
    assert ("template:global:rke2-rancher-hybrid-calico", "cluster:site1:site1-apps") in rels
    assert ("cluster:site1:site1-apps", "site1:SITE1-NODE002") in rels
    kubernetes_graph([], [], [])  # no crash


def test_site2_and_dci_link(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    devices = client.get("/api/sites/site2/devices").json()
    names = {item["hostname"] for item in devices}
    assert names == {"SITE2-CTRL001", "SITE2-CTRL002", "SITE2-LEAF001", "SITE2-DCGW001"}
    graph = client.get("/api/sites/site1/graph").json()
    ids = {node["id"] for node in graph["nodes"]}
    assert "site2:SITE2-DCGW001" in ids
    edge = client.get("/api/sites/site2/clusters/site2-edge").json()
    assert edge["data"]["template_ref"] == "rke2-rancher-hybrid-calico"
