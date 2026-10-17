import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { defaultCompare, isLocalRef, LOCAL_LABEL, LOCAL_REF, normalizeRef, refLabel, swapRefs, type CompareRefs } from "../compare";
import type { GitRef, RefMeta, RefsPayload } from "../types";

type Side = "base" | "head";

type Props = {
  refs: RefsPayload | null;
  value: CompareRefs | null;
  resolved?: { base?: RefMeta | null; head?: RefMeta | null };
  disabled?: boolean;
  onChange: (value: CompareRefs | null) => void;
};

/**
 * Control único de «Comparar» (barra superior): base → head, donde cada lado es
 * Local (working tree), una rama, un tag o un commit (lista reciente o SHA a mano,
 * validado por el backend con git rev-parse --verify). Compacto; se despliega al pulsar.
 */
export function CompareControl({ refs, value, resolved, disabled, onChange }: Props) {
  const [open, setOpen] = useState<Side | null>(null);
  const root = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(null);
    };
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(null);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!value) {
    return (
      <nav className="view-tabs">
        <button type="button" id="compare-toggle" disabled={disabled} onClick={() => onChange(defaultCompare(refs))} title="Comparar Local (working tree), ramas y commits de este repo">
          Comparar
        </button>
      </nav>
    );
  }

  const side = (which: Side) => {
    const ref = value[which];
    const meta = resolved?.[which];
    const sha = meta && !meta.local ? meta.sha : null;
    return (
      <button
        type="button"
        id={`compare-${which}`}
        className={`compare-ref${open === which ? " is-open" : ""}${isLocalRef(ref) ? " is-local" : ""}`}
        aria-expanded={open === which}
        title={`${which === "base" ? "Base" : "Head"}: ${isLocalRef(ref) ? LOCAL_LABEL : ref}${sha ? ` (${sha.slice(0, 12)})` : ""}${meta?.subject ? ` · ${meta.subject}` : ""}`}
        onClick={() => setOpen(open === which ? null : which)}
      >
        <span className="compare-side">{which}</span>
        <span className="compare-name">{refLabel(ref)}</span>
        {sha && !/^[0-9a-f]{7,40}$/i.test(ref) ? <span className="compare-sha mono">{sha.slice(0, 7)}</span> : null}
        {meta?.local && meta.dirty ? <span className="compare-dirty" title="archivos con cambios sin commit">{meta.dirty}✎</span> : null}
        <span aria-hidden="true">▾</span>
      </button>
    );
  };

  return (
    <div className="compare-control" ref={root}>
      <nav className="view-tabs">
        <button type="button" id="compare-toggle" className="is-active" aria-pressed="true" onClick={() => onChange(null)} title="Salir de Comparar">
          Comparar
        </button>
      </nav>
      {side("base")}
      <span className="compare-arrow" aria-hidden="true">→</span>
      {side("head")}
      <button type="button" className="link-button compare-swap" title="Intercambiar base y head" onClick={() => onChange(swapRefs(value))}>
        ⇄
      </button>
      {open ? (
        <RefMenu
          side={open}
          refs={refs}
          current={value[open]}
          onPick={(ref) => {
            onChange({ ...value, [open]: ref });
            setOpen(null);
          }}
        />
      ) : null}
    </div>
  );
}

function RefMenu({ side, refs, current, onPick }: { side: Side; refs: RefsPayload | null; current: string; onPick: (ref: string) => void }) {
  const [text, setText] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needle = text.trim().toLowerCase();
  const match = (item: GitRef) => !needle || [item.name, item.sha, item.short, item.subject, item.author].filter(Boolean).join(" ").toLowerCase().includes(needle);
  const branches = useMemo(() => (refs?.branches ?? []).filter(match), [refs, needle]);
  const tags = useMemo(() => (refs?.tags ?? []).filter(match), [refs, needle]);
  const commits = useMemo(() => (refs?.commits ?? []).filter(match).slice(0, 30), [refs, needle]);

  const submit = async () => {
    const ref = normalizeRef(text);
    if (!ref) {
      setError("Ref inválida: usa local, una rama, un tag o un SHA (corto o completo).");
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const meta = await api.resolveRef(ref);
      onPick(meta.local ? LOCAL_REF : ref);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  };

  const item = (ref: string, label: string, detail?: string, key = ref) => (
    <li key={key}>
      <button type="button" className={`compare-option${current === ref ? " is-active" : ""}`} onClick={() => onPick(ref)}>
        <span className="mono">{label}</span>
        {detail ? <span className="muted"> {detail}</span> : null}
      </button>
    </li>
  );

  return (
    <div className={`compare-menu is-${side}`} role="dialog" aria-label={`Elegir ${side}`}>
      <form
        className="compare-menu-search"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <input
          id={`compare-${side}-input`}
          autoFocus
          value={text}
          placeholder="Buscar o pegar SHA / rama / tag…"
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => {
            setText(event.target.value);
            setError(null);
          }}
        />
        <button type="submit" disabled={!text.trim() || checking}>
          {checking ? "…" : "Usar"}
        </button>
      </form>
      {error ? <p className="error compare-menu-error">{error}</p> : null}
      <ul className="compare-menu-list">
        {!needle || "local working tree".includes(needle) ? item(LOCAL_REF, LOCAL_LABEL, "cambios sin commit incluidos") : null}
        {branches.length ? <li className="compare-menu-group">Ramas</li> : null}
        {branches.map((b) => item(b.name, b.name, b.remote ? "remota" : undefined, `b:${b.name}`))}
        {tags.length ? <li className="compare-menu-group">Tags</li> : null}
        {tags.map((t) => item(t.name, t.name, undefined, `t:${t.name}`))}
        {commits.length ? <li className="compare-menu-group">Commits recientes</li> : null}
        {commits.map((c) => item(c.sha, c.short || c.sha.slice(0, 7), `${c.subject ?? ""}${c.date ? ` · ${c.date.slice(0, 10)}` : ""}`, `c:${c.sha}`))}
      </ul>
    </div>
  );
}
