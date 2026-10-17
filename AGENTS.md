# AGENTS.md

Punto de entrada para agentes de IA (cualquier modelo) que trabajan con ISoT.
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
- Trabaja en una rama y abre un PR. No hagas merge.
