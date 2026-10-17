# Arquitectura de ISoT

ISoT (*Infrastructure Source of Truth*) es una herramienta **local**: un
visor/editor web y una CLI sobre uno o varios repositorios Git de datos que
el usuario ya tiene clonados («Git-as-a-Database»). El repositorio de ISoT
contiene solo código, plantillas genéricas, schemas y ejemplos ficticios.

## Vista general

```mermaid
flowchart LR
  subgraph tool["Repo / instalación de ISoT"]
    cli["CLI isot<br/>(isot/__main__.py)"]
    api["API FastAPI<br/>(isot/web.py, web_renders.py)"]
    core["Núcleo Python<br/>inventory · indexer · validator<br/>rules · renders · wizard · gitdiff"]
    ui["SPA React<br/>(web/src)"]
    lib["templates/ · schemas/ · examples/"]
    cli --> core
    api --> core
    ui <-->|HTTP JSON| api
    lib --> core
  end
  subgraph data["Repos de datos (Git del usuario)"]
    repo[("sites/, cluster-templates/,<br/>manifest-templates/, renders.yaml,<br/>_ingest/, rendered/")]
  end
  subgraph target["Repos destino (opcional)"]
    gitops[("salida de renders")]
  end
  core -->|lee working tree o refs| repo
  core -->|commits en ramas wizard/…| repo
  core -->|apply de renders en ramas wizard/…| gitops
```

## Piezas

### Núcleo de inventario
- `isot/inventory.py` carga perezosa de sitios, equipos, clústeres y
  cluster-templates (YAML). `isot/indexer.py` genera índices ligeros
  deterministas (`_sites_index.json`, `_device_index.json`,
  `_cluster_index.json`, `_template_index.json`) que la UI lee al arrancar;
  el YAML completo se abre bajo demanda.
- `isot/validator.py` integra integridad referencial, IPAM, cableado y
  reglas; `isot/schemas.py` genera los JSON Schema de `schemas/` **desde el
  código** (y `--check` verifica que estén al día).
- `isot/helm.py` resuelve charts/values de cluster-templates (lazy) y el diff
  values de clúster vs template. `isot/links.py` calcula el detalle de una
  conexión (arista). `isot/cabling.py` y `isot/allocator.py` son la política
  de cableado y la asignación determinista de IPs/hostnames/puertos.

### Motor de reglas (`isot/rules/`)
Registro declarativo: cada regla es una función decorada con
`@rule(id, severity, description, entity, recommendation)` que recibe un
`RuleContext` (snapshot del inventario) y emite `Finding`s. `evaluate.py`
importa los módulos de reglas, aplica overrides de severidad
(`overrides.py`), añade remediaciones legibles por máquina
(`remediation.py`) y agrupa por sitio. Las mismas reglas corren en la UI
(campana de hallazgos), en el validador y en la CI del repo de datos.
Catálogo: [docs/rules.md](../rules.md).

### Renders (`isot/renders.py`, `isot/web_renders.py`, `isot/rules/renders.py`)
Templates Jinja2 (`template.yaml` + `*.j2`) sobre un **contexto versionado**
derivado del inventario (`ContextBuilder`, `context_version: 1`, esquema en
`schemas/render-context.schema.json`). `RenderService` resuelve instancias
(`renders.yaml`), destinos (`targets`, `target_repos`), renderiza en memoria,
compara contra la carpeta destino (`target_diff`) y prepara el «apply»
(`apply_plan` + `commit_files`). Guías: [docs/renders/GUIDE.md](../renders/GUIDE.md),
[docs/llm/RENDERS.md](../llm/RENDERS.md).

### Edición (`isot/wizard.py`)
Los wizards (nuevo equipo, nuevo sitio, arreglar un hallazgo) y las
escrituras de renders calculan un **preview en memoria** (diff + grafo +
hallazgos nuevos) y, al confirmar, hacen commit en una rama nueva
`wizard/<slug>` (o continúan una rama `wizard/…`), vuelven a la rama
original y nunca tocan ramas protegidas. El push es opcional
(`edit.push`), solo de ramas `wizard/`.

### Git (`isot/gitdiff.py`)
Lectura de refs sin checkout: `git archive` de un SHA a un directorio
temporal (`_materialize`), snapshots del inventario por ref, diff semántico
entre refs (Comparar) y hallazgos por ref. Valida refs (`validate_ref`) antes
de pasarlas a git.

