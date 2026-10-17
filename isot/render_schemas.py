"""JSON Schema de los renders: contexto (context_version 1), template.yaml y renders.yaml.

Los genera `python -m isot.schemas --write` junto con los del inventario.
"""

from __future__ import annotations

from typing import Any

DRAFT = "https://json-schema.org/draft/2020-12/schema"
ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,62}$"


def _s(description: str, *, nullable: bool = True) -> dict[str, Any]:
    return {"type": ["string", "null"] if nullable else "string", "description": description}


def _arr(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description}


def _obj(props: dict[str, Any], description: str, *, required: list[str] | None = None, extra: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "description": description, "properties": props}
    if required:
        out["required"] = required
    if not extra:
        out["additionalProperties"] = False
    return out


def render_context_schema() -> dict[str, Any]:
    vlan = {"type": ["integer", "string", "null"], "description": "Id de VLAN (entero si se puede)."}
    subnet = _obj(
        {
            "name": _s("Nombre de la subred (topology/index.yaml subnets[].name)."),
            "cidr": _s("CIDR, p.ej. 10.0.0.0/24."),
            "prefix": {"type": ["integer", "null"], "description": "Longitud de prefijo del CIDR."},
            "netmask": _s("Máscara del CIDR."),
            "vlan": vlan,
            "gateway": _s("Gateway."),
            "vrf": _s("VRF."),
            "mtu": {"type": ["integer", "string", "null"]},
            "description": _s("Descripción."),
        },
        "Subred del sitio.",
    )
    interface = _obj(
        {
            "name": _s("Nombre lógico (networking[].interface)."),
            "parent": _s("Bond/NIC padre (VLAN sobre parent)."),
            "vlan": vlan,
            "ip": _s("IP con prefijo tal como está en el inventario."),
            "address": _s("IP sin prefijo."),
            "prefix": {"type": ["integer", "null"], "description": "Prefijo de la IP o, si falta, el de la subred de su VLAN."},
            "gateway": _s("Gateway declarado en la interfaz."),
            "network": _s("Red lógica (VMs)."),
            "subnet": _s("Nombre de la subred de su VLAN."),
            "subnet_cidr": _s("CIDR de la subred de su VLAN."),
            "nad": _s("NAD (namespace/nombre) para VMs KubeVirt."),
            "mtu": {"type": ["integer", "string", "null"]},
            "mac": _s("MAC."),
            "connected_to": {"type": ["object", "null"], "properties": {"device": _s("Peer."), "port": _s("Puerto del peer.")}},
        },
        "Interfaz (devices[].networking[] normalizada).",
    )
    bond = _obj(
        {
            "name": _s("Nombre del bond."),
            "mode": _s("Modo (si está documentado)."),
            "mtu": {"type": ["integer", "string", "null"]},
            "members": _arr({"type": "string"}, "NICs miembro (members/interfaces)."),
            "allowed_vlans": _arr({"type": "integer"}, "VLANs permitidas (rangos expandidos)."),
            "extra_host_vlans": _arr({"type": "integer"}, "VLANs extra del host."),
            "leaf_pc": {"type": "object", "additionalProperties": {"type": ["string", "null"]}, "description": "leaf → PortChannel."},
            "note": _s("Nota."),
        },
        "Bond del dispositivo.",
    )
    device = _obj(
        {
            "hostname": _s("Hostname tal cual.", nullable=False),
            "name": _s("Hostname en minúsculas (útil para nombres de archivo).", nullable=False),
            "site": _s("Sitio.", nullable=False),
            "role": _s("Rol."),
            "kind": _s("baremetal | vm | network | …"),
            "k8s_roles": _arr({"type": "string"}, "Roles Kubernetes."),
            "cluster": _s("cluster_ref."),
            "os": _s("SO."),
            "vendor": _s("Fabricante."),
            "model": _s("Modelo."),
            "labels": {"type": "object", "description": "labels del YAML."},
            "bonds": _arr(bond, "Bonds."),
            "interfaces": _arr(interface, "Interfaces."),
            "oob": {"type": ["object", "null"], "properties": {"type": _s("bmc…"), "ip": _s("IP OOB.")}},
            "primary_ip": _s("Primera IP declarada."),
            "hypervisor": {"type": ["object", "null"], "description": "VMs: cluster/namespace/device del hypervisor."},
            "resources": {"type": ["object", "null"], "description": "VMs: vcpu, memory, disk…"},
            "path": _s("Ruta del YAML en el repo de datos."),
            "raw": {"type": "object", "description": "YAML original con secretos reemplazados por <redacted>."},
        },
        "Dispositivo (YAML bajo sites/<id>/devices/).",
        required=["hostname", "name", "site", "kind", "bonds", "interfaces"],
    )
    nad = _obj(
        {
            "namespace": _s("Namespace."),
            "name": _s("Nombre."),
            "type": _s("macvlan | macvtap | bridge | vlan | …"),
            "master": _s("Interfaz master."),
            "bridge": _s("Bridge (type bridge)."),
            "vlan": vlan,
            "mtu": {"type": ["integer", "string", "null"]},
            "ipam": _s("IPAM (p.ej. whereabouts)."),
            "subnet": _s("Subred referida."),
            "cidr": _s("CIDR de esa subred."),
            "gateway": _s("Gateway de esa subred."),
        },
        "NetworkAttachmentDefinition documentado en el clúster.",
    )
    pool = _obj(
        {
            "name": _s("Nombre del pool."),
            "addresses": _arr({"type": "string"}, "Rangos o CIDRs."),
            "auto_assign": {"type": ["boolean", "null"]},
            "subnet": _s("Subred."),
            "l2_interfaces": _arr({"type": "string"}, "Interfaces L2."),
        },
        "Pool MetalLB.",
    )
    cluster = _obj(
        {
            "name": _s("Nombre.", nullable=False),
            "site": _s("Sitio.", nullable=False),
            "kind": _s("kubernetes | ceph | …"),
            "distro": _s("Distribución."),
            "version": _s("Versión."),
            "template_ref": _s("cluster-template."),
            "pod_cidr": _s("CIDR de pods."),
            "service_cidr": _s("CIDR de servicios."),
            "cni": {"description": "Bloque cni tal cual (string u objeto)."},
            "nads": _arr(nad, "NADs."),
            "metallb": _obj(
                {
                    "version": _s("Versión."),
                    "mode": _s("L2 | BGP."),
                    "pools": _arr(pool, "Pools."),
                    "services": {"type": "object", "additionalProperties": {"type": "string"}, "description": "servicio → IP."},
                },
                "MetalLB.",
            ),
            "vips": _arr({"type": "string"}, "VIPs (metallb.vips + IPs de metallb.services)."),
            "nodes": _arr(
                _obj(
                    {
                        "hostname": _s("Hostname."),
                        "k8s_name": _s("Nombre del nodo en Kubernetes."),
                        "roles": _arr({"type": "string"}, "Roles."),
                        "internal_ip": _s("InternalIP."),
                    },
                    "Nodo.",
                ),
                "Nodos (cluster.nodes).",
            ),
            "members": _arr({"type": "string"}, "Hostnames de los devices del clúster."),
            "namespaces": _arr({"type": "string"}, "Namespaces conocidos."),
            "helm_values": {"type": "object", "description": "Values efectivos (template + values_overrides), sin secretos."},
            "vms": _arr({"$ref": "#/$defs/device"}, "VMs KubeVirt alojadas en este clúster."),
            "path": _s("Ruta del YAML."),
            "raw": {"type": "object", "description": "YAML original (sin secretos)."},
        },
        "Clúster (sites/<id>/clusters/*.yaml).",
        required=["name", "site", "nads", "metallb", "nodes", "members"],
    )
    site = _obj(
        {
            "id": _s("Id.", nullable=False),
            "name": _s("Nombre.", nullable=False),
            "description": _s("Descripción."),
            "operator": _s("Operador."),
            "domain": _s("Dominio."),
            "infra_range": _s("Rango de infraestructura."),
            "vlan_range": _s("Rango de VLANs."),
            "dns_servers": _arr({"type": "string"}, "DNS."),
            "dns_search": _arr({"type": "string"}, "Dominios de búsqueda."),
            "ntp_servers": _arr({"type": "string"}, "NTP."),
            "http_proxy": _s("Proxy HTTP."),
            "https_proxy": _s("Proxy HTTPS (o el HTTP)."),
            "no_proxy": _s("NO_PROXY."),
            "subnets": _arr({"$ref": "#/$defs/subnet"}, "Subredes."),
            "vlans": _arr(
                _obj({"id": vlan, "name": _s("Nombre."), "subnet": _s("Subred.")}, "VLAN."), "VLANs."
            ),
        },
        "Sitio (sites.yaml + topology/index.yaml).",
        required=["id", "name", "subnets", "vlans"],
    )
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/render-context",
        "title": "Contexto de render ISoT (context_version 1)",
        "description": (
            "Variables que ve un template de render. Además: funciones subnet(name), subnet_by_vlan(vlan), "
            "device(hostname) y, en outputs per_item, `item` e `index`; en single con for_each, `items`. "
            "Ver docs/llm/RENDERS.md."
        ),
        "type": "object",
        "required": ["context_version", "site", "subnets", "vlans", "devices", "site_devices", "clusters", "cluster", "params"],
        "properties": {
            "context_version": {"const": 1},
            "site": {"$ref": "#/$defs/site"},
            "subnets": _arr({"$ref": "#/$defs/subnet"}, "= site.subnets"),
            "vlans": {"type": "array", "description": "= site.vlans"},
            "devices": _arr({"$ref": "#/$defs/device"}, "Devices del ámbito: los del clúster de la instancia o, sin clúster, todos los del sitio."),
            "site_devices": _arr({"$ref": "#/$defs/device"}, "Todos los devices del sitio."),
            "clusters": _arr({"$ref": "#/$defs/cluster"}, "Clústeres del sitio."),
            "cluster": {"oneOf": [{"$ref": "#/$defs/cluster"}, {"type": "null"}], "description": "Clúster de la instancia (scope cluster) o null."},
            "vms": _arr({"$ref": "#/$defs/device"}, "VMs (del clúster si hay, si no del sitio)."),
            "params": {"type": "object", "description": "template.params con los params de la instancia encima."},
            "instance": {"type": "object", "properties": {"id": _s("Id."), "site": _s("Sitio."), "cluster": _s("Clúster.")}},
            "template": {"type": "object", "properties": {"id": _s("Id."), "version": _s("Versión."), "scope": _s("Scope.")}},
        },
        "$defs": {"site": site, "subnet": subnet, "device": device, "cluster": cluster},
    }


