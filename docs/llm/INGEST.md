# Guía de ingesta para LLM: de fuentes desordenadas a un repositorio de datos ISoT

Esta guía sirve para **cualquier modelo o agente** (y para personas). Explica qué
espera ISoT, cómo convertir planillas, CSV, notas, configs e inventarios
sueltos en el repositorio YAML que la herramienta valida y dibuja, y cómo
mantenerlo al día cuando las fuentes cambian.

- Desarrollo de la herramienta y edición puntual de un repo de datos ya
  existente: [`LLM_INSTRUCTIONS.md`](../../LLM_INSTRUCTIONS.md).
- Catálogo de reglas y hallazgos: [`docs/rules.md`](../rules.md).
- Schemas: [`schemas/`](../../schemas) (generados desde el código; ver §1.13).
- Ejemplo completo: [`examples/messy-source/`](../../examples/messy-source).

ISoT no guarda inventario: el resultado de la ingesta es un repositorio de
datos propio (uno por inventario), con sus permisos y su CI. Si `isot.yaml`
lista varios repositorios, ingiere en uno a la vez; cada uno lleva su propio
`_ingest/`.

Los nombres de campo van tal como los lee el código (`cluster_ref`,
`leaf_pc`, ...). Todo lo que se cita aquí sale de `isot/inventory.py`,
`isot/validator.py`, `isot/cabling.py`, `isot/links.py`, `isot/rules/` y
`isot/ingest.py`.

---

## 0. Resultado esperado

Una rama (`ingest/<fecha>`) con un PR/MR en el **repositorio de datos** que:

1. Contiene solo datos presentes en las fuentes, normalizados.
2. Pasa `isot validate --repo <repo> --schemas` (exit 0) e `isot index --check`.
3. Trae `_ingest/state.yaml`, `_ingest/provenance.yaml` y `_ingest/gaps.yaml`.
4. No tiene secretos.
5. Explica en la descripción del PR lo que cambió, los huecos y los conflictos
   que esperan a una persona.

---

## 1. Qué espera ISoT

### 1.1 Estructura del repositorio de datos

```
sites.yaml                                   # catálogo de sitios y address_spaces
sites/<sitio>/topology/index.yaml            # subredes, VLANs, clústeres, huecos, reglas de cableado
sites/<sitio>/devices/<tipo>/<HOSTNAME>.yaml # un archivo por dispositivo
sites/<sitio>/clusters/<cluster_name>.yaml   # un archivo por clúster
sites/<sitio>/cluster-templates/<nombre>/template.yaml   # templates solo de ese sitio (opcional)
cluster-templates/<nombre>/template.yaml     # templates globales (opcional)
cluster-templates/<nombre>/values/<archivo>.yaml         # values Helm citados por values_ref
site-templates/<id>/topology/index.yaml      # plantillas de sitio para el wizard (opcional)
_ingest/state.yaml | provenance.yaml | gaps.yaml          # metadatos de ingesta (§3)
**/_*_index.json                             # GENERADOS por `isot index`; no se editan
```

- `<tipo>` es la carpeta del dispositivo: `baremetal`, `network`,
  `virtual-machines` (u otra). El grafo la usa para agrupar («Agrupar por
  tipo») y la etiqueta en la UI.
- Archivos y carpetas que empiezan por `_` no se cargan como inventario.
  Por eso `_ingest/` no aparece en el grafo; lo leen el validador y
  `isot ingest`.
- Si `sites.yaml` no lista sitios, cada carpeta de `sites/` es un sitio.
  Lístalos siempre.
- Un template se resuelve en este orden: sitio, global del repo y, al final,
  la biblioteca de ISoT (`templates/cluster-templates/`). No copies un
  template de la biblioteca si ya sirve tal cual: basta con `template_ref`.
- `isot init --scaffold DIR` crea un repo vacío con esta estructura y CI.

### 1.2 Identificadores y nombres

| Qué | Regla | Dónde se usa |
| --- | --- | --- |
| Id de sitio | `^[a-z][a-z0-9-]{1,31}$` (minúsculas) | `sites.yaml`, carpeta `sites/<id>`, prefijo de ids del grafo |
| Hostname | `^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$`, **igual al nombre del archivo** | clave del device en el sitio; único sin distinguir mayúsculas (INV-HOST-001) |
| `cluster_name` | igual al nombre del archivo (`clusters/<cluster_name>.yaml`) | `cluster_ref` busca `clusters/<ref>.yaml` |
| `template_name` | igual a la carpeta (`cluster-templates/<template_name>/`) | `template_ref` |
| Subred | `subnets[].name` único en el sitio (p.ej. `HOST_MGMT`) | `vlans[].subnet`, NADs, pools MetalLB |
| VLAN | entero 1-4094 | `vlan`, `allowed_vlans`, `vlans[].id` |
| Referencia | `nombre` (mismo sitio) o `sitio/nombre` | `cluster_ref`, `connected_to.device`, claves de `leaf_pc`, `oob.switch`, `hypervisor.device` |

Ids que ve la UI (útiles para leer hallazgos): device `"<sitio>:<HOSTNAME>"`,
clúster `"cluster:<sitio>:<nombre>"`, template `"template:<sitio|global>:<nombre>"`,
sitio `"site:<id>"`.

Las IPs y VLANs pueden repetirse **entre** sitios. Solo se cruzan si un
`address_space` de `sites.yaml` agrupa esos sitios (§1.3).

### 1.3 `sites.yaml`

