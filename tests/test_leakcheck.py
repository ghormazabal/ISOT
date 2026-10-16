"""Tests del check de patrones prohibidos."""

from __future__ import annotations

from pathlib import Path

from isot.leakcheck import scan


def test_working_tree_has_no_forbidden_patterns(repo_root: Path) -> None:
    assert scan(repo_root) == []


def test_scan_detects_hashed_token(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "note.yaml"
    leaked.parent.mkdir(parents=True)
    needle = "".join(["IX", "LA"])
    leaked.write_text(f"comment: sample token {needle}\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert hits
    assert any("note.yaml" in hit for hit in hits)


def test_scan_detects_legacy_cluster_host_tokens(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "legacy.yaml"
    leaked.parent.mkdir(parents=True)
    cluster = "".join(["cn", "im", "s"])
    host = "".join(["IN", "FR"])
    kind = "".join(["C", "NF"])
    leaked.write_text(f"cluster: site1-{cluster}\nhost: SITE1-{host}001\nkind: {kind}\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert hits
    assert any("legacy.yaml" in hit for hit in hits)


def test_scan_detects_legacy_platform_cluster_token(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "platform.yaml"
    leaked.parent.mkdir(parents=True)
    cluster = "".join(["infra", "-cluster"])
    leaked.write_text(f"cluster_ref: site1-{cluster}\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert hits
    assert any("platform.yaml" in hit for hit in hits)


def test_scan_does_not_flag_infrastructure_or_claims(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    note = tmp_path / "README.md"
    note.write_text(
        "ISoT — Infrastructure Source of Truth\n"
        "infra-source-of-truth\n"
        "infra_range: 10.0.0.0/16\n"
        "claims: none\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == []


def test_scan_detects_product_and_vendor_tokens(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "vendors.yaml"
    leaked.parent.mkdir(parents=True)
    cloud_kind = "".join(["Tel", "co Cloud"])
    hcp_brand = "".join(["Ka", "maji"])
    switch_oem = "".join(["Edge", "core"])
    leaf_sku = "".join(["AS", "7726"])
    nos_name = "".join(["SON", "iC"])
    srv_oem = "".join(["H", "PE"])
    pc_oem = "".join(["Del", "l"])
    leaked.write_text(
        f"note: {cloud_kind} {hcp_brand} {switch_oem} {leaf_sku} {nos_name} {srv_oem} {pc_oem}\n",
        encoding="utf-8",
    )
    hits = scan(tmp_path)
    assert hits
    assert any("vendors.yaml" in hit for hit in hits)


def test_scan_detects_product_namespace_and_doc_tokens(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "audit.yaml"
    leaked.parent.mkdir(parents=True)
    vrf_name = "".join(["VPN", "SYSTEM"])
    ns_name = "".join(["nco", "-system"])
    doc_hyphen = "".join(["as", "-", "built"])
    doc_word = "".join(["plan", "ner"])
    leaked.write_text(
        f"vrf: {vrf_name}\nnamespace: {ns_name}\nnote: {doc_hyphen} {doc_word}\n",
        encoding="utf-8",
    )
    hits = scan(tmp_path)
    assert hits
    assert any("audit.yaml" in hit for hit in hits)


def test_scan_detects_legacy_storage_prefix(tmp_path: Path, repo_root: Path) -> None:
    config = (repo_root / "isot" / "forbidden_patterns.json").read_text(encoding="utf-8")
    (tmp_path / "isot").mkdir()
    (tmp_path / "isot" / "forbidden_patterns.json").write_text(config, encoding="utf-8")
    (tmp_path / "sites.yaml").write_text("sites: []\n", encoding="utf-8")
    leaked = tmp_path / "sites" / "site1" / "vlan.yaml"
    leaked.parent.mkdir(parents=True)
    prefix = "".join(["TC", "LOUD"])
    leaked.write_text(f"name: {prefix}_STOR\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert hits
    assert any("vlan.yaml" in hit for hit in hits)
