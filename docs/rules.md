# Reglas de control (ISoT)

El motor vive en `isot/rules/`. Cada regla tiene un **id estable**, severidad
por defecto, entidad y recomendación. `python -m isot.validator` las evalúa
sobre el inventario; sale distinto de 0 **solo si hay errores**. `--strict`
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
warnings nuevos del sitio piden confirmar con el checkbox.

UI: dentro de un sitio la campana y el panel muestran solo ese sitio; en la
vista global el panel agrupa por sitio (más «Global / plantillas») y cada
nodo de sitio del mapa lleva sus contadores.

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
