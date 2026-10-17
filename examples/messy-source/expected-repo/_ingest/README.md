# Ingesta: mapa de fuentes y decisiones

Notas de trabajo de la ingesta (pasos 1, 3 y 4 de `docs/llm/INGEST.md`). El
validador solo lee `state.yaml`, `gaps.yaml` y `provenance.yaml`; este archivo
es para las personas que revisan el PR.

## 1. Mapa de fuentes (`messy` = `examples/messy-source/source`)

| Archivo | Tipo | Qué aporta | Confianza |
| --- | --- | --- | --- |
| `hosts.csv` | CSV, 1 fila por host | hostname, rol, IP de gestión, BMC (IP, switch, puerto), modelo, SO, clúster | alta (inventario) |
| `notes.md` | Markdown libre | VLANs y subredes, clúster Kubernetes, pendientes | media (apuntes) |
| `switch-config.txt` | Pseudo-config de los 2 leafs | Management1, uplinks a spine, Port-Channel por host y VLANs permitidas | alta (config) |

Contiene secretos (`username … secret`, `snmp-server community`, líneas 4-5 de
`switch-config.txt`): **no se copian**.

## 2. Entidades identificadas

- Sitio: `lab1` (prefijo de todos los hostnames).
- Devices: 4 switches (spine, 2 leafs, mgmt) y 4 servidores.
- Clúster: `lab1-k8s` (kubernetes), nodos NODE01-03.
- Redes: VLAN 10/100/200 con su subred. VLAN 300 ambigua (GAP-001).

## 3. Tabla de mapeo

| Fuente | Campo origen | Campo ISoT | Transformación |
| --- | --- | --- | --- |
| hosts.csv | `Hostname` | `hostname` + nombre de archivo | recortar espacios, MAYÚSCULAS |
| hosts.csv | `Tipo` | carpeta `devices/<tipo>/` | switch → `network`, servidor → `baremetal` |
| hosts.csv | `Rol` | `role` (y `k8s_roles` en servidores) | tal cual |
| hosts.csv | `IP gestion` | `networking[]` (`OOB` en switches, `HOST_MGMT` en servidores) | añadir el prefijo de la subred que la contiene |
| hosts.csv | `BMC IP`, `Switch BMC`, `Puerto BMC` | `oob.ip`, `oob.switch`, `oob.port`, `oob.vlan: 10` | `?` o vacío → hueco, no se escribe |
| hosts.csv | `Modelo`, `SO` | `model`, `os` | tal cual |
| hosts.csv | `Cluster` | `cluster_ref` | `?` → hueco |
| notes.md | líneas «VLAN n … red/gw» | `topology.subnets[]` + `vlans[]` | `gateway .1` → primera IP de la red |
| notes.md | «Cluster …: node01 es el control plane…» | `clusters/lab1-k8s.yaml` `nodes[]` | `nodeNN` → `LAB1-NODENN` |
| switch-config.txt | `interface Management1` + `ip address` | `networking[Management1]` del leaf | tal cual |
| switch-config.txt | `description uplink <spine> <puerto>` | `connected_to` de `Ethernet1` del leaf | |
| switch-config.txt | `Port-Channel1N` con `description <host> bond0` en ambos leafs | `bonds[bond0].leaf_pc` del host | `allowed vlan` → `allowed_vlans`; `mode active` → `mode: lacp` (solo donde aparece) |

## 4. Normalización

| Tipo | Regla | Ejemplo |
| --- | --- | --- |
| Hostname | trim + MAYÚSCULAS, numeración de la fuente | `LAB1-NODE02 ` → `LAB1-NODE02` |
| IP | siempre con prefijo de la subred que la contiene | `10.60.1.11` → `10.60.1.11/24` |
| VLAN | entero | `vlan 100` → `100` |
| Interfaz | `Eth` → `Ethernet`, `Port-Channel` → `PortChannel`, `Gi` → `GigabitEthernet` | `Eth1` → `Ethernet1` |
| Interfaz lógica sin nombre en la fuente | nombre de la subred | IP de gestión de un servidor → `HOST_MGMT` |
