"""Carga perezosa y escaneo de YAML del inventario ISoT multi-sitio.

La UI web no usa este módulo al arrancar: lee los JSON índice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from isot.paths import CATALOG_NAME

DEVICE_INDEX_NAME = "_device_index.json"
CLUSTER_INDEX_NAME = "_cluster_index.json"
TEMPLATE_INDEX_NAME = "_template_index.json"
SITES_INDEX_NAME = "_sites_index.json"
YAML_SUFFIXES = {".yaml", ".yml"}
GLOBAL_SITE = "global"
TEMPLATE_FILENAME = "template.yaml"
VALUES_DIRNAME = "values"


def parse_ref(value: str | None, default_site: str) -> tuple[str, str] | None:
    """Resuelve `nombre` o `sitio/nombre` al par (sitio, nombre)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "/" in text:
        site, _, name = text.partition("/")
        site, name = site.strip(), name.strip()
        if site and name:
            return site, name
    return default_site, text


@dataclass
class Interface:
    """Una interfaz de red declarada en el YAML del dispositivo."""

    name: str
    mac: str | None = None
    ip: str | None = None
    vlan: int | str | None = None
    connected_to_device: str | None = None
    connected_to_port: str | None = None
    connected_to_site: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ip_address(self) -> str | None:
        if not self.ip:
            return None
        return str(self.ip).split("/", 1)[0].strip() or None


@dataclass
class Device:
    """Dispositivo parseado desde un YAML bajo sites/<id>/devices/."""

    path: Path
    rel_path: str
    site_id: str
    hostname: str
    role: str | None
    cluster_ref: str | None
    cluster_ref_site: str | None
    os: str | None
    vendor: str | None
    model: str | None
    data: dict[str, Any]
    interfaces: list[Interface]

    @property
    def primary_ip(self) -> str | None:
        for iface in self.interfaces:
            if iface.ip:
                return str(iface.ip)
        return None

    @property
    def kind(self) -> str:
        parts = Path(self.rel_path).parts
        if "devices" in parts:
            idx = parts.index("devices")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return "otros"

    def index_links(self) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []

        def add(interface: str | None, device: str | None, port: str | None, vlan: Any, site: str | None) -> None:
            if not device:
                return
            peer_site = site or self.site_id
            links.append(
                {
                    "interface": interface or None,
                    "device": device,
                    "port": port,
                    "vlan": vlan,
                    "site": peer_site,
                }
            )

        for iface in self.interfaces:
            add(
                iface.name,
                iface.connected_to_device,
                iface.connected_to_port,
                iface.vlan,
                iface.connected_to_site,
            )
        bonds = self.data.get("bonds") or []
        if isinstance(bonds, list):
            for bond in bonds:
                if not isinstance(bond, dict):
                    continue
                leaf_pc = bond.get("leaf_pc") or {}
                if not isinstance(leaf_pc, dict):
                    continue
                for switch, port in leaf_pc.items():
                    parsed = parse_ref(str(switch), self.site_id)
                    if parsed is None:
                        continue
                    site, hostname = parsed
                    add(bond.get("name"), hostname, None if port is None else str(port), None, site)
        hypervisor = self.data.get("hypervisor") or {}
        if isinstance(hypervisor, dict) and hypervisor.get("device"):
            parsed = parse_ref(str(hypervisor["device"]), self.site_id)
            if parsed:
                add("hypervisor", parsed[1], None, None, parsed[0])
        oob = self.data.get("oob") or {}
        if isinstance(oob, dict) and oob.get("switch") and not oob.get("undocumented_peer"):
            parsed = parse_ref(str(oob["switch"]), self.site_id)
            if parsed:
                add("oob", parsed[1], _optional_str(oob.get("port")), oob.get("vlan") or 2002, parsed[0])
        return links


@dataclass
class Site:
    id: str
    name: str
    rel_path: str
    description: str | None
    root: Path

    @property
    def devices_dir(self) -> Path:
        return self.root / "devices"

    @property
    def clusters_dir(self) -> Path:
        return self.root / "clusters"

    @property
    def templates_dir(self) -> Path:
        return self.root / "cluster-templates"

    @property
    def topology_path(self) -> Path:
        return self.root / "topology" / "index.yaml"


@dataclass
class AddressSpace:
    id: str
    description: str | None
    sites: list[str]
    match_subnets: list[str]
    match_vlans: list[Any]


