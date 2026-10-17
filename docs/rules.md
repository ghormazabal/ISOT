# Reglas de control (ISoT)

El motor vive en `isot/rules/`. Cada regla tiene un **id estable**, severidad
por defecto, entidad y recomendación. `python -m isot.validator` las evalúa
sobre el repositorio de datos configurado (`isot.yaml` o `--repo DIR`); sale distinto de 0 **solo si hay errores**. `--strict`
también falla por warnings. `--format json` imprime el payload.

Overrides en YAML de clúster, device o `sites/<id>/topology/index.yaml`:

```yaml
rule_overrides:
  - id: K8S-CP-001
    severity: info   # error | warning | info | off
    reason: "lab de una sola VM"
```

Sin `reason` el override **se ignora** y aparece `RULE-OVR-001`.

En GITHUB_ACTIONS el validador emite annotations (`::warning` / `::notice`).

## Hallazgos por sitio

Cada hallazgo pertenece a un sitio (`site`) o es global (`site: null`).

- **Templates.** Un hallazgo sobre un template (p. ej. `K8S-VER-003`,
  `INV-HELM-001`) se replica en cada clúster que usa ese template: la copia
  lleva `site` y `cluster` del clúster, `graph_id` del nodo del clúster y
  `source_template` con el origen (`global/<nombre>` o `<sitio>/<nombre>`).
  Un template que ningún clúster usa queda global (o en su sitio, si es un
  template de sitio). Así un template usado en dos sitios cuenta en ambos.
- **Overrides.** Un `rule_overrides` en el clúster también aplica a los
  hallazgos de template atribuidos a ese clúster (silenciar en un sitio no
  silencia el otro).

API:

- `GET /api/findings?site=<id>[&ref=<ref>]`: solo hallazgos con
  `site == <id>` (sin globales). Ids de sitio en minúsculas.
- `GET /api/findings` (vista global): todos, más `by_site`
  (`{site: {error, warning, info, total}}`, incluye sitios en cero) y
  `global` (no atribuibles a un sitio). `counts.total` =
  Σ `by_site[*].total` + `global.total`.
- `GET /api/findings?base=&head=[&site=]`: `base` y `head` con el mismo
  formato, `added` / `resolved` acotados al sitio, `added_counts`,
  `resolved_counts`, `added_by_site` y `resolved_by_site`.

Wizards: la vista previa y el commit comparan el inventario antes y después
y solo **bloquean por errores nuevos del sitio del wizard** o nuevos sin
sitio atribuible. Errores previos o de otros sitios no bloquean. Los
warnings nuevos del sitio piden confirmar con «Acepto N aviso(s) nuevo(s)»,
junto a Confirmar.

UI: dentro de un sitio la campana y el panel muestran solo ese sitio; en la
vista global el panel agrupa por sitio (más «Global / plantillas») y cada
nodo de sitio del mapa lleva sus contadores.

## Remediación

Cada hallazgo trae `remediation` (en `/api/findings` y en `--format json`),
una acción legible por máquina:

```yaml
# Se corrige agregando componentes → wizard «Agregar device»
remediation:
  kind: add_device
  site: site2
  cluster: site2-edge        # null para equipos de red
  role: edge-server          # rol existente del clúster
  device_kind: baremetal     # baremetal | vm | network
  count: 1                   # cuántos faltan
  reason: "site2-edge: 2 → 3 control planes (quórum etcd impar)"
  label: "Agregar 1 control plane"
  followup: "..."            # opcional: paso manual posterior

# No se corrige agregando algo → navegar a la entidad
remediation:
  kind: open_entity
  target: template           # cluster | template | values | device | site
  site: global
  name: rke2-rancher-hybrid-calico
  label: "Abrir template rke2-rancher-hybrid-calico"
```

| Regla | Acción |
| --- | --- |
| K8S-CP-001 | `add_device`: control planes con el rol de los CP del clúster hasta 3 (o +1 si es par ≤ 6). Más de 7 → `open_entity`. |
| K8S-WK-001 | `add_device`: workers hasta el mínimo (2, o 3 con Ceph/Longhorn). Rol de los workers puros, si no de los CP+worker; sin workers, rol `worker` (patrón por convenio del allocator) con el tipo de device de los CP. |
| K8S-ST-001 | `add_device`: nodos (rol de los workers) hasta igualar las réplicas. |
| K8S-CP-002 (mismo par de leafs) | `add_device` de un leaf (`device_kind: network`, rol de los leafs actuales) + `followup`: reconectar un control plane o hacer dual-home. El leaf nace sin servidores, con IP de gestión y loopback nuevas. |
| K8S-CP-002 (mismo hypervisor), K8S-VER-*, K8S-NET-001, K8S-LB-001, K8S-NAD-001, K8S-TPL-001 | `open_entity` del clúster. |
| K8S-CNI-001 | `open_entity` con `target: values`. |
| K8S-VER-003, INV-HELM-001 (templates) | `open_entity` del template. |
| INV-* de device / sitio | `open_entity` del device o del sitio. |

