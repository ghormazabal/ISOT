/** Lógica pura de los paneles redimensionables (sin React ni DOM). */

export type PanelSpec = {
  /** Tamaño por defecto (px, o % si unit === "%"). */
  defaultSize: number;
  min: number;
  max: number;
  /** Si se arrastra por debajo de la mitad del mínimo el panel se colapsa (tamaño 0). */
  collapsible?: boolean;
  unit?: "px" | "%";
};

export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export const LAYOUT_STORAGE_KEY = "isot.layout.v1";
export const KEY_STEP = 16;
export const KEY_STEP_LARGE = 64;
export const PERCENT_STEP = 2;

/** Máximo efectivo: nunca por debajo del mínimo aunque el espacio sea chico. */
export function effectiveMax(spec: PanelSpec): number {
  return Math.max(spec.min, spec.max);
}

/** Acota un tamaño al rango del panel; 0 significa colapsado (si el panel lo admite). */
export function clampSize(value: number, spec: PanelSpec): number {
  if (!Number.isFinite(value)) return spec.defaultSize;
  if (spec.collapsible && value < spec.min / 2) return 0;
  const size = Math.min(Math.max(value, spec.min), effectiveMax(spec));
  return spec.unit === "%" ? Math.round(size * 10) / 10 : Math.round(size);
}

/**
 * Tamaño durante un arrastre. `sign` es +1 si el panel crece hacia donde se mueve
 * el puntero (handle a la derecha / abajo del panel) y -1 si el handle está a su
 * izquierda / arriba. `containerSize` convierte píxeles a % cuando unit === "%".
 */
export function dragSize(
  startSize: number,
  startPos: number,
  pos: number,
  sign: 1 | -1,
  spec: PanelSpec,
  containerSize = 0,
): number {
  let delta = (pos - startPos) * sign;
  if (spec.unit === "%") delta = containerSize > 0 ? (delta / containerSize) * 100 : 0;
  return clampSize(startSize + delta, spec);
}

/** Tamaño tras una tecla (flechas, Home/End, Enter). null = tecla no manejada. */
export function keyboardSize(
  current: number,
  key: string,
  spec: PanelSpec,
  orientation: "vertical" | "horizontal",
  sign: 1 | -1,
  large = false,
): number | null {
  const base = spec.unit === "%" ? PERCENT_STEP : KEY_STEP;
  const step = large ? base * 4 : base;
  const grow = orientation === "vertical" ? (sign > 0 ? "ArrowRight" : "ArrowLeft") : sign > 0 ? "ArrowDown" : "ArrowUp";
  const shrink = orientation === "vertical" ? (sign > 0 ? "ArrowLeft" : "ArrowRight") : sign > 0 ? "ArrowUp" : "ArrowDown";
  if (key === grow) return clampSize(current === 0 ? spec.min : current + step, spec);
  if (key === shrink) {
    if (current === 0) return 0;
    const next = current - step;
    // Con teclado el colapso es explícito: desde el mínimo, una pulsación más colapsa.
    if (spec.collapsible && current <= spec.min) return 0;
    return clampSize(Math.max(next, spec.min), spec);
  }
  if (key === "Home") return spec.collapsible ? 0 : clampSize(spec.min, spec);
  if (key === "End") return clampSize(effectiveMax(spec), spec);
  if (key === "Enter") return current === 0 ? clampSize(spec.defaultSize, spec) : spec.collapsible ? 0 : current;
  return null;
}

export function loadLayout(storage: StorageLike | null): Record<string, number> {
  if (!storage) return {};
  try {
    const parsed = JSON.parse(storage.getItem(LAYOUT_STORAGE_KEY) || "{}") as Record<string, unknown>;
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed ?? {})) {
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveLayout(storage: StorageLike | null, layout: Record<string, number>): void {
  if (!storage) return;
  try {
    storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Cuota llena o storage deshabilitado: el tamaño vive solo en memoria.
  }
}

/** Tamaño guardado para `key`, acotado al spec actual (o el default). */
export function storedSize(layout: Record<string, number>, key: string, spec: PanelSpec): number {
  return key in layout ? clampSize(layout[key], spec) : clampSize(spec.defaultSize, spec);
}

/** Clave de layout por vista: «network.sidebar», «compare.detail», etc. */
export function layoutKey(view: string, compare: boolean, panel: string): string {
  return `${compare && view !== "wizards" ? "compare" : view}.${panel}`;
}

/** Ancho mínimo del área central (grafo / explorador) en la vista normal. */
export const MIN_MAIN_WIDTH = 360;
export const MIN_MAIN_HEIGHT = 160;
export const MIN_BOTTOM_HEIGHT = 140;

export type AppPanelSpecs = {
  sidebar: PanelSpec;
  detail: PanelSpec;
  mainHeight: PanelSpec;
  findings: PanelSpec;
};

/**
 * Specs de los paneles de la app según viewport. `current` son los tamaños
 * actuales del menú y del detalle: el máximo de cada uno deja al menos
 * MIN_MAIN_WIDTH para el área central.
 */
export function appPanelSpecs(
  viewport: { width: number; height: number },
  narrow: boolean,
  current: { sidebar?: number; detail?: number; topbar?: number } = {},
): AppPanelSpecs {
  const { width, height } = viewport;
  const detailWidth = narrow ? 0 : (current.detail ?? 380);
  const sidebarWidth = current.sidebar ?? (narrow ? 240 : 280);
  const topbar = current.topbar ?? 56;
  return {
    sidebar: {
      defaultSize: narrow ? 240 : 280,
      min: 180,
      max: Math.min(560, width - detailWidth - MIN_MAIN_WIDTH),
      collapsible: true,
    },
    detail: {
      defaultSize: 380,
      min: 260,
      max: Math.min(800, width - sidebarWidth - MIN_MAIN_WIDTH),
      collapsible: true,
    },
    mainHeight: {
      defaultSize: Math.round(height * 0.42),
      min: MIN_MAIN_HEIGHT,
      max: height - topbar - MIN_BOTTOM_HEIGHT,
    },
    findings: {
      defaultSize: 420,
      min: 300,
      max: Math.min(800, width - 160),
    },
  };
}

/** Specs del explorador de templates según el alto/ancho del panel. */
export function explorerPanelSpecs(pane: { width: number; height: number }): { graph: PanelSpec; tree: PanelSpec } {
  return {
    graph: {
      defaultSize: Math.round(Math.min(280, Math.max(100, pane.height * 0.4))),
      min: 80,
      max: Math.max(80, pane.height - 140),
    },
    tree: { defaultSize: 300, min: 200, max: Math.max(200, Math.min(640, pane.width - 280)) },
  };
}

export const COMPARE_SIDE_SPEC: PanelSpec = { defaultSize: 50, min: 20, max: 80, unit: "%" };