def render_template_schema() -> dict[str, Any]:
    output = _obj(
        {
            "template": {"type": "string", "description": "Archivo .j2 de la carpeta del template."},
            "mode": {"enum": ["single", "per_item"], "description": "single: un archivo. per_item: un archivo por elemento de for_each."},
            "path": {"type": "string", "description": "Ruta de salida relativa (Jinja; en per_item usa item.*)."},
            "for_each": {"type": "string", "description": "Expresión Jinja que devuelve la lista (sobrescribe la del template)."},
            "where": {"type": "string", "description": "Expresión Jinja por item; false lo descarta."},
            "header": {"type": "boolean", "description": "Cabecera '# GENERATED BY ISoT — DO NOT EDIT' (por defecto en .yaml/.conf/.txt…)."},
            "description": {"type": "string"},
        },
        "Una salida del template.",
        required=["template", "path"],
        extra=False,
    )
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/render-template",
        "title": "Template de render ISoT (manifest-templates/<id>/template.yaml)",
        "type": "object",
        "additionalProperties": False,
        "required": ["outputs"],
        "properties": {
            "id": {"type": "string", "pattern": ID_PATTERN, "description": "Igual al nombre de la carpeta."},
            "version": {"type": ["integer", "string"], "description": "Versión del template (queda en el sidecar)."},
            "description": {"type": "string"},
            "scope": {"enum": ["site", "cluster", "device"], "description": "cluster exige instance.cluster; device ≈ per_item sobre devices."},
            "for_each": {"type": "string", "description": "Expresión Jinja por defecto para per_item (y `items` en single)."},
            "where": {"type": "string", "description": "Filtro por item por defecto."},
            "params": {"type": "object", "description": "Parámetros con su valor por defecto."},
            "tags": {"type": "array", "items": {"type": "string"}},
            "outputs": {"type": "array", "items": output, "minItems": 1},
            "output": output,
        },
    }