```yaml
sites:
  - id: lab1                 # obligatorio
    name: lab1               # opcional; etiqueta en la UI (por defecto el id)
    path: sites/lab1         # opcional; por defecto sites/<id>
    description: Laboratorio # opcional; vista Sitios
address_spaces:              # opcional: espacios de direcciones compartidos entre sitios
  - id: dci-overlay
    sites: [lab1, lab2]      # 2 o más sitios
    match: {subnets: [DCI], vlans: [4094]}   # subredes (por name) y/o VLANs compartidas
```

Con un `address_space`, una IP repetida en dos sitios dentro de esas
subredes/VLANs es un error (`INV-IP-001`).

### 1.4 `sites/<sitio>/topology/index.yaml`

```yaml
site:
  id: lab1
  operator: ...            # opcional
  description: ...
  infra_range: 10.60.0.0/16   # opcional: rango del sitio (lo usa el wizard)
  vlan_range: "100-299"       # opcional
  dns_servers: [10.60.0.53]   # opcional
  ntp_servers: [10.60.0.123]  # opcional
subnets:
  - name: HOST_MGMT        # obligatorio
    cidr: 10.60.1.0/24     # vacío solo si es desconocido (y hueco en gaps)
    vlan: 100
    gateway: 10.60.1.1
    vrf: ...               # opcional
    vrrp_ip: ...           # opcional: el validador lo reserva para pools/VIPs
    description: ...
vlans:
  - {id: 100, name: HOST_MGMT, subnet: HOST_MGMT}   # subnet = subnets[].name
clusters:
  - {name: lab1-k8s, kind: kubernetes, path: sites/lab1/clusters/lab1-k8s.yaml}
undocumented_peers: []     # equipos citados en las fuentes sin datos para tener ficha
gaps: ["texto libre"]      # resumen humano de huecos (se ve en la ficha del sitio)
connection_rules: {...}    # opcional, §1.10
rule_overrides: [...]      # opcional, §1.10
```

### 1.5 Dispositivos: `sites/<sitio>/devices/<tipo>/<HOSTNAME>.yaml`

Obligatorios: `hostname`, `role`. El resto es opcional y solo va si la fuente
lo da.

```yaml
hostname: LAB1-NODE01
role: control-plane          # ver vocabulario de roles abajo
k8s_roles: [control-plane]   # roles Kubernetes del nodo (si aplica)
kind: baremetal              # informativo (el grafo usa la carpeta)
vendor: ...                  # valores de la fuente
model: ...
os: ...
cluster_ref: lab1-k8s        # o "otro-sitio/nombre" o {name: x, site: y}
pair: LAB1-LEAF02            # solo switches de un par MLAG/ESI
leaf_pair: PAIR1             # id del par (agrupa «por par»: PAIRn, spines, mgmt, border, unpaired)
oob:                         # gestión fuera de banda
  type: bmc
  ip: 10.60.0.31/24
  switch: LAB1-MGMT01        # arista hacia el switch OOB
  port: GigabitEthernet1
  vlan: 10                   # ponla siempre: si falta, el índice asume 2002
  undocumented_peer: true    # en vez de switch/port si el switch OOB no está documentado
bonds:
  - name: bond0
    mode: lacp
    members: [eno1, eno2]
    leaf_pc: {LAB1-LEAF01: PortChannel11, LAB1-LEAF02: PortChannel11}   # una arista por switch
    allowed_vlans: [100, 200]
    mtu: 9000
networking:
  - interface: HOST_MGMT     # o name:
    parent: bond0
    vlan: 100
    ip: 10.60.1.11/24        # siempre con prefijo
    gateway: 10.60.1.1
    mac: "aa:bb:cc:dd:ee:ff"
    mtu: 1500
    connected_to: {device: LAB1-LEAF01, port: PortChannel11}   # opcional; peer debe existir
hypervisor: {device: LAB1-NODE02}   # solo VMs: host físico (arista «hypervisor»)
bgp: {asn: 65001, router_id: 10.60.255.1}
vlans: [10, 100, 200]        # switches: VLANs configuradas
rule_overrides: [...]
```

Vocabulario de roles que entienden las reglas de cableado (`isot/cabling.py`):

- cómputo: `worker`, `apps-worker`, `k8s-worker`, `tenant`, `compute`, `edge-server`;
- leaf: `server-leaf`, `leaf`, `tor`, `pair-leaf`;
- spine: `spine`.

Además, `mgmt` y `dcgw` se reconocen por substring en el rol o el hostname.
Usa el rol de la fuente. Si coincide con uno de estos, escríbelo igual.

Cuándo cada cosa es un error:

- `connected_to.device` que no existe en el sitio → **INV-REF-001** (error).
  Las claves de `leaf_pc`, `oob.switch` y `hypervisor.device` que no existen
  se dibujan como nodo «remoto», sin error. Si el peer no está documentado,
  no inventes su ficha: omite `connected_to` y anótalo en
  `undocumented_peers` o en `gaps`.
- Dos devices en el mismo puerto del mismo peer → **INV-PORT-001**.
- Misma IP (interfaces u `oob.ip`) dos veces en un sitio → **INV-IP-001**.
- `cluster_ref` a un clúster sin archivo → **INV-REF-001**.

### 1.6 Clústeres: `sites/<sitio>/clusters/<cluster_name>.yaml`

Obligatorios: `cluster_name`, `kind`.

