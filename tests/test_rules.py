"""Motor de reglas: un caso que dispara y uno que no, por regla, más overrides."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from isot.rules.evaluate import evaluate, evaluate_overlay
from isot.validator import validate
from isot.web import create_app
from isot.wizard import WizardError, save_preview

from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _find(findings, rule_id: str, needle: str | None = None, site: str | None = "site-a"):
    return [
        item
        for item in findings
        if item.id == rule_id
        and (needle is None or needle in item.message)
        and (site == "*" or item.site == site or (site is None and not item.site))
    ]


def _ha_cluster(nodes: list[dict], **extra) -> dict:
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


def _cp(name: str, extra_roles: list[str] | None = None) -> dict:
    return {"hostname": name, "roles": ["control-plane", "etcd", *(extra_roles or [])]}


def test_k8s_cp_001_count(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("server-01")]),
    )
    hits = _find(evaluate(root), "K8S-CP-001", "sin quórum")
    assert hits
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("cp1"), _cp("cp2"), _cp("cp3")]),
    )
    assert not _find(evaluate(root), "K8S-CP-001")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), _cp("d")]),
    )
    assert _find(evaluate(root), "K8S-CP-001", "cantidad par")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp(f"cp{i}") for i in range(9)]),
    )
    assert _find(evaluate(root), "K8S-CP-001", "más de 7")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("only")], distro="hosted-controlplane"),
    )
    assert not _find(evaluate(root), "K8S-CP-001")


def test_k8s_cp_002_placement(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    for host, hyper in (("vm-a", "hv1"), ("vm-b", "hv1"), ("vm-c", "hv2")):
        write_yaml(
            root / "sites" / "site-a" / "devices" / "virtual-machines" / f"{host}.yaml",
            {
                "hostname": host,
                "kind": "vm",
                "cluster_ref": "k8s-prod",
                "hypervisor": {"device": hyper},
                "networking": [{"interface": "eth0", "ip": f"10.0.1.{10 + ord(host[-1]) % 9}/24", "vlan": 100}],
            },
        )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "hv1.yaml",
        {
            "hostname": "hv1",
            "role": "platform-server",
            "bonds": [{"name": "bondC", "leaf_pc": {"leaf-a": "Po1", "leaf-b": "Po1"}}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "hv2.yaml",
        {
            "hostname": "hv2",
            "role": "platform-server",
            "bonds": [{"name": "bondC", "leaf_pc": {"leaf-a": "Po2", "leaf-b": "Po2"}}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("vm-a"), _cp("vm-b"), _cp("vm-c")]),
    )
    hits = _find(evaluate(root), "K8S-CP-002", "hypervisor")
    assert hits and hits[0].severity == "error"
    write_yaml(
        root / "sites" / "site-a" / "devices" / "virtual-machines" / "vm-b.yaml",
        {
            "hostname": "vm-b",
            "kind": "vm",
            "cluster_ref": "k8s-prod",
            "hypervisor": {"device": "hv2"},
            "networking": [{"interface": "eth0", "ip": "10.0.1.21/24", "vlan": 100}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "virtual-machines" / "vm-c.yaml",
        {
            "hostname": "vm-c",
            "kind": "vm",
            "cluster_ref": "k8s-prod",
            "hypervisor": {"device": "hv3"},
            "networking": [{"interface": "eth0", "ip": "10.0.1.22/24", "vlan": 100}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "hv3.yaml",
        {
            "hostname": "hv3",
            "role": "platform-server",
            "bonds": [{"name": "bondC", "leaf_pc": {"leaf-c": "Po1", "leaf-d": "Po1"}}],
        },
    )
    assert not _find(evaluate(root), "K8S-CP-002", "hypervisor")
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "hv2.yaml",
        {
            "hostname": "hv2",
            "role": "platform-server",
            "bonds": [{"name": "bondC", "leaf_pc": {"leaf-a": "Po2", "leaf-b": "Po2"}}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "hv3.yaml",
        {
            "hostname": "hv3",
            "role": "platform-server",
            "bonds": [{"name": "bondC", "leaf_pc": {"leaf-a": "Po3", "leaf-b": "Po3"}}],
        },
    )
    hits = _find(evaluate(root), "K8S-CP-002", "par de leafs")
    assert hits and hits[0].severity == "warning"


def test_k8s_net_001_cidrs(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")], pod_cidr="", service_cidr=""),
    )
    assert _find(evaluate(root), "K8S-NET-001", "desconocidos")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")], pod_cidr="10.42.0.0/16", service_cidr="10.42.0.0/17"),
    )
    assert _find(evaluate(root), "K8S-NET-001", "solapa con service_cidr")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")], pod_cidr="10.0.1.0/24", service_cidr="10.43.0.0/16"),
    )
    assert _find(evaluate(root), "K8S-NET-001", "subred de nodos")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")], pod_cidr="192.0.2.0/24", service_cidr="10.43.0.0/16"),
    )
    assert _find(evaluate(root), "K8S-NET-001", "DCI")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")]),
    )
    assert not _find(evaluate(root), "K8S-NET-001", "solapa")


def test_k8s_lb_001_metallb(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a"), _cp("b"), _cp("c")],
            metallb={"pools": [{"name": "mgmt", "subnet": "mgmt", "addresses": ["10.0.1.15-10.0.1.16"]}]},
        ),
    )
    assert _find(evaluate(root), "K8S-LB-001", "choca")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a"), _cp("b"), _cp("c")],
            metallb={"pools": [{"name": "mgmt", "subnet": "mgmt", "addresses": ["10.9.9.9"]}]},
        ),
    )
    assert _find(evaluate(root), "K8S-LB-001", "fuera")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a"), _cp("b"), _cp("c")],
            metallb={"pools": [{"name": "mgmt", "subnet": "mgmt", "addresses": ["10.0.1.50-10.0.1.60"]}]},
        ),
    )
    assert not _find(evaluate(root), "K8S-LB-001")


def test_k8s_cni_001_mtu(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "values" / "cluster.yaml",
        {"cniConfig": {"mtu": 1500, "tunnelProtocol": "VXLAN"}},
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("server-01", ["worker"])],
            nads=[{"namespace": "ns", "name": "mgmt-nad", "vlan": 100, "subnet": "mgmt", "mtu": 1500}],
        ),
    )
    assert _find(evaluate(root), "K8S-CNI-001", "MTU CNI")
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "values" / "cluster.yaml",
        {"cniConfig": {"mtu": 1400, "tunnelProtocol": "VXLAN"}},
    )
    assert not _find(evaluate(root), "K8S-CNI-001")


def test_k8s_nad_001(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("server-01", ["worker"])],
            nads=[{"namespace": "ns", "name": "bad", "vlan": 999, "subnet": "mgmt"}],
        ),
    )
    assert _find(evaluate(root), "K8S-NAD-001", "no está en la topología")
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-prod",
            "bonds": [{"name": "bondC", "allowed_vlans": [200]}],
            "networking": [{"interface": "eno1", "ip": "10.0.1.15/24", "vlan": 200}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("server-01", ["worker"])],
            nads=[{"namespace": "ns", "name": "mgmt-nad", "vlan": 100, "subnet": "mgmt"}],
        ),
    )
    assert _find(evaluate(root), "K8S-NAD-001", "no está en el bond")
    write_yaml(
        root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml",
        {
            "hostname": "server-01",
            "role": "k8s-worker",
            "cluster_ref": "k8s-prod",
            "bonds": [{"name": "bondC", "allowed_vlans": [100]}],
            "networking": [
                {"interface": "eno1", "ip": "10.0.1.15/24", "vlan": 100, "connected_to": {"device": "switch-core", "port": "GigabitEthernet1/0/5"}}
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("server-01", ["worker"])],
            nads=[{"namespace": "ns", "name": "mgmt-nad", "vlan": 100, "subnet": "mgmt"}],
        ),
    )
    assert not _find(evaluate(root), "K8S-NAD-001")


def test_k8s_wk_001_workers(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")]),
    )
    assert _find(evaluate(root), "K8S-WK-001", "al menos 2")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a", ["worker"]), {"hostname": "w1", "roles": ["worker"]}],
            storage_classes=[{"name": "longhorn", "provisioner": "driver.longhorn.io"}],
        ),
    )
    assert _find(evaluate(root), "K8S-WK-001", "Longhorn")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [
                _cp("a", ["worker"]),
                {"hostname": "w1", "roles": ["worker"]},
                {"hostname": "w2", "roles": ["worker"]},
            ],
            storage_classes=[{"name": "longhorn", "provisioner": "driver.longhorn.io"}],
        ),
    )
    assert not _find(evaluate(root), "K8S-WK-001")


def test_k8s_st_001_replicas(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a", ["worker"]), {"hostname": "w1", "roles": ["worker"]}],
            storage_classes=[{"name": "longhorn", "provisioner": "driver.longhorn.io"}],
            longhorn={"replicas": 3},
        ),
    )
    assert _find(evaluate(root), "K8S-ST-001", "réplicas")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a", ["worker"]), {"hostname": "w1", "roles": ["worker"]}],
            storage_classes=[{"name": "longhorn", "provisioner": "driver.longhorn.io"}],
            longhorn={"replicas": 2},
        ),
    )
    assert not _find(evaluate(root), "K8S-ST-001")


def test_k8s_ver_001_eol(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}], version="v1.28.0"),
    )
    assert _find(evaluate(root, as_of=date(2026, 10, 17)), "K8S-VER-001", "EOL")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}], version="v1.36.1"),
    )
    assert not _find(evaluate(root, as_of=date(2026, 10, 17)), "K8S-VER-001")


def test_k8s_ver_002_skew(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "template.yaml",
        {
            "template_name": "rke2-lab",
            "kubernetes_version": "v1.34.0",
            "charts": [
                {
                    "name": "cluster",
                    "chart": "rke2-lab",
                    "version": "1.0.0",
                    "values_ref": "values/cluster.yaml",
                }
            ],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}], version="v1.32.0"),
    )
    assert _find(evaluate(root), "K8S-VER-002", "desfase")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}], version="v1.34.2"),
    )
    assert not _find(evaluate(root), "K8S-VER-002")


def test_k8s_ver_003_unpinned(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "template.yaml",
        {
            "template_name": "rke2-lab",
            "kubernetes_version": "v1.34.0",
            "charts": [
                {"name": "cluster", "chart": "rke2-lab", "version": "", "values_ref": "values/cluster.yaml"},
            ],
        },
    )
    hits = _find(evaluate(root), "K8S-VER-003", "sin versión", site="*")
    # El template global se atribuye a cada clúster que lo usa (uno por sitio aquí).
    assert {(item.site, item.cluster) for item in hits} == {("site-a", "k8s-prod"), ("site-b", "k8s-edge")}
    assert all(item.source_template == "global/rke2-lab" for item in hits)
    assert not _find(evaluate(root), "K8S-VER-003", site=None)
    write_yaml(
        root / "cluster-templates" / "rke2-lab" / "template.yaml",
        {
            "template_name": "rke2-lab",
            "kubernetes_version": "v1.34.0",
            "charts": [
                {"name": "cluster", "chart": "rke2-lab", "version": "1.0.0", "values_ref": "values/cluster.yaml"},
            ],
        },
    )
    assert not _find(evaluate(root), "K8S-VER-003", site="*")


def test_k8s_tpl_001_missing_template(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}], template_ref=None),
    )
    assert _find(evaluate(root), "K8S-TPL-001", "template_ref")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c"), {"hostname": "w1", "roles": ["worker"]}]),
    )
    assert not _find(evaluate(root), "K8S-TPL-001")


def test_inventory_rules_still_fire(tmp_path: Path) -> None:
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
    findings = evaluate(root)
    assert _find(findings, "INV-IP-001")


def test_override_requires_reason_and_can_silence(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a")],
            rule_overrides=[{"id": "K8S-CP-001", "severity": "off"}],
        ),
    )
    findings = evaluate(root)
    assert _find(findings, "RULE-OVR-001", "sin reason")
    assert _find(findings, "K8S-CP-001")
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster(
            [_cp("a")],
            rule_overrides=[{"id": "K8S-CP-001", "severity": "off", "reason": "lab de una sola VM"}],
        ),
    )
    findings = evaluate(root)
    assert not _find(findings, "K8S-CP-001")
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
            "rule_overrides": [{"id": "K8S-WK-001", "severity": "info", "reason": "edge de un nodo"}],
        },
    )
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        _ha_cluster([_cp("a"), _cp("b"), _cp("c")]),
    )
    hits = _find(evaluate(root), "K8S-WK-001")
    assert hits and hits[0].severity == "info" and hits[0].override_reason


def test_api_findings_and_validator_json(repo_root: Path, capsys) -> None:
    client = TestClient(create_app(repo_root))
    response = client.get("/api/findings")
    assert response.status_code == 200
    body = response.json()
    assert "counts" in body and "findings" in body
    ids = {item["id"] for item in body["findings"]}
    assert "K8S-CP-001" in ids
    assert client.get("/api/rules").status_code == 200
    code = validate(repo_root, fmt="json")
    out = capsys.readouterr().out
    assert code == 0, out
    assert '"id": "K8S-CP-001"' in out
    assert validate(repo_root, strict=True) != 0


def test_wizard_blocks_on_new_error(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    git_init_and_commit(root)
    preview = {
        "files": [
            {
                "path": "sites/site-a/clusters/k8s-prod.yaml",
                "action": "modify",
                "content": "cluster_name: k8s-prod\nkind: kubernetes\nnodes: []\npod_cidr: 10.0.1.0/24\nservice_cidr: 10.43.0.0/16\n",
            }
        ]
    }
    overlay = evaluate_overlay(root, preview["files"])
    assert overlay["blocked"] is True
    assert any(item["id"] == "K8S-NET-001" and item["severity"] == "error" for item in overlay["added"])
    try:
        save_preview(root, preview)
        raised = False
    except WizardError as exc:
        raised = True
        assert "K8S-NET-001" in str(exc) or "solapa" in str(exc)
    assert raised


def test_sample_inventory_demo_findings(repo_root: Path) -> None:
    findings = evaluate(repo_root, as_of=date(2026, 10, 17))
    grouped: dict[str, list[str]] = {}
    for item in findings:
        grouped.setdefault(item.site or "global", []).append(f"{item.severity}:{item.id}:{item.entity_id}")
    assert any(item.id == "K8S-CP-001" and item.cluster == "site2-edge" for item in findings)
    assert any(item.id == "K8S-TPL-001" and item.cluster == "site1-platform" for item in findings)
    assert any(item.id == "K8S-NET-001" and "desconocido" in item.message for item in findings)
    assert not any(item.severity == "error" for item in findings)
