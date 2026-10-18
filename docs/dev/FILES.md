# Mapa de archivos

Rutas relativas a la raíz del repo de ISoT. Las funciones listadas son las
principales (no exhaustivo); los módulos privados (`_x`) se omiten.

## Raíz

| Archivo | Qué es |
|---|---|
| `README.md` | Documentación de uso (funcionalidades, configuración, seguridad, API) |
| `AGENTS.md`, `LLM_INSTRUCTIONS.md` | Punto de entrada y reglas para agentes de IA |
| `pyproject.toml`, `requirements.txt` | Paquete `isot` (FastAPI, PyYAML, Jinja2…); scripts `isot`, `isot-validate`, `isot-index`, `isot-web`, `isot-leakcheck`; extra `[dev]` |
| `isot.example.yaml` | Plantilla de configuración (`isot.yaml` real va en `.gitignore`) |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | Imagen: build del frontend + paquete Python; `ISOT_HOME=/app` |
| `.pre-commit-config.yaml` | Hooks de este repo (leakcheck, etc.) |
| `.pre-commit-hooks.yaml` | Hooks reutilizables para repos de datos (`isot-index`, `isot-validate`) |

## `isot/` (backend y CLI)

| Archivo | Qué hace | Principales |
|---|---|---|
| `__main__.py` | CLI `isot <comando>`: web, validate, index, init, demo, demo-diff, config, leakcheck, ingest, schemas | `main` |
| `paths.py` | Rutas de la herramienta (`ISOT_HOME`) y detección de repos de datos | `tool_root`, `bundled_templates_dir`, `demo_repo_dir`, `is_data_repo`, `find_repo_root` |
| `config.py` | `isot.yaml`: repos, edición, UI; búsqueda y validación | `Config`, `DataRepoConfig`, `EditConfig`, `parse_config`, `load_config`, `resolve_repos`, `repo_status` |
| `inventory.py` | Carga perezosa del inventario y de la biblioteca de templates | `Site`, `Device`, `Interface`, `AddressSpace`, `iter_yaml_files`, `find_template_file`, `template_library`, `parse_ref` |
| `indexer.py` | Índices deterministas `_*_index.json`, artefactos y manifiesto de huellas; reindexado incremental, estado y overlay en memoria (`--check`, `--status`, `--full`) | `build_sites_index`, `build_artifacts_index`, `IndexBuilder`, `write_indexes`, `check_index`, `index_status`, `cached_status`, `live_indexes`, `pending_index_writes`, `plan_builder`, `main` |
| `refs.py` | Refs de Comparar: `local` (working tree) o commit; validación, huella del working tree, caché de árboles por SHA fuera de ISoT | `parse_ref`, `is_local`, `resolve_ref`, `describe_ref`, `RefSpec`, `TreeCache`, `TREES`, `tree_for` |
| `compare.py` | Archivos que cambian entre base y head y diff de uno (solo lectura) | `changed_files`, `file_diff`, `read_at`, `safe_repo_path` |
| `validator.py` | Validación (IPAM, refs, cableado, reglas, schemas) y salida texto/JSON/anotaciones GitHub | `validate`, `collect_issues`, `collect_warnings`, `format_report`, `github_annotations`, `main` |
| `schemas.py` | JSON Schema del repo de datos generados desde el código; validación de documentos | `build_schemas`, `write_schemas`, `check_schemas`, `repo_documents`, `validate_document`, `main` |
| `render_schemas.py` | Schemas del contexto de render, `template.yaml` y `renders.yaml` | `render_context_schema`, `render_template_schema`, `renders_config_schema`, `render_schemas` |
| `renders.py` | Motor de renders: filtros, sandbox, contexto, templates, instancias, carpeta destino (mismo repo), diff, apply | `make_env`, `redact`, `scan_secrets`, `ContextBuilder`, `TemplateDef`, `parse_template`, `list_templates`, `find_template`, `load_render_config`, `check_target_path`, `target_folder`, `render_template`, `RenderService`, `diff_files`, `file_diffs` |
| `web.py` | App FastAPI: índices, grafos, detalle, values, refs/diff, hallazgos, wizards, multi-repo, estáticos | `create_app`, `RepoRegistry`, `RepoPrefixMiddleware`, `graph_from_index`, `kubernetes_graph`, `global_sites_graph`, `main` |
| `web_renders.py` | Rutas `/api/renders/*` (lectura siempre; `…/preview` + `…/write` con edición, guardan en el working tree) | `register`, `validate_renders_text` |
| `worktree.py` | Guardar vs Commit: escribir en el working tree con token de concurrencia y rutas protegidas, `git status` por área, diff, descartar y commit en `wizard/…` con índice regenerado | `save_files`, `check_paths`, `plan_token`, `summary`, `status_entries`, `file_diff`, `pending_info`, `snapshot`, `restore`, `discard`, `commit`, `WorktreeError`, `ConflictError` |
| `web_worktree.py` | Rutas `/api/worktree` (estado, diff; descartar y commit con edición) | `register` |
| `wizard.py` | Wizards: preview en memoria y guardado en el working tree (restaura si el validador da errores nuevos) | `device_preview`, `site_preview`, `save_preview`, `unique_wizard_branch`, `current_branch`, `WizardError` |
| `gitdiff.py` | Git sin checkout: refs, commits, snapshots por ref (o `local`), diff semántico | `validate_ref`, `resolve_sha`, `list_refs`, `list_commits`, `snapshot_ref`, `snapshot_for`, `semantic_diff`, `git`, `clear_caches` |
| `helm.py` | Charts/values de cluster-templates y diff values clúster vs template | `charts_from_doc`, `load_chart_values`, `merge_values`, `cluster_values_diff`, `collect_chart_issues` |
| `links.py` | Detalle de una conexión del grafo (y su diff entre refs) | `link_details`, `diff_details`, `view_from_tree`, `view_from_snapshot` |
| `cabling.py` | Política de cableado (`connection_rules`) y elección de leafs/puertos | `infer_policy`, `load_or_infer`, `pick_pair`, `check_device`, `collect_connection_warnings` |
| `allocator.py` | IPAM, hostnames y puertos deterministas para wizards | `next_ip`, `next_hostname`, `next_port`, `parse_hostname`, `HostnamePlan`, `IpPlan` |
| `bootstrap.py` | `isot init` / `isot demo` / `demo-diff` | `init_main`, `demo_main`, `create_demo`, `create_multi_demo`, `scaffold`, `create_diff_example` |
| `ingest.py` | `_ingest/` (state, provenance, gaps), hashes de fuentes, detección de secretos | `load_ingest`, `check_state`, `check_gaps`, `check_provenance`, `secret_issues`, `hash_tree`, `source_status`, `main` |
| `leakcheck.py` | Patrones prohibidos por hash (`forbidden_patterns.json`) en el árbol | `scan`, `digest`, `iter_files`, `format_report`, `main` |
| `forbidden_patterns.json` | Hashes de identificadores que no deben reaparecer | — |

