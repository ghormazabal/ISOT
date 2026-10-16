"""Tests del generador de índice: contenido y modo --check."""

from __future__ import annotations

from pathlib import Path

from isot.indexer import (
    build_cluster_index_all,
    build_device_index_all,
    build_index,
    build_sites_index,
    build_template_index_all,
    check_index,
    main as indexer_main,
    render_index,
    write_index,
)

from tests.conftest import seed_mini_repo


def test_index_contains_both_sites(repo_root: Path) -> None:
    sites = build_sites_index(repo_root)
    ids = {item["id"] for item in sites}
    assert ids == {"site1", "site2"}
    site1 = next(item for item in sites if item["id"] == "site1")
    site2 = next(item for item in sites if item["id"] == "site2")
    assert site1["device_count"] == 22
    assert site2["device_count"] == 4
    assert any(link["peer_site"] == "site2" for link in site1["inter_site_links"])
    assert any(link["peer_device"] == "SITE2-DCGW001" for link in site1["inter_site_links"])

    entries = build_index(repo_root)
    assert len(entries) == 26
    hostnames = {item["hostname"] for item in entries}
    assert "SITE1-CTRL001" in hostnames
    assert "SITE1-MGMT001" in hostnames
    assert "SITE2-DCGW001" in hostnames
    ctrl = next(item for item in entries if item["hostname"] == "SITE1-CTRL001")
    assert ctrl["site"] == "site1"
    assert ctrl["cluster_ref"] == "site1-platform"
    assert any(link["device"] == "SITE1-LEAF001" for link in ctrl["links"])
    dcgw = next(item for item in entries if item["hostname"] == "SITE1-DCGW001")
    assert any(link["device"] == "SITE2-DCGW001" and link["site"] == "site2" for link in dcgw["links"])


def test_cluster_and_template_indexes(repo_root: Path) -> None:
    clusters = build_cluster_index_all(repo_root)
    names = {item["name"] for item in clusters}
    assert "site1-apps" in names
    assert "site2-edge" in names
    assert "ceph-site1" in names
    apps = next(item for item in clusters if item["name"] == "site1-apps")
    assert apps["template_ref"] == "rke2-rancher-hybrid-calico"
    assert apps["site"] == "site1"
    templates = build_template_index_all(repo_root)
    names = {item["name"] for item in templates}
    assert "hosted-controlplane-baremetal" in names
    hcp = next(item for item in templates if item["name"] == "hosted-controlplane-baremetal")
    assert hcp["distro"] == "hosted-controlplane"
    assert hcp["path"] == "cluster-templates/hosted-controlplane-baremetal/template.yaml"
    hybrid = next(item for item in templates if item["name"] == "rke2-rancher-hybrid-calico")
    assert hybrid["scope"] == "global"
    assert hybrid["path"] == "cluster-templates/rke2-rancher-hybrid-calico/template.yaml"
    assert hybrid["chart_count"] >= 8
    assert "metallb" in hybrid["charts"]
    assert "site1/site1-apps" in hybrid["used_by"]
    assert "site2/site2-edge" in hybrid["used_by"]
    apps_idx = next(item for item in clusters if item["name"] == "site1-apps")
    assert apps_idx["override_count"] == 1
    edge = next(item for item in templates if item["name"] == "rke2-edge-calico")
    assert edge["site"] == "site2"
    assert edge["used_by"] == []


def test_check_mode_detects_stale_index(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path)
    write_index(root)
    ok, message = check_index(root)
    assert ok, message
    assert indexer_main(["--root", str(root), "--check"]) == 0

    (root / "sites" / "_sites_index.json").write_text("[]\n", encoding="utf-8")
    ok, _ = check_index(root)
    assert not ok
    assert indexer_main(["--root", str(root), "--check"]) == 1


def test_committed_index_matches_generator(repo_root: Path) -> None:
    ok, message = check_index(repo_root)
    assert ok, message


def test_render_is_deterministic(repo_root: Path) -> None:
    first = render_index(build_device_index_all(repo_root))
    second = render_index(build_device_index_all(repo_root))
    assert first == second
    assert first.endswith("\n")
