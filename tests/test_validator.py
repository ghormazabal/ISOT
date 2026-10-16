"""Tests del validador: inventario de ejemplo, colisiones y referencias."""

from __future__ import annotations

from pathlib import Path

from isot.validator import collect_issues, validate

from tests.conftest import seed_mini_repo, write_yaml


def test_sample_inventory_is_valid(repo_root: Path, capsys) -> None:
    code = validate(repo_root)
    captured = capsys.readouterr()
    assert code == 0, captured.out
    assert "Resultado: OK" in captured.out
    assert collect_issues(repo_root) == []


def test_mini_repo_is_valid(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    assert collect_issues(root) == []


def test_duplicate_ip_within_site_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-02.yaml",
        {
            "hostname": "server-02",
            "role": "k8s-worker",
            "cluster_ref": "k8s-prod",
            "networking": [
                {
                    "interface": "eno1",
                    "ip": "10.0.1.15/32",
                    "vlan": 100,
                    "connected_to": {"device": "switch-core", "port": "GigabitEthernet1/0/6"},
                }
            ],
        },
    )
    issues = collect_issues(root)
    assert any("IP duplicada en site-a: 10.0.1.15" in issue for issue in issues)
    assert validate(root) != 0


def test_same_ip_in_different_sites_is_allowed(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-b" / "devices" / "baremetal" / "edge-02.yaml",
        {
            "hostname": "edge-02",
            "networking": [{"interface": "eno1", "ip": "10.0.1.15/24", "vlan": 100}],
        },
    )
    issues = collect_issues(root)
    assert not any("10.0.1.15" in issue and "duplicada" in issue for issue in issues)


def test_shared_address_space_collision_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-b" / "devices" / "network" / "switch-edge.yaml",
        {
            "hostname": "switch-edge",
            "role": "edge-switch",
            "networking": [
                {"interface": "Vlan100", "ip": "10.1.1.1/24", "vlan": 100},
                {
                    "interface": "Ethernet31",
                    "ip": "192.0.2.1/24",
                    "vlan": 4094,
                    "connected_to": {"device": "switch-core", "site": "site-a", "port": "Ethernet31"},
                },
            ],
        },
    )
    issues = collect_issues(root)
    assert any("address_space dci-overlay" in issue and "192.0.2.1" in issue for issue in issues)


def test_broken_cluster_ref_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-missing",
            "networking": [
                {
                    "interface": "eno1",
                    "ip": "10.0.1.15/24",
                    "connected_to": {"device": "switch-core", "port": "Gi1/0/1"},
                }
            ],
        },
    )
    issues = collect_issues(root)
    assert any("cluster_ref='k8s-missing'" in issue for issue in issues)
    assert validate(root) != 0


def test_broken_cross_site_connected_to_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "switch-core.yaml",
        {
            "hostname": "switch-core",
            "networking": [
                {
                    "interface": "Ethernet31",
                    "ip": "192.0.2.1/24",
                    "connected_to": {"device": "ghost-gw", "site": "site-b", "port": "Ethernet31"},
                }
            ],
        },
    )
    issues = collect_issues(root)
    assert any("connected_to.device='ghost-gw'" in issue and "site-b" in issue for issue in issues)


def test_broken_template_ref_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "template_ref": "missing-template",
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
        },
    )
    issues = collect_issues(root)
    assert any("template_ref='missing-template'" in issue for issue in issues)
    assert validate(root) != 0


def test_metallb_pool_outside_subnet_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
            "metallb": {
                "pools": [
                    {
                        "name": "bad",
                        "subnet": "mgmt",
                        "addresses": ["198.51.100.10-198.51.100.20"],
                    }
                ]
            },
        },
    )
    issues = collect_issues(root)
    assert any("MetalLB" in issue and "198.51.100.10" in issue for issue in issues)


def test_nad_unknown_vlan_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
            "nads": [{"namespace": "ns", "name": "foo", "vlan": 9999}],
        },
    )
    issues = collect_issues(root)
    assert any("vlan=9999" in issue for issue in issues)


def test_cluster_node_cluster_ref_mismatch(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "other-cluster",
            "networking": [{"interface": "eno1", "ip": "10.0.1.15/24"}],
        },
    )
    issues = collect_issues(root)
    assert any("cluster_ref='other-cluster'" in issue for issue in issues)