```yaml
cluster_name: lab1-k8s
kind: kubernetes           # kubernetes | ceph | ... (solo kubernetes va a la vista Kubernetes y a K8S-*)
template_ref: rke2-...     # opcional; sin él, K8S-TPL-001 (info)
distro: rke2
version: v1.30.4+rke2r1    # versión de Kubernetes (K8S-VER-001/002)
pod_cidr: 10.244.0.0/16    # desconocidos → K8S-NET-001 info
service_cidr: 10.96.0.0/12
nodes:                     # kubernetes: cada hostname debe existir en el sitio con cluster_ref a este clúster
  - {hostname: LAB1-NODE01, roles: [control-plane], internal_ip: 10.60.1.11}
hosts_missing: [LAB1-NODE09]   # nodos conocidos por nombre que aún no tienen ficha (no fallan)
hosts: [...]               # ceph u otros: miembros
nads: [{namespace: x, name: y, vlan: 100, subnet: HOST_MGMT}]   # vlan/subnet deben estar en la topología
metallb:
  pools: [{name: p1, subnet: SERVICES, addresses: ["10.60.3.10-10.60.3.20"]}]   # dentro de la subred
  vips: {api: 10.60.3.5}
values_overrides:          # por release del template
  - {release: <charts[].name>, values_inline: {...}}   # o values_ref
rule_overrides: [...]
```

### 1.7 Templates y values

`cluster-templates/<nombre>/template.yaml` lleva `template_name` (obligatorio),
`version`, `distro`, `kubernetes_version`, `cni: {primary, defaults}` y
`charts[]` con `name` (único), `repo`, `chart`, `version`, `namespace`,
`order`, `depends_on` y `values_ref` (relativo a la carpeta del template) o
`values_inline`. Un `values_ref` inexistente o un override a un release que no
existe es **INV-HELM-001**. Solo crea templates si la fuente describe el
catálogo (Helm releases, versiones). El estado observado de un clúster no es
el template.

### 1.8 Plantillas de sitio

`site-templates/<id>/topology/index.yaml` usa el mismo formato que la
topología y solo la usa el wizard «Nuevo sitio». Una ingesta normalmente no la
necesita.

### 1.9 Cómo se expresan enlaces, bonds, VLANs y subredes

| Hecho en la fuente | YAML |
| --- | --- |
| «host X, NIC eth0, al switch S puerto Ethernet5» | en X: `networking: [{interface: eth0, connected_to: {device: S, port: Ethernet5}}]` |
| «X usa un LAG/Port-Channel 11 hacia el par S1/S2» | en X: `bonds: [{name: bond0, leaf_pc: {S1: PortChannel11, S2: PortChannel11}}]` |
| «en el bond pasan las VLAN 100 y 200» | `allowed_vlans: [100, 200]` en el bond |
| «X tiene 10.0.1.5 en la VLAN 100 sobre bond0» | `networking: [{interface: <nombre o subred>, parent: bond0, vlan: 100, ip: 10.0.1.5/24}]` |
| «VLAN 100 = 10.0.1.0/24 gw .1» | `subnets: [{name: N, cidr: 10.0.1.0/24, vlan: 100, gateway: 10.0.1.1}]` y `vlans: [{id: 100, name: N, subnet: N}]` |
| «BMC de X en 10.0.0.5, switch M puerto 3» | `oob: {ip: 10.0.0.5/24, switch: M, port: <puerto normalizado>, vlan: <VLAN OOB>}` |
| «VM V corre en el host H» | en V: `hypervisor: {device: H}` |
| «S1 y S2 forman par MLAG» | `pair:` en cada uno y el mismo `leaf_pair: PAIRn` en ambos (y en los hosts que cuelgan del par) |
| enlace entre sitios (DCI) | `connected_to: {device: OTRO, site: otro-sitio, port: ...}` y, si comparten direcciones, un `address_space` |

Un enlace se declara **en un solo extremo** (normalmente el host o el leaf,
hacia arriba). El grafo une ambos sentidos en una sola arista. Si un switch
y un host describen el mismo cable, basta con declararlo en el host. Si los
datos de los dos lados no coinciden, eso es un conflicto (§3.2).

### 1.10 `connection_rules` y `rule_overrides`

`connection_rules` (en la topología) es la **política de cableado** del sitio.
Activa los avisos INV-CAB-001 y la usa el wizard para generar devices nuevos:

```yaml
connection_rules:
  architecture: spine-leaf        # spine-leaf | collapsed-core | custom
  pair_policy: fill-first         # fill-first | round-robin
  ports_per_leaf: 48
  fabric: {spines: 2, leaf_pairs: 1, unpaired_leafs: 0, mgmt_switches: 1, dcgw: 0}
  rules:
    - {id: compute-mlag, roles: [worker], attach: leaf-pair, leafs_per_node: 2, bond: lacp, bond_name: bond0}
    - {id: leaf-to-spine, roles: [leaf], attach: all-spines}
    - {id: oob-bmc, roles: [worker, leaf, spine], attach: mgmt, via: oob}
```

`attach` puede ser `leaf-pair`, `all-spines` o `mgmt`. **Escríbelo solo si
una fuente documenta la política**. Sin el bloque, ISoT no emite avisos de
cableado y el wizard la infiere de los hermanos.

`rule_overrides` (en el clúster, el device o la topología) cambia la severidad
de una regla en ese alcance:

```yaml
rule_overrides:
  - id: K8S-CP-001
    severity: off            # error | warning | info | off
    reason: "Laboratorio de un solo nodo de control, aceptado por <equipo> en <ticket/acta>"
```

