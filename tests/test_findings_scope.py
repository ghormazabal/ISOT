"""Hallazgos por sitio: atribución de templates, API, comparar y wizard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from isot.rules.context import load_context
from isot.rules.evaluate import attribute_template_findings, evaluate, evaluate_overlay
from isot.rules.model import Finding
from isot.web import create_app
from isot.wizard import WizardError, commit_preview

from tests.conftest import _git_ident_env, git_init_and_commit, seed_mini_repo, write_yaml

UNPINNED = {
    "template_name": "rke2-lab",
    "kubernetes_version": "v1.34.0",
    "charts": [{"name": "cluster", "chart": "rke2-lab", "version": "", "values_ref": "values/cluster.yaml"}],
}

BAD_CIDR = (
    "cluster_name: k8s-prod\nkind: kubernetes\ntemplate_ref: rke2-lab\nnodes: []\n"
    "pod_cidr: 10.0.1.0/24\nservice_cidr: 10.43.0.0/16\n"
)


def _template_finding(entity_id: str, path: str) -> Finding:
    return Finding(
        id="INV-HELM-001",
        severity="error",
        entity_kind="template",
        entity_id=entity_id,
        message=f"problema en {path}",
        recommendation="x",
        path=path,
    )


def _total(counts: dict[str, int]) -> int:
    return counts["error"] + counts["warning"] + counts["info"]


def test_template_finding_attributed_only_to_sites_that_use_it(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(root / "cluster-templates" / "rke2-lab" / "template.yaml", UNPINNED)
    # site-b deja de usar el template: el hallazgo ya no aparece en site-b.
    write_yaml(
        root / "sites" / "site-b" / "clusters" / "k8s-edge.yaml",
        {"cluster_name": "k8s-edge", "kind": "kubernetes", "template_ref": None, "nodes": []},
    )
    site_a = [item for item in evaluate(root, site="site-a") if item.id == "K8S-VER-003"]
    site_b = [item for item in evaluate(root, site="site-b") if item.id == "K8S-VER-003"]
    assert len(site_a) == 1
    assert site_a[0].cluster == "k8s-prod"
    assert site_a[0].source_template == "global/rke2-lab"
    assert site_a[0].graph_id == "cluster:site-a:k8s-prod"
    assert site_b == []


def test_site_scope_is_strict(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(root / "cluster-templates" / "rke2-lab" / "template.yaml", UNPINNED)
    for site in ("site-a", "site-b"):
        findings = evaluate(root, site=site)
        assert findings, site
        assert all(item.site == site for item in findings)


def test_unused_template_stays_global_and_path_match(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(
        root / "cluster-templates" / "orphan" / "template.yaml",
        {"template_name": "orphan", "charts": []},
    )
    ctx = load_context(root)
    orphan = _template_finding("global/orphan", "cluster-templates/orphan/template.yaml")
    by_path = _template_finding("inventory", "cluster-templates/rke2-lab/values/cluster.yaml")
    out = attribute_template_findings([orphan, by_path], ctx)
    globals_ = [item for item in out if item.site is None]
    assert [item.entity_id for item in globals_] == ["global/orphan"]
    attributed = sorted((item.site, item.cluster) for item in out if item.source_template)
    assert attributed == [("site-a", "k8s-prod"), ("site-b", "k8s-edge")]


def test_cluster_override_applies_to_attributed_template_finding(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(root / "cluster-templates" / "rke2-lab" / "template.yaml", UNPINNED)
    cluster_path = root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml"
    data = yaml.safe_load(cluster_path.read_text())
    data["rule_overrides"] = [{"id": "K8S-VER-003", "severity": "off", "reason": "charts fijados en el pipeline"}]
    write_yaml(cluster_path, data)
    hits = [item for item in evaluate(root) if item.id == "K8S-VER-003"]
    assert [(item.site, item.cluster) for item in hits] == [("site-b", "k8s-edge")]


def test_api_findings_by_site_and_global(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_yaml(root / "cluster-templates" / "rke2-lab" / "template.yaml", UNPINNED)
    client = TestClient(create_app(root))
    body = client.get("/api/findings").json()
    assert body["scope"] == "all"
    assert set(body["by_site"]) == {"site-a", "site-b"}
    assert _total(body["counts"]) == sum(_total(item) for item in body["by_site"].values()) + _total(body["global"])
    for site in ("site-a", "site-b"):
        scoped = client.get("/api/findings", params={"site": site}).json()
        assert scoped["scope"] == site
        assert list(scoped["by_site"]) == [site]
        assert all(item["site"] == site for item in scoped["findings"])
        assert scoped["counts"] == body["by_site"][site]
        assert _total(scoped["global"]) == 0
        ver = [item for item in scoped["findings"] if item["id"] == "K8S-VER-003"]
        assert len(ver) == 1 and ver[0]["source_template"] == "global/rke2-lab"
    # Mayúsculas: los ids de sitio son minúsculas en la API.
    assert client.get("/api/findings", params={"site": "SITE-A"}).json()["scope"] == "site-a"


def test_api_compare_scoped_per_site(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    base = git_init_and_commit(root)
    (root / "sites" / "site-a" / "clusters" / "k8s-prod.yaml").write_text(BAD_CIDR)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "cidr"], cwd=root, check=True, capture_output=True, env=_git_ident_env())
    client = TestClient(create_app(root))
    all_sites = client.get("/api/findings", params={"base": base, "head": "HEAD"}).json()
    assert any(item["id"] == "K8S-NET-001" and item["site"] == "site-a" for item in all_sites["added"])
    assert all_sites["added_by_site"]["site-a"]["error"] >= 1
    assert all_sites["added_by_site"]["site-b"]["error"] == 0
    site_b = client.get("/api/findings", params={"base": base, "head": "HEAD", "site": "site-b"}).json()
    assert site_b["scope"] == "site-b"
    assert all(item["site"] == "site-b" for item in site_b["added"] + site_b["resolved"])
    assert not any(item["id"] == "K8S-NET-001" for item in site_b["added"])
    assert list(site_b["head"]["by_site"]) == ["site-b"]


def test_wizard_only_blocks_on_its_site(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    git_init_and_commit(root)
    files = [{"path": "sites/site-a/clusters/k8s-prod.yaml", "action": "modify", "content": BAD_CIDR}]
    other = evaluate_overlay(root, files, site="site-b")
    assert other["blocked"] is False
    assert all(item["site"] in ("site-b", None) for item in other["added"])
    own = evaluate_overlay(root, files, site="site-a")
    assert own["blocked"] is True
    try:
        commit_preview(root, {"files": files}, "bad-cidr", site="site-a")
        raised = False
    except WizardError:
        raised = True
    assert raised
    result = commit_preview(root, {"files": files}, "cidr-otro-sitio", site="site-b")
    assert result["ok"] and result["branch"].startswith("wizard/")


def test_sample_inventory_template_finding_per_site(repo_root: Path) -> None:
    client = TestClient(create_app(repo_root))
    for site in ("site1", "site2"):
        body = client.get("/api/findings", params={"site": site}).json()
        assert all(item["site"] == site for item in body["findings"])
        for item in body["findings"]:
            if item["id"] == "K8S-VER-003":
                assert item["source_template"] == "global/rke2-rancher-hybrid-calico"
                assert item["cluster"]
