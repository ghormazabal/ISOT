import type { ConfigInfo } from "../types";

type Props = {
  info: Extract<ConfigInfo, { configured: false }> | null;
  error?: string | null;
  checking?: boolean;
  onRetry: () => void;
};

const EXAMPLE = `version: 1
data_repo:
  path: ~/repos/mi-inventario   # clon local de tu repositorio de datos
edit:
  enabled: true                 # wizards (ramas wizard/… en ese repo)
  push: false`;

/** Pantalla inicial cuando no hay repositorio de datos configurado. */
export function Onboarding({ info, error, checking, onRetry }: Props) {
  const problem = info?.error ?? error ?? null;
  return (
    <main className="onboarding" id="onboarding">
      <section className="onboarding-card">
        <p className="brand">ISoT</p>
        <h1>Configura el repositorio de datos</h1>
        <p className="onboarding-lead">
          {info?.message ??
            "No hay repositorio de datos configurado. Copia isot.example.yaml a isot.yaml y define data_repo.path, o ejecuta `isot init`."}
        </p>
        {problem ? (
          <p className="onboarding-error" id="onboarding-error">
            {problem}
          </p>
        ) : null}
        <p className="muted">
          ISoT visualiza y edita un repositorio que ya tienes clonado. No guarda credenciales: lee el clon con tus
          permisos y usa tu git (config y credential helper) para refs, diff y ramas del wizard.
        </p>
        <ol className="onboarding-steps">
          <li>
            <strong>Tengo un repositorio de datos.</strong> Crea <code>isot.yaml</code> junto a la herramienta:
            <pre>{EXAMPLE}</pre>
            o genera uno con <code>isot init --path ~/repos/mi-inventario</code>.
          </li>
          <li>
            <strong>Empiezo desde cero.</strong> <code>isot init --scaffold ~/repos/mi-inventario</code> crea un repo
            vacío (sites.yaml, sites/, cluster-templates/, una plantilla de sitio y <code>git init</code>) y su{" "}
            <code>isot.yaml</code>. El primer sitio se crea con el wizard «Nuevo sitio».
          </li>
          <li className="onboarding-demo" id="onboarding-demo">
            <strong>Ver demo.</strong> <code>isot demo</code> copia el inventario de ejemplo a un directorio temporal
            (con su propio git y la rama <code>demo/diff-example</code>) y lo sirve.
          </li>
        </ol>
        <p className="muted">
          Se busca la configuración en: {(info?.search ?? ["--config", "ISOT_CONFIG", "./isot.yaml", "~/.config/isot/config.yaml"]).join(" → ")}.
        </p>
        <button type="button" onClick={onRetry} disabled={checking} id="onboarding-retry">
          {checking ? "Comprobando…" : "Ya lo configuré: reintentar"}
        </button>
      </section>
    </main>
  );
}