### `isot/rules/`

| Archivo | Qué hace | Principales |
|---|---|---|
| `registry.py` | Registro `id → RuleSpec` | `rule` (decorador), `emit`, `catalog` |
| `model.py` | Tipos estables | `RuleSpec`, `Finding`, `finding_from_dict` |
| `context.py` | Snapshot de inventario para reglas | `RuleContext`, `load_context` |
| `evaluate.py` | Ejecuta las reglas, overrides, remediaciones, agrupación por sitio, por ref y diff | `evaluate`, `evaluate_ref`, `evaluate_overlay`, `diff_findings`, `scope_findings`, `payload` |
| `inventory.py` | Reglas INV-* (IP, hostname, puerto, refs, cableado, Helm, schema, overrides) | `rule_ip`, `rule_ref`, `rule_cabling`, `collect_integrity_findings` |
| `k8s.py` | Reglas K8S-* (CP, CIDRs, MetalLB, MTU, NAD, EOL, skew, charts) | `rule_cp_count`, `rule_cidrs`, `rule_metallb`, `rule_nad`, `rule_k8s_eol`, `_cluster_values` |
| `index.py` | IDX-001: índice commiteado ausente o desactualizado | `rule_index_fresh` |
| `renders.py` | REN-001, REN-002, K8S-NAD-002 | `rule_render_errors`, `rule_render_secrets`, `rule_nad_leaf_trunk` |
| `ingest.py` | ING-* y secretos en el inventario | `rule_ingest_format`, `rule_ingest_gaps`, `rule_secrets` |
| `overrides.py` | Overrides de severidad en YAML | `parse_overrides`, `apply_overrides` |
| `remediation.py` | Acciones de remediación (abrir entidad, wizard) | `default_remediation`, `fill_default_remediations`, `fix_status` |
| `k8s_eol.yaml` | Fechas de fin de soporte de Kubernetes | — |

