"""Tests deterministas del allocator (hostnames, IPAM, puertos)."""

from __future__ import annotations

import pytest

from isot.allocator import (
    AllocatorError,
    convention_prefix,
    expand_range_token,
    hostname_role_warning,
    next_hostname,
    next_ip,
    next_port,
    next_port_bundle,
    next_port_for_peers,
    same_role_family,
    token_for_role,
    used_ips_from_devices,
    used_ports_by_peer,
)


def test_next_hostname_consecutive_and_padding() -> None:
    plan = next_hostname(["SITE1-NODE007"], sibling="SITE1-NODE007")
    assert plan.value == "SITE1-NODE008"
    assert "siguiente" in plan.origin


def test_next_hostname_fills_gaps() -> None:
    plan = next_hostname(["SITE1-NODE001", "SITE1-NODE003"], sibling="SITE1-NODE003")
    assert plan.value == "SITE1-NODE002"
    assert "hueco" in plan.origin


def test_next_hostname_new_prefix_starts_at_one() -> None:
    plan = next_hostname(["SITE2-CTRL001", "SITE2-CTRL002", "SITE2-LEAF001"], prefix="SITE2-NODE", width=3)
    assert plan.value == "SITE2-NODE001"


def test_role_hostname_conventions() -> None:
    assert token_for_role("apps-worker") == "NODE"
    assert token_for_role("edge-server") == "CTRL"
    assert token_for_role("platform-server") == "CTRL"
    assert token_for_role("storage-server") == "STOR"
    assert token_for_role("server-leaf") == "LEAF"
    assert token_for_role("spine") == "SPNE"
    assert token_for_role("management-switch") == "MGMT"
    assert token_for_role("dcgw") == "DCGW"
    assert convention_prefix("SITE2", "apps-worker") == "SITE2-NODE"
    assert same_role_family("apps-worker", "k8s-worker")
    assert not same_role_family("apps-worker", "edge-server")


def test_hostname_role_warning_mixed_prefix() -> None:
    assert hostname_role_warning("SITE2-CTRL003", "apps-worker")
    assert hostname_role_warning("SITE2-NODE001", "apps-worker") is None
    assert hostname_role_warning("server-02", "k8s-worker") is None


def test_next_ip_after_sibling() -> None:
    plan = next_ip("10.0.1.0/24", ["10.0.1.1", "10.0.1.15"], reserved=["10.0.1.1"], after="10.0.1.15")
    assert plan.value == "10.0.1.16/24"


def test_next_ip_skips_reserved_and_gateway() -> None:
    plan = next_ip("10.0.1.0/29", ["10.0.1.1"], reserved=["10.0.1.1", "10.0.1.2"], after=None)
    assert plan.value == "10.0.1.3/29"


def test_next_ip_full_subnet_raises() -> None:
    used = [f"10.0.1.{i}" for i in range(1, 7)]
    with pytest.raises(AllocatorError, match="subred llena"):
        next_ip("10.0.1.0/29", used, reserved=["10.0.1.1"])


def test_expand_metallb_range() -> None:
    ips = expand_range_token("10.0.1.50-10.0.1.52")
    assert ips == ["10.0.1.50", "10.0.1.51", "10.0.1.52"]


def test_next_port_increments() -> None:
    plan = next_port({"PortChannel39", "PortChannel40"}, "PortChannel39")
    assert plan.value == "PortChannel41"


def test_next_port_for_leaf_pair() -> None:
    used = {
        "LEAF-A": {"PortChannel10", "PortChannel11"},
        "LEAF-B": {"PortChannel10", "PortChannel12"},
    }
    assert next_port_for_peers(used, ["LEAF-A", "LEAF-B"], "PortChannel10") == "PortChannel13"


def test_next_port_bundle_preserves_offsets() -> None:
    used = {
        "LEAF-A": {"PortChannel10", "PortChannel11", "PortChannel12"},
        "LEAF-B": {"PortChannel10", "PortChannel11", "PortChannel12"},
    }
    assert next_port_bundle(used, ["LEAF-A", "LEAF-B"], ["PortChannel10", "PortChannel11", "PortChannel12"]) == 3


def test_used_ips_and_ports_from_devices() -> None:
    devices = [
        {
            "hostname": "n1",
            "oob": {"ip": "10.0.0.11/24", "switch": "MGMT", "port": "Ethernet1"},
            "bonds": [{"name": "bondC", "leaf_pc": {"LEAF-A": "PortChannel5"}}],
            "networking": [{"interface": "eth0", "ip": "10.0.1.11/24", "connected_to": {"device": "LEAF-A", "port": "PortChannel5"}}],
        }
    ]
    assert "10.0.0.11" in used_ips_from_devices(devices)
    assert "10.0.1.11" in used_ips_from_devices(devices)
    ports = used_ports_by_peer(devices)
    assert "PortChannel5" in ports["LEAF-A"]
    assert "Ethernet1" in ports["MGMT"]
