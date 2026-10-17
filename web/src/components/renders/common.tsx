import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { buildTree, classifyDiffLine, FILE_FILTER_MIN, filterByPath, sideBySide, splitPath, type TreeNode } from "../../renders";
import type { RenderError, RenderWarning, WritePreview, WriteResult } from "../../rendersTypes";

/** Permisos de escritura de la vista actual (edición + rama que se continúa). */
export type WriteMode = {
  canWrite: boolean;
  /** Rama `wizard/…` que se continúa (la ref que se está viendo) o null = rama nueva. */
  branch: string | null;
  reason: string;
};

export const STATUS_LABEL: Record<string, string> = {
  new: "nuevo",
  modified: "modificado",
  unchanged: "igual",
  orphaned: "huérfano",
  deleted: "se borra",
  added: "añadido",
  removed: "eliminado",
  ok: "ok",
  error: "error",
  disabled: "deshabilitado",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`render-status is-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function ErrorList({ errors, warnings, onJump }: { errors: RenderError[]; warnings?: RenderWarning[]; onJump?: (file: string, line: number) => void }) {
  if (!errors.length && !warnings?.length) return null;
  return (
    <ul className="render-errors">
      {errors.map((error, index) => (
        <li key={`e${index}`} className="is-error">
          <strong>{error.kind}</strong>
          {error.file ? (
            <button type="button" className="link-button" disabled={!onJump || !error.line} onClick={() => error.file && error.line && onJump?.(error.file, error.line)}>
              {error.file}
              {error.line ? `:${error.line}` : ""}
            </button>
          ) : null}
          {error.item ? <span className="muted"> [{error.item}]</span> : null}
          <span> {error.message}</span>
        </li>
      ))}
      {(warnings ?? []).map((warning, index) => (
        <li key={`w${index}`} className="is-warning">
          <strong>{warning.kind}</strong> <span className="muted">{warning.file}{warning.line ? `:${warning.line}` : ""}</span> {warning.message}
        </li>
      ))}
    </ul>
  );
}

/** Editor de texto con números de línea; resalta las líneas con error. */
export function CodeEditor({
  value,
  onChange,
  readOnly,
  errorLines,
  jumpTo,
  label,
}: {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  errorLines?: Set<number>;
  jumpTo?: { line: number; key: number } | null;
  label: string;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => Math.max(1, value.split("\n").length), [value]);

  useEffect(() => {
    if (!jumpTo || !area.current) return;
    const text = area.current.value;
    let offset = 0;
    for (let i = 1; i < jumpTo.line; i += 1) {
      const next = text.indexOf("\n", offset);
      if (next < 0) break;
      offset = next + 1;
    }
    const end = text.indexOf("\n", offset);
    area.current.focus();
    area.current.setSelectionRange(offset, end < 0 ? text.length : end);
    const lineHeight = 18;
    area.current.scrollTop = Math.max(0, (jumpTo.line - 4) * lineHeight);
  }, [jumpTo]);

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Tab" || readOnly || !onChange) return;
    event.preventDefault();
    const target = event.currentTarget;
    const start = target.selectionStart;
    const next = `${value.slice(0, start)}  ${value.slice(target.selectionEnd)}`;
    onChange(next);
    requestAnimationFrame(() => target.setSelectionRange(start + 2, start + 2));
  };

  return (
    <div className="code-editor">
      <div className="code-gutter" ref={gutter} aria-hidden>
        {Array.from({ length: lines }, (_, index) => (
          <div key={index} className={errorLines?.has(index + 1) ? "is-error" : undefined}>
            {index + 1}
          </div>
        ))}
      </div>
      <textarea
        ref={area}
        aria-label={label}
        spellCheck={false}
        value={value}
        readOnly={readOnly}
        onChange={(event) => onChange?.(event.target.value)}
        onKeyDown={onKeyDown}
        onScroll={(event) => {
          if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
        }}
      />
    </div>
  );
}

export function UnifiedDiff({ diff }: { diff: string }) {
  if (!diff) return <p className="muted">Sin diferencias.</p>;
  return (
    <pre className="render-diff">
      {diff.replace(/\n$/, "").split("\n").map((line, index) => (
        <span key={index} className={`diff-${classifyDiffLine(line)}`}>
          {line}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

export function SideBySideDiff({
  before,
  after,
  leftLabel = "Destino (actual)",
  rightLabel = "Render (nuevo)",
}: {
  before: string | null;
  after: string | null;
  leftLabel?: string;
  rightLabel?: string;
}) {
  const rows = useMemo(() => sideBySide(before, after), [before, after]);
  let left = 0;
  let right = 0;
  return (
    <div className="render-sbs" role="table">
      <div className="render-sbs-head" role="row">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
      {rows.map((row, index) => {
        if (row.left !== null) left += 1;
        if (row.right !== null) right += 1;
        return (
          <div key={index} className={`render-sbs-row is-${row.kind}`} role="row">
            <span className="ln">{row.left !== null ? left : ""}</span>
            <code className="l">{row.left ?? ""}</code>
            <span className="ln">{row.right !== null ? right : ""}</span>
            <code className="r">{row.right ?? ""}</code>
          </div>
        );
      })}
    </div>
  );
}

export function FileTree<T extends { path: string }>({
  items,
  selected,
  onSelect,
  badge,
}: {
  items: T[];
  selected: string | null;
  onSelect: (item: T) => void;
  badge?: (item: T) => ReactNode;
}) {
  const tree = useMemo(() => buildTree(items), [items]);
  const renderNodes = (nodes: TreeNode<T>[], depth: number): ReactNode =>
    nodes.map((node) =>
      node.item ? (
        <li key={node.path}>
          <button
            type="button"
            className={`file-tree-item${selected === node.path ? " is-active" : ""}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => node.item && onSelect(node.item)}
            title={node.path}
          >
            <span className="file-name">{node.name}</span>
            {badge ? badge(node.item) : null}
          </button>
        </li>
      ) : (
        <li key={`d:${node.path}`}>
          <div className="file-tree-dir" style={{ paddingLeft: 8 + depth * 14 }}>
            {node.name}/
          </div>
          <ul>{renderNodes(node.children, depth + 1)}</ul>
        </li>
      ),
    );
  if (!items.length) return <p className="muted">Sin archivos.</p>;
  return <ul className="file-tree">{renderNodes(tree, 0)}</ul>;
}

