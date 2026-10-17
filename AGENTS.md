# AGENTS.md

Punto de entrada para agentes de IA (cualquier modelo) que trabajan con ISoT.

ISoT es un visualizador/editor **local** de uno o varios repositorios Git de
datos que el usuario ya tiene clonados. Este repositorio no guarda inventario:
solo código, plantillas (`templates/`), schemas (`schemas/`) y ejemplos
ficticios (`examples/`). Los datos, los permisos y la CI viven en cada
repositorio de datos.

Elige la guía según la tarea:

| Tarea | Guía |
| --- | --- |
| Construir o actualizar un **repositorio de datos** desde fuentes desordenadas (planillas, CSV, notas, configs, inventarios) | [docs/llm/INGEST.md](docs/llm/INGEST.md) |
| Navegar o editar un repositorio de datos que ya existe (índices, referencias, wizards, reglas) | [LLM_INSTRUCTIONS.md](LLM_INSTRUCTIONS.md) §1-9 |
| Cambiar la **herramienta** (código Python/React, schemas, CI) | [LLM_INSTRUCTIONS.md](LLM_INSTRUCTIONS.md) §10 |

Reglas comunes:

- No inventes inventario; lo desconocido se marca, no se rellena.
- Sin secretos.
- Este repositorio es público: sus ejemplos son ficticios y genéricos, y
  `isot leakcheck` debe quedar en verde.
- Con varios repositorios de datos (`repos:` en `isot.yaml`), cada uno es
  una fuente de verdad independiente: trabaja en uno a la vez.
- Trabaja en una rama y abre un PR. No hagas merge.