Sin `reason`, el override se ignora y aparece RULE-OVR-001. Catálogo de reglas:
[docs/rules.md](../rules.md).

### 1.11 Cómo usa la UI cada campo

| Campo | Dónde se ve |
| --- | --- |
| `sites.yaml` `sites[]` (`name`, `description`) | vista **Sitios**, selector de sitio, barra lateral |
| `connected_to` entre sitios | aristas de la vista Sitios (DCI) |
| `hostname`, `role`, primera `networking[].ip` | nodo del grafo **Red** (título, rol, IP) |
| carpeta `<tipo>` | «Agrupar por tipo» (Baremetal, Red, Máquinas virtuales, ...) |
| `cluster_ref` | «Agrupar por clúster»; aristas clúster→device en la vista **Kubernetes** |
| `leaf_pair` | «Agrupar por par» (PAIRn, spines, mgmt, border, unpaired) |
| `networking[].connected_to`, `bonds[].leaf_pc`, `oob.switch`, `hypervisor.device` | **aristas** del grafo Red; la VLAN de la arista colorea y agrupa «por VLAN» |
| bond (`name`, `mode`, `members`, `leaf_pc`, `allowed_vlans`, `extra_host_vlans`, `mtu`), interfaz (`interface`, `parent`, `vlan`, `ip`, `mac`, `mtu`, `network`), OOB (`type`, `ip`, `switch`, `port`, `vlan`) | panel **Conexión** al hacer clic en una arista. Las VLANs se resuelven contra `topology.vlans`/`subnets` (nombre, CIDR, gateway). Las claves desconocidas salen en «otros atributos». |
| `kind`, `template_ref`, `distro`, `version`, `nodes`, `values_overrides` | vista **Kubernetes** (grafo, Explorador, Diff values) |
| `template_name`, `charts[]`, values | Explorador de templates |
| hallazgos (`isot validate`) | campana **Hallazgos**; resalta el nodo (`graph_id`); remediación sugerida |
| `topology.gaps`, `undocumented_peers` | ficha del sitio |
| `_ingest/gaps.yaml` | hallazgos ING-GAP-001 en la campana, sobre la entidad citada |
| cualquier cambio entre dos refs git | modo **Comparar** (diff semántico de devices, clústeres, templates y values) |

### 1.12 Índices generados

`isot index --repo <repo>` regenera `sites/_sites_index.json`,
`sites/<sitio>/devices/_device_index.json`, `sites/<sitio>/clusters/_cluster_index.json`
y `cluster-templates/_template_index.json`. La UI lee los índices. Si no están
al día, el grafo no muestra los cambios (`isot index --check` falla en CI). No
los edites a mano.

### 1.13 Schemas

`schemas/*.schema.json` (JSON Schema 2020-12) describen cada archivo:
`sites`, `topology`, `device`, `cluster`, `cluster-template` e
`ingest-state` / `ingest-gaps` / `ingest-provenance`.

- Se **generan desde el código** (`python -m isot.schemas --write`); un test
  comprueba que están al día y que las enumeraciones (severidades, `attach`,
  `pair_policy`, estados de hueco) son las del validador.
- Son permisivos: tipan los campos que ISoT lee y admiten claves extra (la UI
  las muestra como «otros atributos»).
- `null` o `""` en un campo opcional se acepta: significa desconocido o
  inexistente (§3.1).
- Se aplican con `isot validate --repo <repo> --schemas` (hallazgo
  INV-SCH-001) o con `isot schemas --repo <repo>`. Requieren `jsonschema`
  (`pip install "isot[schemas]"`).

---

## 2. Reglas duras

1. **No inventes datos.** Cada valor sale de una fuente concreta. No completes
   con valores «típicos»: puertos, gateways, máscaras, versiones, ASN, modelos,
   VLANs, roles, hostnames de peers.
   - Se permite derivar sin ambigüedad: el prefijo de la subred que contiene
     una IP, `.1` → primera IP de la red cuando la fuente lo escribe así, o un
     id de agrupación (`leaf_pair`) para un par que la fuente declara. Anota
     cada derivación en la tabla de mapeo.
2. **Marca lo desconocido** con la convención de §3.1. No lo rellenes ni
   «adivines» por patrones.
3. **Guarda la procedencia** de cada entidad en `_ingest/provenance.yaml` (§3.3).
4. **No copies secretos:** passwords, hashes de password, claves privadas,
   community strings SNMP, tokens, PSK, certificados con clave, credenciales
   en URLs. Si un campo los necesita, deja una referencia (`${VAR}`,
   `<secreto>`, `vault:ruta`). El validador falla con ING-SEC-001 si detecta
   uno.
5. **Si las fuentes se contradicen, pregunta a una persona** y registra el
   conflicto (§3.2). Mientras tanto no elijas un valor.
6. **No silencies hallazgos sin razón real**: un `rule_overrides` necesita un
   `reason` que venga de una fuente o de una persona, no del agente.
7. **Cambios mínimos**: no reformatees, reordenes ni reescribas archivos que
   no cambian por la ingesta. No toques los `_*_index.json` a mano.
8. **No trabajes en `main`**: rama `ingest/<fecha>` y PR/MR revisado por una
   persona.
9. Usa los valores reales de las fuentes (fabricante, modelo, nombres) en el
   repositorio de datos de tu organización. Los ejemplos que viven en el repo
   de ISoT son genéricos y ficticios.

