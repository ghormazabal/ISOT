import { useEffect, useMemo, useState, type ReactNode } from "react";

import { parseInline, parseMarkdown, safeHref, type Block } from "../../markdown";
import { rendersApi } from "../../rendersApi";
import { errorMessage } from "./common";

function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((part, index) => {
        if (part.kind === "code") return <code key={index}>{part.text}</code>;
        if (part.kind === "strong") return <strong key={index}>{part.text}</strong>;
        if (part.kind === "em") return <em key={index}>{part.text}</em>;
        if (part.kind === "link") {
          const href = safeHref(part.href);
          return href ? (
            <a key={index} href={href} target={href.startsWith("#") ? undefined : "_blank"} rel="noreferrer">
              {part.text}
            </a>
          ) : (
            <span key={index} title={part.href} className="guide-link">
              {part.text}
            </span>
          );
        }
        return <span key={index}>{part.text}</span>;
      })}
    </>
  );
}

function renderBlock(block: Block, index: number): ReactNode {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${Math.min(6, block.level + 1)}` as "h2";
      return (
        <Tag key={index} id={`guia-${block.id}`}>
          <Inline text={block.text} />
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p key={index}>
          <Inline text={block.text} />
        </p>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag key={index}>
          {block.items.map((item, i) => (
            <li key={i}>
              <Inline text={item} />
            </li>
          ))}
        </Tag>
      );
    }
    case "code":
      return (
        <pre key={index} className="guide-code" data-lang={block.lang}>
          {block.text}
        </pre>
      );
    case "table":
      return (
        <table key={index} className="render-table">
          <thead>
            <tr>
              {block.header.map((cell, i) => (
                <th key={i}>
                  <Inline text={cell} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, i) => (
                  <td key={i}>
                    <Inline text={cell} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    case "quote":
      return (
        <blockquote key={index}>
          <Inline text={block.text} />
        </blockquote>
      );
    case "rule":
      return <hr key={index} />;
  }
}

/** Guía de renders dentro de la UI (docs/renders/GUIDE.md) y la guía para LLMs (docs/llm/RENDERS.md). */
export function RendersGuideTab() {
  const [doc, setDoc] = useState<"human" | "llm">("human");
  const [source, setSource] = useState<{ path: string; markdown: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    rendersApi
      .guide(doc)
      .then((value) => !cancelled && setSource(value))
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)));
    return () => {
      cancelled = true;
    };
  }, [doc]);

  const blocks = useMemo(() => (source ? parseMarkdown(source.markdown) : []), [source]);
  const toc = blocks.filter((block): block is Extract<Block, { kind: "heading" }> => block.kind === "heading" && block.level === 2);

  return (
    <div className="renders-body renders-guide">
      <aside className="guide-toc">
        <nav className="view-tabs">
          <button type="button" className={doc === "human" ? "is-active" : ""} onClick={() => setDoc("human")}>
            Guía (personas)
          </button>
          <button type="button" className={doc === "llm" ? "is-active" : ""} onClick={() => setDoc("llm")}>
            Guía para LLMs
          </button>
        </nav>
        <ul>
          {toc.map((item) => (
            <li key={item.id}>
              <a
                href={`#guia-${item.id}`}
                onClick={(event) => {
                  event.preventDefault();
                  document.getElementById(`guia-${item.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {item.text.replace(/`/g, "")}
              </a>
            </li>
          ))}
        </ul>
        {source ? <p className="muted">Fuente: <code>{source.path}</code></p> : null}
        {doc === "llm" ? (
          <p className="muted">Pásale este documento (y schemas/render-context.schema.json) a tu LLM para que escriba templates a partir de una descripción.</p>
        ) : null}
      </aside>
      <article className="guide-body" key={doc}>
        {error ? <p className="error">{error}</p> : null}
        {blocks.map(renderBlock)}
      </article>
    </div>
  );
}