def iter_yaml_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in YAML_SUFFIXES:
            continue
        if path.name.startswith("_"):
            continue
        yield path


def iter_template_files(directory: Path) -> Iterator[Path]:
    """YAML de template: `<nombre>/template.yaml` o `<nombre>.yaml` en el primer nivel.

    No recorre `values/`: esos archivos se cargan bajo demanda.
    """
    if not directory.is_dir():
        return
    for child in sorted(directory.iterdir()):
        if child.name.startswith("_"):
            continue
        if child.is_dir():
            candidate = child / TEMPLATE_FILENAME
            if candidate.is_file():
                yield candidate
            continue
        if child.suffix.lower() in YAML_SUFFIXES:
            yield child


def find_template_file(templates_dir: Path, name: str) -> Path | None:
    if not name:
        return None
    folder = templates_dir / name / TEMPLATE_FILENAME
    if folder.is_file():
        return folder
    for suffix in (".yaml", ".yml"):
        flat = templates_dir / f"{name}{suffix}"
        if flat.is_file():
            return flat
    return None


def template_stem(path: Path) -> str:
    if path.name == TEMPLATE_FILENAME:
        return path.parent.name
    return path.stem


def template_dir(path: Path) -> Path:
    if path.name == TEMPLATE_FILENAME:
        return path.parent
    return path.parent


def load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return {} if data is None else data