---

## 3. Convenciones de ingesta (`_ingest/`)

Los tres archivos son YAML con `version: 1`. El validador comprueba su formato
(ING-FMT-001, error), que las entidades y fuentes citadas existan
(ING-REF-001, warning) y muestra los huecos abiertos (ING-GAP-001). Otros
archivos YAML en `_ingest/` generan un aviso. Un `README.md` con el mapa de
fuentes (paso 1) es bienvenido.

**Clave de entidad** (para provenance y gaps):

| Entidad | Clave |
| --- | --- |
| sitio | `site:<sitio>` |
| address space | `address_space:<id>` |
| template global / de sitio | `template:<nombre>` / `template:<sitio>/<nombre>` |
| device | `device:<sitio>/<HOSTNAME>` |
| clúster | `cluster:<sitio>/<cluster_name>` |
| subred | `subnet:<sitio>/<name>` |
| VLAN | `vlan:<sitio>/<id>` |

Los enlaces no tienen clave propia. Su procedencia va en el device que los
declara, con `fields: [bonds]` o `fields: [networking]`.

**Referencia de fuente** (se usa en provenance y gaps):

```yaml
{source: <id de state.sources>, file: <ruta en la fuente>, locator: "línea 12", commit: <sha opcional>, fields: [campo, ...]}
```

`locator`: `línea N` / `líneas N-M` para texto y CSV (el encabezado es la
línea 1), `Hoja!B5` o `Hoja fila 5` para planillas y `sección X` para documentos
sin líneas estables. Pon `commit` cuando la fuente es git y la entidad sale de
una revisión distinta de la de `state.yaml`.

### 3.1 Desconocidos (`_ingest/gaps.yaml`, `kind: unknown`)

- **Desconocido**: omite la clave (preferido) o déjala vacía (`""` / `null`)
  si la estructura la necesita, por ejemplo una subred sin CIDR conocido.
  **Siempre** con una entrada en `gaps.yaml`.
- **Inexistente según la fuente** («sin gateway», «L2 puro»): valor vacío
  explícito y una nota en `description`, sin hueco.
- Registra huecos para los campos que la fuente menciona sin valor (`?`,
  celda vacía, «no sé», «pendiente») y para los que ISoT necesita para
  validar o dibujar (IPs, enlaces, `cluster_ref`, CIDRs del clúster). No hace
  falta un hueco por cada campo opcional ausente.
- Resume los huecos en una línea de `topology.gaps` (texto, con su id) para
  que se vean en la ficha del sitio.

```yaml
version: 1
gaps:
  - id: GAP-003                      # único, estable entre ingestas
    entity: device:lab1/LAB1-NODE04
    field: oob                       # ruta del campo (oob.ip, networking[HOST_MGMT].ip, ...)
    kind: unknown                    # unknown | conflict | ambiguous
    status: open                     # open | needs-human | resolved
    detail: "BMC IP «?» en el CSV."
    sources: [{source: messy, file: hosts.csv, locator: "línea 9"}]
```

`status`:

- `open`: falta el dato. Se muestra como info.
- `needs-human`: hace falta una decisión. Se muestra como warning.
- `resolved`: requiere `resolution` explicando quién o qué fuente lo resolvió.
  Deja de mostrarse.

### 3.2 Conflictos (`kind: conflict`)

Dos fuentes dan valores distintos para el mismo campo, o los dos extremos de
un cable no coinciden:

1. No escribas ninguno de los valores en el YAML (omite el campo).
2. Registra el conflicto con **todos** los candidatos y su fuente. Hacen falta
   al menos 2. Usa `status: needs-human`.
3. Pregunta a una persona: en el chat si estás en una sesión, si no en la
   descripción del PR, citando el id.
4. Cuando responda, escribe el valor, pasa el hueco a `resolved` con
   `resolution: "<persona/fuente>, <fecha>: <decisión>"` y añade la fuente
   ganadora a la procedencia.

```yaml
  - id: GAP-002
    entity: device:lab1/LAB1-NODE03
    field: networking[HOST_MGMT].ip
    kind: conflict
    status: needs-human
    detail: "IP de gestión distinta en CSV y notas."
    candidates:
      - {value: 10.60.1.13/24, source: {source: messy, file: hosts.csv, locator: "línea 8"}}
      - {value: 10.60.1.23/24, source: {source: messy, file: notes.md, locator: "línea 19"}}
```

`kind: ambiguous` es para un dato que la fuente menciona de forma dudosa
(«¿borrar?», «creo que»). Tampoco se escribe hasta confirmarlo.

### 3.3 Procedencia (`_ingest/provenance.yaml`)

Va en un archivo aparte, así los YAML de inventario quedan limpios y el diff
de Comparar solo muestra datos.

```yaml
version: 1
entities:
  device:lab1/LAB1-NODE01:
    - {source: messy, file: hosts.csv, locator: "línea 6", fields: [hostname, role, oob, networking]}
    - {source: messy, file: switch-config.txt, locator: "líneas 10-15, 28-30", fields: [bonds]}
```

- Cada entidad creada o modificada tiene al menos una referencia.
- `fields` es opcional. Úsalo cuando una entidad mezcla fuentes.
- Al borrar una entidad, borra su entrada. Al cambiarla, actualiza su
  `locator` y `fields`.

### 3.4 Estado (`_ingest/state.yaml`)

