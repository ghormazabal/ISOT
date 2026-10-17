"""Comparar universal: base/head = `local` (working tree), rama, tag o commit del mismo repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot.compare import changed_files, file_diff, safe_repo_path
from isot.gitdiff import RefError, clear_caches, semantic_diff
from isot.paths import tool_root
from isot.refs import LOCAL, TREES, describe_ref, is_local, parse_ref, resolve_ref, tree_for
from isot.rules.evaluate import diff_findings, evaluate_ref
from isot.web import create_app
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    clear_caches()
    root = seed_mini_repo(tmp_path / "inv")
    from isot.indexer import write_indexes

    write_indexes(root)
    sha = git_init_and_commit(root, "base")
    _git(root, "tag", "v1")
    return root, sha


def _local_edit(root: Path) -> None:
    """Cambios sin commit: VLAN nueva, device quitado, archivo no rastreado."""
    topo = root / "sites" / "site-a" / "topology" / "index.yaml"
    write_yaml(
        topo,
        {
            "subnets": [{"name": "mgmt", "cidr": "10.0.1.0/24", "vlan": 100}, {"name": "LAB_NET", "cidr": "10.0.31.0/24", "vlan": 2099}],
            "vlans": [{"id": 100, "name": "mgmt", "subnet": "mgmt"}, {"id": 2099, "name": "LAB", "subnet": "LAB_NET"}],
        },
    )
    (root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml").unlink()
    write_yaml(root / "notes" / "nuevo.yaml", {"hola": "mundo"})


def test_parse_ref_local_aliases_and_injection() -> None:
    for value in ("local", "LOCAL", "working", "worktree", "working-tree"):
        assert is_local(value) and parse_ref(value) == LOCAL
    for bad in ("", "main; rm -rf /", "$(id)", "-c", "--upload-pack=x", "a..b", "x y", "HEAD@{1}", "`id`"):
        with pytest.raises(RefError):
            parse_ref(bad)
    assert parse_ref("main") == "main" and parse_ref("abc1234") == "abc1234" and parse_ref("v1.2") == "v1.2"


def test_resolve_branch_tag_short_sha_and_unknown(repo: tuple[Path, str]) -> None:
    root, sha = repo
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    assert resolve_ref(root, branch).sha == sha
    assert resolve_ref(root, "v1").sha == sha
    assert resolve_ref(root, sha[:7]).sha == sha
    local = resolve_ref(root, "local")
    assert local.local and local.sha == sha and local.key.startswith("local:")
    with pytest.raises(RefError):
        resolve_ref(root, "no-existe")
    with pytest.raises(RefError):
        resolve_ref(root, "deadbeef")
    meta = describe_ref(root, sha[:7])
    assert meta["subject"] == "base" and meta["local"] is False
    _local_edit(root)
    assert describe_ref(root, "local")["dirty"] >= 3
    assert resolve_ref(root, "local").key != local.key  # la huella cambia con el working tree


def test_tree_cache_by_sha_outside_isot_and_cleanup(repo: tuple[Path, str]) -> None:
    root, sha = repo
    with tree_for(root, sha) as first:
        assert (first / "sites.yaml").is_file()
        assert not first.resolve().is_relative_to(tool_root().resolve())
        assert not first.resolve().is_relative_to(root.resolve())
    with tree_for(root, "v1") as second:
        assert second == first  # mismo SHA → mismo árbol cacheado
    with tree_for(root, "local") as local:
        assert local == root
    TREES.clear()
    assert not first.exists()


def test_semantic_diff_commit_vs_local(repo: tuple[Path, str]) -> None:
    root, sha = repo
    _local_edit(root)
    payload = semantic_diff(root, sha[:7], "local", site="site-a")
    by_id = {item["id"]: item for item in payload["entities"]}
    assert payload["head"]["local"] is True and payload["base"]["sha"] == sha
    assert by_id["site-a:vlan:2099"]["status"] == "added"
    assert by_id["site-a:subnet:LAB_NET"]["status"] == "added"
    assert by_id["site-a:server-01"]["status"] == "removed"
    # Cada entidad dice de qué archivo sale (para abrir su diff YAML en «Archivos»).
    assert by_id["site-a:server-01"]["path"] == "sites/site-a/devices/baremetal/server-01.yaml"
    assert by_id["site-a:vlan:2099"]["path"] == "sites/site-a/topology/index.yaml"
    reverse = semantic_diff(root, "local", "v1", site="site-a")
    assert {i["id"]: i["status"] for i in reverse["entities"]}["site-a:server-01"] == "added"
    # Un nuevo cambio local invalida la caché (huella del working tree).
    (root / "sites" / "site-a" / "devices" / "network" / "switch-core.yaml").unlink()
    again = semantic_diff(root, sha, "local", site="site-a")
    assert {i["id"]: i["status"] for i in again["entities"]}["site-a:switch-core"] == "removed"


def test_ipam_address_spaces_are_compared(repo: tuple[Path, str]) -> None:
    root, sha = repo
    catalog = root / "sites.yaml"
    catalog.write_text(catalog.read_text().replace("dci-overlay", "dci-overlay-v2"))
    ids = {i["id"]: i["status"] for i in semantic_diff(root, sha, "local")["entities"]}
    assert ids["global:space:dci-overlay"] == "removed"
    assert ids["global:space:dci-overlay-v2"] == "added"


def test_changed_files_and_file_diff_local_vs_commit(repo: tuple[Path, str]) -> None:
    root, sha = repo
    _local_edit(root)
    listing = changed_files(root, sha, "local")
    status = {f["path"]: f["status"] for f in listing["files"]}
    assert status["sites/site-a/topology/index.yaml"] == "modified"
    assert status["sites/site-a/devices/baremetal/server-01.yaml"] == "removed"
    assert status["notes/nuevo.yaml"] == "added"  # no rastreado
    inverse = {f["path"]: f["status"] for f in changed_files(root, "local", sha)["files"]}
    assert inverse["notes/nuevo.yaml"] == "removed" and inverse["sites/site-a/devices/baremetal/server-01.yaml"] == "added"
    scoped = changed_files(root, sha, "local", prefix=["sites/site-a/topology"])
    assert [f["path"] for f in scoped["files"]] == ["sites/site-a/topology/index.yaml"]
    one = file_diff(root, sha, "local", "sites/site-a/topology/index.yaml")
    assert one["status"] == "modified" and "+  subnet: LAB_NET" in one["diff"]
    gone = file_diff(root, sha, "local", "sites/site-a/devices/baremetal/server-01.yaml")
    assert gone["status"] == "removed" and gone["after"] is None
    assert changed_files(root, "local", "local")["files"] == []


def test_commit_vs_commit_and_path_validation(repo: tuple[Path, str]) -> None:
    root, sha = repo
    _local_edit(root)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.net", "commit", "-qm", "cambio")
    head = _git(root, "rev-parse", "HEAD")
    status = {f["path"]: f["status"] for f in changed_files(root, "v1", head[:8])["files"]}
    assert status["notes/nuevo.yaml"] == "added"
    for bad in ("../etc/passwd", "/etc/passwd", ".git/config", "a/../../b", "x;rm", ""):
        with pytest.raises(RefError):
            safe_repo_path(bad)


def test_findings_delta_local_vs_commit(repo: tuple[Path, str]) -> None:
    root, sha = repo
    device = root / "sites" / "site-a" / "devices" / "baremetal" / "server-01.yaml"
    device.write_text(device.read_text().replace("cluster_ref: k8s-prod", "cluster_ref: no-existe"))
    base = evaluate_ref(root, sha)
    head = evaluate_ref(root, "local")
    added, resolved = diff_findings(base, head)
    assert added, "un cluster_ref roto debe aparecer como hallazgo nuevo en local"
    client = TestClient(create_app(root))
    payload = client.get("/api/findings", params={"base": sha[:7], "head": "local"}).json()
    assert payload["refs"]["head"]["local"] is True
    assert {item["id"] for item in payload["added"]} == {item.id for item in added}


def test_api_compare_endpoints_are_read_only(repo: tuple[Path, str]) -> None:
    root, sha = repo
    _local_edit(root)
    before = _git(root, "status", "--porcelain")
    client = TestClient(create_app(root))
    assert client.get("/api/refs/resolve", params={"ref": "local"}).json()["local"] is True
    assert client.get("/api/refs/resolve", params={"ref": "$(id)"}).status_code == 400
    assert client.get("/api/refs/resolve", params={"ref": "nope"}).status_code == 400
    diff = client.get("/api/diff", params={"base": "v1", "head": "local", "site": "site-a"}).json()
    assert diff["head"]["local"] and diff["summary"]["removed"] >= 1
    files = client.get("/api/compare/files", params={"base": "v1", "head": "local"}).json()
    assert files["counts"]["added"] >= 1
    one = client.get("/api/compare/file", params={"base": "v1", "head": "local", "path": "notes/nuevo.yaml"}).json()
    assert one["status"] == "added"
    assert client.get("/api/compare/file", params={"base": "v1", "head": "local", "path": "../x"}).status_code == 400
    link = client.get(
        "/api/link",
        params={"source": "site-a:server-01", "target": "site-a:switch-core", "base": "v1", "head": "local"},
    )
    assert link.status_code == 200 and link.json()["head"] is None and link.json()["base"] is not None
    assert _git(root, "status", "--porcelain") == before  # comparar nunca escribe
