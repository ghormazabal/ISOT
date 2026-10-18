# Desarrollo de ISoT

## Entorno

Requisitos: Python 3.12, Node 22, git.

```bash
git clone <tu fork> isot && cd isot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm ci)
isot demo --multi --dir ~/isot-demos --no-serve   # repos de datos de prueba, FUERA del clon
```

Nunca uses el clon de ISoT como repo de datos: los datos (y las salidas de
renders) van en repos aparte. `examples/demo-repo` sirve para tests y CI,
pero ISoT rechaza escribir renders dentro de su propia instalación.

## Ejecutar

```bash
./scripts/dev.sh                                   # API :8000 + Vite :5173 (recarga)
./scripts/serve.sh --config ~/isot-demos/isot.yaml # build + API/UI en :8000
ISOT_EDIT=1 ./scripts/serve.sh --edit              # con wizards y escritura en ramas
isot config                                        # qué repos ve y su estado
```

## Tests y build (lo mismo que la CI)

```bash
python -m isot.validator --repo examples/demo-repo
python -m isot.indexer --check --repo examples/demo-repo
python -m isot.schemas --check
python -m isot.validator --repo examples/messy-source/expected-repo --schemas
python -m isot.leakcheck
pytest
cd web && npm test && npm run build               # build = tsc --noEmit + vite build
```

Tras cambiar un schema (o algo que lo genera): `isot schemas --write`.
Tras cambiar la demo: `python -m isot.indexer --repo examples/demo-repo`.

## Añadir una regla

1. Elige módulo en `isot/rules/` (o crea uno e impórtalo en
   `isot/rules/evaluate.py`).
2. Escribe la función:

```python
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule

@rule(
    "K8S-FOO-001",
    severity="warning",
    description="Qué comprueba",
    entity="cluster",
    recommendation="Qué hacer para arreglarlo",
)
def rule_foo(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-FOO-001"]
    out: list[Finding] = []
    for cluster in ctx.clusters:
        if ...:
            out.append(emit(spec, entity_kind="cluster", entity_id=cluster["_stem"],
                            message="…", site=cluster["_site"], cluster=cluster["_stem"],
                            path=cluster["_path"]))
    return out
```

3. Añade la fila `| K8S-FOO-001 | … |` a [docs/rules.md](../rules.md): un
   test exige que todas las reglas estén catalogadas.
4. Test en `tests/test_rules.py` con un inventario mínimo en `tmp_path`.
5. La demo no debe tener hallazgos de severidad `error` (lo comprueba un test).

## Añadir un template de render incluido

1. `templates/manifests/<id>/template.yaml` + `*.j2` (ver
   [docs/llm/RENDERS.md](../llm/RENDERS.md)). Solo contenido **genérico**,
   sin nombres ni datos reales.
2. Pruébalo en la UI (Renders → Plantillas, preview contra la demo) o con
   `render_template` en un test de `tests/test_renders.py`.
3. Si usas un campo nuevo del contexto, añádelo en `ContextBuilder`
   (`isot/renders.py`) **y** en `render_context_schema()`
   (`isot/render_schemas.py`), regenera `schemas/` y documenta el campo en
   ambas guías. Cambios incompatibles del contexto exigen subir
   `CONTEXT_VERSION`.
4. Evita claves que choquen con métodos de dict en Jinja (`values`, `items`,
   `keys`).

## Añadir una ruta de API

- Rutas de datos en `isot/web.py` (dentro de `create_app`) o en un módulo
  `register(app, …)` como `isot/web_renders.py`. Usa el prefijo `/api/…`: el
  middleware las expone también como `/api/repos/{id}/…`.
- Lecturas: aceptan `ref` opcional (materializado con `gitdiff`), nunca
  escriben. Escrituras: `…/preview` (sin efectos, devuelve `token` y
  `pending`) + `…/save` o `…/write`; exige edición (`edit_globally()` +
  `edit_for(entry)`) y devuelve 403 si no. Guardar escribe **solo en el
  working tree** vía `worktree.save_files` (o `wizard.save_preview`), con el
  `token` del preview (409 si el working tree cambió). Nunca hagas commit
  desde una ruta de guardado: el commit es `POST /api/worktree/commit`
  (`worktree.commit`, ramas `wizard/…`, sin push).
- Valida todo lo que llegue a git o al disco (`validate_ref`, ids con regex,
  rutas con `safe_rel_dir`/`check_target_path`).
