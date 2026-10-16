"""Utilidades compartidas para los tests ISoT."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from isot.paths import find_repo_root


@pytest.fixture
def repo_root() -> Path:
    """Raíz del clon con el inventario de ejemplo."""
    return find_repo_root(Path(__file__).resolve().parent)


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def seed_mini_repo(root: Path) -> Path:
    """Dos sitios mínimos: DCI compartido y un template global."""
    write_yaml(
        root / "sites.yaml",
        {
            "sites": [
                {"id": "site-a", "name": "SITE-A", "path": "sites/site-a"},
                {"id": "site-b", "name": "SITE-B", "path": "sites/site-b"},
            ],
            "address_spaces": [
                {
                    "id": "dci-overlay",
                    "sites": ["site-a", "site-b"],
                    "match": {"subnets": ["DCI"]},
                }
            ],
        },
    )
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "template.yaml",
        {
            "template_name": "rke2-lab",
            "distro": "rke2",
            "cni": {"primary": "calico", "defaults": {"podCIDR": "10.42.0.0/16"}},
            "charts": [
                {
                    "name": "cluster",
                    "repo": "oci://registry.example.net/cluster-templates",
                    "chart": "rke2-lab",
                    "version": "1.0.0",
                    "namespace": "",
                    "order": 0,
                    "depends_on": [],
                    "values_ref": "values/cluster.yaml",
                }
            ],
        },
    )
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "values" / "cluster.yaml",
        {"cniConfig": {"podCIDR": "10.42.0.0/16", "mtu": 1440}},
    )
    write_yaml(
        root / "sites" / "site-a" / "topology" / "index.yaml",
        {
            "subnets": [
                {"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100, "gateway": "10.0.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
            ],
            "vlans": [
                {"id": 100, "name": "mgmt", "subnet": "mgmt"},
                {"id": 4094, "name": "DCI", "subnet": "DCI"},
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-b" / "topology" / "index.yaml",
        {
            "subnets": [
                {"name": "mgmt", "cidr": "10.1.1.0/24", "vlan": 100, "gateway": "10.1.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
            ],
            "vlans": [
                {"id": 100, "name": "mgmt", "subnet": "mgmt"},
                {"id": 4094, "name": "DCI", "subnet": "DCI"},
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "template_ref": "rke2-lab",
            "values_overrides": [
                {
                    "release": "cluster",
                    "values_inline": {
                        "cluster": {"name": "k8s-prod"},
                        "cniConfig": {"podCIDR": "10.42.0.0/16"},
                    },
                }
            ],
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
            "nads": [{"namespace": "ns", "name": "mgmt-nad", "vlan": 100, "subnet": "mgmt"}],
            "metallb": {"pools": [{"name": "mgmt", "subnet": "mgmt", "addresses": ["10.0.1.50-10.0.1.60"]}]},
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "network" / "switch-core.yaml",
        {
            "hostname": "switch-core",
            "role": "core-switch",
            "networking": [
                {"interface": "Vlan100", "ip": "10.0.1.1/24", "vlan": 100},
                {
                    "interface": "Ethernet31",
                    "ip": "192.0.2.1/24",
                    "vlan": 4094,
                    "connected_to": {"device": "switch-edge", "site": "site-b", "port": "Ethernet31"},
                },
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-prod",
            "os": "ubuntu-24.04",
            "networking": [
                {
                    "interface": "eno1",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ip": "10.0.1.15/24",
                    "vlan": 100,
                    "connected_to": {"device": "switch-core", "port": "GigabitEthernet1/0/5"},
                }
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-b" / "clusters" / "k8s-edge.yaml",
        {
            "cluster_name": "k8s-edge",
            "kind": "kubernetes",
            "template_ref": "rke2-lab",
            "nodes": [{"hostname": "edge-01", "roles": ["worker"]}],
        },
    )
    write_yaml(
        root / "sites" / "site-b" / "devices" / "network" / "switch-edge.yaml",
        {
            "hostname": "switch-edge",
            "role": "edge-switch",
            "networking": [
                {"interface": "Vlan100", "ip": "10.1.1.1/24", "vlan": 100},
                {
                    "interface": "Ethernet31",
                    "ip": "192.0.2.2/24",
                    "vlan": 4094,
                    "connected_to": {"device": "switch-core", "site": "site-a", "port": "Ethernet31"},
                },
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-b" / "devices" / "baremetal" / "edge-01.yaml",
        {
            "hostname": "edge-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-edge",
            "networking": [
                {
                    "interface": "eno1",
                    "ip": "10.1.1.15/24",
                    "vlan": 100,
                    "connected_to": {"device": "switch-edge", "port": "Gi1/0/1"},
                }
            ],
        },
    )
    return root


def _git_ident_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "ISoT Tests",
        "GIT_AUTHOR_EMAIL": "dev@example.net",
        "GIT_COMMITTER_NAME": "ISoT Tests",
        "GIT_COMMITTER_EMAIL": "dev@example.net",
    }


def git_init_and_commit(root: Path, message: str = "base") -> str:
    """Inicializa un repo git en root y hace un commit. Devuelve el SHA."""
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.net"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ISoT Tests"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        check=True,
        capture_output=True,
        env=_git_ident_env(),
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sha.stdout.strip()


def git_commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        check=True,
        capture_output=True,
        env=_git_ident_env(),
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return sha.stdout.strip()
