# ISoT — Infrastructure Source of Truth

Inventario de infraestructura con enfoque **Git-as-a-Database**: el estado se
declara en YAML/JSON versionados, sin base de datos ni DCIM físico. El
repositorio modela **varios sitios lógicos** (datacenters) y un catálogo de
cluster-templates compartido. Los datos de ejemplo son ficticios y
anonimizados (SITE1 / SITE2, rangos RFC1918 y de documentación).

## Arquitectura

```
.
├── sites.yaml                          # Catálogo de sitios y address_spaces
├── sites/
│   ├── _sites_index.json               # Índice global de sitios
│   ├── site1/                          # nube privada de ejemplo
│   │   ├── topology/index.yaml
│   │   ├── clusters/ + _cluster_index.json
│   │   └── devices/  + _device_index.json
│   └── site2/                          # Sitio edge de ejemplo
│       └── cluster-templates/          # Templates solo de este sitio
├── cluster-templates/<nombre>/         # Templates globales (compartidos)
│   ├── template.yaml                   # distro, CNI, node pools, charts[]
│   ├── values/<release>.yaml           # Helm values (lazy-load)
│   └── _template_index.json            # (en cluster-templates/)
├── isot/                               # Validador, indexer, leakcheck, API
└── web/                                # SPA React (Vite + TypeScript)
```

Tres patrones:

1. **Índices ligeros.** La UI lee `_sites_index.json` y los índices por
   sitio. No parsea todos los YAML al arrancar.
2. **Carga perezosa.** El YAML de un dispositivo, clúster o template se abre
   al seleccionarlo. Los values de un Helm chart se leen al expandir el
   chart (`values_ref` → `values/<release>.yaml` o `values_inline`).
3. **Integridad en CI.** El validador rechaza IPs duplicadas **dentro de un
   sitio** y entre sitios que comparten un `address_space`, más
   `cluster_ref` / `template_ref` / `connected_to` rotos, `values_ref`
   inexistentes o YAML inválido, y `values_overrides` que apuntan a un
   release que el template no declara. `isot-leakcheck` cubre también los
   archivos de values.

Punteros lógicos (se resuelven **en el sitio actual** salvo que se indique otro):

- `cluster_ref: site1-apps` → `sites/site1/clusters/site1-apps.yaml`
- `template_ref: rke2-rancher-hybrid-calico` → `cluster-templates/<nombre>/template.yaml` (o el del sitio)
- `charts[].values_ref` → relativo a la carpeta del template (p.ej. `values/rke2-rancher-hybrid-calico.yaml`)
- `values_overrides[].release` → un `charts[].name` del template
- `connected_to: {device: SITE2-DCGW001, site: site2}` → hostname en ese sitio

Campos vacíos (`""`, `[]`, `null`) son **desconocidos**. No se rellenan.

La UI es **de solo lectura**. Git es la fuente de verdad.

### Stack

FastAPI + Vite/React/TypeScript + React Flow. El arranque pide
`/api/sites`, índices por sitio, `/api/templates` y `/api/graph`.

## Inventario de ejemplo

Dos sitios ficticios. SITE1 reutiliza una topología de nube privada (VLANs
2000–2011, RKE2, Calico, Multus, MetalLB, Ceph). SITE2 es un edge reducido
que **comparte** el template `rke2-rancher-hybrid-calico` y un enlace DCI
(`192.0.2.0/24`, VLAN 4094, address_space `dci-overlay`). Las VLANs 2002/2003
se repiten en ambos sitios con CIDR distintos: no colisionan.

MGMT001 de SITE1 es un switch de gestión genérico (`vendor: generic`,
`model: mgmt-48x1g`, `os: nos`); el resto de switches son `leaf-32x100g`
con NOS genérico. Los servidores usan `compute-2u` o `compute-12x25g`.

| Sitio | Clúster | Kind | Template | Nodos |
| --- | --- | --- | --- | --- |
| site1 | site1-platform | kubernetes | ninguno (RKE2 custom) | CTRL001-003 |
| site1 | site1-manager | kubernetes | ninguno (kubespray) | mgrvm001-003 |
| site1 | site1-apps | kubernetes | rke2-rancher-hybrid-calico | appvm001-003, NODE002, NODE007 |
| site1 | ceph-site1 | ceph | — | STOR001-002 |
| site2 | site2-edge | kubernetes | rke2-rancher-hybrid-calico | CTRL001-002 |