## `web/` (frontend)

| Archivo | Qué hace | Exports |
|---|---|---|
| `package.json` | Scripts `dev`, `build` (`tsc --noEmit && vite build`), `test` (lista explícita de `src/*.test.ts`) | — |
| `tsconfig.json`, `vite.config.ts` | TS estricto; proxy `/api` en dev | — |
| `src/main.tsx`, `src/Root.tsx` | Arranque; config, onboarding, repo activo/no disponible | `Root` |
| `src/App.tsx` | Orquestación de vistas, cabecera, paneles, hallazgos, Comparar, Renders (`?tab=renders&sub=`) | `App` |
| `src/api.ts` | Cliente HTTP con prefijo de repo; `ApiError` (409 = conflicto), eventos `isot:worktree-changed` / `isot:open-changes` | `api`, `repoUrl`, `setApiRepo`, `ApiError`, `isConflict`, `notifyWorktreeChanged`, `openChanges` |
| `src/worktree.ts` | Cambios: grupos por área, selección, rama destino, motivo de bloqueo del commit | `groupByArea`, `defaultSelection`, `mergeSelection`, `commitTargets`, `newBranchName`, `commitBlockReason`, `isWizardBranch` |
| `src/types.ts` | Tipos de la API | `AppView`, `GraphPayload`, `Finding`, `ConfigInfo`… |
| `src/repos.ts` | Selección de repo (URL `?repo=`, localStorage) | `pickRepo`, `withRepoParam`, `repoFromUrl` |
| `src/dataRepo.ts` | Lógica del repo activo y biblioteca | `mergeLibraryTemplates`, `isOffBranch`, `repoSummary` |
| `src/layout.ts` | Construcción del grafo (nodos/aristas, colores VLAN) | `buildFlow`, `vlanColor` |
| `src/links.ts` | Detalle de conexiones | `edgeSelection`, `sideRows`, `relatedFindings` |
| `src/findings.ts` | Conteos, filtrado y anotación de hallazgos | `countFindings`, `scopeFindings`, `annotateGraph`, `canFixWithWizard` |
| `src/compareRefs.ts` | Refs por defecto de Comparar | `pickDefaultRefs` |
| `src/compare.ts` | Comparar universal: `local`, validación de refs, URL `?base=&head=`, por defecto, ⇄, escritura | `LOCAL_REF`, `normalizeRef`, `compareFromUrl`, `withCompareParams`, `defaultCompare`, `swapRefs`, `headIsWritable` |
| `src/resize.ts` | Paneles redimensionables (lógica pura) | `PanelSpec`, `clampSize`, `appPanelSpecs`, `loadLayout` |
| `src/wizardDraft.ts` | Borrador del wizard en sessionStorage | `loadSession`, `saveSession`, `applyEditToggle` |
| `src/renders.ts` | Renders: URL de sub-pestañas, árbol, diff lado a lado, edición de `template.yaml` | `rendersFromUrl`, `withRendersParam`, `buildTree`, `sideBySide`, `setTopLevelKey`, `appendOutput` |
| `src/markdown.ts` | Markdown mínimo para la Guía | `parseMarkdown`, `parseInline`, `safeHref` |
| `src/rendersApi.ts`, `src/rendersTypes.ts` | Cliente y tipos de `/api/renders` | `rendersApi` |
| `src/styles.css` | Estilos (variables `--bg`, `--panel`, `--accent`…) | — |
| `src/*.test.ts` | Tests de la lógica pura (`node --test`) | — |

`src/components/`:

