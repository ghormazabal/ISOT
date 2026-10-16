"""Diff semántico entre dos commits de un repo git temporal."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from isot.gitdiff import RefError, clear_caches, list_commits, list_refs, semantic_diff, validate_ref
from isot.web import create_app
from tests.conftest import git_commit_all, git_init_and_commit, seed_mini_repo, write_yaml


def _two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = seed_mini_repo(tmp_path / "inv")
    base = git_init_and_commit(root, "inventario base")

    topology = root / "sites" / "site-a" / "topology" / "index.yaml"
    write_yaml(
        topology,
        {
            "subnets": [
                {"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100, "gateway": "10.0.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
                {"name": "LAB_NET", "cidr": "10.0.31.0/24", "vlan": 2099},
            ],
            "vlans": [
                {"id": 100, "name": "mgmt", "subnet": "mgmt"},
                {"id": 4094, "name": "DCI", "subnet": "DCI"},
                {"id": 2099, "name": "LAB", "subnet": "LAB_NET"},
            ],
        },
    )
    (root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml").unlink()
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "values" / "cluster.yaml",
        {"cniConfig": {"podCIDR": "10.42.0.0/16", "mtu": 1500}},
    )
    head = git_commit_all(root, "vlan nueva, device quitado, value helm")
    return root, base, head


def test_validate_ref_rejects_injection() -> None:
    for bad in ("main; rm -rf /", "HEAD && cat", "$(whoami)", "foo bar", "a..b", "-evil"):
        try:
            validate_ref(bad)
        except RefError:
            continue
        raise AssertionError(f"debería rechazar {bad!r}")
    assert validate_ref("main") == "main"
    assert validate_ref("demo/diff-example") == "demo/diff-example"
    assert validate_ref("origin/main") == "origin/main"
    assert validate_ref("HEAD~3") == "HEAD~3"
    assert validate_ref("HEAD^2") == "HEAD^2"
    assert validate_ref("main~1") == "main~1"
    assert validate_ref("abc1234") == "abc1234"


def test_semantic_diff_vlan_device_and_values(tmp_path: Path) -> None:
    clear_caches()
    root, base, head = _two_commit_repo(tmp_path)
    payload = semantic_diff(root, base, head, site="site-a")
    by_id = {item["id"]: item for item in payload["entities"]}

    vlan = by_id["site-a:vlan:2099"]
    assert vlan["status"] == "added"
    assert vlan["kind"] == "network"

    subnet = by_id["site-a:subnet:LAB_NET"]
    assert subnet["status"] == "added"

    device = by_id["site-a:server-01"]
    assert device["status"] == "removed"
    assert device["kind"] == "device"

    values = by_id["template:global:rke2-lab:cluster"]
    assert values["kind"] == "values"
    assert values["status"] == "modified"
    mtu = next(row for row in values["changes"] if row["path"] == "cniConfig.mtu")
    assert mtu["before"] == 1440
    assert mtu["after"] == 1500
    assert mtu["status"] == "modified"

    assert payload["summary"]["added"] >= 2
    assert payload["summary"]["removed"] >= 1
    assert payload["summary"]["modified"] >= 1

    graph = payload["graphs"]["network"]
    statuses = {node["id"]: node.get("diff_status") for node in graph["nodes"]}
    assert statuses.get("site-a:server-01") == "removed"

    again = semantic_diff(root, base, head, site="site-a")
    assert again["base"]["sha"] == payload["base"]["sha"]
    assert again["summary"] == payload["summary"]


def test_list_refs_and_api(tmp_path: Path) -> None:
    clear_caches()
    root, base, head = _two_commit_repo(tmp_path)
    refs = list_refs(root)
    names = {item["name"] for item in refs["branches"]}
    assert names
    assert refs["commits"]

    client = TestClient(create_app(root))
    listed = client.get("/api/refs")
    assert listed.status_code == 200
    assert listed.json()["branches"]

    ok = client.get("/api/diff", params={"base": base, "head": head, "site": "site-a"})
    assert ok.status_code == 200
    body = ok.json()
    ids = {item["id"] for item in body["entities"] if item["status"] != "unchanged"}
    assert "site-a:vlan:2099" in ids
    assert "site-a:server-01" in ids

    bad = client.get("/api/diff", params={"base": "main; id", "head": head})
    assert bad.status_code == 400

    missing = client.get("/api/diff", params={"base": base, "head": "no-such-ref-xyz"})
    assert missing.status_code == 400


def _branch_name(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _three_commit_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    root, first, second = _two_commit_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "topology" / "index.yaml",
        {
            "subnets": [
                {"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100, "gateway": "10.0.1.1"},
                {"name": "DCI", "cidr": "192.0.2.0/24", "vlan": 4094},
                {"name": "LAB_NET", "cidr": "10.0.31.0/24", "vlan": 2099, "gateway": "10.0.31.1"},
            ],
            "vlans": [
                {"id": 100, "name": "mgmt", "subnet": "mgmt"},
                {"id": 4094, "name": "DCI", "subnet": "DCI"},
                {"id": 2099, "name": "LAB", "subnet": "LAB_NET"},
            ],
        },
    )
    third = git_commit_all(root, "gateway de LAB_NET")
    return root, first, second, third


def test_commit_versus_commit_resolves_sha(tmp_path: Path) -> None:
    clear_caches()
    root, first, second, third = _three_commit_repo(tmp_path)
    payload = semantic_diff(root, first, third, site="site-a")
    assert payload["base"]["ref"] == first
    assert payload["base"]["sha"] == first
    assert payload["base"]["short"] == first[:7]
    assert payload["head"]["sha"] == third
    by_id = {item["id"]: item for item in payload["entities"]}
    assert by_id["site-a:vlan:2099"]["status"] == "added"
    assert by_id["site-a:server-01"]["status"] == "removed"

    short = semantic_diff(root, first[:7], second[:9], site="site-a")
    assert short["base"]["sha"] == first
    assert short["head"]["sha"] == second

    same_branch = semantic_diff(root, "HEAD~1", "HEAD", site="site-a")
    assert same_branch["base"]["sha"] == second
    assert same_branch["head"]["sha"] == third
    gw = next(
        row
        for row in next(item for item in same_branch["entities"] if item["id"] == "site-a:subnet:LAB_NET")["changes"]
        if row["path"] == "gateway"
    )
    assert gw["after"] == "10.0.31.1"


def test_branch_versus_commit(tmp_path: Path) -> None:
    clear_caches()
    root, first, _second, third = _three_commit_repo(tmp_path)
    branch = _branch_name(root)
    payload = semantic_diff(root, branch, first, site="site-a")
    assert payload["base"]["sha"] == third
    assert payload["head"]["sha"] == first
    assert payload["base"]["ref"] == branch
    history = list_commits(root, branch)
    assert history["sha"] == third
    shas = [item["sha"] for item in history["commits"]]
    assert shas[0] == third
    assert first in shas
    assert all(item.get("author") for item in history["commits"])

    client = TestClient(create_app(root))
    listed = client.get("/api/commits", params={"rev": branch})
    assert listed.status_code == 200
    body = listed.json()
    assert body["sha"] == third
    assert body["commits"][0]["sha"] == third

    via_head = client.get("/api/diff", params={"base": "HEAD~2", "head": "HEAD", "site": "site-a"})
    assert via_head.status_code == 200
    assert via_head.json()["base"]["sha"] == first
    assert via_head.json()["head"]["sha"] == third

    by_branch_and_sha = client.get("/api/diff", params={"base": branch, "head": first[:8], "site": "site-a"})
    assert by_branch_and_sha.status_code == 200
    assert by_branch_and_sha.json()["base"]["sha"] == third
    assert by_branch_and_sha.json()["head"]["sha"] == first


def _by_id(payload: dict) -> dict:
    return {item["id"]: item for item in payload["entities"]}


def _node_status(graph: dict, node_id: str) -> str | None:
    for node in graph.get("nodes") or []:
        if node["id"] == node_id:
            return node.get("diff_status")
    return None


def _node(graph: dict, node_id: str) -> dict:
    for node in graph.get("nodes") or []:
        if node["id"] == node_id:
            return node
    raise AssertionError(f"nodo {node_id} ausente")


def test_parent_status_propagates_to_site_cluster_and_template(tmp_path: Path) -> None:
    clear_caches()
    root, base, head = _two_commit_repo(tmp_path)
    payload = semantic_diff(root, base, head)
    by_id = _by_id(payload)

    site_a = by_id["site-a"]
    assert site_a["status"] == "modified"
    assert site_a["inherited"] is True
    assert site_a["child_counts"]["added"] >= 2
    assert site_a["child_counts"]["removed"] >= 1
    child_ids = {item["id"] for item in site_a["children"]}
    assert "site-a:vlan:2099" in child_ids
    assert "site-a:subnet:LAB_NET" in child_ids
    assert "site-a:server-01" in child_ids

    site_b = by_id["site-b"]
    assert site_b["status"] == "unchanged"
    assert site_b["child_counts"]["added"] == 0
    assert site_b["child_counts"]["removed"] == 0
    assert site_b["child_counts"]["modified"] == 0

    cluster = by_id["site-a:k8s-prod"]
    assert cluster["status"] == "modified"
    assert cluster["inherited"] is True
    assert any(item["id"] == "site-a:server-01" for item in cluster["children"])

    template = by_id["global:rke2-lab"]
    assert template["status"] == "modified"
    assert template["inherited"] is True
    release = by_id["global:rke2-lab:cluster"]
    assert release["status"] == "modified"
    assert release["inherited"] is True
    assert any(item["id"] == "template:global:rke2-lab:cluster" for item in release["children"])

    assert payload["summary"]["added"] >= 2
    assert payload["summary"]["removed"] >= 1
    assert payload["summary"]["modified"] >= 1
    direct_modified = [item for item in payload["entities"] if item["status"] == "modified" and not item.get("inherited")]
    assert any(item["id"] == "template:global:rke2-lab:cluster" for item in direct_modified)
    assert all(item["id"] != "site-a" for item in direct_modified)

    global_graph = payload["graphs"]["global"]
    site_node = _node(global_graph, "site:site-a")
    assert site_node["diff_status"] == "modified"
    assert site_node["diff_counts"]["added"] >= 2
    assert site_node["diff_counts"]["removed"] >= 1
    assert _node_status(global_graph, "site:site-b") == "unchanged"

    k8s = payload["graphs"]["kubernetes"]
    assert _node_status(k8s, "template:global:rke2-lab") == "modified"
    release_node = _node(k8s, "release:global:rke2-lab:cluster")
    assert release_node["diff_status"] == "modified"
    cluster_node = _node(k8s, "cluster:site-a:k8s-prod")
    assert cluster_node["diff_status"] == "modified"

    scoped = semantic_diff(root, base, head, site="site-a")
    network = scoped["graphs"]["network"]
    vlan = _node(network, "network:site-a:vlan:2099")
    assert vlan["diff_status"] == "added"
    subnet = _node(network, "network:site-a:subnet:LAB_NET")
    assert subnet["diff_status"] == "added"
    removed = _node(network, "site-a:server-01")
    assert removed["diff_status"] == "removed"

    client = TestClient(create_app(root))
    listed = client.get("/api/diff", params={"base": base, "head": head})
    assert listed.status_code == 200
    body = listed.json()
    site = next(item for item in body["entities"] if item["id"] == "site-a")
    assert site["status"] == "modified"
    assert site["inherited"] is True
    assert site["children"]