En la UI cada tarjeta del panel muestra **Corregir · …** (`add_device`) o
**Abrir …** (`open_entity`). «Corregir» entra en Editar con el wizard
«Agregar device» precargado (sitio, clúster, rol, tipo y cantidad; el
direccionamiento lo asigna el allocator), una barra de estado con el
hallazgo y la vista previa automática. El preview manda `fix` (id, sitio, entidad y
clúster del hallazgo) y la respuesta trae `findings.fix`:
`{resolved, remaining, also_resolves}`, comparando el estado antes/después
por identidad (sin el mensaje). La barra de estado (una línea) muestra
«Corrigiendo K8S-CP-001 · site2-edge · Propuesta: +1 control plane (2→3) |
✔ Resuelve CP-001, WK-001 | ⚠ 1 aviso nuevo»; clic en **detalles** despliega
el mensaje, la propuesta, el `followup` y los hallazgos nuevos. Sin modo edición el botón queda
deshabilitado con un tooltip. El borrador (incluida la corrección) se
conserva al salir de Editar y al recargar.

## Catálogo

| id | sev | Qué revisa | Cómo silenciarla |
| --- | --- | --- | --- |
| K8S-CP-001 | warning | Control plane &lt;3 (sin quórum etcd), par (2/4/6) o &gt;7. Exento hosted/etcd externo. | `rule_overrides` en el clúster |
| K8S-CP-002 | error / warning | VMs de CP en el mismo hypervisor (error). Todos los CP en el mismo par de leafs (warning). | override por clúster |
| K8S-NET-001 | error / info | `pod_cidr`/`service_cidr` solapan entre sí, con nodos, otros clústeres o DCI. Desconocido → info. | override o documenta CIDRs |
| K8S-LB-001 | error | Pool MetalLB y VIP del API fuera de subred o chocan con device/gateway/VRRP. | override o recorta el pool |
| K8S-CNI-001 | warning | MTU CNI &gt; MTU interfaz − overhead (VXLAN 50, IPIP 20, nativo 0). | override o baja `cniConfig.mtu` |
| K8S-NAD-001 | error | NAD con VLAN ausente en topología o no presente en bond/interfaz de los nodos. | override o alinea VLAN |
| K8S-WK-001 | warning | &lt;2 workers; &lt;3 si hay Ceph/Longhorn. | override en clúster o sitio |
| K8S-ST-001 | error | Réplicas Longhorn/Ceph (values o ficha) &gt; nodos elegibles. | override o baja réplicas |
| K8S-VER-001 | warning | Kubernetes EOL (`isot/rules/k8s_eol.yaml`, fácil de actualizar). | override o upgrade |
| K8S-VER-002 | warning | Desfase de más de una minor template vs clúster. | override o alinea versiones |
| K8S-VER-003 | warning | Charts del template sin `version` fijada. | override o fija `charts[].version` |
| K8S-TPL-001 | info | Clúster sin `template_ref`. | override o enlaza template |
| INV-IP-001 | error | IPs duplicadas (sitio o address_space). | no silenciar salvo anycast documentado |
| INV-HOST-001 | error | Hostname duplicado en un sitio. | — |
| INV-PORT-001 | error | Puerto duplicado en un peer. | — |
| INV-REF-001 | error | `cluster_ref` / `connected_to` / `template_ref` / nodos rotos. | — |
| INV-HELM-001 | error | `values_ref` inválido, chart duplicado, override a release inexistente. | — |
| INV-CAB-001 | warning | Device no cumple `connection_rules`. | override o corrige el cableado |
| RULE-OVR-001 | warning | Override sin `reason` o severity inválida (se ignora). | añade `reason` |
| INV-SCH-001 | error | YAML que no cumple `schemas/*.schema.json`. Solo con `isot validate --schemas`. | — (corrige el campo) |
| ING-FMT-001 | error | `_ingest/state.yaml`, `gaps.yaml` o `provenance.yaml` con formato inválido (warning: YAML desconocido en `_ingest/`). | — |
| ING-REF-001 | warning | `_ingest/` cita una entidad o una fuente que no existe. | corrige la clave `<tipo>:<sitio>/<nombre>` |
| ING-GAP-001 | info / warning | Hueco abierto (info) o pendiente de una persona (`needs-human`, warning) en `_ingest/gaps.yaml`. | resuelve el hueco (`status: resolved` + `resolution`) |
| ING-SEC-001 | error | Posible secreto (password, community, token, clave privada) en el repo de datos. | — (quítalo; usa `${VAR}`, `<secreto>`, `vault:`) |

Las reglas `ING-*` y el formato de `_ingest/` se describen en
[docs/llm/INGEST.md](llm/INGEST.md).
