# Instrucciones para agentes de IA (ISoT)

ISoT es una herramienta: el inventario vive en un **repositorio de datos**
aparte (configurado en `isot.yaml` → `data_repo.path` o la lista `repos`, o
`--repo DIR`; ver `isot config`). Con varios repositorios, cada uno es una
fuente de verdad independiente: no mezcles sitios ni referencias entre ellos. Ese repositorio es la **fuente de verdad** de varios sitios
lógicos; en este repo el inventario de ejemplo está en `examples/demo-repo/`.
Todas las rutas de abajo son relativas a la raíz del repositorio de datos. El
estado vive en Git como YAML/JSON. Si un objeto no está en estos archivos,
**no existe**. Los campos vacíos y `topology/index.yaml` → `gaps` /
`undocumented_peers` son huecos reales: no los rellenes.

Los datos de ejemplo están **anonimizados**. No reintroduzcas nombres de
clientes, operadores, FQDN reales ni planes IP originales. El hook
`isot-leakcheck` (hashes, no texto en claro) debe seguir en verde.

## 1. Navegar siempre desde los índices

No parsees todos los YAML de golpe.

1. `sites.yaml` — catálogo de sitios y `address_spaces` compartidos.
2. `sites/_sites_index.json` — conteos y enlaces entre sitios.
3. `sites/<id>/devices/_device_index.json` — hosts de ese sitio.
4. `sites/<id>/clusters/_cluster_index.json`
5. `cluster-templates/_template_index.json` — catálogo global (`used_by` incluye `sitio/clúster`).
6. `sites/<id>/cluster-templates/_template_index.json` — templates solo de ese sitio.

Los JSON índice son **generados**. No los edites a mano.

## 2. Carga perezosa

Abre el YAML de detalle **solo bajo demanda**:

- Host: `path` del índice de dispositivos.
- Clúster: `sites/<id>/clusters/<nombre>.yaml`.
- Template global: `cluster-templates/<nombre>/template.yaml` (legacy: `<nombre>.yaml` en el primer nivel). Si el repo de datos no lo tiene, se resuelve en las carpetas `templates:` de `isot.yaml` y luego en `templates/cluster-templates/` de ISoT (ruta virtual `@isot/<nombre>/template.yaml`).
- Template de sitio: `sites/<id>/cluster-templates/<nombre>/template.yaml`.
- Values de un chart: **no** están en el YAML del template si usa `values_ref`. Abre `cluster-templates/<nombre>/values/<archivo>.yaml` (o el `values_inline` del chart) **solo al expandir ese release**.
- No indexar ni tratar `values/*.yaml` como templates.

## 3. Cómo resolver referencias

Por defecto el sitio del documento actual. Para cruzar DCs hay que ser explícito.

| Campo | Resolución |
| --- | --- |
| `cluster_ref: nombre` | `sites/<sitio-actual>/clusters/<nombre>.yaml` |
| `cluster_ref: otro/nombre` | `sites/otro/clusters/nombre.yaml` |
| `template_ref: nombre` | primero `sites/<sitio>/cluster-templates/<nombre>/template.yaml`, si no el catálogo global |
| `charts[].values_ref` | ruta relativa a la carpeta del template; si no existe o no es YAML, el validador falla |
| `values_overrides[].release` | debe ser un `charts[].name` del template resuelto |
| `connected_to.device` + `site` | hostname en `sites/<site>/devices/` |
| `connected_to.device` sin `site` | hostname en el sitio actual |
| NAD `vlan` / `subnet` | topology **de ese sitio** |
| MetalLB `subnet` y addresses | CIDR de la topology **de ese sitio** |

Las IPs y VLANs **pueden repetirse entre sitios** (espacios separados). El
validador solo cruza sitios si un `address_space` los lista (p. ej.
`dci-overlay` con la subred `DCI`).

Los nodos de un clúster kubernetes (`nodes[].hostname`) deben existir en el
**mismo sitio** y tener el mismo `cluster_ref`. Los hosts de un clúster Ceph
viven en `hosts[]`; no inventes fichas para equipos que no están ahí.

Hostnames de Kubernetes a veces van en minúsculas (`site1-ctrl001`); el
hostname canónico del dispositivo es el del YAML (`SITE1-CTRL001`). El
campo `k8s_name` guarda el nombre k8s.

## 4. No alucinar inventario

- No inventes hosts, IPs, VLANs, templates ni workloads.
- Hardware sin marca: `vendor: generic`, `model: leaf-32x100g` /
  `mgmt-48x1g` / `compute-2u` / `compute-12x25g`. Switches: `os: nos`.
  OOB: `type: bmc` (no nombres de BMC de fabricante).
- No conviertas el modelo en DCIM físico (racks, U, cables).
- `undocumented_peers` no tienen YAML: menciónalos como ausentes.

## 5. Tras editar YAML

1. `python -m isot.validator --repo <repo de datos>` (sin `--repo` usa `isot.yaml`; `--repo-id ID` o `--all`)
2. `python -m isot.indexer --repo <repo de datos>`
3. `python -m isot.leakcheck` (si tocaste el repo de la herramienta o `examples/`)
4. No hagas commit si alguno falla o si un `_*_index.json` quedó desfasado.
5. Altas por UI: wizard (rama `wizard/…`), no commits directos en main.

## 6. `[AI-Context]`

Comentarios `# [AI-Context]: ...` son contexto semántico. No son campos parseables.

## 7. Kubernetes y sitios

Un clúster k8s referencia opcionalmente un template (`template_ref`). El
template define distro, CNI, node_pools, add-ons y `charts[]` (nombre, repo,
chart, versión, namespace, orden, `depends_on`, values). El clúster aporta
CIDRs, NADs, MetalLB, StorageClasses, nodos y `values_overrides` por release.