- Renders: todo ocurre dentro del mismo repo de datos. No añadas destinos en
  otros repositorios ni código que copie o escriba de un repo a otro;
  `save_files` solo acepta rutas dentro de los prefijos permitidos (carpeta
  destino, `renders.yaml`, `manifest-templates/<id>/`) de ese repo.
- Test con `fastapi.testclient.TestClient(create_app(repo, edit=True))` sobre
  una copia de la demo con `git init` en `tmp_path`.
- Documenta la ruta en la sección API del README.

## Añadir una vista o pestaña de la UI

1. Lógica pura en `web/src/<algo>.ts` + `web/src/<algo>.test.ts`, y añade el
   test al script `test` de `web/package.json` (la lista es explícita).
2. Componentes en `web/src/components/` (o una subcarpeta como `renders/`).
   Reutiliza `Splitter`/`usePanelSize`, `ValuesTree`, las clases
   `.wizard-actions`, `.view-tabs`, `.badge`, `.muted`, `.error` y las
   variables CSS.
3. Vista de primer nivel: añade el valor a `AppView` (`types.ts`), el botón en
   la cabecera de `App.tsx` y el render condicional. Si debe ser enlazable,
   refléjala en la URL (como `?tab=renders&sub=…`) conservando `?repo=`.
4. Respeta el solo lectura: deshabilita botones de escritura si la API no
   indica edición.
5. `npm test && npm run build` sin errores (TS estricto, sin locales sin usar).

## Escrituras e índice

Guardar y hacer commit son acciones separadas. Las rutas de guardado
escriben en el working tree con `worktree.save_files` (no tocan el índice);
`worktree.commit` llama a `indexer.write_indexes` (incremental) justo antes
del `git add` e incluye el índice en el mismo commit (para una selección
parcial, calcula el índice sobre HEAD + los archivos elegidos). Si añades un
tipo de artefacto nuevo, súmalo a `indexer.source_groups` (huella), a
`build_artifacts_index` y a `worktree._area` (grupo en **Cambios**).
`tests/test_index_fresh.py` recorre todas las rutas de escritura, hace un
solo commit y comprueba `check_index`; `tests/test_worktree.py` cubre varios
guardados → un commit, selección parcial, descartar, protecciones de rutas
y que nunca se hace commit en `main`.

## Comparar

Una vista nueva que deba compararse recibe `base`/`head` (strings; `local`
es válido) y los resuelve con `refs.resolve_ref` + `refs.tree_for` (o
`gitdiff.snapshot_for`). Nunca hagas checkout ni escribas en el árbol
materializado: es una caché compartida y de solo lectura. En la UI, lee las
refs de `compareRefs` en `App.tsx` (no añadas selectores de ref propios).
Tests: `tests/test_compare.py` y `web/src/compare.test.ts`.

## Convenciones

- Textos de UI, mensajes y docs en castellano; identificadores en inglés.
- Python: tipado, funciones puras donde se pueda, `from __future__ import
  annotations`; sin dependencias nuevas sin motivo (añádelas a
  `pyproject.toml` y `requirements.txt`).
- Salidas deterministas (orden estable, sin timestamps en archivos
  generados) para que índices, schemas y renders tengan diffs limpios.
- Nada de secretos en código, ejemplos ni fixtures.

## Repo público: controles de fuga

- `python -m isot.leakcheck` debe pasar (hashes en
  `isot/forbidden_patterns.json`; no añadas los términos en claro).
- Antes de publicar, busca también términos sensibles propios de tu
  organización en el árbol (`git grep -iE '<patrones>' HEAD`) y en los bytes
  de las imágenes (`grep -a`): si una PNG coincide por azar, vuelve a
  capturarla o recomprímela.
- Ejemplos y capturas solo con la demo ficticia; nunca rutas personales,
  hostnames o planes IP reales.
- Commits con tu email *noreply* de GitHub
  (`git config user.email <id>+<usuario>@users.noreply.github.com`).
- La descripción de un PR no menciona repos privados.

## Flujo de ramas y merge

- Trabaja en una rama `feat/…` / `fix/…`; abre un PR contra `main`. No
  hagas push directo a `main` ni merges propios sin revisión.
- La CI (`.github/workflows/ci.yml`) debe estar verde.
- Capturas nuevas en `docs/images/` (1280×800) referenciadas desde el README.
- Versionado: los repos de datos fijan ISoT por rama/tag/SHA en el workflow
  reutilizable (`isot-ref`); al romper compatibilidad del formato, documenta
  la migración y considera un tag.