| Componente | Qué es |
|---|---|
| `Sidebar.tsx` | Lista de sitios/equipos/clústeres/templates y búsqueda |
| `TopologyGraph.tsx`, `DeviceNode.tsx`, `ClusterGroup.tsx`, `RefitOnResize.tsx` | Grafo (React Flow) y sus nodos |
| `DetailPanel.tsx` | Detalle de la selección (equipo, clúster, template, sitio) |
| `LinkDetail.tsx` | Detalle de una conexión |
| `TemplateExplorer.tsx`, `ValuesTree.tsx`, `ValuesDiff.tsx` | Explorador de cluster-templates y values |
| `CompareControl.tsx` | Control único de Comparar en la barra (base → head, Local/rama/tag/commit, SHA validado) |
| `CompareSidebar.tsx`, `CompareValues.tsx` | Lista e IPAM en Comparar; diff de values |
| `FilesCompare.tsx` | Archivos que cambian base → head con diff unificado/lado a lado (pestaña Archivos, Renders) |
| `IndexBanner.tsx` | Aviso «Índice desactualizado» (cuenta los guardados sin commit) y botón Regenerar índice |
| `ChangesControl.tsx` | Botón **Cambios (N)** de la barra (solo edición) y panel de commit: `git status` por área, diff, Descartar, selección, mensaje, rama `wizard/…`, Comparar base → rama |
| `FindingsPanel.tsx` | Campana y panel de hallazgos (con arreglo por wizard) |
| `WizardsView.tsx` | Wizards de equipo y sitio (Guardar en el working tree, aviso de cambios sin commit) |
| `RepoSwitcher.tsx`, `RepoUnavailable.tsx`, `Onboarding.tsx` | Multi-repo y bienvenida |
| `Splitter.tsx` | Divisores arrastrables | 
| `renders/RendersView.tsx` | Sección Renders: sub-pestañas; datos = head de Comparar |
| `renders/RendersSummaryTab.tsx` | Resumen, instancias, destino, `renders.yaml` como texto |
| `renders/RendersTemplatesTab.tsx` | Biblioteca, editor, preview en vivo, contexto |
| `renders/SaveOutputDialog.tsx` | «Guardar en el repo» desde la preview: destino, diff por archivo, crear instancia, escritura en el working tree, resultado con Abrir Cambios / Comparar |
| `renders/RendersDiffTab.tsx` | Diff contra el destino y aplicar; con Comparar: destino (base) vs render (head), salida base → head, templates |
| `renders/RendersGuideTab.tsx` | Guías dentro de la UI |
| `renders/common.tsx` | Editor con números de línea, diffs, árbol, diálogo de escritura (working tree, aviso de pendientes, re-preview ante 409) |

## Otros directorios

| Ruta | Qué es |
|---|---|
| `templates/sites/` | Plantillas de sitio (edge, cloud) del wizard «Nuevo sitio» |
| `templates/cluster-templates/` | Biblioteca de cluster-templates (fallback) |
| `templates/manifests/` | Templates de render incluidos (solo lectura): `netplan-node`, `netplan-per-interface`, `multus-nad`, `metallb-pools`, `core-namespaces` |
| `schemas/` | JSON Schema generados (`isot schemas --write`); `render-*.schema.json`, `renders.schema.json` |
| `examples/demo-repo/` | Inventario ficticio (base de `isot demo` y de los tests), con `renders.yaml` y `manifest-templates/chrony-conf` |
| `examples/messy-source/` | Ejemplo de ingesta: `source/` desordenado → `expected-repo/` |
| `scripts/serve.sh` | Instala, construye el frontend y sirve en 127.0.0.1:8000 (`--edit`, `--config`, `--repo`) |
| `scripts/dev.sh` | API :8000 + Vite :5173 con recarga |
| `scripts/demo-diff-example.sh` | Rama `demo/diff-example` en un repo demo |
| `tests/` | pytest (`conftest.py` con `repo_root` = demo); un archivo por área (`test_api`, `test_rules`, `test_wizard`, `test_renders`, `test_multi_repo`…) |
| `.github/workflows/ci.yml` | CI de este repo: validator/indexer/schemas/ingest sobre los ejemplos, leakcheck, pytest, npm test/build |
| `.github/workflows/validate-data-repo.yml` | Workflow reutilizable para repos de datos (solo `contents: read`) |
| `.github/actions/validate/` | Acción compuesta equivalente |
| `docs/` | `rules.md`, `renders/GUIDE.md`, `llm/INGEST.md`, `llm/RENDERS.md`, `dev/`, `images/` (capturas del README) |
