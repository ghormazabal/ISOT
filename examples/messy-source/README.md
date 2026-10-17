# Ejemplo de ingesta: de fuentes desordenadas a un repositorio ISoT

Ejemplo trabajado de [`docs/llm/INGEST.md`](../../docs/llm/INGEST.md). Todo es
ficticio (IPs RFC 1918, nombres genéricos).

- `source/`: lo que un equipo suele tener: `hosts.csv` (planilla exportada),
  `notes.md` (apuntes libres con VLANs y el clúster) y `switch-config.txt`
  (pseudo-config de dos leafs, con secretos que **no** deben copiarse).
- `expected-repo/`: el repositorio de datos que debe producir la ingesta,
  incluidos `_ingest/state.yaml`, `gaps.yaml`, `provenance.yaml` y el mapa de
  fuentes en `_ingest/README.md`.

```bash
isot validate --repo examples/messy-source/expected-repo --schemas   # exit 0 (avisos esperados)
isot index --check --repo examples/messy-source/expected-repo
isot ingest status --repo examples/messy-source/expected-repo --source examples/messy-source/source
```

Avisos esperados, que **no** se silencian porque ninguna fuente da una razón:
`K8S-CP-001` (un solo control plane) y los `ING-GAP-001` de GAP-001 (VLAN 300
ambigua) y GAP-002 (IP en conflicto), que esperan a una persona.

`tests/test_ingest.py` valida este ejemplo en CI.