Cómo leer charts y values **sin alucinar**:

1. La lista de Helm releases es `charts[]`. Si un chart no está ahí, **no lo
   instala el template**. `apps_live` del clúster es estado observado, no
   catálogo del template: no copies `rancher-logging` / `etcd-defrag` /
   `rke2-traefik` al template porque aparecen en vivo.
2. `version`, `repo` o `namespace` vacíos son **faltantes**. No inventes
   registry, chart name distinto ni versión a partir de `apps_live`.
3. Values del template: `values_ref` (archivo) o `values_inline`. Si ambos
   faltan, los values son desconocidos. No rellenes un árbol Helm “típico”.
4. Values del clúster: solo las claves en `values_overrides` de ese release.
   El resto se **hereda** del template. Deep-merge de mapas; listas y
   escalares se sustituyen. No asumas que `pod_cidr` del clúster es un
   override Helm si no está en `values_overrides`.
5. `others_keys` del template se modelan como charts sin versión ni values.
   No los traduzcas a nombres de chart de Rancher (`logging` ≠
   `rancher-logging`) salvo que el inventario lo declare.

`site1-platform` y `site1-manager` no usan template (`template_ref: null`).
`site1-apps` y `site2-edge` usan el template **global**
`rke2-rancher-hybrid-calico`.

La UI tiene selector de sitio (Todos / SITE1 / SITE2). Vista **Sitios**
(global: DCs y DCI), **Red** (dispositivos de un sitio) y **Kubernetes**
(templates y clústeres; pestañas Grafo / Explorador / Diff values). El modo
**Comparar** pide `/api/refs`, `/api/commits?rev=` y `/api/diff` (árboles
de git, sin checkout). Base y head pueden ser rama, tag, SHA o `HEAD~N`.
El estado de un hijo se propaga al sitio/clúster/template/release padre
(`inherited`, `child_counts`, `children`). El default es **solo lectura**.

## 8. Wizards (solo con `ISOT_EDIT=1`)

No edites YAML a mano si el usuario pide un alta por UI. Endpoints
`GET /api/wizard/cabling-defaults`, `POST /api/wizard/site/preview|commit` y
`/api/wizard/device/preview|commit`.
**Editar** es un toggle: si ya estás en el wizard, vuelve a Sitios /
Kubernetes / Comparar con el mismo sitio. El avance (wizard, paso, campos,
reglas, preview) vive en el estado de la app y en `sessionStorage`
(`isot.wizard.session`); sobrevive recarga y cambio de pestaña. Badge
**borrador** en el botón; **Descartar borrador** con confirmación. Tras un
commit exitoso el borrador se limpia.
El preview no escribe: devuelve YAML, campos auto y el **grafo propuesto**
(mismo diff semántico que Comparar, overlay en memoria). El commit:

1. crea `wizard/<slug>` desde HEAD
2. escribe YAML + índices
3. corre el validador; si falla, `git reset --hard` y borra la rama
4. hace commit y vuelve a la rama original

Arquitectura y cableado del sitio nuevo (`isot/cabling.py`): el wizard pregunta
Spine-Leaf / collapsed-core / custom y las cantidades (spines, pares de leafs,
MGMT, DCGW, nodos por rol). Las **reglas de conexión** viven en
`sites/<id>/topology/index.yaml` → `connection_rules` (dual-home a 2 leafs del
mismo par con bond LACP, leaf→todos los spines, bondS de storage, OOB al MGMT,
fill-first o round-robin). El generador crea devices, `connected_to`,
PortChannels y bonds a partir de esas reglas. **Agregar device** las respeta
(siguiente par con puertos libres). Sitios existentes sin el bloque: infiere
de los hermanos y ofrece guardarlas. El validador emite `[WARN]` (no error) si
un device las viola.

Autoasignación (`isot/allocator.py`): hostname del **mismo rol y tipo**
(número más bajo libre de ese prefijo). Si no hay hermanos en el clúster,
busca el rol en otros clústeres del sitio; si no existe en el sitio, el
patrón sale de otro sitio o del convenio (apps-worker/worker→NODE,
edge-server/platform-server→CTRL, storage→STOR, leaf→LEAF, spine→SPNE,
mgmt→MGMT, dcgw→DCGW). Nunca reutilices el prefijo de otro rol. Specs
(interfaces, bonds) salen de connection_rules o del device más parecido
del clúster **sin** heredar hostname ni rol; la UI dice
`sin hermanos del mismo rol: patrón nuevo`. Si el usuario escribe un
hostname cuyo prefijo es de otro rol, warning. Siguiente IP libre en la
subred del hermano (sin gateway ni VIPs MetalLB), siguiente PortChannel
libre en el par de leafs. No inventes rangos. El usuario confirma campos
`auto` (origen en tooltip). Tras el commit, abre Comparar base vs
`wizard/…`.

## 9. Reglas de control

No silencies hallazgos sin `reason`. Catálogo: [docs/rules.md](docs/rules.md).
EOL de Kubernetes: `isot/rules/k8s_eol.yaml`. Endpoints `GET /api/findings`
y `GET /api/rules`. El validador (`python -m isot.validator`) agrupa
hallazgos y solo sale ≠ 0 si hay **errores**; `--strict` también por
warnings; `--format json`. En CI, los warnings salen como annotations.

Overrides: `rule_overrides: [{id, severity, reason}]` en clúster, device o
`topology/index.yaml`. Sin reason se ignora (`RULE-OVR-001`).

La vista previa del wizard evalúa el estado propuesto. Errores **nuevos**
bloquean Confirmar; warnings nuevos exigen checkbox `accept_warnings`.
Comparar distingue hallazgos añadidos y resueltos entre refs.