def _parse_interface(raw: Any, default_site: str) -> Interface | None:
    if not isinstance(raw, dict):
        return None
    connected = raw.get("connected_to") or {}
    if not isinstance(connected, dict):
        connected = {}
    name = raw.get("interface") or raw.get("name") or ""
    peer = _optional_str(connected.get("device"))
    peer_site = _optional_str(connected.get("site"))
    if peer and not peer_site:
        parsed = parse_ref(peer, default_site)
        if parsed:
            peer_site, peer = parsed
    return Interface(
        name=str(name) if name else "",
        mac=_optional_str(raw.get("mac")),
        ip=_optional_str(raw.get("ip")),
        vlan=raw.get("vlan"),
        connected_to_device=peer,
        connected_to_port=_optional_str(connected.get("port")),
        connected_to_site=peer_site or default_site if peer else None,
        raw=raw,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_catalog(repo_root: Path) -> tuple[list[Site], list[AddressSpace]]:
    path = repo_root / CATALOG_NAME
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = load_yaml(path)
        if isinstance(loaded, dict):
            data = loaded
    sites: list[Site] = []
    for item in data.get("sites") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        site_id = str(item["id"])
        rel = str(item.get("path") or f"sites/{site_id}")
        sites.append(
            Site(
                id=site_id,
                name=str(item.get("name") or site_id),
                rel_path=rel,
                description=_optional_str(item.get("description")),
                root=repo_root / rel,
            )
        )
    if not sites:
        sites_dir = repo_root / "sites"
        if sites_dir.is_dir():
            for child in sorted(sites_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("_"):
                    sites.append(
                        Site(
                            id=child.name,
                            name=child.name,
                            rel_path=f"sites/{child.name}",
                            description=None,
                            root=child,
                        )
                    )
    spaces: list[AddressSpace] = []
    for item in data.get("address_spaces") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        match = item.get("match") or {}
        if not isinstance(match, dict):
            match = {}
        spaces.append(
            AddressSpace(
                id=str(item["id"]),
                description=_optional_str(item.get("description")),
                sites=[str(s) for s in (item.get("sites") or [])],
                match_subnets=[str(s) for s in (match.get("subnets") or [])],
                match_vlans=list(match.get("vlans") or []),
            )
        )
    return sites, spaces


def parse_device(path: Path, repo_root: Path, site: Site) -> Device:
    data = load_yaml(path)
    if not isinstance(data, dict):
        data = {"_raw": data}

    networking = data.get("networking") or []
    interfaces: list[Interface] = []
    if isinstance(networking, list):
        for item in networking:
            iface = _parse_interface(item, site.id)
            if iface is not None:
                interfaces.append(iface)

    rel = path.relative_to(repo_root).as_posix()
    hostname = _optional_str(data.get("hostname")) or path.stem
    cluster_raw = data.get("cluster_ref")
    cluster_site = site.id
    cluster_name = None
    if isinstance(cluster_raw, dict):
        cluster_name = _optional_str(cluster_raw.get("name") or cluster_raw.get("ref"))
        cluster_site = _optional_str(cluster_raw.get("site")) or site.id
    else:
        parsed = parse_ref(_optional_str(cluster_raw), site.id)
        if parsed:
            cluster_site, cluster_name = parsed
    return Device(
        path=path,
        rel_path=rel,
        site_id=site.id,
        hostname=hostname,
        role=_optional_str(data.get("role")),
        cluster_ref=cluster_name,
        cluster_ref_site=cluster_site if cluster_name else None,
        os=_optional_str(data.get("os")),
        vendor=_optional_str(data.get("vendor")),
        model=_optional_str(data.get("model") or data.get("hw_model")),
        data=data,
        interfaces=interfaces,
    )


def load_devices(repo_root: Path, sites: list[Site] | None = None) -> list[Device]:
    sites = sites if sites is not None else load_catalog(repo_root)[0]
    devices: list[Device] = []
    for site in sites:
        for path in iter_yaml_files(site.devices_dir):
            devices.append(parse_device(path, repo_root, site))
    return devices


def load_docs(
    directory: Path,
    repo_root: Path,
    site_id: str,
    files: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in files if files is not None else iter_yaml_files(directory):
        data = load_yaml(path)
        if not isinstance(data, dict):
            data = {"_raw": data}
        data = dict(data)
        data["_path"] = path.relative_to(repo_root).as_posix()
        data["_stem"] = template_stem(path) if files is not None else path.stem
        data["_site"] = site_id
        docs.append(data)
    return docs


def load_clusters(repo_root: Path, sites: list[Site] | None = None) -> list[dict[str, Any]]:
    sites = sites if sites is not None else load_catalog(repo_root)[0]
    docs: list[dict[str, Any]] = []
    for site in sites:
        docs.extend(load_docs(site.clusters_dir, repo_root, site.id))
    return docs


def load_templates(repo_root: Path, sites: list[Site] | None = None) -> list[dict[str, Any]]:
    sites = sites if sites is not None else load_catalog(repo_root)[0]
    global_dir = repo_root / "cluster-templates"
    docs = load_docs(global_dir, repo_root, GLOBAL_SITE, files=iter_template_files(global_dir))
    for site in sites:
        docs.extend(
            load_docs(
                site.templates_dir,
                repo_root,
                site.id,
                files=iter_template_files(site.templates_dir),
            )
        )
    return docs


def load_topology(site: Site) -> dict[str, Any]:
    path = site.topology_path
    if not path.is_file():
        return {}
    data = load_yaml(path)
    return data if isinstance(data, dict) else {}


def site_by_id(sites: list[Site], site_id: str) -> Site | None:
    return next((item for item in sites if item.id == site_id), None)


def cluster_file(site: Site, cluster_ref: str) -> Path:
    return site.clusters_dir / f"{cluster_ref}.yaml"


def template_file(repo_root: Path, site: Site | None, template_ref: str) -> Path | None:
    """Busca el YAML de un template: sitio actual, luego catálogo global."""
    name = template_ref
    target_site = site
    parsed = parse_ref(template_ref, site.id if site else GLOBAL_SITE)
    if parsed:
        ref_site, name = parsed
        if ref_site == GLOBAL_SITE:
            return find_template_file(repo_root / "cluster-templates", name)
        if site is None or ref_site != site.id:
            return None
        target_site = site
    if target_site is not None:
        local = find_template_file(target_site.templates_dir, name)
        if local is not None:
            return local
    return find_template_file(repo_root / "cluster-templates", name)


def resolve_template_path(
    repo_root: Path,
    sites: list[Site],
    from_site: str,
    template_ref: str,
) -> Path | None:
    parsed = parse_ref(template_ref, from_site)
    if parsed is None:
        return None
    ref_site, name = parsed
    if ref_site == GLOBAL_SITE:
        return find_template_file(repo_root / "cluster-templates", name)
    if ref_site != from_site:
        other = site_by_id(sites, ref_site)
        if other is None:
            return None
        candidate = find_template_file(other.templates_dir, name)
        if candidate is not None:
            return candidate
        return find_template_file(repo_root / "cluster-templates", name)
    here = site_by_id(sites, from_site)
    return template_file(repo_root, here, name)
