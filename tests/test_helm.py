"""Charts Helm: values_ref, overrides y diff clúster vs template."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from isot.helm import charts_from_doc, diff_maps, flatten_values, merge_values
from isot.indexer import write_indexes
from isot.validator import collect_issues
from isot.web import create_app

from tests.conftest import seed_mini_repo, write_yaml


def test_flatten_and_merge() -> None:
    template = {"cniConfig": {"podCIDR": "10.42.0.0/16", "mtu": 1440}, "storage": {"longhorn": "enabled"}}
    override = {"cniConfig": {"podCIDR": "10.44.0.0/16"}, "cluster": {"name": "edge"}}
    effective = merge_values(template, override)
    assert effective["cniConfig"]["mtu"] == 1440
    assert effective["cniConfig"]["podCIDR"] == "10.44.0.0/16"
    assert effective["cluster"]["name"] == "edge"
    paths = dict(flatten_values(effective))
    assert paths["cniConfig.podCIDR"] == "10.44.0.0/16"
    rows = {row["path"]: row for row in diff_maps(template, override)}
    assert rows["cniConfig.mtu"]["status"] == "inherited"
    assert rows["cniConfig.podCIDR"]["status"] == "overridden"
    assert rows["cluster.name"]["status"] == "added"


def test_sample_template_has_structured_charts(repo_root: Path) -> None:
    from isot.inventory import load_templates

    docs = load_templates(repo_root)
    hybrid = next(item for item in docs if item.get("template_name") == "rke2-rancher-hybrid-calico")
    names = {item["name"] for item in charts_from_doc(hybrid)}
    assert "rke2-rancher-hybrid-calico" in names
    assert "metallb" in names
    assert "logging" in names
    assert "rancher-logging" not in names
    values = repo_root / "cluster-templates/rke2-rancher-hybrid-calico/values/rke2-rancher-hybrid-calico.yaml"
    assert values.is_file()


def test_missing_values_ref_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    (
        root / "cluster-templates" / "rke2-lab" / "template.yaml"
    ).write_text(
        (root / "cluster-templates" / "rke2-lab" / "template.yaml").read_text(encoding="utf-8").replace(
            "values/cluster.yaml", "values/missing.yaml"
        ),
        encoding="utf-8",
    )
    issues = collect_issues(root)
    assert any("values_ref='values/missing.yaml'" in issue for issue in issues)


def test_invalid_values_yaml_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    (root / "cluster-templates" / "rke2-lab" / "values" / "cluster.yaml").write_text(
        "cniConfig: [\n  broken\n",
        encoding="utf-8",
    )
    issues = collect_issues(root)
    assert any("YAML inválido" in issue and "values/cluster.yaml" in issue for issue in issues)


def test_override_unknown_release_is_detected(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml",
        {
            "cluster_name": "k8s-prod",
            "kind": "kubernetes",
            "template_ref": "rke2-lab",
            "values_overrides": [{"release": "ghost-chart", "values_inline": {"foo": 1}}],
            "nodes": [{"hostname": "server-01", "roles": ["worker"]}],
        },
    )
    issues = collect_issues(root)
    assert any("values_overrides.release='ghost-chart'" in issue for issue in issues)


def test_values_and_diff_endpoints(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_indexes(root)
    client = TestClient(create_app(root))
    template = client.get("/api/templates/rke2-lab")
    assert template.status_code == 200
    body = template.json()
    assert body["path"] == "cluster-templates/rke2-lab/template.yaml"
    assert body["charts"][0]["name"] == "cluster"
    assert "values_inline" not in (body["data"].get("charts") or [{}])[0]
    assert body["data"]["charts"][0].get("values_ref") == "values/cluster.yaml"

    values = client.get("/api/templates/rke2-lab/values/cluster")
    assert values.status_code == 200
    payload = values.json()
    assert payload["source"] == "ref"
    assert payload["values"]["cniConfig"]["mtu"] == 1440

    missing = client.get("/api/templates/rke2-lab/values/nope")
    assert missing.status_code == 404

    diff = client.get("/api/sites/site-a/clusters/k8s-prod/values-diff")
    assert diff.status_code == 200
    data = diff.json()
    assert data["template"]["name"] == "rke2-lab"
    cluster_release = next(item for item in data["releases"] if item["release"] == "cluster")
    by_path = {entry["path"]: entry for entry in cluster_release["entries"]}
    assert by_path["cniConfig.mtu"]["status"] == "inherited"
    assert by_path["cluster.name"]["status"] == "added"
    assert by_path["cniConfig.podCIDR"]["status"] == "overridden"
    assert by_path["cniConfig.podCIDR"]["effective"] == "10.42.0.0/16"


def test_real_inventory_values_diff(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    response = client.get("/api/sites/site1/clusters/site1-apps/values-diff")
    assert response.status_code == 200
    data = response.json()
    umbrella = next(item for item in data["releases"] if item["release"] == "rke2-rancher-hybrid-calico")
    by_path = {entry["path"]: entry for entry in umbrella["entries"]}
    assert by_path["cniConfig.mtu"]["status"] == "inherited"
    assert by_path["cniConfig.mtu"]["effective"] == 1440
    assert by_path["storage.longhorn"]["status"] == "overridden"
    assert by_path["storage.longhorn"]["effective"] == "disabled"
    assert by_path["cluster.name"]["status"] == "added"

    edge = client.get("/api/sites/site2/clusters/site2-edge/values-diff").json()
    umbrella = next(item for item in edge["releases"] if item["release"] == "rke2-rancher-hybrid-calico")
    by_path = {entry["path"]: entry for entry in umbrella["entries"]}
    assert by_path["cniConfig.podCIDR"]["status"] == "overridden"
    assert by_path["cniConfig.podCIDR"]["effective"] == "10.44.0.0/16"
    assert by_path["cniConfig.mtu"]["status"] == "inherited"
