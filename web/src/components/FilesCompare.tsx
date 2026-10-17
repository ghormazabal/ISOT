import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { refLabel, type CompareRefs } from "../compare";
import type { CompareFileEntry, CompareFilePayload, CompareFilesPayload } from "../types";
import { CopyButton, FileList, SideBySideDiff, StatusBadge, UnifiedDiff, useWrap } from "./renders/common";

type Props = {
  refs: CompareRefs;
  /** Carpetas/archivos a los que se limita la lista (p.ej. manifest-templates/). */
  prefix?: string[];
  /** Archivo a abrir (p.ej. el YAML de la entidad elegida en el grafo). */
  focusPath?: string | null;
  title?: string;
  className?: string;
};

/** Archivos que cambian de base a head, con diff unificado o lado a lado (solo lectura). */
export function FilesCompare({ refs, prefix, focusPath, title = "Archivos", className = "" }: Props) {
  const [list, setList] = useState<CompareFilesPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(focusPath ?? null);
  const [file, setFile] = useState<CompareFilePayload | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const [wrap, setWrap] = useWrap();
  const [query, setQuery] = useState("");
  const [onlyYaml, setOnlyYaml] = useState(false);
  const prefixKey = (prefix ?? []).join("|");

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setList(null);
    api
      .compareFiles(refs.base, refs.head, prefix)
      .then((payload) => !cancelled && setList(payload))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [refs.base, refs.head, prefixKey]);

  useEffect(() => {
    if (focusPath) setSelected(focusPath);
  }, [focusPath]);

  const files: CompareFileEntry[] = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (list?.files ?? []).filter((item) => (!onlyYaml || item.kind === "yaml") && (!needle || item.path.toLowerCase().includes(needle)));
  }, [list, query, onlyYaml]);

  useEffect(() => {
    if (!list) return;
    if (selected && (list.files.some((item) => item.path === selected) || selected === focusPath)) return;
    setSelected(files[0]?.path ?? null);
  }, [list, files]);

  useEffect(() => {
    if (!selected) {
      setFile(null);
      return;
    }
    let cancelled = false;
    setFileError(null);
    api
      .compareFile(refs.base, refs.head, selected)
      .then((payload) => !cancelled && setFile(payload))
      .catch((err: Error) => {
        if (!cancelled) {
          setFile(null);
          setFileError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refs.base, refs.head, selected]);

  const baseLabel = `base · ${refLabel(refs.base)}`;
  const headLabel = `head · ${refLabel(refs.head)}`;

  return (
    <section className={`files-compare ${className}`} aria-label={title}>
      <aside className="files-compare-list">
        <header>
          <strong>{title}</strong>
          {list ? (
            <span className="muted">
              {" "}
              <span className="render-status is-added">+{list.counts.added}</span> <span className="render-status is-removed">−{list.counts.removed}</span>{" "}
              <span className="render-status is-modified">~{list.counts.modified}</span>
            </span>
          ) : null}
        </header>
        <input className="files-compare-search" value={query} placeholder="Filtrar rutas…" onChange={(event) => setQuery(event.target.value)} />
        <label className="checkbox">
          <input type="checkbox" checked={onlyYaml} onChange={(event) => setOnlyYaml(event.target.checked)} />
          <span>solo YAML</span>
        </label>
        {error ? <p className="error">{error}</p> : null}
        {!list && !error ? <p className="muted">Comparando…</p> : null}
        {list ? <FileList items={files} selected={selected} onSelect={(item) => setSelected(item.path)} badge={(item) => <StatusBadge status={item.status} />} filterMin={Number.POSITIVE_INFINITY} className="is-tall" /> : null}
        {list?.truncated ? <p className="muted">Lista truncada: filtra por carpeta.</p> : null}
      </aside>
      <div className={`files-compare-view${wrap ? " is-wrap" : ""}`}>
        <div className="render-diff-controls">
          {file ? (
            <>
              <StatusBadge status={file.status} /> <code>{file.path}</code>
            </>
          ) : (
            <span className="muted">{selected ? "Cargando…" : "Elige un archivo."}</span>
          )}
          <nav className="view-tabs">
            <button type="button" className={mode === "unified" ? "is-active" : ""} onClick={() => setMode("unified")}>
              Unificado
            </button>
            <button type="button" className={mode === "split" ? "is-active" : ""} onClick={() => setMode("split")}>
              Lado a lado
            </button>
          </nav>
          <label className="checkbox viewer-wrap" title="Ajustar las líneas largas al ancho">
            <input type="checkbox" checked={wrap} onChange={(event) => setWrap(event.target.checked)} />
            <span>ajustar líneas</span>
          </label>
          {file && !file.binary ? <CopyButton text={file.after ?? file.before ?? ""} /> : null}
        </div>
        {fileError ? <p className="error">{fileError}</p> : null}
        {file?.binary ? <p className="muted">Archivo binario: no se muestra el contenido.</p> : null}
        {file && !file.binary ? (
          mode === "unified" ? (
            <UnifiedDiff diff={file.diff} />
          ) : (
            <SideBySideDiff before={file.before} after={file.after} leftLabel={baseLabel} rightLabel={headLabel} />
          )
        ) : null}
      </div>
    </section>
  );
}
