import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { wizardCanCommit } from "./findings.ts";
import type { WizardPreview } from "./types.ts";

function preview(added: Array<{ severity: "error" | "warning" | "info" }>): WizardPreview {
  return {
    findings: {
      added: added.map((item, index) => ({
        id: "K8S-CP-001",
        severity: item.severity,
        entity_kind: "cluster",
        entity_id: "site2/site2-edge",
        site: "site2",
        cluster: "site2-edge",
        message: `n${index}`,
        recommendation: "x",
        path: null,
        graph_id: "cluster:site2:site2-edge",
      })),
      resolved: [],
      current: [],
      blocked: added.some((item) => item.severity === "error"),
    },
  };
}

describe("wizardCanCommit", () => {
  it("bloquea si hay errores nuevos", () => {
    const result = wizardCanCommit(preview([{ severity: "error" }]), true);
    assert.equal(result.ok, false);
    assert.equal(result.reason, "error");
  });

  it("exige checkbox si hay warnings nuevos", () => {
    const pending = wizardCanCommit(preview([{ severity: "warning" }]), false);
    assert.equal(pending.ok, false);
    assert.equal(pending.reason, "warning");
    const accepted = wizardCanCommit(preview([{ severity: "warning" }]), true);
    assert.equal(accepted.ok, true);
  });

  it("permite confirmar sin hallazgos nuevos", () => {
    assert.equal(wizardCanCommit(preview([]), false).ok, true);
  });
});
