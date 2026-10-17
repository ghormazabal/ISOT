import { useCallback, useMemo, useState } from "react";

import { isLocalRef, refLabel, type CompareRefs } from "../../compare";
import { RENDER_SUBTAB_LABELS, RENDER_SUBTABS, type RenderSubTab } from "../../renders";
import type { SiteIndexEntry } from "../../types";
import type { WriteMode } from "./common";
import { RendersDiffTab } from "./RendersDiffTab";
import { RendersGuideTab } from "./RendersGuideTab";
import { RendersSummaryTab } from "./RendersSummaryTab";
import { RendersTemplatesTab } from "./RendersTemplatesTab";

type Props = {
  sub: RenderSubTab;
  onSub: (sub: RenderSubTab) => void;
  sites: SiteIndexEntry[];
  editEnabled: boolean;
  /** Comparar global: los datos que se ven son los de head; null = working tree sin comparar. */
  compare: CompareRefs | null;
  onCompare: (refs: CompareRefs) => void;
  onAfterCommit?: () => void;
};

/** Sección Renders: sub-pestañas Resumen · Plantillas · Diff · Guía (`?tab=renders&sub=…`). */
export function RendersView({ sub, onSub, sites, editEnabled, compare, onCompare, onAfterCommit }: Props) {
  // Una sola fuente de refs: el control Comparar de la barra superior (head = lo que se ve).
  const ref = compare && !isLocalRef(compare.head) ? compare.head : "";
  const [version, setVersion] = useState(0);
  const [focusTemplate, setFocusTemplate] = useState<{ id: string; source?: "repo" | "bundled"; key: number } | null>(null);
  const [focusInstance, setFocusInstance] = useState<string | null>(null);

  const write: WriteMode = useMemo(() => {
    if (!editEnabled) return { canWrite: false, branch: null, reason: "Solo lectura: arranca ISoT con --edit (ISOT_EDIT=1) para escribir." };
    if (!ref) return { canWrite: true, branch: null, reason: "Los cambios se guardan en una rama wizard/… nueva." };
    if (ref.startsWith("wizard/")) return { canWrite: true, branch: ref, reason: `Los cambios continúan la rama ${ref}.` };
    return { canWrite: false, branch: null, reason: `Viendo ${refLabel(ref)}: solo se escribe con head = Local o una rama wizard/….` };
  }, [editEnabled, ref]);

  const afterCommit = useCallback(() => {
    setVersion((value) => value + 1);
    onAfterCommit?.();
  }, [onAfterCommit]);

  // «Ver la rama»: head = la rama nueva, base = Local (qué añade la rama).
  const viewBranch = useCallback(
    (branch: string) => {
      onCompare({ base: "local", head: branch });
      setVersion((value) => value + 1);
      onAfterCommit?.();
    },
    [onCompare, onAfterCommit],
  );

  return (
    <section className="wizard-pane renders-pane" aria-label="Renders">
      <div className="renders-bar">
        <nav className="view-tabs renders-subtabs" aria-label="Secciones de Renders">
          {RENDER_SUBTABS.map((item) => (
            <button key={item} type="button" id={`renders-sub-${item}`} className={sub === item ? "is-active" : ""} aria-pressed={sub === item} onClick={() => onSub(item)}>
              {RENDER_SUBTAB_LABELS[item]}
            </button>
          ))}
        </nav>
        {sub !== "guia" ? (
          <span className="muted renders-ref" id="renders-ref" title="Cambia la ref con «Comparar» en la barra superior">
            Datos: <strong>{ref ? refLabel(ref) : "Local (working tree)"}</strong>
            {compare ? ` · base ${refLabel(compare.base)}` : ""}
          </span>
        ) : null}
        <p className={`badge${write.canWrite ? "" : " is-readonly"}`} title={write.reason}>
          {write.canWrite ? (write.branch ? `edición · ${write.branch}` : "edición · rama nueva") : "solo lectura"}
        </p>
      </div>
      {sub === "resumen" ? (
          <RendersSummaryTab
            refName={ref || null}
            version={version}
            sites={sites}
            write={write}
            onAfterCommit={afterCommit}
            onViewBranch={viewBranch}
            onOpenDiff={(instance) => {
              setFocusInstance(instance);
              onSub("diff");
            }}
            onOpenTemplate={(id, source) => {
              setFocusTemplate({ id, source, key: Date.now() });
              onSub("plantillas");
            }}
          />
      ) : null}
      {sub === "plantillas" ? (
        <RendersTemplatesTab
          onReview={(refs) => {
            onCompare(refs);
            setVersion((value) => value + 1);
            onSub("diff");
          }}
          refName={ref || null}
          version={version}
          sites={sites}
          write={write}
          focus={focusTemplate}
          onAfterCommit={afterCommit}
          onViewBranch={viewBranch}
        />
      ) : null}
      {sub === "diff" ? (
        <RendersDiffTab
          refName={ref || null}
          compare={compare}
          onCompare={onCompare}
          version={version}
          write={write}
          instance={focusInstance}
          onInstance={setFocusInstance}
          onAfterCommit={afterCommit}
        />
      ) : null}
      {sub === "guia" ? <RendersGuideTab /> : null}
    </section>
  );
}
