import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { fixHeadline, shortRuleId, wizardStatus } from "./findings.ts";
import type { Finding, FixContext, WizardPreview } from "./types.ts";
import {
  DEFAULT_DOCK_WIDTH,
  MIN_DOCK_WIDTH,
  MIN_GRAPH_WIDTH,
  STORAGE_KEY,
  clampDockWidth,
  emptyWizardDraft,
  loadSession,
  saveSession,
  type StorageLike,
} from "./wizardDraft.ts";

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}

const FIX: FixContext = {
  finding: {
    id: "K8S-CP-001",
    severity: "warning",
    site: "site2",
    cluster: "site2-edge",
    entity_kind: "cluster",
    entity_id: "site2/site2-edge",
    message: "site2-edge tiene 2 control plane(s)",
  },
  remediation: {
    kind: "add_device",
    site: "site2",
    cluster: "site2-edge",
    role: "edge-server",
    device_kind: "baremetal",
    count: 1,
    reason: "site2-edge: 2 → 3 control planes",
    label: "Agregar 1 control plane",
  },
};

const WARN: Finding = {
  id: "K8S-CP-002",
  severity: "warning",
  entity_kind: "cluster",
  entity_id: "site2/site2-edge",
  site: "site2",
  cluster: "site2-edge",
  message: "m",
  recommendation: "r",
  path: null,
  graph_id: null,
};

function preview(fix: Partial<NonNullable<NonNullable<WizardPreview["findings"]>["fix"]>> | null, added: Finding[] = []): WizardPreview {
  return {
    findings: {
      added,
      fix: fix
        ? { id: "K8S-CP-001", resolved: true, remaining: [], also_resolves: [], ...fix }
        : null,
    },
  } as unknown as WizardPreview;
}

describe("barra de estado del wizard", () => {
  it("acorta ids y propuesta", () => {
    assert.equal(shortRuleId("K8S-CP-001"), "CP-001");
    assert.equal(shortRuleId("INV-IP-001"), "INV-IP-001");
    assert.equal(fixHeadline(FIX), "+1 control plane (2→3)");
  });

  it("resume corrección, resultado y avisos en una línea", () => {
    const status = wizardStatus(preview({ also_resolves: ["K8S-WK-001"] }, [WARN]), FIX);
    assert.ok(status);
    assert.equal(status.fix, "Corrigiendo K8S-CP-001 · site2-edge · Propuesta: +1 control plane (2→3)");
    assert.deepEqual(status.resolve, { ok: true, text: "✔ Resuelve CP-001, WK-001" });
    assert.equal(status.warnings, 1);
    assert.equal(status.errors, 0);
  });

  it("indica cuando la propuesta aún no resuelve", () => {
    const status = wizardStatus(preview({ resolved: false, also_resolves: ["K8S-WK-001"] }), FIX);
    assert.equal(status?.resolve?.ok, false);
    assert.equal(status?.resolve?.text, "… Aún no resuelve CP-001 (sí WK-001)");
  });

  it("sin corrección ni hallazgos nuevos no muestra barra", () => {
    assert.equal(wizardStatus(preview(null), null), null);
    assert.equal(wizardStatus(null, null), null);
    const onlyWarn = wizardStatus(preview(null, [WARN]), null);
    assert.equal(onlyWarn?.fix, null);
    assert.equal(onlyWarn?.warnings, 1);
  });
});

describe("layout del wizard", () => {
  it("acota el ancho del panel al escenario", () => {
    assert.equal(clampDockWidth(100, 1200), MIN_DOCK_WIDTH);
    assert.equal(clampDockWidth(5000, 1200), 1200 - MIN_GRAPH_WIDTH);
    assert.equal(clampDockWidth(500.4, 1200), 500);
    assert.equal(clampDockWidth(400, 300), MIN_DOCK_WIDTH);
  });

  it("borrador vacío: grafo visible y ancho por defecto", () => {
    const draft = emptyWizardDraft();
    assert.equal(draft.device.graphHidden, false);
    assert.equal(draft.site.dockWidth, DEFAULT_DOCK_WIDTH);
  });

  it("persiste grafo oculto y ancho del panel", () => {
    const storage = memoryStorage();
    const draft = emptyWizardDraft();
    draft.mode = "device";
    draft.device = { ...draft.device, graphHidden: true, dockWidth: 560 };
    assert.ok(saveSession({ v: 1, draft, lastBrowse: { view: "network", compare: false, siteFilter: "all" }, editing: true }, storage));
    const loaded = loadSession(storage);
    assert.equal(loaded?.draft.device.graphHidden, true);
    assert.equal(loaded?.draft.device.dockWidth, 560);
    assert.equal(loaded?.draft.site.graphHidden, false);
  });

  it("sesiones antiguas o corruptas usan valores por defecto", () => {
    const storage = memoryStorage();
    const draft = emptyWizardDraft();
    const raw = { v: 1, editing: true, draft: { mode: "device", device: { ...draft.device, graphHidden: undefined, dockWidth: "x" }, site: {} } };
    storage.setItem(STORAGE_KEY, JSON.stringify(raw));
    const loaded = loadSession(storage);
    assert.equal(loaded?.draft.device.graphHidden, false);
    assert.equal(loaded?.draft.device.dockWidth, DEFAULT_DOCK_WIDTH);
    assert.equal(loaded?.draft.site.dockWidth, DEFAULT_DOCK_WIDTH);
  });
});
