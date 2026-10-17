import { useEffect, useState } from "react";

import { api } from "../api";
import type { IndexStatus } from "../types";

type Props = {
  editEnabled: boolean;
  /** Cambia tras cada commit (wizard, renders, reindexar) para volver a mirar el estado. */
  refreshKey: number;
  onReindexed: () => void;
};

/**
 * Aviso de índice desactualizado: el backend ya sirve un índice recalculado al vuelo
 * (nunca datos viejos en silencio); con edición, «Reindexar» lo deja commiteado en una rama wizard/….
 */
export function IndexBanner({ editEnabled, refreshKey, onReindexed }: Props) {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hiddenKey, setHiddenKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .indexStatus()
      .then((value) => !cancelled && setStatus(value))
      .catch(() => !cancelled && setStatus(null));
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (message && status?.fresh !== false) {
    return (
      <p className="index-banner is-ok" role="status">
        {message}{" "}
        <button type="button" className="link-button" onClick={() => setMessage(null)}>
          ✕
        </button>
      </p>
    );
  }
  if (!status || status.fresh) return null;
  const key = `${status.missing.join(",")}|${status.stale.join(",")}`;
  if (hiddenKey === key) return null;

  const what = [...status.missing.map((item) => `falta ${item}`), ...status.stale.slice(0, 4)].join(" · ");
  const reindex = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.reindex();
      setMessage(result.branch ? `Índice regenerado y commiteado en ${result.branch}.` : (result.message ?? "El índice ya está al día."));
      onReindexed();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="index-banner" role="alert" id="index-banner">
      <strong>Índice desactualizado</strong>
      <span>
        {" "}
        — los archivos del repo cambiaron desde el último <code>isot index</code>; los datos se calculan al vuelo.
        {what ? <span className="muted"> ({what}{status.stale.length > 4 ? " …" : ""})</span> : null}
      </span>
      {editEnabled && status.can_reindex ? (
        <button type="button" className="primary" id="index-reindex" disabled={busy} onClick={() => void reindex()}>
          {busy ? "Reindexando…" : "Reindexar"}
        </button>
      ) : (
        <span className="muted"> Ejecuta <code>isot index</code> y commitea los índices.</span>
      )}
      {error ? <span className="error"> {error}</span> : null}
      <button type="button" className="link-button" title="Ocultar este aviso (vuelve si cambia el estado)" onClick={() => setHiddenKey(key)}>
        ✕
      </button>
    </div>
  );
}
