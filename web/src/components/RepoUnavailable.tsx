import type { ConfigInfo } from "../types";
import { RepoSwitcher } from "./RepoSwitcher";

type Info = Extract<ConfigInfo, { configured: true }>;

type Props = {
  info: Info;
  checking?: boolean;
  onSwitch: (repoId: string) => void;
  onRetry: () => void;
};

/** El repositorio elegido existe en la configuración pero no se puede abrir. */
export function RepoUnavailable({ info, checking, onSwitch, onRetry }: Props) {
  const repo = info.data_repo;
  const others = info.repos.filter((item) => item.id !== repo.id && item.available);
  return (
    <main className="onboarding" id="repo-unavailable">
      <section className="onboarding-card">
        <div className="repo-unavailable-head">
          <p className="brand">{info.ui.title || "ISoT"}</p>
          <RepoSwitcher current={repo} repos={info.repos} activeId={info.active_repo} onSelect={onSwitch} />
        </div>
        <h1>El repositorio «{repo.name}» no está disponible</h1>
        <p className="onboarding-error" id="repo-unavailable-error">
          {repo.error ?? "No se pudo abrir el repositorio de datos."}
        </p>
        <ol className="onboarding-steps">
          <li>
            <strong>Clónalo en la ruta configurada</strong> (<code>repos[].path</code> del id <code>{repo.id}</code>) o
            corrige la ruta en <code>isot.yaml</code>.
          </li>
          <li>
            <strong>Si es nuevo,</strong> <code>isot init --scaffold RUTA</code> crea un repositorio de datos vacío con{" "}
            <code>git init</code>.
          </li>
          <li>
            <strong>Comprueba la configuración</strong> con <code>isot config</code>: lista cada repositorio y su estado.
          </li>
        </ol>
        <div className="repo-unavailable-actions">
          <button type="button" onClick={onRetry} disabled={checking} id="repo-unavailable-retry">
            {checking ? "Comprobando…" : "Reintentar"}
          </button>
          {others.map((item) => (
            <button type="button" key={item.id} onClick={() => onSwitch(item.id)} id={`repo-unavailable-go-${item.id}`}>
              Abrir «{item.name}»
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}
