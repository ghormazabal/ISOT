import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  COMPARE_SIDE_SPEC,
  KEY_STEP,
  LAYOUT_STORAGE_KEY,
  MIN_MAIN_WIDTH,
  appPanelSpecs,
  clampSize,
  dragSize,
  explorerPanelSpecs,
  keyboardSize,
  layoutKey,
  loadLayout,
  saveLayout,
  storedSize,
  type PanelSpec,
  type StorageLike,
} from "./resize.ts";

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}

const SIDEBAR: PanelSpec = { defaultSize: 280, min: 180, max: 560, collapsible: true };
const DETAIL: PanelSpec = { defaultSize: 380, min: 260, max: 800 };

describe("clampSize", () => {
  it("acota al rango y redondea", () => {
    assert.equal(clampSize(100.4, DETAIL), 260);
    assert.equal(clampSize(9999, DETAIL), 800);
    assert.equal(clampSize(301.6, DETAIL), 302);
  });

  it("colapsa por debajo de la mitad del mínimo solo si el panel lo admite", () => {
    assert.equal(clampSize(80, SIDEBAR), 0);
    assert.equal(clampSize(95, SIDEBAR), 180);
    assert.equal(clampSize(0, DETAIL), 260);
  });

  it("si el máximo queda bajo el mínimo (viewport chico) gana el mínimo", () => {
    assert.equal(clampSize(500, { defaultSize: 300, min: 260, max: 120 }), 260);
  });

  it("valores inválidos vuelven al default", () => {
    assert.equal(clampSize(Number.NaN, DETAIL), 380);
  });
});

describe("dragSize", () => {
  it("handle a la derecha del panel: arrastrar a la derecha agranda", () => {
    assert.equal(dragSize(280, 300, 380, 1, SIDEBAR), 360);
  });

  it("handle a la izquierda del panel: arrastrar a la izquierda agranda", () => {
    assert.equal(dragSize(380, 900, 800, -1, DETAIL), 480);
    assert.equal(dragSize(380, 900, 1000, -1, DETAIL), 280);
  });

  it("arrastrar muy a la izquierda colapsa el menú y volver lo expande", () => {
    assert.equal(dragSize(280, 280, 0, 1, SIDEBAR), 0);
    assert.equal(dragSize(0, 0, 60, 1, SIDEBAR), 0);
    assert.equal(dragSize(0, 0, 120, 1, SIDEBAR), 180);
  });

  it("en % convierte con el ancho del contenedor", () => {
    assert.equal(dragSize(50, 100, 200, 1, COMPARE_SIDE_SPEC, 1000), 60);
    assert.equal(dragSize(50, 100, 2000, 1, COMPARE_SIDE_SPEC, 1000), 80);
    assert.equal(dragSize(50, 100, 200, 1, COMPARE_SIDE_SPEC, 0), 50);
  });
});

describe("keyboardSize", () => {
  it("flechas según orientación y lado del panel", () => {
    assert.equal(keyboardSize(280, "ArrowRight", SIDEBAR, "vertical", 1), 280 + KEY_STEP);
    assert.equal(keyboardSize(280, "ArrowLeft", SIDEBAR, "vertical", 1), 280 - KEY_STEP);
    assert.equal(keyboardSize(380, "ArrowLeft", DETAIL, "vertical", -1), 380 + KEY_STEP);
    assert.equal(keyboardSize(300, "ArrowDown", DETAIL, "horizontal", 1), 300 + KEY_STEP);
    assert.equal(keyboardSize(300, "ArrowUp", DETAIL, "horizontal", 1), 300 - KEY_STEP);
    assert.equal(keyboardSize(300, "ArrowRight", DETAIL, "vertical", 1, true), 300 + KEY_STEP * 4);
  });

  it("no baja del mínimo; en el mínimo colapsa si el panel lo admite", () => {
    assert.equal(keyboardSize(265, "ArrowLeft", DETAIL, "vertical", 1), 260);
    assert.equal(keyboardSize(260, "ArrowLeft", DETAIL, "vertical", 1), 260);
    assert.equal(keyboardSize(180, "ArrowLeft", SIDEBAR, "vertical", 1), 0);
    assert.equal(keyboardSize(0, "ArrowRight", SIDEBAR, "vertical", 1), 180);
  });

  it("Home / End / Enter y teclas ajenas", () => {
    assert.equal(keyboardSize(300, "Home", DETAIL, "vertical", 1), 260);
    assert.equal(keyboardSize(300, "End", DETAIL, "vertical", 1), 800);
    assert.equal(keyboardSize(300, "Enter", SIDEBAR, "vertical", 1), 0);
    assert.equal(keyboardSize(0, "Enter", SIDEBAR, "vertical", 1), 280);
    assert.equal(keyboardSize(300, "a", DETAIL, "vertical", 1), null);
  });
});