```yaml
version: 1
isot_version: 0.10.0          # `pip show isot` / importlib.metadata
processed_at: "2026-10-17"    # fecha ISO de la ingesta
sources:
  - id: inventario            # corto; lo citan provenance y gaps
    repo: https://git.example.net/equipo/inventario-crudo.git   # URL o ruta relativa, SIN credenciales
    ref: main
    commit: 3f2c1e9...        # fuente en git: el commit procesado
  - id: planillas
    repo: compartido/planillas    # fuente sin git
    commit: null
    files:                    # sha256 por archivo (`isot ingest hash --source DIR`)
      - {path: hosts.xlsx, sha256: ...}
```

Cada fuente necesita `commit` o `files[]`. Con `files`, `isot ingest status`
compara hashes; con `commit`, hace `git diff --name-status <commit> HEAD` en la
fuente.

### 3.5 Secretos

ING-SEC-001 (error) revisa `sites.yaml`, `sites/`, `cluster-templates/`,
`site-templates/` y `_ingest/`. Busca claves `password`, `passwd`, `secret`,
`secret_key`, `enable_secret`, `community`, `snmp_community`, `token`,
`api_token`, `auth_token`, `access_key`, `private_key`, `ssh_private_key`,
`psk` y `pre_shared_key` con valor, y bloques PEM de clave privada. Acepta
`${VAR}`, `<...>`, `***`, `REDACTED`, `vault:...`, `ref+...` y
`secretRef:...`. El chequeo es una red de seguridad, no un permiso: no
copies secretos aunque la clave tenga otro nombre.

---

## 4. Procedimiento paso a paso

### Paso 1. Inventariar las fuentes y escribir el mapa

Lista cada archivo con: tipo (CSV, planilla, Markdown, config de switch,
inventario Ansible, diagrama exportado, ...), qué entidades aporta, fecha o
commit y confianza (config o inventario > apuntes). Anota qué archivos tienen
secretos (no se copian) y cuáles descartas y por qué. Guárdalo en
`_ingest/README.md`. Registra cada fuente en `state.yaml` (`id`, `repo`,
`commit` o `files`).

### Paso 2. Identificar sitios, clústeres, dispositivos y redes

- **Sitios**: por prefijo de hostname, columna «sitio/DC», carpeta o grupo
  de inventario. Un sitio = un plan de direcciones y VLANs propio.
- **Dispositivos**: una ficha por hostname único. Las variantes de mayúsculas
  y espacios son el mismo equipo (normaliza); dos equipos distintos con el
  mismo nombre son un conflicto.
- **Clústeres**: Kubernetes (control plane / workers), Ceph, etc. Solo si
  una fuente lo nombra.
- **Redes**: VLANs con su subred, gateway y VRF. Rangos de servicios (pools,
  VIPs).
- Equipos citados sin datos suficientes (solo un nombre en una descripción de
  puerto): `undocumented_peers` o hueco, sin ficha.

### Paso 3. Mapear campos (tabla de mapeo)

Por cada fuente y columna o patrón: campo de origen → campo ISoT →
transformación. Ejemplo (de `examples/messy-source`):

| Fuente | Origen | Campo ISoT | Transformación |
| --- | --- | --- | --- |
| hosts.csv | `Hostname` | `hostname` + nombre de archivo | trim, MAYÚSCULAS |
| hosts.csv | `Tipo` | carpeta `devices/<tipo>/` | switch → `network`, servidor → `baremetal` |
| hosts.csv | `BMC IP`, `Switch BMC`, `Puerto BMC` | `oob.ip`, `oob.switch`, `oob.port` | `?` → hueco |
| switch-config.txt | `interface Port-Channel11` + `description <host> bond0` | `bonds[].leaf_pc` del host | |
| notes.md | «VLAN 100 → 10.60.1.0/24, gw .1» | `subnets[]`, `vlans[]` | `.1` → `10.60.1.1` |

Inventarios Ansible: `host_vars`/`group_vars` → campos del device (sin
variables de vault). Grupos → `role`/`cluster_ref` solo si el nombre del grupo
lo dice explícitamente.

### Paso 4. Normalizar

| Tipo | Regla |
| --- | --- |
| Hostname | trim, sin dominio (`host.dc.example.net` → `HOST`), una sola forma de mayúsculas para todo el repo (la más usada en las fuentes). Conserva la numeración de la fuente. Guarda el nombre k8s en `nodes[].k8s_name` si difiere. |
| IP | `ip` siempre con prefijo, el de la subred que la contiene (`10.0.1.5` → `10.0.1.5/24`). Sin subred conocida, la IP va sin prefijo y con un hueco. |
| CIDR | dirección de red real (`10.0.1.7/24` → `10.0.1.0/24` en `subnets[].cidr`) |
| VLAN | entero (`Vlan100`, `vlan 100`, `"100"` → `100`). Listas de rangos expandidas (`100-102` → `[100, 101, 102]`). |
| Interfaz | expandir abreviaturas: `Eth`/`Et` → `Ethernet`, `Po`/`Port-Channel` → `PortChannel`, `Gi` → `GigabitEthernet`, `Te` → `TenGigabitEthernet`, `Mgmt` → `Management`. Sin nombre en la fuente: nombre lógico = `name` de la subred (`HOST_MGMT`). |
| MAC | `aa:bb:cc:dd:ee:ff` en minúsculas |
| Booleanos y vacíos | `?`, `-`, `N/A`, `TBD`, celda vacía → desconocido (§3.1) |

