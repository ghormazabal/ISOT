import { useEffect } from "react";
import { useReactFlow, useStore } from "@xyflow/react";

/**
 * Vuelve a encuadrar el grafo cuando cambia el tamaño de su contenedor (p.ej.
 * al arrastrar un divisor). Espera a que el tamaño se estabilice para no
 * animar en cada píxel. `ids` restringe el encuadre a esos nodos (foco).
 */
export function RefitOnResize({ ids = [], padding = 0.15, delay = 140 }: { ids?: string[]; padding?: number; delay?: number }) {
  const { fitView } = useReactFlow();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  const key = ids.join("|");
  useEffect(() => {
    if (!width || !height) return;
    const handle = window.setTimeout(() => {
      const list = key ? key.split("|").filter(Boolean) : [];
      void fitView(list.length ? { nodes: list.map((id) => ({ id })), padding: 0.45, duration: 160 } : { padding, duration: 160 });
    }, delay);
    return () => window.clearTimeout(handle);
  }, [width, height, fitView, key, padding, delay]);
  return null;
}