def renders_config_schema() -> dict[str, Any]:
    instance = _obj(
        {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "template": {"type": "string", "pattern": ID_PATTERN, "description": "Id del template de render."},
            "site": {"type": "string", "description": "Sitio (en sites/<id>/renders.yaml es implícito)."},
            "cluster": {"type": ["string", "null"]},
            "target": {"type": ["string", "null"], "description": "Id de targets[] (por defecto default_target)."},
            "dir": {"type": ["string", "null"], "description": "Carpeta relativa al dir del destino (por defecto <site>/<cluster|site>/<template>)."},
            "params": {"type": "object"},
            "enabled": {"type": "boolean"},
            "description": {"type": "string"},
        },
        "Instancia de render.",
        required=["template"],
        extra=False,
    )
    target = _obj(
        {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "name": {"type": "string"},
            "repo": {"type": ["string", "null"], "description": "Id de repos[] o target_repos[] de isot.yaml; vacío = este repo de datos."},
            "dir": {"type": "string", "description": "Carpeta dentro de ese repo."},
            "description": {"type": "string"},
        },
        "Destino de renders.",
        required=["id"],
        extra=False,
    )
    return {
        "$schema": DRAFT,
        "$id": "isot:schemas/renders",
        "title": "renders.yaml del repositorio de datos",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"const": 1},
            "target_dir": {"type": "string", "description": "Carpeta destino por defecto en este repo (rendered)."},
            "default_target": {"type": "string"},
            "targets": {"type": "array", "items": target},
            "instances": {"type": "array", "items": instance},
        },
    }


def render_schemas() -> dict[str, dict[str, Any]]:
    return {
        "render-context.schema.json": render_context_schema(),
        "render-template.schema.json": render_template_schema(),
        "renders.schema.json": renders_config_schema(),
    }
