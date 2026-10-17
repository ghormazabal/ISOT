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
| Escribir o modificar **templates de render** (Netplan, manifiestos…) e instancias en `renders.yaml` | [docs/llm/RENDERS.md](docs/llm/RENDERS.md) (personas: [docs/renders/GUIDE.md](docs/renders/GUIDE.md)) |
| Cambiar la **herramienta** (código Python/React, schemas, CI) | [LLM_INSTRUCTIONS.md](LLM_INSTRUCTIONS.md) §10 y [docs/dev/](docs/dev/README.md) (arquitectura, mapa de archivos, cómo añadir reglas/rutas/pestañas) |

Reglas comunes:

- No inventes inventario; lo desconocido se marca, no se rellena.
- Sin secretos.
- Este repositorio es público: sus ejemplos son ficticios y genéricos, y
  `isot leakcheck` debe quedar en verde.
- Con varios repositorios de datos (`repos:` en `isot.yaml`), cada uno es
  una fuente de verdad independiente: trabaja en uno a la vez.
- Trabaja en una rama y abre un PR. No hagas merge.
- **Límite de los renders**: el repositorio de ISoT solo gestiona templates
  genéricos (`templates/manifests/`, solo lectura) y ejemplos ficticios. Los
  datos reales, los templates personalizados de un cliente y **toda salida
  renderizada** viven siempre en el repositorio de datos (`manifest-templates/`,
  `renders.yaml`, `rendered/`), nunca en el repo de ISoT ni en su carpeta de
  instalación: ISoT rechaza cualquier destino que resuelva dentro de ella y no
  guarda cachés ni salidas propias.
- **Un solo repositorio por render**: los renders funcionan enteramente dentro
  del mismo repo de datos. La carpeta destino es siempre una carpeta de ese
  repo (`rendered/` por defecto, o el `target_dir` de la instancia) y
  «Aplicar» hace commit en una rama `wizard/…` de ese mismo repo. ISoT nunca
  mueve ni escribe nada de un repositorio a otro: no hay repos destino.