describe("persistencia del layout", () => {
  it("guarda y lee tamaños por clave", () => {
    const storage = memoryStorage();
    saveLayout(storage, { "network.sidebar": 320, "explorer.graph": 200 });
    assert.deepEqual(loadLayout(storage), { "network.sidebar": 320, "explorer.graph": 200 });
    assert.ok(storage.data.has(LAYOUT_STORAGE_KEY));
  });

  it("descarta JSON roto y valores no numéricos", () => {
    const storage = memoryStorage();
    storage.setItem(LAYOUT_STORAGE_KEY, "{roto");
    assert.deepEqual(loadLayout(storage), {});
    storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({ a: 10, b: "x", c: -5, d: null }));
    assert.deepEqual(loadLayout(storage), { a: 10 });
    assert.deepEqual(loadLayout(null), {});
  });

  it("tamaño guardado acotado al spec actual", () => {
    assert.equal(storedSize({ "network.detail": 900 }, "network.detail", DETAIL), 800);
    assert.equal(storedSize({}, "network.detail", DETAIL), 380);
    assert.equal(storedSize({ "network.sidebar": 0 }, "network.sidebar", SIDEBAR), 0);
  });

  it("clave por vista (comparar tiene la suya; wizards ignora comparar)", () => {
    assert.equal(layoutKey("network", false, "sidebar"), "network.sidebar");
    assert.equal(layoutKey("kubernetes", true, "detail"), "compare.detail");
    assert.equal(layoutKey("wizards", true, "findings"), "wizards.findings");
  });
});

describe("specs de la app", () => {
  it("menú y detalle dejan siempre MIN_MAIN_WIDTH al centro", () => {
    const specs = appPanelSpecs({ width: 1280, height: 800 }, false, { sidebar: 280, detail: 380 });
    assert.equal(specs.sidebar.max, 1280 - 380 - MIN_MAIN_WIDTH);
    assert.equal(specs.detail.max, 1280 - 280 - MIN_MAIN_WIDTH);
    assert.equal(clampSize(9999, specs.detail) + 280 + MIN_MAIN_WIDTH, 1280);
  });

  it("pantalla angosta: el detalle va abajo y se mueve el alto del grafo", () => {
    const specs = appPanelSpecs({ width: 1024, height: 640 }, true, { topbar: 90 });
    assert.equal(specs.sidebar.defaultSize, 240);
    assert.equal(specs.sidebar.max, Math.min(560, 1024 - MIN_MAIN_WIDTH));
    assert.equal(appPanelSpecs({ width: 600, height: 640 }, true).sidebar.max, 600 - MIN_MAIN_WIDTH);
    assert.equal(specs.mainHeight.defaultSize, Math.round(640 * 0.42));
    assert.equal(specs.mainHeight.max, 640 - 90 - 140);
  });

  it("explorador: grafo y lista nunca dejan sin espacio al detalle del chart", () => {
    const specs = explorerPanelSpecs({ width: 900, height: 700 });
    assert.equal(specs.graph.defaultSize, 280);
    assert.equal(clampSize(9999, specs.graph), 700 - 140);
    assert.equal(clampSize(9999, specs.tree), 900 - 280);
    const tiny = explorerPanelSpecs({ width: 300, height: 200 });
    assert.equal(clampSize(9999, tiny.graph), 80);
    assert.equal(clampSize(9999, tiny.tree), 200);
  });
});
