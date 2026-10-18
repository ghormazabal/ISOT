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
  core -->|lee working tree o refs| repo
  core -->|Guardar: escribe en el working tree| repo
  core -->|Commit: rama wizard/… del mismo repo, sin push| repo
```

## Piezas

### Núcleo de inventario
- `isot/inventory.py` carga perezosa de sitios, equipos, clústeres y
  cluster-templates (YAML). `isot/indexer.py` genera índices ligeros
  deterministas (`_sites_index.json`, `_device_index.json`,
  `_cluster_index.json`, `_template_index.json`, `_artifacts_index.json` con
  `manifest-templates/`, `renders.yaml`, `rendered/` y `_ingest/`) que la UI
  lee al arrancar; el YAML completo se abre bajo demanda.
- **Frescura del índice.** `_index_manifest.json` guarda la huella (sha256 de
  rutas y contenidos) de cada grupo de fuentes (`sites.yaml`, devices y
  clústeres por sitio, cluster-templates, `manifest-templates/`,
  `renders.yaml`, cada carpeta de salida, `_ingest/`). `index_status`
  compara huellas (con caché de hashes por tamaño/mtime) y `IndexBuilder`
  reutiliza del disco los índices de los grupos sin cambios: reindexar es
  **incremental**. Si el índice está desfasado la API sirve un overlay en
  memoria (`live_indexes`) y la UI muestra el aviso; las huellas se calculan
  sobre el **working tree**, así que los guardados sin commit también lo
  marcan. Nunca escribe índices por su cuenta: `worktree.commit` llama a
  `write_indexes` (incremental) justo antes de cada commit y los incluye
  («Regenerar índice» los escribe en el working tree a petición). La regla
  IDX-001 y `indexer --check` (CI) detectan índices commiteados desfasados.
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
(`renders.yaml`) y su carpeta destino (`target_dir`), renderiza en memoria,
compara contra la carpeta destino (`target_diff`) y prepara el «apply»
(`apply_plan`, escrito con `worktree.save_files`). Guías: [docs/renders/GUIDE.md](../renders/GUIDE.md),
[docs/llm/RENDERS.md](../llm/RENDERS.md). Principio: **un render vive en un
solo repo**; la carpeta destino es siempre una carpeta del mismo repo de datos
y ISoT nunca escribe de un repositorio a otro.

### Edición: Guardar vs Commit (`isot/wizard.py`, `isot/worktree.py`)
Guardar y hacer commit son acciones separadas:

- **Guardar.** Los wizards (nuevo equipo, nuevo sitio, Corregir un hallazgo)
  y las escrituras de renders (Guardar en el repo, Aplicar, plantillas,
  `renders.yaml`) calculan un **preview en memoria** (diff + grafo +
  hallazgos nuevos) con un `token` (sha1 de ruta, hash en disco y hash
  nuevo de cada archivo). Al confirmar, `worktree.save_files` vuelve a
  comprobar el token (si el working tree cambió → `ConflictError`, HTTP 409,
  la UI re-previsualiza), valida rutas (`check_paths`: dentro del repo de
  datos y del prefijo permitido, sin `..`, `.git` ni symlinks que escapen;
  nunca la instalación de ISoT) y escribe **solo en el working tree**, sin
  cambiar de rama. `wizard.save_preview` guarda con `snapshot`/`restore`:
  si el validador da errores nuevos, restaura los archivos. Ya no se exige
  árbol limpio: el preview marca con `pending` (y su diff) los archivos que
  ya tenían cambios sin commit.
- **Commit.** `worktree.summary` agrupa el `git status` por área (salida de
  renders, plantillas, `renders.yaml`, sitios, índice). `worktree.commit`
  hace commit de todo lo pendiente o de una selección: en la rama actual si
  es `wizard/…`, si no en `wizard/<slug>` nueva desde HEAD; rechaza
  `main`/`master` y ramas no `wizard/`. Regenera el índice justo antes (para
  una selección, lo calcula sobre `git archive HEAD` + los archivos
  elegidos, así el índice commiteado es coherente con el árbol commiteado).
  Ante un fallo deshace con `git reset` y borra la rama creada (nunca
  `reset --hard`). `worktree.discard` restaura archivos desde HEAD (o borra
  los nuevos). No hay push: `edit.push` se ignora.

### Git y Comparar (`isot/refs.py`, `isot/gitdiff.py`, `isot/compare.py`)
Todo Comparar usa base/head donde cualquiera puede ser `local` (working
tree). `refs.py` resuelve refs (`parse_ref` → `git rev-parse --verify
--end-of-options <ref>^{commit}`, siempre con lista de argumentos, sin shell)
a un `RefSpec`; `local` lleva una huella del working tree (HEAD + rutas,
tamaños y mtimes) como clave de caché. `TreeCache` materializa commits con
`git archive` en un `mkdtemp` **fuera** de ISoT y del repo, cacheado por SHA
(LRU con refcount, borrado con `atexit`); `tree_for` da el propio repo para
`local`. Sobre eso: `gitdiff.semantic_diff` (snapshots por ref que reutilizan
el índice commiteado de esa ref si está al día; entidades con `path` del
YAML de origen), `evaluate_ref`/`diff_findings` (hallazgos nuevos/resueltos),
`links.diff_details` (aristas), `compare.changed_files`/`file_diff`
(archivos, con no rastreados en `local`) y `renders.compare_outputs` (salida
renderizada base → head). Todo es de solo lectura.

### Configuración y multi-repo (`isot/config.py`, `isot/web.py`)
`isot.yaml` declara `repos` (id, path, branch, edit…), `default_repo`,
`edit` y `ui`. En la API,
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
Kubernetes, Renders, Editar) y el control único `CompareControl` (base →
head, estado en `?base=&head=` vía `compare.ts`, compartido por grafo,
listas, detalle, Archivos, hallazgos y Renders), los paneles redimensionables
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
   hallazgos nuevos → `POST …/save` (working tree) → … más guardados … →
   **Cambios** → `POST /api/worktree/commit` → rama `wizard/…` → revisión y
   merge en el repo de datos (PR/MR, CI con el workflow reutilizable).
4. **Renders**: template + instancia + contexto → archivos en memoria →
   diff contra la carpeta destino del mismo repo → «Guardar/Aplicar» en el
   working tree de ese mismo repo → commit desde **Cambios**.

## Por qué está diseñado así

- **Datos fuera de ISoT.** El inventario vive en repos de datos con sus
  propios permisos, revisiones y CI. ISoT no tiene usuarios ni roles: usa la
  identidad git del usuario. Así ISoT puede ser público y genérico, y el
  control de acceso se **delega en el repo de datos** (ramas protegidas,
  CODEOWNERS, CI).
- **Solo plantillas genéricas en ISoT.** `templates/` (sitios,
  cluster-templates, renders) es una biblioteca de solo lectura; personalizar
  = clonar al repo de datos. Toda salida renderizada va al mismo repo de
  datos, nunca a otro repositorio ni a la instalación de ISoT (se rechaza por código y
  hay tests de ello). Esto evita mezclar datos de clientes con la
  herramienta.
- **Guardar en el working tree, commit aparte en ramas.** Guardar no hace
  commit, para poder juntar varios cambios (renders, plantillas,
  `renders.yaml`, wizards) en un solo commit y revisarlos con Comparar →
  Local antes. El commit nunca va a `main`: los wizards y renders proponen
  ramas `wizard/…` que una persona revisa y publica (ISoT no hace push).
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
