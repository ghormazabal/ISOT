# Templates de render genéricos (bundled)

Templates de **render** que trae ISoT: genéricos y de solo lectura. Para
personalizarlos, usa **Renders → Plantillas → Clonar al repo de datos**: la copia
queda en `manifest-templates/<id>/` del repositorio de datos (en una rama
`wizard/…`) y desde ahí la editas en la web.

| id | scope | salida |
|----|-------|--------|
| `netplan-node` | site/cluster | un netplan por nodo (`per_item`) **y** uno combinado (`single`, multi-documento) |
| `netplan-per-interface` | site/cluster | un archivo por interfaz VLAN de cada nodo (`per_item` con `explode`) |
| `multus-nad` | cluster | `NetworkAttachmentDefinition` de los NADs del clúster |
| `metallb-pools` | cluster | `IPAddressPool` + `L2Advertisement` |
| `core-namespaces` | cluster | `Namespace` de los namespaces del clúster |

Formato: [docs/renders/GUIDE.md](../../docs/renders/GUIDE.md) (personas) y
[docs/llm/RENDERS.md](../../docs/llm/RENDERS.md) (LLM).
