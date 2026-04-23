import type { Block } from "../api/client";

// Strip leading "1. ", "2) ", "(3) " etc. so we don't double up the marker
// when the <ol> auto-numbers the item. Also handles bare dashes and bullets.
function stripLeadingNumber(text: string): string {
  return text.replace(/^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+/, "").replace(/^\s*[-•]\s+/, "");
}

export function BlockRenderer({ blocks }: { blocks: Block[] }) {
  return (
    <div>
      {blocks.map((b, i) => (
        <BlockView key={i} block={b} />
      ))}
    </div>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.t) {
    case "p":
      return (
        <div className="blk">
          <p className="blkp">{block.c}</p>
        </div>
      );
    case "h3":
      return (
        <div className="blk">
          <h3 className="blkh3">{block.c}</h3>
        </div>
      );
    case "eq":
      return (
        <div className="blk">
          <div className="blkeq">{block.c}</div>
        </div>
      );
    case "def":
      return (
        <div className="blk">
          <div className="blkdef">
            <div className="dl">Definition</div>
            <div className="dt">{block.term}</div>
            <div className="dd">{block.c}</div>
          </div>
        </div>
      );
    case "kp":
      return (
        <div className="blk">
          <div className="blkkp">
            <div className="kl">Key Point</div>
            <div className="kb">{block.c}</div>
          </div>
        </div>
      );
    case "fig":
      return (
        <div className="blk">
          <div className="blkfig">{block.c}</div>
        </div>
      );
    case "list":
      return (
        <div className="blk">
          <ol className="blkul">
            {block.items.map((it, i) => (
              <li key={i}>{stripLeadingNumber(it)}</li>
            ))}
          </ol>
        </div>
      );
    case "table":
      return (
        <div className="blk">
          {block.caption && <div style={{ fontSize: "0.78rem", color: "var(--text3)", marginBottom: 6, fontStyle: "italic" }}>{block.caption}</div>}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            {block.headers?.length > 0 && (
              <thead>
                <tr>
                  {block.headers.map((h: string, i: number) => (
                    <th key={i} style={{ border: "1px solid var(--border)", padding: "6px 10px", background: "var(--surface2)", textAlign: "left", fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {block.rows?.map((row: string[], ri: number) => (
                <tr key={ri}>
                  {row.map((cell: string, ci: number) => (
                    <td key={ci} style={{ border: "1px solid var(--border)", padding: "6px 10px", verticalAlign: "top" }}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "example":
      return (
        <div className="blk">
          <div className="blkex">
            <div className="exh">
              <div className="exl">{block.label || "Example"}</div>
              {block.prob && <div className="exp">{block.prob}</div>}
            </div>
            {block.eqs.length > 0 && (
              <div className="exb">
                {block.eqs.map((e, i) => (
                  <div key={i} className="exeq">
                    {e}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      );
    default:
      return null;
  }
}