export type WriteFlow = {
  title: string;
  description?: string;
  preview: () => Promise<WritePreview>;
  commit: () => Promise<WriteResult>;
};

/** Diálogo «ver cambios → confirmar → commit en rama». Nunca escribe sin confirmar. */
export function WriteDialog({
  flow,
  onClose,
  onDone,
  onViewBranch,
}: {
  flow: WriteFlow;
  onClose: () => void;
  onDone?: (result: WriteResult) => void;
  onViewBranch?: (branch: string) => void;
}) {
  const [preview, setPreview] = useState<WritePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WriteResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    flow
      .preview()
      .then((value) => {
        if (cancelled) return;
        setPreview(value);
        setOpen(value.files[0]?.path ?? null);
      })
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [flow]);

  const errors = (preview?.errors ?? []).map((item) => (typeof item === "string" ? item : `${item.file ?? ""}${item.line ? `:${item.line}` : ""} ${item.message}`));
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const value = await flow.commit();
      setResult(value);
      onDone?.(value);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="render-modal" role="dialog" aria-modal="true" aria-label={flow.title}>
      <div className="render-modal-box">
        <header>
          <h3>{flow.title}</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>
        {flow.description ? <p className="muted">{flow.description}</p> : null}
        {!preview && !error ? <p className="muted">Calculando cambios…</p> : null}
        {error ? <p className="error">{error}</p> : null}
        {errors.length ? (
          <ul className="render-errors">
            {errors.map((item, index) => (
              <li key={index} className="is-error">
                {item}
              </li>
            ))}
          </ul>
        ) : null}
        {preview && !result ? (
          <>
            {preview.files.length ? (
              <p className="muted">
                {preview.files.length} archivo(s) cambian. Se hará commit en una rama <code>wizard/…</code> de este mismo
                repo de datos; nunca en main ni en otro repositorio.
              </p>
            ) : (
              <p className="muted">No hay cambios que escribir.</p>
            )}
            <div className="render-modal-files">
              {preview.files.map((file) => (
                <details key={file.path} open={open === file.path} onToggle={(event) => event.currentTarget.open && setOpen(file.path)}>
                  <summary>
                    <StatusBadge status={file.status} /> <code>{file.path}</code>
                  </summary>
                  <UnifiedDiff diff={file.diff} />
                </details>
              ))}
            </div>
          </>
        ) : null}
        {result ? (
          <div className="render-result">
            <p>
              ✔ Commit <code>{result.short}</code> en la rama <code>{result.branch}</code>
              {result.created ? " (nueva)" : " (continuada)"}.
            </p>
            {result.push ? <p className={result.push.ok ? "muted" : "error"}>Push: {result.push.message}</p> : null}
            <p className="muted">
              Revisa la rama y ábrela como PR/MR desde tu flujo Git habitual. main no se toca.
            </p>
          </div>
        ) : null}
        <div className="wizard-actions">
          {result ? (
            <>
              {onViewBranch ? (
                <button type="button" className="primary" onClick={() => onViewBranch(result.branch)}>
                  Ver rama {result.branch}
                </button>
              ) : null}
              <button type="button" onClick={onClose}>
                Cerrar
              </button>
            </>
          ) : (
            <>
              <button type="button" className="primary" disabled={busy || !preview || !preview.files.length || errors.length > 0} onClick={confirm}>
                {busy ? "Guardando…" : "Confirmar y hacer commit"}
              </button>
              <button type="button" onClick={onClose}>
                Cancelar
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Lista compacta de archivos (ruta relativa: carpeta atenuada + nombre), con filtro
 * si hay muchos y altura limitada con scroll propio. Se apila sobre el visor.
 */
export function FileList<T extends { path: string }>({
  items,
  selected,
  onSelect,
  badge,
  filterMin = FILE_FILTER_MIN,
  className = "",
  id,
}: {
  items: T[];
  selected: string | null;
  onSelect: (item: T) => void;
  badge?: (item: T) => ReactNode;
  filterMin?: number;
  className?: string;
  id?: string;
}) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => filterByPath(items, query), [items, query]);
  const active = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    active.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  if (!items.length) return <p className="muted">Sin archivos.</p>;
  return (
    <div className={`file-list-box ${className}`} id={id}>
      {items.length > filterMin ? (
        <div className="file-list-filter">
          <input value={query} placeholder={`Filtrar ${items.length} archivos…`} spellCheck={false} onChange={(event) => setQuery(event.target.value)} aria-label="Filtrar archivos" />
          <span className="muted">{query ? `${shown.length}/${items.length}` : items.length}</span>
        </div>
      ) : null}
      <ul className="file-list" role="listbox">
        {shown.map((item) => {
          const { dir, name } = splitPath(item.path);
          const isActive = item.path === selected;
          return (
            <li key={item.path}>
              <button
                type="button"
                role="option"
                aria-selected={isActive}
                ref={isActive ? active : undefined}
                className={`file-list-item${isActive ? " is-active" : ""}`}
                title={item.path}
                onClick={() => onSelect(item)}
              >
                <span className="file-path">
                  {dir ? <span className="file-dir">{dir}</span> : null}
                  <span className="file-base">{name}</span>
                </span>
                {badge ? badge(item) : null}
              </button>
            </li>
          );
        })}
        {!shown.length ? <li className="muted file-list-empty">Ningún archivo coincide.</li> : null}
      </ul>
    </div>
  );
}

