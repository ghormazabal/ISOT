import { useCallback, useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

import {
  clampSize,
  dragSize,
  keyboardSize,
  loadLayout,
  saveLayout,
  type PanelSpec,
  type StorageLike,
} from "../resize";

function browserStorage(): StorageLike | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

// Cache compartido por todos los paneles; se guarda en localStorage con debounce.
let layoutCache: Record<string, number> | null = null;
let saveTimer: number | undefined;

function layout(): Record<string, number> {
  if (!layoutCache) layoutCache = loadLayout(browserStorage());
  return layoutCache;
}

function persist(key: string, value: number | null): void {
  const current = { ...layout() };
  if (value === null) delete current[key];
  else current[key] = value;
  layoutCache = current;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => saveLayout(browserStorage(), layoutCache ?? {}), 120);
}

/**
 * Tamaño de un panel persistido en localStorage bajo `key`. El valor devuelto ya
 * está acotado al spec actual (que puede depender del viewport).
 */
export function usePanelSize(key: string, spec: PanelSpec): { size: number; setSize: (value: number) => void; reset: () => void } {
  const [raw, setRaw] = useState<number>(() => layout()[key] ?? spec.defaultSize);
  useEffect(() => {
    setRaw(layout()[key] ?? spec.defaultSize);
  }, [key]);
  const setSize = useCallback(
    (value: number) => {
      setRaw(value);
      persist(key, value);
    },
    [key],
  );
  const reset = useCallback(() => {
    setRaw(spec.defaultSize);
    persist(key, null);
  }, [key, spec.defaultSize]);
  return { size: clampSize(raw, spec), setSize, reset };
}

/** true si la media query coincide (se actualiza al cambiar el viewport). */
export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== "undefined" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Tamaño del viewport, para acotar paneles al espacio disponible. */
export function useViewport(): { width: number; height: number } {
  const read = () => ({ width: window.innerWidth, height: window.innerHeight });
  const [size, setSize] = useState(read);
  useEffect(() => {
    const onResize = () => setSize(read());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return size;
}

type Props = {
  /** "vertical": barra vertical que mueve un ancho (↔). "horizontal": barra que mueve un alto (↕). */
  orientation: "vertical" | "horizontal";
  size: number;
  spec: PanelSpec;
  /** +1: el panel está antes del handle (izquierda / arriba). -1: después. */
  sign: 1 | -1;
  onSize: (value: number) => void;
  onReset?: () => void;
  label: string;
  id?: string;
  className?: string;
  style?: CSSProperties;
  /** Contenedor de referencia para tamaños en %. */
  containerRef?: RefObject<HTMLElement | null>;
  controls?: string;
};

/** Divisor arrastrable compartido (mouse, touch y teclado). */
export function Splitter({
  orientation,
  size,
  spec,
  sign,
  onSize,
  onReset,
  label,
  id,
  className = "",
  style,
  containerRef,
  controls,
}: Props) {
  const drag = useRef<{ pos: number; size: number; container: number; pointer: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const vertical = orientation === "vertical";
  const collapsed = size === 0;

  const end = (target: HTMLElement) => {
    if (drag.current && target.hasPointerCapture?.(drag.current.pointer)) target.releasePointerCapture(drag.current.pointer);
    drag.current = null;
    setDragging(false);
    document.body.classList.remove("is-resizing", "resizing-col", "resizing-row");
  };

  useEffect(() => () => document.body.classList.remove("is-resizing", "resizing-col", "resizing-row"), []);

  const reset = () => (onReset ? onReset() : onSize(clampSize(spec.defaultSize, spec)));
  const unit = spec.unit === "%" ? "%" : "px";

  return (
    <div
      id={id}
      role="separator"
      tabIndex={0}
      aria-orientation={orientation}
      aria-label={label}
      aria-controls={controls}
      aria-valuenow={Math.round(size)}
      aria-valuemin={spec.collapsible ? 0 : Math.round(spec.min)}
      aria-valuemax={Math.round(Math.max(spec.min, spec.max))}
      aria-valuetext={collapsed ? "colapsado" : `${Math.round(size)} ${unit}`}
      data-collapsed={collapsed ? "true" : undefined}
      title={`${label}: arrastra para redimensionar · doble clic restablece · flechas con foco${
        spec.collapsible ? " · Enter colapsa/expande" : ""
      }`}
      className={`splitter splitter-${orientation}${dragging ? " is-dragging" : ""}${collapsed ? " is-collapsed" : ""} ${className}`.trim()}
      style={style}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        const container = containerRef?.current?.getBoundingClientRect();
        drag.current = {
          pos: vertical ? event.clientX : event.clientY,
          size,
          container: container ? (vertical ? container.width : container.height) : 0,
          pointer: event.pointerId,
        };
        setDragging(true);
        document.body.classList.add("is-resizing", vertical ? "resizing-col" : "resizing-row");
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (!start) return;
        const pos = vertical ? event.clientX : event.clientY;
        // Desde colapsado, crecer exige pasar la mitad del mínimo (clampSize).
        onSize(dragSize(start.size, start.pos, pos, sign, spec, start.container));
      }}
      onPointerUp={(event) => end(event.currentTarget)}
      onPointerCancel={(event) => end(event.currentTarget)}
      onLostPointerCapture={() => {
        if (drag.current) {
          drag.current = null;
          setDragging(false);
          document.body.classList.remove("is-resizing", "resizing-col", "resizing-row");
        }
      }}
      onDoubleClick={reset}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          (event.currentTarget as HTMLElement).blur();
          return;
        }
        const next = keyboardSize(size, event.key, spec, orientation, sign, event.shiftKey);
        if (next === null) return;
        event.preventDefault();
        onSize(next);
      }}
    >
      <span className="splitter-grip" aria-hidden="true" />
    </div>
  );
}