Templates globales no usados: `rke2-openstack-calico`, `rke2-openstack-cilium`,
`hosted-controlplane-baremetal` (`charts: []`, ningún release documentado). SITE2
tiene además `rke2-edge-calico` (local, sin uso; solo `metallb` enabled).

El template `rke2-rancher-hybrid-calico` declara los Helm charts que ya
estaban en `addons_default` (más el chart umbral del catálogo). Las claves
de `others_keys` aparecen como charts con versión/repo/values **faltantes**.
No se copian `apps_live` del clúster al template: eso es estado observado.

`site1-apps` sobrescribe el chart umbral (`values_overrides`, antes
`values_summary`). `site2-edge` solo sobrescribe los CIDRs documentados
(`10.44.0.0/16` / `10.45.0.0/16`).

Los nombres de host, VLAN y clúster son genéricos (`SITE1-CTRL*`,
`site1-platform`, `site1-apps`, `APP_CTRL` / `APP_DATA`). Quedan a
propósito la marca del proyecto (**ISoT / Infrastructure Source of Truth**),
el campo YAML `infra:` / `infra_range` (notas de plataforma, no un hostname)
e «infraestructura» en español.

La UI, en la vista Kubernetes: **Explorador** (grafo + árbol del template,
values al expandir un chart, búsqueda por ruta tipo `cniConfig.podCIDR`) y
**Diff values** (heredado / sobrescrito / efectivo, con colores). Desde un
clúster se salta a su template y viceversa.

El modo **Comparar** elige dos refs de git y pinta el grafo (verde agregado,
rojo quitado, amarillo modificado, gris sin cambios) más el diff de values
Helm. Un sitio, clúster, template o release **hereda** el estado de sus hijos:
queda amarillo con un badge `+N −N ~N` y el panel lista las entidades
cambiadas (con enlace). Clic o doble clic en un sitio modificado abre ese
sitio sin salir de Comparar. VLANs, devices, enlaces y releases quitados se
dibujan como fantasma (borde rojo punteado). La lista lateral ordena primero
lo cambiado y activa **solo cambios** cuando hay diferencias. Cada lado
permite: (1) rama o tag, (2) un commit del historial reciente de esa rama
(SHA corto, mensaje, autor, fecha, con búsqueda) y (3) pegar un SHA o una
expresión (`HEAD~3`, `main~1`). Comparar dos commits de la misma rama es
válido. El backend resuelve cada ref con `git rev-parse --verify` y
devuelve el SHA en `base.sha` / `head.sha`. Lee los árboles con `git archive`
(sin checkout).

Demo con cambios ficticios (`demo/diff-example` vs el tip de esta rama):

```bash
# Comparar → Base HEAD de esta rama → Head demo/diff-example
# VLAN 2099 LAB / LAB_NET, se quita site1-jump001, cniConfig.mtu 1440 → 1500
```

## Cómo usarlo

```bash
./scripts/serve.sh          # http://localhost:8000
# o: docker compose up --build
```

Desarrollo: `./scripts/dev.sh` (API :8000, Vite :5173).

```bash
python -m isot.validator
python -m isot.indexer          # sitios + devices + clusters + templates
python -m isot.indexer --check
python -m isot.leakcheck
pytest
cd web && npm ci && npm run build
```

API de solo lectura: `/api/sites`, `/api/sites/{id}/devices`,
`/api/sites/{id}/clusters`, `/api/sites/{id}/graph?view=network|kubernetes`,
`/api/graph?view=global`, `/api/templates`, detalle bajo demanda,
`/api/templates/{name}/values/{release}` (lazy),
`/api/sites/{id}/clusters/{name}/values-diff`,
`/api/refs`, `/api/commits?rev=<ref>`, y
`/api/diff?base=<ref>&head=<ref>[&site=<id>]`.

Convenciones para agentes: [LLM_INSTRUCTIONS.md](LLM_INSTRUCTIONS.md).