const WRAP_KEY = "isot.renders.wrap";

/** Ajuste de líneas largas, recordado en localStorage. */
export function useWrap(): [boolean, (value: boolean) => void] {
  const [wrap, setWrap] = useState<boolean>(() => {
    try {
      return localStorage.getItem(WRAP_KEY) === "1";
    } catch {
      return false;
    }
  });
  const update = (value: boolean) => {
    setWrap(value);
    try {
      localStorage.setItem(WRAP_KEY, value ? "1" : "0");
    } catch {
      /* sin almacenamiento */
    }
  };
  return [wrap, update];
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

export function CopyButton({ text, label = "Copiar" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "error">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1500);
    return () => window.clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className="viewer-button"
      title="Copiar el contenido al portapapeles"
      onClick={() => {
        copyText(text).then(
          () => setState("ok"),
          () => setState("error"),
        );
      }}
    >
      {state === "ok" ? "✔ copiado" : state === "error" ? "no se pudo copiar" : label}
    </button>
  );
}

/** Barra del visor: ruta, controles propios (p.ej. Unificado/Lado a lado), ajustar líneas y copiar. */
export function ViewerBar({
  path,
  wrap,
  onWrap,
  copy,
  before,
  children,
}: {
  path: string | null;
  wrap: boolean;
  onWrap: (value: boolean) => void;
  copy?: string | null;
  before?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="viewer-bar">
      {before}
      <code className="viewer-path" title={path ?? ""}>
        {path ?? ""}
      </code>
      <span className="viewer-actions">
        {children}
        <label className="checkbox viewer-wrap" title="Ajustar las líneas largas al ancho">
          <input type="checkbox" checked={wrap} onChange={(event) => onWrap(event.target.checked)} />
          <span>ajustar líneas</span>
        </label>
        {copy !== undefined && copy !== null ? <CopyButton text={copy} /> : null}
      </span>
    </div>
  );
}

/** Visor de un archivo de texto: ocupa el ancho y el alto restantes, con scroll propio. */
export function CodeViewer({ path, content, className = "" }: { path: string | null; content: string | null; className?: string }) {
  const [wrap, setWrap] = useWrap();
  return (
    <div className={`code-viewer ${wrap ? "is-wrap" : ""} ${className}`}>
      <ViewerBar path={path} wrap={wrap} onWrap={setWrap} copy={content ?? ""} />
      <pre className="render-output-content">{content ?? ""}</pre>
    </div>
  );
}