### Configuración y multi-repo (`isot/config.py`, `isot/web.py`)
`isot.yaml` declara `repos` (id, path, branch, edit…), `default_repo`,
`target_repos` (destinos de renders), `edit` y `ui`. En la API,
`RepoRegistry` mantiene los repos y `RepoPrefixMiddleware` reescribe
`/api/repos/{id}/…` a `/api/…` con el repo elegido; sin prefijo se usa el
repo por defecto. Cada repo es una fuente de verdad independiente.

### Ingesta (`isot/ingest.py`, `isot/rules/ingest.py`, `docs/llm/INGEST.md`)
ISoT no llama a LLMs: define el formato, `_ingest/` (state, provenance, gaps)
y comprobaciones (`isot ingest status|hash|check`, reglas ING-*) para que un
agente externo construya o actualice el repo de datos.

### Frontend (`web/`)
React 18 + TypeScript + Vite + @xyflow/react. `Root.tsx` resuelve la
configuración/repo y monta `App.tsx`, que orquesta las vistas (Sitios/Red,
Kubernetes, Renders, Comparar, Editar), los paneles redimensionables
(`Splitter`), el panel de hallazgos y el detalle. La lógica pura vive en
módulos `*.ts` sin React (testeados con `node --test`); los componentes en
`components/`. Todas las llamadas pasan por `api.ts`/`rendersApi.ts`, que
añaden el prefijo `/api/repos/{id}`.

## Flujo de datos

1. **Lectura**: la UI pide índices (`/api/sites`, `/api/sites/{id}/devices`…)
   y grafos; el backend lee el working tree del repo de datos (o un ref
   materializado). Detalles, values y contexto de render se cargan al
   seleccionar.
2. **Reglas**: `load_context(root)` → `evaluate()` → hallazgos por sitio,
   con remediaciones que la UI convierte en acciones («abrir», «arreglar con
   wizard»).
3. **Edición**: formulario → `POST …/preview` (sin escribir) → diff y
   hallazgos nuevos → `POST …/commit` → rama `wizard/…` → revisión y merge
   en el repo de datos (PR/MR, CI con el workflow reutilizable).
4. **Renders**: template + instancia + contexto → archivos en memoria →
   diff contra el destino → «aplicar» en una rama del repo destino.

## Por qué está diseñado así

- **Datos fuera de ISoT.** El inventario vive en repos de datos con sus
  propios permisos, revisiones y CI. ISoT no tiene usuarios ni roles: usa la
  identidad git del usuario. Así ISoT puede ser público y genérico, y el
  control de acceso se **delega en el repo de datos** (ramas protegidas,
  CODEOWNERS, CI).
- **Solo plantillas genéricas en ISoT.** `templates/` (sitios,
  cluster-templates, renders) es una biblioteca de solo lectura; personalizar
  = clonar al repo de datos. Toda salida renderizada va al repo de datos o a
  un repo destino, nunca a la instalación de ISoT (se rechaza por código y
  hay tests de ello). Esto evita mezclar datos de clientes con la
  herramienta.
- **Edición vía ramas.** Nada escribe en `main`: los wizards y renders
  proponen ramas `wizard/…` que una persona revisa. El árbol debe estar
  limpio y se vuelve a la rama original tras el commit.
- **Render determinista y en sandbox.** Jinja2 `SandboxedEnvironment` +
  `StrictUndefined`: un template no puede ejecutar código ni leer archivos,
  y una variable inexistente es un error con archivo:línea en lugar de una
  salida vacía silenciosa. El contexto está versionado y documentado; los
  secretos se redactan antes de llegar al template y la salida se escanea.
  El mismo input produce el mismo output (rutas ordenadas, saltos de línea
  normalizados), así el diff contra el destino es fiable.
- **Índices ligeros + carga perezosa.** Repos grandes abren rápido; los
  índices son deterministas y se verifican en CI (`--check`).
- **Schemas desde el código.** Una sola fuente de verdad del formato; CI
  falla si `schemas/` no está regenerado.
- **Repo público, anonimizado.** Los ejemplos son ficticios; `isot
  leakcheck` busca identificadores prohibidos guardados como **hashes**
  (`isot/forbidden_patterns.json`) para no publicar los términos en claro;
  los commits se firman con el email *noreply* del autor.
- **Lógica pura separada de la UI.** Cada comportamiento no trivial del
  frontend (URLs, layout, hallazgos, diff, árboles) es una función pura con
  tests rápidos sin navegador.