Escribe las reglas que uses en `_ingest/README.md` para que la próxima
ingesta sea idéntica.

### Paso 5. Generar el YAML

1. `sites.yaml`, luego la topología de cada sitio, luego los devices (switches
   primero: los peers deben existir antes que los `connected_to`), luego los
   clústeres.
2. Claves en el orden de los ejemplos (§1.5, §1.6) y listas en el orden de la
   fuente. Así dos ingestas iguales producen el mismo texto.
3. `_ingest/provenance.yaml`, `gaps.yaml` y `state.yaml`.
4. `isot index --repo <repo>` — **obligatorio tras añadir o cambiar
   cualquier archivo** (también `_ingest/`, que entra en `_artifacts_index.json`
   y en la huella de `_index_manifest.json`). La ingesta no pasa por la UI, así
   que nadie más lo hará por ti; sin esto la regla `IDX-001` avisa, la UI
   muestra «Índice desactualizado» y `isot index --check` falla en CI.

### Paso 6. Validar e iterar

```bash
isot validate --repo <repo> --schemas --format json > /tmp/hallazgos.json   # exit 0 = sin errores
isot index --check --repo <repo>
isot ingest check --repo <repo>        # solo _ingest/, rápido
```

En el JSON, `counts` resume y cada `findings[]` trae `id`, `severity`,
`entity_id`, `path`, `message` y `recommendation`. Corrige todos los
**errores**:

- un error de referencia suele ser un hostname mal normalizado o un peer sin
  ficha;
- un error de schema suele ser una VLAN en texto o una IP sin formato.

Repite hasta exit 0. No borres datos para «hacer pasar» el validador: si un
dato de la fuente provoca un error real (IP duplicada), es un conflicto (§3.2).

### Paso 7. Revisar los hallazgos

Lee cada warning e info. Pueden ser:

- un problema real del inventario: queda visible y se menciona en el PR;
- un error de ingesta: corrígelo;
- una desviación aceptada **documentada** en la fuente o confirmada por una
  persona: `rule_overrides` con `reason` que cite esa fuente o persona.

Nunca uses un override para tapar un dato que falta.

### Paso 8. Commit, rama y PR/MR

```bash
git switch -c ingest/2026-10-17
git add -A && git commit -m "ingesta: <fuentes> @ <commit/fecha>"
git push -u origin ingest/2026-10-17
```

Descripción del PR:

- fuentes y revisiones procesadas;
- resumen de altas, cambios y bajas;
- huecos `needs-human` y conflictos (ids);
- hallazgos que quedan;
- cómo revisarlo: `isot web` → **Comparar**, base `main`, head `ingest/<fecha>`.

No hagas merge tú.

---

## 5. Seguimiento de cambios (reingesta)

1. Parte de `main` actualizado del repo de datos y lee `_ingest/state.yaml`.
2. Calcula qué cambió en cada fuente:
   `isot ingest status --repo <repo> --source <carpeta> [--source-id ID] [--format json]`.
   Exit 0: sin cambios, termina sin rama. Exit 1: lista `added`, `modified`,
   `removed`. En fuentes git, el método es `git diff` desde el `commit`
   registrado. Si no, se comparan sha256.
3. Crea `ingest/<fecha>` y procesa **solo** los archivos cambiados:
   - con `provenance.yaml`, busca qué entidades salen de cada archivo
     cambiado (`file`) y revísalas;
   - un archivo eliminado: las entidades que solo venían de él se borran solo
     si la fuente confirma la baja; si no, hueco `needs-human`.
4. Aplica cambios **mínimos**: edita las líneas afectadas y deja intacto el
   resto del archivo (comentarios, orden, formato). No regeneres archivos
   completos ni toques archivos sin cambios.
5. Actualiza `provenance.yaml`, resuelve o abre huecos y actualiza
   `state.yaml` (`commit`/`files`, `processed_at`, `isot_version`).
6. `isot index`, paso 6 y paso 7.
7. Revisa con **Comparar** (refs `main` ↔ `ingest/<fecha>`): solo deben
   aparecer las entidades que cambiaron en la fuente. Si aparecen otras, has
   reescrito de más.
8. PR/MR como en el paso 8.

**Chequeo de idempotencia.** Ejecuta la ingesta dos veces seguidas sin cambios
en las fuentes. La segunda debe dar `isot ingest status` con exit 0 y, si
igualmente regeneras, `git status --porcelain` vacío (ni siquiera cambia
`processed_at`: solo se actualiza cuando hay cambios). Si hay diff, tu
normalización u orden no es determinista: corrígelo antes de abrir el PR.

---

## 6. Ejemplo trabajado: `examples/messy-source/`

- `source/hosts.csv`: planilla con 8 equipos, nombres con mayúsculas y
  espacios mezclados, IPs sin prefijo y `?` en datos faltantes.
- `source/notes.md`: apuntes con VLANs, el clúster y una IP que contradice al
  CSV.
- `source/switch-config.txt`: pseudo-config de dos leafs (uplinks,
  Port-Channels por host, VLANs permitidas) con un usuario/secret y una
  community SNMP que **no** se copian.
- `expected-repo/`: el resultado. 1 sitio (`lab1`), 3 subredes/VLANs, 4
  switches, 4 servidores, el clúster `lab1-k8s`, índices y `_ingest/` con 7
  huecos:
  - VLAN 300 ambigua;
  - IP de NODE03 en conflicto;
  - BMC, clúster y cableado de NODE04;
  - `service_cidr` y template del clúster.

  `_ingest/README.md` trae el mapa de fuentes, la tabla de mapeo y las reglas
  de normalización.

