"""Remediación de hallazgos: acciones add_device / open_entity y wizard «Corregir»."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from isot.rules.evaluate import evaluate
from isot.rules.remediation import fix_status
from isot.web import create_app
from isot.wizard import device_preview

from tests.conftest import seed_mini_repo, write_yaml


def _by(findings, rule_id: str, cluster: str | None = None):
    hits = [item for item in findings if item.id == rule_id and (cluster is None or item.cluster == cluster)]
    assert hits, rule_id
    return hits[0]


def _cluster(nodes: list[dict], **extra) -> dict:
    body = {
        "cluster_name": "k8s-prod",
        "kind": "kubernetes",
        "template_ref": "rke2-lab",
        "version": "v1.34.1",
        "pod_cidr": "10.42.0.0/16",
        "service_cidr": "10.43.0.0/16",
        "nodes": nodes,
    }
    body.update(extra)
    return body


def _cp(name: str, extra: list[str] | None = None) -> dict:
    return {"hostname": name, "roles": ["control-plane", "etcd", *(extra or [])]}


def _fix(finding) -> dict:
    return {
        "id": finding.id,
        "site": finding.site,
        "entity_kind": finding.entity_kind,
        "entity_id": finding.entity_id,
        "cluster": finding.cluster,
    }


def test_sample_inventory_actions(repo_root: Path) -> None:
    findings = evaluate(repo_root)
    cp = _by(findings, "K8S-CP-001", "site2-edge").remediation
    assert cp["kind"] == "add_device"
    assert (cp["site"], cp["cluster"], cp["role"], cp["device_kind"], cp["count"]) == (
        "site2",
        "site2-edge",
        "edge-server",
        "baremetal",
        1,
    )
    wk_edge = _by(findings, "K8S-WK-001", "site2-edge").remediation
    assert (wk_edge["role"], wk_edge["count"]) == ("edge-server", 1)
    # Sin workers: rol por defecto «worker» con el tipo de los control planes (VM).
    wk_mgr = _by(findings, "K8S-WK-001", "site1-manager").remediation
    assert (wk_mgr["role"], wk_mgr["device_kind"], wk_mgr["count"]) == ("worker", "vm", 2)
    leaf = _by(findings, "K8S-CP-002", "site2-edge").remediation
    assert (leaf["kind"], leaf["role"], leaf["device_kind"], leaf["cluster"]) == (
        "add_device",
        "server-leaf",
        "network",
        None,
    )
    assert "reconecta" in leaf["followup"]
    tpl = _by(findings, "K8S-VER-003", "site2-edge").remediation
    assert tpl == {
        "kind": "open_entity",
        "target": "template",
        "site": "global",
        "name": "rke2-rancher-hybrid-calico",
        "label": "Abrir template rke2-rancher-hybrid-calico",
    }
    assert _by(findings, "K8S-TPL-001", "site1-platform").remediation["target"] == "cluster"
    assert _by(findings, "K8S-NET-001", "site1-platform").remediation["kind"] == "open_entity"
    assert all(item.remediation for item in findings)


def test_api_exposes_remediation(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    body = client.get("/api/findings", params={"site": "site2"}).json()
    kinds = {item["id"]: item["remediation"]["kind"] for item in body["findings"]}
    assert kinds["K8S-CP-001"] == "add_device"
    assert kinds["K8S-VER-003"] == "open_entity"


def test_cp_001_count_rules(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    path = root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml"
    write_yaml(path, _cluster([_cp("a")]))
    action = _by(evaluate(root), "K8S-CP-001").remediation
    assert (action["kind"], action["count"], action["role"]) == ("add_device", 2, "control-plane")
    write_yaml(path, _cluster([_cp(name) for name in "abcd"]))
    assert _by(evaluate(root), "K8S-CP-001").remediation["count"] == 1
    write_yaml(path, _cluster([_cp(name) for name in "abcdefgh"]))
    assert _by(evaluate(root), "K8S-CP-001").remediation["kind"] == "open_entity"


def test_st_001_adds_missing_nodes(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _cluster(
            [_cp("a", ["worker"]), {"hostname": "w1", "roles": ["worker"]}],
            storage_classes=[{"name": "longhorn", "provisioner": "driver.longhorn.io"}],
            longhorn={"replicas": 4},
        ),
    )
    action = _by(evaluate(root), "K8S-ST-001").remediation
    assert (action["kind"], action["count"]) == ("add_device", 2)


def test_fix_preview_cp_001_site2_edge(repo_root: Path) -> None:
    finding = _by(evaluate(repo_root), "K8S-CP-001", "site2-edge")
    action = finding.remediation
    preview = device_preview(
        repo_root,
        {
            "site": action["site"],
            "kind": action["device_kind"],
            "cluster": action["cluster"],
            "role": action["role"],
            "count": action["count"],
            "fix": _fix(finding),
        },
    )
    assert preview["hostnames"] == ["SITE2-CTRL003"]
    device = yaml.safe_load(preview["files"][0]["content"])
    mgmt = next(item for item in device["networking"] if item["interface"] == "MGMT_SW")
    assert mgmt["ip"] == "10.80.1.13/24"
    assert device["k8s_roles"] == ["control-plane", "etcd", "worker"]
    fix = preview["findings"]["fix"]
    assert fix["resolved"] is True
    assert "K8S-WK-001" in fix["also_resolves"]
    assert not any(item["severity"] == "error" for item in preview["findings"]["added"])


def test_fix_preview_leaf_for_cp_002(repo_root: Path) -> None:
    finding = _by(evaluate(repo_root), "K8S-CP-002", "site2-edge")
    action = finding.remediation
    preview = device_preview(
        repo_root,
        {"site": action["site"], "kind": "network", "role": action["role"], "count": 1, "fix": _fix(finding)},
    )
    assert preview["hostnames"] == ["SITE2-LEAF002"]
    leaf = yaml.safe_load(preview["files"][0]["content"])
    assert "k8s_roles" not in leaf and "cluster_ref" not in leaf
    peers = {item.get("connected_to", {}).get("device") for item in leaf["networking"]}
    assert "SITE2-CTRL001" not in peers and "SITE2-CTRL002" not in peers
    loopback = next(item for item in leaf["networking"] if item["interface"] == "Loopback0")
    assert loopback["ip"] != "10.80.15.1/32"
    assert leaf["bgp"]["router_id"] == loopback["ip"].split("/")[0]
    assert not [item["id"] for item in preview["findings"]["added"] if item["id"].startswith("INV-")]
    # Agregar el leaf no basta: falta reconectar un control plane.
    assert preview["findings"]["fix"]["resolved"] is False


def test_fix_status_ignores_message_changes() -> None:
    target = {"id": "K8S-CP-002", "site": "s", "entity_kind": "cluster", "entity_id": "s/c", "cluster": "c"}
    still = [{**target, "message": "otro texto"}]
    assert fix_status(target, still, [])["resolved"] is False
    status = fix_status(target, [], [{**target, "message": "x"}, {**target, "id": "K8S-WK-001"}])
    assert status["resolved"] is True and status["also_resolves"] == ["K8S-WK-001"]
    assert fix_status(None, [], []) is None
