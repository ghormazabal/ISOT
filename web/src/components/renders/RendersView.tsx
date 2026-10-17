import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import { RENDER_SUBTAB_LABELS, RENDER_SUBTABS, type RenderSubTab } from "../../renders";
import type { RefsPayload, SiteIndexEntry } from "../../types";
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
  onAfterCommit?: () => void;
};

/** Sección Renders: sub-pestañas Resumen · Plantillas · Diff · Guía (`?tab=renders&sub=…`). */
export function RendersView({ sub, onSub, sites, editEnabled, onAfterCommit }: Props) {
  const [ref, setRef] = useState<string>("");
  const [refs, setRefs] = useState<RefsPayload | null>(null);
  const [version, setVersion] = useState(0);
  const [focusTemplate, setFocusTemplate] = useState<{ id: string; source?: "repo" | "bundled"; key: number } | null>(null);
  const [focusInstance, setFocusInstance] = useState<string | null>(null);

  const loadRefs = useCallback(() => {
    api
      .refs()
      .then(setRefs)
      .catch(() => setRefs(null));
  }, []);
  useEffect(loadRefs, [loadRefs]);

  const branches = useMemo(() => (refs?.branches ?? []).filter((item) => !item.remote).map((item) => item.name), [refs]);

  const write: WriteMode = useMemo(() => {
    if (!editEnabled) return { canWrite: false, branch: null, reason: "Solo lectura: arranca ISoT con --edit (ISOT_EDIT=1) para escribir." };
    if (!ref) return { canWrite: true, branch: null, reason: "Los cambios se guardan en una rama wizard/… nueva." };
    if (ref.startsWith("wizard/")) return { canWrite: true, branch: ref, reason: `Los cambios continúan la rama ${ref}.` };
    return { canWrite: false, branch: null, reason: `Viendo ${ref}: solo se escribe sobre el working tree o una rama wizard/….` };
  }, [editEnabled, ref]);

  const afterCommit = useCallback(() => {
    setVersion((value) => value + 1);
    loadRefs();
    onAfterCommit?.();
  }, [loadRefs, onAfterCommit]);

  const viewBranch = useCallback(
    (branch: string) => {
      setRef(branch);
      setVersion((value) => value + 1);
      loadRefs();
    },
    [loadRefs],
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
          <label className="site-picker">
            <span>Datos</span>
            <select id="renders-ref" value={ref} onChange={(event) => setRef(event.target.value)}>
              <option value="">working tree</option>
              {branches.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
              {ref && !branches.includes(ref) ? <option value={ref}>{ref}</option> : null}
            </select>
          </label>
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
          version={version}
          branches={branches}
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
