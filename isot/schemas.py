"""JSON Schema del repositorio de datos, generados desde las reglas del código.

`python -m isot.schemas --write` regenera `schemas/*.schema.json`;
`--check` falla si no están al día (lo corre pytest). Las constantes (severidades
de override, valores de connection_rules, formato de `_ingest/`) se importan de
los módulos que las usan, para que el schema y el validador no se separen.

Los schemas son **permisivos**: describen los campos que ISoT lee y su tipo,
pero aceptan claves adicionales (el modelo es abierto: lo que no se reconoce se
muestra como «otros atributos»). `isot validate --schemas` los aplica a un
repositorio (requiere `jsonschema`, extra `[schemas]`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from isot import ingest
from isot.cabling import ARCH_COLLAPSED, ARCH_CUSTOM, ARCH_SPINE_LEAF, ATTACH_KINDS, PAIR_POLICIES
from isot.inventory import iter_template_files, iter_yaml_files, load_catalog, load_yaml
from isot.paths import tool_root
from isot.rules.model import OVERRIDE_SEVERITIES

DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIR = "schemas"
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
# Hostname: lo que acepta la API (SAFE_NAME) y el wizard.
HOSTNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"
IP_PATTERN = r"^[0-9A-Fa-f:.]+(/[0-9]{1,3})?$"


def _str(description: str | None = None, *, nullable: bool = True, **extra: Any) -> dict[str, Any]:
    """Texto. Por defecto admite null: un dato desconocido puede quedar vacío."""
    out: dict[str, Any] = {"type": ["string", "null"] if nullable else "string", **extra}
    if description:
        out["description"] = description
    return out


def _opt(schema: dict[str, Any]) -> dict[str, Any]:
    """Campo que puede quedar vacío ("" o null) cuando el dato es desconocido."""
    return {"anyOf": [schema, {"type": "null"}, {"const": ""}], **({"description": schema["description"]} if "description" in schema else {})}


def _defs() -> dict[str, Any]:
    return {
        "name": _str(nullable=False, pattern=NAME_PATTERN),
        "ip": _str("IPv4/IPv6, con o sin prefijo (10.0.0.5 o 10.0.0.5/24).", nullable=False, pattern=IP_PATTERN),
        "cidr": _str("Red en notación CIDR (10.0.0.0/24).", nullable=False, pattern=r"^[0-9A-Fa-f:.]+/[0-9]{1,3}$"),
        "vlan": {"type": "integer", "minimum": 1, "maximum": 4094, "description": "VLAN 802.1Q (1-4094)."},
        "ref": _str("`nombre` (mismo sitio) o `sitio/nombre`.", nullable=False, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$"),
        "rule_overrides": {
            "type": "array",
            "description": "Cambia la severidad de una regla en este alcance. Sin reason no aplica (RULE-OVR-001).",
            "items": {
                "type": "object",
                "required": ["id", "severity", "reason"],
                "properties": {
                    "id": _str("Id de regla, p.ej. K8S-CP-001 (ver docs/rules.md).", nullable=False),
                    "severity": {"enum": list(OVERRIDE_SEVERITIES)},
                    "reason": _str("Por qué se acepta la desviación (obligatorio).", nullable=False, minLength=1),
                },
            },
        },
    }


def _device_schema() -> dict[str, Any]:
    peer = {
        "type": "object",
        "required": ["device"],
        "description": "Extremo remoto. device debe existir en el sitio (o en `site`); si no, INV-REF-001.",
        "properties": {
            "device": {"$ref": "#/$defs/ref"},
            "site": {"$ref": "#/$defs/name"},
            "port": _str("Puerto del peer (Ethernet1/1, PortChannel10, br-x)."),
        },
    }
    interface = {
        "type": "object",
        "description": "Interfaz lógica o física. Genera una arista si tiene connected_to.",
        "anyOf": [{"required": ["interface"]}, {"required": ["name"]}],
        "properties": {
            "interface": _str("Nombre de la interfaz (eth0, Management0, VLAN_X)."),
            "name": _str("Alias de interface."),
            "parent": _str("Bond o interfaz física que la transporta."),
            "vlan": _opt({"$ref": "#/$defs/vlan"}),
            "ip": _opt({"$ref": "#/$defs/ip"}),
            "gateway": _opt({"$ref": "#/$defs/ip"}),
            "mac": _opt(_str(pattern=r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")),
            "mtu": {"type": "integer", "minimum": 576},
            "network": _str("Nombre de subred de la topología."),
            "vrf": _str(),
            "connected_to": peer,
        },
    }
    bond = {
        "type": "object",
        "required": ["name"],
        "description": "Bond/LAG. leaf_pc genera una arista por switch (panel Conexión).",
        "properties": {
            "name": _str(),
            "mode": _str("lacp, active-backup, ..."),
            "members": {"type": "array", "items": {"type": "string"}},
            "leaf_pc": {
                "type": "object",
                "description": "{SWITCH: puerto} en cada switch del par.",
                "additionalProperties": {"type": ["string", "integer", "null"]},
            },
            "allowed_vlans": {"type": "array", "items": {"$ref": "#/$defs/vlan"}},
            "extra_host_vlans": {"type": "array", "items": {"$ref": "#/$defs/vlan"}},
            "mtu": {"type": "integer", "minimum": 576},
        },
    }
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/device",
        "title": "Dispositivo: sites/<sitio>/devices/<tipo>/<HOSTNAME>.yaml",
        "description": "Un archivo por dispositivo. <tipo> (baremetal, network, virtual-machines, ...) es la carpeta y agrupa el grafo.",
        "type": "object",
        "required": ["hostname", "role"],
        "properties": {
            "hostname": _str("Clave del dispositivo en el sitio; igual al nombre del archivo.", nullable=False, pattern=HOSTNAME_PATTERN),
            "role": _str("Rol funcional (spine, server-leaf, mgmt, worker, control-plane, storage, ...).", nullable=False, minLength=1),
            "kind": _str("baremetal, network, vm, ... (informativo; el grafo usa la carpeta)."),
            "vendor": _opt(_str()),
            "model": _opt(_str()),
            "hw_model": _opt(_str()),
            "os": _opt(_str()),
            "serial": _opt(_str()),
            "k8s_roles": {"type": "array", "items": {"type": "string"}},
            "cluster_ref": {
                "description": "Clúster al que pertenece: nombre, sitio/nombre o {name, site}. null/vacío: sin clúster.",
                "anyOf": [
                    {"$ref": "#/$defs/ref"},
                    {"type": "null"},
                    {"const": ""},
                    {
                        "type": "object",
                        "anyOf": [{"required": ["name"]}, {"required": ["ref"]}],
                        "properties": {"name": _str(), "ref": _str(), "site": {"$ref": "#/$defs/name"}},
                    },
                ],
            },
            "pair": _str("Hostname del otro switch del par MLAG/ESI."),
            "leaf_pair": _str("Id del par de leafs (agrupa el grafo)."),
            "networking": {"type": "array", "items": interface},
            "bonds": {"type": "array", "items": bond},
            "oob": {
                "type": "object",
                "description": "Gestión fuera de banda (BMC). switch+port generan una arista hacia el switch MGMT.",
                "properties": {
                    "type": _str(),
                    "ip": _opt({"$ref": "#/$defs/ip"}),
                    "switch": _opt({"$ref": "#/$defs/ref"}),
                    "port": _str(),
                    "vlan": _opt({"$ref": "#/$defs/vlan"}),
                    "undocumented_peer": {"type": "boolean", "description": "true: el switch OOB no está documentado; no se dibuja arista."},
                },
            },
            "hypervisor": {
                "type": "object",
                "description": "Solo VMs: host físico que la ejecuta.",
                "properties": {"device": _opt({"$ref": "#/$defs/ref"}), "cluster": _str(), "namespace": _str()},
            },
            "bgp": {
                "type": "object",
                "properties": {
                    "asn": {"type": ["integer", "string", "null"]},
                    "router_id": _opt({"$ref": "#/$defs/ip"}),
                },
            },
            "vlans": {"type": "array", "items": {"$ref": "#/$defs/vlan"}},
            "mtu": {"type": "integer", "minimum": 576},
            "rule_overrides": {"$ref": "#/$defs/rule_overrides"},
        },
        "$defs": _defs(),
    }


def _topology_schema() -> dict[str, Any]:
    rule = {
        "type": "object",
        "required": ["id", "attach"],
        "properties": {
            "id": _str(),
            "roles": {"type": "array", "items": {"type": "string"}},
            "attach": {"enum": list(ATTACH_KINDS)},
            "leafs_per_node": {"type": "integer", "minimum": 1},
            "bond": _str(),
            "bond_name": _str(),
            "via": _str(),
            "purpose": _str(),
            "description": _str(),
        },
    }
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/topology",
        "title": "Topología de sitio: sites/<sitio>/topology/index.yaml (y site-templates/<id>/topology/index.yaml)",
        "type": "object",
        "properties": {
            "site": {
                "type": "object",
                "properties": {
                    "id": {"$ref": "#/$defs/name"},
                    "operator": _str(),
                    "description": _str(),
                    "infra_range": _opt({"$ref": "#/$defs/cidr"}),
                    "vlan_range": _opt(_str("Rango de VLANs del sitio, p.ej. \"2000-2099\".")),
                    "dns_servers": {"type": "array", "items": {"$ref": "#/$defs/ip"}},
                    "ntp_servers": {"type": "array", "items": {"$ref": "#/$defs/ip"}},
                },
            },
            "architecture": {"enum": [ARCH_SPINE_LEAF, ARCH_COLLAPSED, ARCH_CUSTOM]},
            "subnets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": _str("Clave de la subred (la citan vlans[].subnet, NADs y pools)."),
                        "cidr": _opt({"$ref": "#/$defs/cidr"}),
                        "vlan": _opt({"$ref": "#/$defs/vlan"}),
                        "gateway": _opt({"$ref": "#/$defs/ip"}),
                        "vrrp": _opt({"$ref": "#/$defs/ip"}),
                        "vrrp_ip": _opt({"$ref": "#/$defs/ip"}),
                        "vrf": _opt(_str()),
                        "description": _str(),
                    },
                },
            },
            "vlans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"$ref": "#/$defs/vlan"},
                        "name": _str(),
                        "subnet": _str("name de una subred de esta topología."),
                    },
                },
            },
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": _str(), "kind": _str(), "path": _str()},
                },
            },
            "undocumented_peers": {"type": "array"},
            "gaps": {"type": "array", "items": {"type": "string"}, "description": "Huecos en texto libre (se muestran en la ficha del sitio)."},
            "connection_rules": {
                "type": "object",
                "description": "Política de cableado. Solo si las fuentes la documentan; activa INV-CAB-001.",
                "properties": {
                    "architecture": {"enum": [ARCH_SPINE_LEAF, ARCH_COLLAPSED, ARCH_CUSTOM]},
                    "pair_policy": {"enum": list(PAIR_POLICIES)},
                    "ports_per_leaf": {"type": "integer", "minimum": 1},
                    "fabric": {
                        "type": "object",
                        "properties": {
                            key: {"type": "integer", "minimum": 0}
                            for key in ("spines", "leaf_pairs", "unpaired_leafs", "mgmt_switches", "dcgw")
                        },
                    },
                    "pairs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": _str(), "leafs": {"type": "array", "items": {"type": "string"}}},
                        },
                    },
                    "rules": {"type": "array", "items": rule},
                },
            },
            "rule_overrides": {"$ref": "#/$defs/rule_overrides"},
        },
        "$defs": _defs(),
    }


def _cluster_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/cluster",
        "title": "Clúster: sites/<sitio>/clusters/<cluster_name>.yaml",
        "description": "El nombre del archivo debe ser cluster_name: cluster_ref lo resuelve así.",
        "type": "object",
        "required": ["cluster_name", "kind"],
        "properties": {
            "cluster_name": _str(nullable=False, pattern=NAME_PATTERN),
            "kind": _str("kubernetes, ceph, ... (solo kubernetes entra en la vista Kubernetes y en las reglas K8S-*)."),
            "template_ref": _opt({"$ref": "#/$defs/ref"}),
            "distro": _str(),
            "version": _str("Versión de Kubernetes (v1.30.4+rke2r1, 1.30, ...)."),
            "kubernetes_version": _str(),
            "pod_cidr": _opt({"$ref": "#/$defs/cidr"}),
            "service_cidr": _opt({"$ref": "#/$defs/cidr"}),
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "anyOf": [{"required": ["hostname"]}, {"required": ["name"]}],
                    "properties": {
                        "hostname": _str("Hostname de un device del sitio con cluster_ref a este clúster."),
                        "name": _str(),
                        "roles": {"type": "array", "items": {"type": "string"}},
                        "internal_ip": _opt({"$ref": "#/$defs/ip"}),
                    },
                },
            },
            "hosts": {"type": "array"},
            "hosts_missing": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Nodos conocidos por nombre sin YAML de device todavía (no fallan INV-REF-001).",
            },
            "nads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "namespace": _str(),
                        "name": _str(),
                        "vlan": {"$ref": "#/$defs/vlan"},
                        "subnet": _str("name de una subred de la topología."),
                    },
                },
            },
            "metallb": {
                "type": "object",
                "properties": {
                    "pools": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": _str(),
                                "subnet": _str(),
                                "addresses": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "vips": {"type": ["array", "object"]},
                },
            },
            "values_overrides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["release"],
                    "properties": {
                        "release": _str("name de un chart del template."),
                        "values_inline": {},
                        "values_ref": _str(),
                    },
                },
            },
            "rule_overrides": {"$ref": "#/$defs/rule_overrides"},
        },
        "$defs": _defs(),
    }


def _template_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/cluster-template",
        "title": "Template: cluster-templates/<nombre>/template.yaml (o sites/<sitio>/cluster-templates/...)",
        "type": "object",
        "required": ["template_name"],
        "properties": {
            "template_name": _str("Igual al nombre de la carpeta; lo citan los clústeres en template_ref.", nullable=False, pattern=NAME_PATTERN),
            "version": _str(),
            "distro": _str(),
            "kubernetes_version": _str(),
            "cni": {"type": "object", "properties": {"primary": _str(), "defaults": {"type": "object"}}},
            "charts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": _str("Nombre del release (único en el template)."),
                        "repo": _str(),
                        "chart": _str(),
                        "version": _str("Fija la versión (K8S-VER-003 si falta)."),
                        "namespace": _str(),
                        "order": {"type": ["integer", "number"]},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "values_ref": _str("Ruta relativa a la carpeta del template (values/<archivo>.yaml)."),
                        "values_inline": {},
                    },
                },
            },
            "rule_overrides": {"$ref": "#/$defs/rule_overrides"},
        },
        "$defs": _defs(),
    }


def _sites_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/sites",
        "title": "Catálogo: sites.yaml",
        "type": "object",
        "properties": {
            "sites": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": _str("Id corto en minúsculas; carpeta sites/<id>.", nullable=False, pattern=r"^[a-z][a-z0-9-]{1,31}$"),
                        "name": _str(),
                        "path": _str("Por defecto sites/<id>."),
                        "description": _str(),
                    },
                },
            },
            "address_spaces": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "sites"],
                    "properties": {
                        "id": _str(pattern=NAME_PATTERN),
                        "description": _str(),
                        "sites": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                        "match": {
                            "type": "object",
                            "properties": {
                                "subnets": {"type": "array", "items": {"type": "string"}},
                                "vlans": {"type": "array", "items": {"$ref": "#/$defs/vlan"}},
                            },
                        },
                    },
                },
            },
        },
        "$defs": _defs(),
    }


def _source_ref() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["source", "file"],
        "properties": {
            "source": _str("id de una fuente de _ingest/state.yaml."),
            "file": _str("Ruta del archivo dentro de la fuente.", minLength=1),
            "locator": {"type": ["string", "integer"], "description": "Línea, fila, hoja!celda o sección."},
            "commit": {"type": ["string", "null"]},
            "fields": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "note": {"type": ["string", "integer", "null"]},
        },
    }


def _ingest_schemas() -> dict[str, dict[str, Any]]:
    version = {"const": ingest.FORMAT_VERSION}
    entity = _str("<tipo>:<nombre> o <tipo>:<sitio>/<nombre>.", pattern=ingest.ENTITY_PATTERN)
    state = {
        "$schema": DRAFT,
        "$id": "isot:schemas/ingest-state",
        "title": "_ingest/state.yaml: última revisión procesada de cada fuente",
        "type": "object",
        "required": ["version", "isot_version", "processed_at", "sources"],
        "properties": {
            "version": version,
            "isot_version": _str(minLength=1),
            "processed_at": _str("Fecha ISO (YYYY-MM-DD[THH:MM...]).", pattern=r"^\d{4}-\d{2}-\d{2}"),
            "sources": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["id", "repo"],
                    "anyOf": [
                        {"required": ["commit"], "properties": {"commit": {"type": "string"}}},
                        {"required": ["files"], "properties": {"files": {"minItems": 1}}},
                    ],
                    "properties": {
                        "id": _str(pattern=ingest.ID_PATTERN),
                        "repo": _str("URL o ruta relativa, sin credenciales.", minLength=1, pattern=r"^(?![a-zA-Z][a-zA-Z0-9+.-]*://[^/@]*@)"),
                        "ref": _str(),
                        "commit": {"anyOf": [_str(pattern=r"^[0-9a-f]{7,64}$"), {"type": "null"}]},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["path", "sha256"],
                                "properties": {"path": _str(minLength=1), "sha256": _str(pattern=ingest.SHA256_PATTERN)},
                            },
                        },
                    },
                },
            },
        },
    }
    gaps = {
        "$schema": DRAFT,
        "$id": "isot:schemas/ingest-gaps",
        "title": "_ingest/gaps.yaml: datos desconocidos y conflictos",
        "type": "object",
        "required": ["version", "gaps"],
        "properties": {
            "version": version,
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "entity", "kind", "status", "detail"],
                    "properties": {
                        "id": _str(pattern=ingest.ID_PATTERN),
                        "entity": entity,
                        "field": _str(minLength=1),
                        "kind": {"enum": list(ingest.GAP_KINDS)},
                        "status": {"enum": list(ingest.GAP_STATUSES)},
                        "detail": _str(minLength=1),
                        "sources": {"type": "array", "items": {"$ref": "#/$defs/source_ref"}},
                        "candidates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["value", "source"],
                                "properties": {"value": {}, "source": {"$ref": "#/$defs/source_ref"}},
                            },
                        },
                        "resolution": _str(minLength=1),
                    },
                    "allOf": [
                        {
                            "if": {"properties": {"kind": {"const": "conflict"}}},
                            "then": {"required": ["candidates"], "properties": {"candidates": {"minItems": 2}}},
                        },
                        {
                            "if": {"properties": {"status": {"const": "resolved"}}},
                            "then": {"required": ["resolution"]},
                        },
                    ],
                },
            },
        },
        "$defs": {"source_ref": _source_ref()},
    }
    provenance = {
        "$schema": DRAFT,
        "$id": "isot:schemas/ingest-provenance",
        "title": "_ingest/provenance.yaml: de dónde sale cada entidad",
        "type": "object",
        "required": ["version", "entities"],
        "properties": {
            "version": version,
            "entities": {
                "type": "object",
                "propertyNames": {"pattern": ingest.ENTITY_PATTERN},
                "additionalProperties": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/source_ref"}},
            },
        },
        "$defs": {"source_ref": _source_ref()},
    }
    return {
        "ingest-state.schema.json": state,
        "ingest-gaps.schema.json": gaps,
        "ingest-provenance.schema.json": provenance,
    }


def build_schemas() -> dict[str, dict[str, Any]]:
    out = {
        "sites.schema.json": _sites_schema(),
        "topology.schema.json": _topology_schema(),
        "device.schema.json": _device_schema(),
        "cluster.schema.json": _cluster_schema(),
        "cluster-template.schema.json": _template_schema(),
    }
    out.update(_ingest_schemas())
    from isot.render_schemas import render_schemas

    out.update(render_schemas())
    return out


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def schema_dir() -> Path:
    return tool_root() / SCHEMA_DIR


def write_schemas(directory: Path | None = None) -> list[Path]:
    directory = directory or schema_dir()
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in build_schemas().items():
        path = directory / name
        path.write_text(render(schema), encoding="utf-8")
        written.append(path)
    return written


def check_schemas(directory: Path | None = None) -> list[str]:
    directory = directory or schema_dir()
    problems = []
    expected = build_schemas()
    for name, schema in expected.items():
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8") != render(schema):
            problems.append(f"{SCHEMA_DIR}/{name} no está al día. Ejecuta: python -m isot.schemas --write")
    for path in sorted(directory.glob("*.schema.json")) if directory.is_dir() else []:
        if path.name not in expected:
            problems.append(f"{SCHEMA_DIR}/{path.name} sobra (no lo genera isot.schemas)")
    return problems


def repo_documents(repo: Path) -> Iterator[tuple[str, Path]]:
    """(schema, archivo) de cada YAML del repo de datos que ISoT carga."""
    if (repo / "sites.yaml").is_file():
        yield "sites.schema.json", repo / "sites.yaml"
    sites, _ = load_catalog(repo)
    for site in sites:
        if site.topology_path.is_file():
            yield "topology.schema.json", site.topology_path
        for path in iter_yaml_files(site.devices_dir):
            yield "device.schema.json", path
        for path in iter_yaml_files(site.clusters_dir):
            yield "cluster.schema.json", path
        for path in iter_template_files(site.templates_dir):
            yield "cluster-template.schema.json", path
    for path in iter_template_files(repo / "cluster-templates"):
        yield "cluster-template.schema.json", path
    site_templates = repo / "site-templates"
    if site_templates.is_dir():
        for child in sorted(site_templates.iterdir()):
            topo = child / "topology" / "index.yaml"
            if topo.is_file():
                yield "topology.schema.json", topo
    if (repo / "renders.yaml").is_file():
        yield "renders.schema.json", repo / "renders.yaml"
    manifests = repo / "manifest-templates"
    if manifests.is_dir():
        for child in sorted(manifests.iterdir()):
            if (child / "template.yaml").is_file():
                yield "render-template.schema.json", child / "template.yaml"
    for name in (ingest.STATE_FILE, ingest.GAPS_FILE, ingest.PROVENANCE_FILE):
        path = repo / ingest.INGEST_DIR / name
        if path.is_file():
            yield f"ingest-{name.removesuffix('.yaml')}.schema.json", path


class SchemasUnavailable(RuntimeError):
    pass


def _validator(schema: dict[str, Any]):
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise SchemasUnavailable("falta jsonschema: pip install 'isot[schemas]' (o pip install jsonschema)") from exc
    return jsonschema.Draft202012Validator(schema)


def validate_document(schema_name: str, data: Any) -> list[str]:
    validator = _validator(build_schemas()[schema_name])
    out = []
    for error in sorted(validator.iter_errors(ingest.jsonable(data)), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(part) for part in error.absolute_path) or "(raíz)"
        out.append(f"{where}: {error.message}")
    return out


def schema_issues(repo: Path) -> list[tuple[str, str]]:
    """[(ruta relativa, mensaje)] de los YAML que no cumplen su schema."""
    issues: list[tuple[str, str]] = []
    for name, path in repo_documents(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            data = load_yaml(path)
        except Exception as exc:  # YAML inválido: ya lo reporta el validador/cargador
            issues.append((rel, f"YAML inválido: {str(exc).splitlines()[0]}"))
            continue
        for message in validate_document(name, data):
            issues.append((rel, f"{rel} ({name}): {message}"))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m isot.schemas", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Regenera schemas/*.schema.json.")
    group.add_argument("--check", action="store_true", help="Falla si schemas/ no está al día.")
    group.add_argument("--repo", help="Valida un repositorio de datos contra los schemas.")
    args = parser.parse_args(argv)
    if args.write:
        for path in write_schemas():
            print(f"escrito {SCHEMA_DIR}/{path.name}")
        return 0
    if args.check:
        problems = check_schemas()
        for line in problems:
            print(line, file=sys.stderr)
        if not problems:
            print("schemas/ al día")
        return 1 if problems else 0
    try:
        issues = schema_issues(Path(args.repo))
    except SchemasUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for _, message in issues:
        print(f"[ERROR] {message}")
    if not issues:
        print("Schemas: OK")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
