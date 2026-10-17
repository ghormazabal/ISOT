"""Reglas de cableado: generación spine-leaf, pares y warnings."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import yaml

from isot.cabling import (
    ARCH_SPINE_LEAF,
    check_device,
    collect_connection_warnings,
    default_policy,
    discover_pairs,
    host_capacity,
    infer_policy,
    leafs_of,
    load_or_infer,
    pick_pair,
    policy_block,
)
from isot.indexer import write_indexes
from isot.validator import collect_issues, collect_warnings, format_report, validate
from isot.web import create_app
from isot.wizard import device_preview
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _yaml_map(files: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in files:
        if not str(item["path"]).endswith(".yaml"):
            continue
        data = yaml.safe_load(item["content"])
        if isinstance(data, dict) and data.get("hostname"):
            out[str(data["hostname"])] = data
    return out


def test_default_spine_leaf_policy() -> None:
    policy = default_policy(ARCH_SPINE_LEAF, spines=2, leaf_pairs=2)
    assert policy["architecture"] == ARCH_SPINE_LEAF
    assert policy["fabric"]["spines"] == 2
    assert policy["fabric"]["leaf_pairs"] == 2
    ids = {item["id"] for item in policy["rules"]}
    assert ids >= {"compute-mlag", "storage-bond", "leaf-to-spine", "oob-bmc"}
    mlag = next(item for item in policy["rules"] if item["id"] == "compute-mlag")
    assert mlag["leafs_per_node"] == 2
    assert mlag["bond_name"] == "bondC"


def test_pick_pair_fill_first_then_overflow() -> None:
    policy = default_policy(ARCH_SPINE_LEAF, spines=2, leaf_pairs=2, ports_per_leaf=4)
    policy["pairs"] = [
        {"id": "PAIR1", "leafs": ["L1", "L2"]},
        {"id": "PAIR2", "leafs": ["L3", "L4"]},
    ]
    assert host_capacity(policy) == 1
    devices: list[dict] = [
        {"hostname": "L1", "role": "server-leaf", "leaf_pair": "PAIR1", "pair": "L2"},
        {"hostname": "L2", "role": "server-leaf", "leaf_pair": "PAIR1", "pair": "L1"},
        {"hostname": "L3", "role": "server-leaf", "leaf_pair": "PAIR2", "pair": "L4"},
        {"hostname": "L4", "role": "server-leaf", "leaf_pair": "PAIR2", "pair": "L3"},
    ]
    first = pick_pair(policy, devices, rr_index=0)
    assert first and first["id"] == "PAIR1"
    devices.append(
        {
            "hostname": "N1",
            "role": "worker",
            "k8s_roles": ["worker"],
            "leaf_pair": "PAIR1",
            "bonds": [{"name": "bondC", "leaf_pc": {"L1": "PortChannel1", "L2": "PortChannel1"}}],
        }
    )
    second = pick_pair(policy, devices, rr_index=1)
    assert second and second["id"] == "PAIR2"


def test_pick_pair_round_robin() -> None:
    policy = default_policy(ARCH_SPINE_LEAF, spines=2, leaf_pairs=2, pair_policy="round-robin")
    policy["pairs"] = [
        {"id": "PAIR1", "leafs": ["L1", "L2"]},
        {"id": "PAIR2", "leafs": ["L3", "L4"]},
    ]
    assert pick_pair(policy, [], rr_index=0)["id"] == "PAIR1"
    assert pick_pair(policy, [], rr_index=1)["id"] == "PAIR2"
    assert pick_pair(policy, [], rr_index=2)["id"] == "PAIR1"


def test_infer_pairs_from_leaf_pc() -> None:
    devices = [
        {
            "hostname": "w1",
            "role": "worker",
            "bonds": [{"name": "bondC", "leaf_pc": {"LEAF-A": "PortChannel1", "LEAF-B": "PortChannel1"}}],
        },
        {"hostname": "LEAF-A", "role": "server-leaf", "pair": "LEAF-B"},
        {"hostname": "LEAF-B", "role": "server-leaf", "pair": "LEAF-A"},
        {"hostname": "SPINE1", "role": "spine"},
    ]
    pairs = discover_pairs(devices)
    assert len(pairs) == 1
    assert set(pairs[0]["leafs"]) == {"LEAF-A", "LEAF-B"}
    inferred = infer_policy(devices)
    assert inferred["architecture"] == ARCH_SPINE_LEAF
    assert inferred["inferred"] is True


def test_check_device_warns_single_leaf() -> None:
    policy = default_policy(ARCH_SPINE_LEAF, spines=2, leaf_pairs=1)
    pairs = [{"id": "PAIR1", "leafs": ["L1", "L2"]}]
    worker = {
        "hostname": "bad-worker",
        "role": "worker",
        "k8s_roles": ["worker"],
        "networking": [{"interface": "eth0", "connected_to": {"device": "L1", "port": "Ethernet3"}}],
    }
    warnings = check_device(worker, policy, pairs)
    assert any("conectado a 1 leaf" in item for item in warnings)


def test_site_preview_spine_leaf_two_pairs(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    client = TestClient(create_app(root, edit=True))
    body = {
        "id": "site-c",
        "name": "SITE-C",
        "architecture": "spine-leaf",
        "spines": 2,
        "leaf_pairs": 2,
        "mgmt_switches": 1,
        "dcgw": 1,
        "ports_per_leaf": 48,
        "pair_policy": "fill-first",
        "template_site": "site-b",
        "infra_range": "10.90.0.0/20",
        "dci": False,
        "clusters": [{"name": "site-c-edge", "template_ref": "rke2-lab", "workers": 2}],
    }
    response = client.post("/api/wizard/site/preview", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    hosts = _yaml_map(payload["files"])
    assert "SITE-C-SPINE001" in hosts and "SITE-C-SPINE002" in hosts
    assert {"SITE-C-LEAF001", "SITE-C-LEAF002", "SITE-C-LEAF003", "SITE-C-LEAF004"} <= set(hosts)
    assert hosts["SITE-C-LEAF001"]["leaf_pair"] == "PAIR1"
    assert hosts["SITE-C-LEAF003"]["leaf_pair"] == "PAIR2"
    n1 = hosts["SITE-C-NODE001"]
    n2 = hosts["SITE-C-NODE002"]
    assert set(n1["bonds"][0]["leaf_pc"]) == {"SITE-C-LEAF001", "SITE-C-LEAF002"}
    assert n1["leaf_pair"] == "PAIR1"
    assert n2["leaf_pair"] == "PAIR1"
    assert len(leafs_of(n1)) >= 2
    topo = next(item for item in payload["files"] if item["path"].endswith("topology/index.yaml"))
    topo_data = yaml.safe_load(topo["content"])
    assert topo_data["architecture"] == "spine-leaf"
    assert topo_data["connection_rules"]["pair_policy"] == "fill-first"
    rule_ids = {item["id"] for item in topo_data["connection_rules"]["rules"]}
    assert "compute-mlag" in rule_ids
    graph_nodes = {item["id"]: item for item in payload["graphs"]["network"]["nodes"]}
    assert graph_nodes["site-c:SITE-C-SPINE001"]["leaf_pair"] == "spines"
    assert graph_nodes["site-c:SITE-C-NODE001"]["leaf_pair"] == "PAIR1"


def test_fill_first_spills_to_second_pair(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    client = TestClient(create_app(root, edit=True))
    body = {
        "id": "site-c",
        "name": "SITE-C",
        "architecture": "spine-leaf",
        "spines": 2,
        "leaf_pairs": 2,
        "mgmt_switches": 0,
        "dcgw": 0,
        "ports_per_leaf": 4,
        "pair_policy": "fill-first",
        "template_site": "site-b",
        "dci": False,
        "clusters": [{"name": "site-c-edge", "template_ref": "rke2-lab", "workers": 2}],
    }
    response = client.post("/api/wizard/site/preview", json=body)
    assert response.status_code == 200, response.text
    hosts = _yaml_map(response.json()["files"])
    assert hosts["SITE-C-NODE001"]["leaf_pair"] == "PAIR1"
    assert hosts["SITE-C-NODE002"]["leaf_pair"] == "PAIR2"
    assert set(hosts["SITE-C-NODE002"]["bonds"][0]["leaf_pc"]) == {"SITE-C-LEAF003", "SITE-C-LEAF004"}


def test_round_robin_alternates_pairs(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    client = TestClient(create_app(root, edit=True))
    body = {
        "id": "site-c",
        "name": "SITE-C",
        "architecture": "spine-leaf",
        "spines": 2,
        "leaf_pairs": 2,
        "ports_per_leaf": 48,
        "pair_policy": "round-robin",
        "template_site": "site-b",
        "dci": False,
        "clusters": [{"name": "edge", "template_ref": "rke2-lab", "workers": 2}],
    }
    hosts = _yaml_map(client.post("/api/wizard/site/preview", json=body).json()["files"])
    assert hosts["SITE-C-NODE001"]["leaf_pair"] == "PAIR1"
    assert hosts["SITE-C-NODE002"]["leaf_pair"] == "PAIR2"


def test_device_preview_infers_rules_and_can_save(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    client = TestClient(create_app(root, edit=True))
    preview = client.post(
        "/api/wizard/device/preview",
        json={"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker"},
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["rules_source"] == "inferred"
    assert payload["inferred_rules"]["architecture"] in {"spine-leaf", "collapsed-core"}
    saved = client.post(
        "/api/wizard/device/preview",
        json={
            "site": "site-a",
            "kind": "baremetal",
            "cluster": "k8s-prod",
            "role": "k8s-worker",
            "save_inferred_rules": True,
        },
    )
    assert saved.status_code == 200, saved.text
    files = {item["path"]: item for item in saved.json()["files"]}
    assert "sites/site-a/topology/index.yaml" in files
    assert files["sites/site-a/topology/index.yaml"]["action"] == "modify"
    assert "connection_rules" in files["sites/site-a/topology/index.yaml"]["content"]


def test_device_respects_declared_pair_policy(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_yaml(
        root / "sites" / "site-a" / "topology" / "index.yaml",
        {
            "architecture": "spine-leaf",
            "connection_rules": policy_block(
                default_policy(ARCH_SPINE_LEAF, spines=0, leaf_pairs=2, mgmt_switches=0, dcgw=0, ports_per_leaf=2)
            ),
            "subnets": [
                {"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100, "gateway": "10.0.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
            ],
            "vlans": [{"id": 100, "name": "mgmt", "subnet": "mgmt"}, {"id": 4094, "name": "DCI", "subnet": "DCI"}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF001.yaml",
        {"hostname": "LEAF001", "role": "server-leaf", "pair": "LEAF002", "leaf_pair": "PAIR1", "kind": "network"},
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF002.yaml",
        {"hostname": "LEAF002", "role": "server-leaf", "pair": "LEAF001", "leaf_pair": "PAIR1", "kind": "network"},
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF003.yaml",
        {"hostname": "LEAF003", "role": "server-leaf", "pair": "LEAF004", "leaf_pair": "PAIR2", "kind": "network"},
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF004.yaml",
        {"hostname": "LEAF004", "role": "server-leaf", "pair": "LEAF003", "leaf_pair": "PAIR2", "kind": "network"},
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "kind": "baremetal",
            "cluster_ref": "k8s-prod",
            "k8s_roles": ["worker"],
            "leaf_pair": "PAIR1",
            "bonds": [{"name": "bondC", "leaf_pc": {"LEAF001": "PortChannel10", "LEAF002": "PortChannel10"}}],
            "networking": [
                {
                    "interface": "eno1",
                    "vlan": 100,
                    "ip": "10.0.1.15/24",
                    "parent": "bondC",
                    "connected_to": {"device": "LEAF001", "port": "PortChannel10"},
                }
            ],
        },
    )
    write_indexes(root)
    git_init_and_commit(root, "base")
    payload = device_preview(
        root,
        {"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker", "count": 1},
    )
    assert payload["rules_source"] == "declared"
    created = next(item for item in payload["files"] if item["action"] == "create")
    data = yaml.safe_load(created["content"])
    assert set(data["bonds"][0]["leaf_pc"]) == {"LEAF003", "LEAF004"}
    assert data["leaf_pair"] == "PAIR2"


def test_validator_warns_but_does_not_fail(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_yaml(
        root / "sites" / "site-a" / "topology" / "index.yaml",
        {
            "architecture": "spine-leaf",
            "connection_rules": policy_block(default_policy(ARCH_SPINE_LEAF, spines=0, leaf_pairs=1, mgmt_switches=0, dcgw=0)),
            "subnets": [
                {"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100, "gateway": "10.0.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
            ],
            "vlans": [{"id": 100, "name": "mgmt", "subnet": "mgmt"}, {"id": 4094, "name": "DCI", "subnet": "DCI"}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF001.yaml",
        {"hostname": "LEAF001", "role": "server-leaf", "pair": "LEAF002", "leaf_pair": "PAIR1"},
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "LEAF002.yaml",
        {"hostname": "LEAF002", "role": "server-leaf", "pair": "LEAF001", "leaf_pair": "PAIR1"},
    )
    issues = collect_issues(root)
    warnings = collect_warnings(root)
    assert issues == []
    assert any("server-01 viola compute-mlag" in item and "1 leaf" in item for item in warnings)
    report = format_report(root, [], issues, warnings)
    assert "[WARN]" in report
    assert "Resultado: OK" in report
    assert validate(root) == 0


def test_cabling_defaults_endpoint(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    client = TestClient(create_app(root, edit=True))
    response = client.get("/api/wizard/cabling-defaults", params={"architecture": "spine-leaf"})
    assert response.status_code == 200
    body = response.json()
    assert body["architecture"] == "spine-leaf"
    assert body["fabric"]["spines"] == 2
    collapsed = client.get("/api/wizard/cabling-defaults", params={"architecture": "collapsed-core"}).json()
    assert collapsed["fabric"]["spines"] == 0
    assert collapsed["fabric"]["leaf_pairs"] == 1
    custom = client.get("/api/wizard/cabling-defaults", params={"architecture": "custom"}).json()
    assert custom["rules"] == []


def test_existing_site_without_rules_has_no_warnings(repo_root: Path) -> None:
    assert collect_connection_warnings(
        [{"hostname": "x", "role": "worker"}],
        {},
    ) == []
    assert collect_warnings(repo_root) == []


def test_load_or_infer_prefers_declared() -> None:
    topology = {"connection_rules": policy_block(default_policy(ARCH_SPINE_LEAF))}
    policy = load_or_infer(topology, [])
    assert policy["inferred"] is False
    inferred = load_or_infer({}, [{"hostname": "SP1", "role": "spine"}])
    assert inferred["inferred"] is True
    assert inferred["architecture"] == ARCH_SPINE_LEAF