```bash
isot validate --repo examples/messy-source/expected-repo --schemas
isot index --check --repo examples/messy-source/expected-repo
isot ingest status --repo examples/messy-source/expected-repo --source examples/messy-source/source
```

El validador da exit 0 con avisos esperados que no se silencian:

- K8S-CP-001: un solo control plane, y ninguna fuente lo justifica;
- ING-GAP-001 de GAP-001 y GAP-002, que esperan a una persona.

`tests/test_ingest.py` lo comprueba en CI.

---

## 7. Prompt listo para pegar

```text
Eres un agente de ingesta para ISoT. Tu trabajo es construir (o actualizar) un
repositorio de datos ISoT a partir de fuentes desordenadas, sin inventar nada.

Contexto:
- Fuentes (solo lectura): <RUTA_O_URL_FUENTES> (ref <RAMA/COMMIT>).
- Repositorio de datos destino: <RUTA_REPO_DATOS> (rama base: main).
- Guía obligatoria: docs/llm/INGEST.md del repo de ISoT (léela entera antes de
  empezar; los schemas están en schemas/*.schema.json).
- Comandos: isot validate --repo <repo> --schemas --format json,
  isot index --repo <repo>, isot index --check --repo <repo>,
  isot ingest status --repo <repo> --source <fuente>, isot ingest hash --source <fuente>.

Reglas duras:
1. No inventes datos: cada valor sale de una fuente; solo derivaciones sin
   ambigüedad, anotadas en la tabla de mapeo.
2. Lo desconocido se omite (o queda vacío) y se registra en _ingest/gaps.yaml
   (kind unknown, status open).
3. Conflictos entre fuentes: no elijas; registra kind conflict con todos los
   candidatos, status needs-human, y pregúntame citando el id.
4. Procedencia de cada entidad en _ingest/provenance.yaml (source, file,
   locator, fields).
5. Nunca copies secretos (passwords, hashes, claves, communities, tokens, PSK,
   credenciales en URLs).
6. rule_overrides solo con una reason que venga de una fuente o de mí.
7. Cambios mínimos; no reescribas archivos que no cambian; no edites
   _*_index.json a mano; no trabajes en main.

Procedimiento:
1. Si existe _ingest/state.yaml, ejecuta isot ingest status; si no hay
   cambios, detente e infórmalo. Si no existe, es una ingesta inicial.
2. Escribe el mapa de fuentes en _ingest/README.md.
3. Identifica sitios, clústeres, dispositivos y redes.
4. Escribe la tabla de mapeo y las reglas de normalización.
5. Genera o actualiza el YAML, _ingest/* e índices.
6. Valida e itera hasta exit 0; revisa warnings e info.
7. Comprueba la idempotencia (segunda pasada sin diff).
8. Crea la rama ingest/<AAAA-MM-DD>, haz commit y abre un PR/MR sin hacer
   merge.

Entrega final: URL del PR, resumen de cambios, huecos needs-human y conflictos
(con id y candidatos), hallazgos que quedan y salida resumida del validador.
```

---

## 8. Checklist

- [ ] Mapa de fuentes en `_ingest/README.md`; cada fuente en `state.yaml` con `commit` o `files[]`.
- [ ] Tabla de mapeo y reglas de normalización escritas.
- [ ] Sitios en `sites.yaml`; topología con `subnets` y `vlans` coherentes (`vlans[].subnet` existe).
- [ ] Un archivo por device, `hostname` = nombre de archivo, en la carpeta de su tipo.
- [ ] IPs con prefijo, VLANs enteras, interfaces normalizadas.
- [ ] `oob.vlan` explícita; enlaces declarados en un extremo; peers existentes o anotados.
- [ ] Clústeres: archivo = `cluster_name`; `nodes[]` existen con el mismo `cluster_ref`.
- [ ] Nada inventado; desconocidos y conflictos en `gaps.yaml` (conflictos con ≥2 candidatos y `needs-human`).
- [ ] Procedencia para cada entidad creada o modificada.
- [ ] Sin secretos (ING-SEC-001 limpio).
- [ ] `isot validate --repo <repo> --schemas` exit 0; `isot index --check` OK.
- [ ] Warnings e info revisados; overrides solo con `reason` real.
- [ ] Segunda pasada sin cambios = sin diff.
- [ ] Comparar `main` ↔ `ingest/<fecha>` muestra solo lo esperado.
- [ ] Rama `ingest/<fecha>`, PR/MR con resumen, huecos y conflictos. Sin merge.

---

## 9. Referencia rápida

| Comando | Para qué |
| --- | --- |
| `isot init --scaffold DIR` | repo de datos vacío con estructura y CI |
| `isot index --repo R` / `--check` | regenerar o comprobar índices |
| `isot validate --repo R [--schemas] [--format json] [--strict]` | validador; exit 0 OK, 1 errores (o warnings con `--strict`), 2 configuración |
| `isot schemas --repo R` | solo schemas |
| `isot ingest check --repo R` | solo el formato de `_ingest/` |
| `isot ingest status --repo R --source S [--source-id ID] [--format json]` | qué cambió en la fuente; exit 1 si hay cambios |
| `isot ingest hash --source S` | bloque `files[]` con sha256 para `state.yaml` |
| `isot web --repo R` | UI; **Comparar** para revisar la rama |
