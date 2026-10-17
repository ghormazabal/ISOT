import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { canFixWithWizard, fixSummary } from "./findings.ts";
import type { Finding, WizardPreview } from "./types.ts";
import { draftFromFinding, emptyWizardDraft, isDraftInProgress } from "./wizardDraft.ts";

const CP001: Finding = {
  id: "K8S-CP-001",
  severity: "warning",
  entity_kind: "cluster",
  entity_id: "site2/site2-edge",
  site: "site2",
  cluster: "site2-edge",
  message: "site2-edge tiene 2 control plane(s)",
  recommendation: "r",
  path: null,
  graph_id: "cluster:site2:site2-edge",
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

const VER003: Finding = {
  ...CP001,
  id: "K8S-VER-003",
  remediation: { kind: "open_entity", target: "template", site: "global", name: "rke2-x", label: "Abrir template rke2-x" },
};

function previewWithFix(resolved: boolean, also: string[] = []): WizardPreview {
  return {
    findings: {
      added: [],
      resolved: [],
      current: [],
      blocked: false,
      fix: { id: "K8S-CP-001", resolved, remaining: [], also_resolves: also },
    },
  };
}

describe("remediación", () => {
  it("solo add_device abre el wizard", () => {
    assert.equal(canFixWithWizard(CP001), true);
    assert.equal(canFixWithWizard(VER003), false);
  });

  it("draftFromFinding precarga el wizard Agregar device", () => {
    const draft = draftFromFinding(CP001, emptyWizardDraft());
    assert.ok(draft);
    assert.equal(draft.mode, "device");
    assert.equal(draft.device.site, "site2");
    assert.equal(draft.device.cluster, "site2-edge");
    assert.equal(draft.device.role, "edge-server");
    assert.equal(draft.device.kind, "baremetal");
    assert.equal(draft.device.count, 1);
    assert.equal(draft.device.fix?.finding.id, "K8S-CP-001");
    assert.equal(draft.device.preview, null);
    assert.equal(isDraftInProgress(draft), true);
    assert.equal(draftFromFinding(VER003, emptyWizardDraft()), null);
  });

  it("conserva el borrador de sitio al abrir una corrección", () => {
    const base = emptyWizardDraft();
    base.site.step = 3;
    const draft = draftFromFinding(CP001, base);
    assert.equal(draft?.site.step, 3);
  });

  it("fixSummary dice si el preview resuelve el hallazgo", () => {
    assert.deepEqual(fixSummary(previewWithFix(true, ["K8S-WK-001"])), {
      ok: true,
      text: "Resuelve K8S-CP-001 · también resuelve K8S-WK-001",
    });
    assert.equal(fixSummary(previewWithFix(false))?.ok, false);
    assert.equal(fixSummary(null), null);
  });
});
