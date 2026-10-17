"""JSON Schema del repositorio de datos: al día con el código y coherentes con el validador."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from isot import bootstrap, cabling, ingest, schemas, validator
from isot.indexer import write_indexes
from isot.paths import tool_root
from isot.rules.model import OVERRIDE_SEVERITIES
from isot.web import create_app
from tests.conftest import git_init_and_commit, seed_mini_repo, write_yaml

pytest.importorskip("jsonschema")

EXAMPLES = tool_root() / "examples"


def test_schema_files_up_to_date() -> None:
    assert schemas.check_schemas() == []


def test_schema_constants_follow_code() -> None:
    built = schemas.build_schemas()
    overrides = built["device.schema.json"]["$defs"]["rule_overrides"]["items"]["properties"]["severity"]["enum"]
    assert overrides == list(OVERRIDE_SEVERITIES)
    rules = built["topology.schema.json"]["properties"]["connection_rules"]["properties"]
    assert rules["rules"]["items"]["properties"]["attach"]["enum"] == list(cabling.ATTACH_KINDS)
    assert rules["pair_policy"]["enum"] == list(cabling.PAIR_POLICIES)
    for kind in cabling.ATTACH_KINDS:  # cada valor del schema lo entiende check_device o el wizard
        assert f'"{kind}"' in Path(cabling.__file__).read_text(encoding="utf-8")
    gaps = built["ingest-gaps.schema.json"]["properties"]["gaps"]["items"]["properties"]
    assert gaps["kind"]["enum"] == list(ingest.GAP_KINDS)
    assert gaps["status"]["enum"] == list(ingest.GAP_STATUSES)


@pytest.mark.parametrize(
    "repo",
    [EXAMPLES / "demo-repo", EXAMPLES / "messy-source" / "expected-repo", tool_root() / "templates"],
    ids=["demo-repo", "messy-expected", "templates"],
)
def test_bundled_repos_match_schemas(repo: Path) -> None:
    assert schemas.schema_issues(repo) == []


def test_demo_sur_and_scaffold_match_schemas(tmp_path: Path) -> None:
    sur = bootstrap.create_demo_sur(tmp_path / "sur")
    assert schemas.schema_issues(sur) == []
    scaffold = tmp_path / "vacio"
    bootstrap.scaffold(scaffold, commit=False)
    assert schemas.schema_issues(scaffold) == []


def test_wizard_output_matches_schemas(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    git_init_and_commit(root, "base")
    assert schemas.schema_issues(root) == []
    client = TestClient(create_app(root, edit=True))
    body = {
        "id": "site-c",
        "name": "SITE-C",
        "template_site": "site-b",
        "infra_range": "10.90.0.0/20",
        "vlan_offset": 0,
        "clusters": [{"name": "site-c-edge", "template_ref": "rke2-lab", "workers": 2}],
    }
    site = client.post("/api/wizard/site/preview", json=body)
    device = client.post(
        "/api/wizard/device/preview",
        json={"site": "site-a", "kind": "baremetal", "cluster": "k8s-prod", "role": "k8s-worker", "count": 2},
    )
    copy = tmp_path / "copia"
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"))
    for response in (site, device):
        assert response.status_code == 200, response.text
        for item in response.json()["files"]:
            target = copy / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
    assert schemas.schema_issues(copy) == []


def test_schema_rejects_what_the_validator_cannot_use(tmp_path: Path) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    device = next((root / "sites").rglob("devices/**/*.yaml"))
    write_yaml(
        device,
        {
            "hostname": device.stem,
            "role": "worker",
            "networking": [{"interface": "eth0", "vlan": "cien", "ip": "no-es-ip"}],
            "rule_overrides": [{"id": "K8S-CP-001", "severity": "mute", "reason": "x"}],
        },
    )
    messages = [message for _, message in schemas.schema_issues(root)]
    assert any("networking/0/vlan" in item for item in messages)
    assert any("networking/0/ip" in item for item in messages)
    assert any("rule_overrides/0/severity" in item for item in messages)


def test_validate_schemas_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = seed_mini_repo(tmp_path / "inv")
    write_indexes(root)
    assert validator.main(["--repo", str(root), "--schemas"]) == 0
    device = next((root / "sites").rglob("devices/**/*.yaml"))
    write_yaml(device, {"hostname": device.stem, "role": "worker", "mtu": "grande"})
    capsys.readouterr()
    assert validator.main(["--repo", str(root), "--schemas", "--format", "json"]) == 1
    out = capsys.readouterr().out
    assert "INV-SCH-001" in out and "mtu" in out
