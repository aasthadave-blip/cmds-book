// MathMarkdown — single rendering primitive for any text that may
// contain markdown + LaTeX math. Used by:
//   - PreviewPage question solution_text
//   - PreviewPage theory blocks (t:'p', t:'eq', etc)
//   - Composer block previews
//
// Why centralized:
// Until now the preview rendered q.solution_text as raw <pre-wrap> which
// printed `$2x^2$` literally and `| X | 0 | 1 |` as raw pipes. Examples
// rendered fine because they had been pre-split into structured blocks.
// One renderer means questions and examples look identical regardless of
// how their content was sourced.

import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';

// Important: katex CSS must be imported somewhere for math to render.
// We import it lazily here so it's pulled in only when MathMarkdown
// is first used in the bundle.
import 'katex/dist/katex.min.css';

type Props = {
  children: string;
  inline?: boolean;
};

export function MathMarkdown({ children, inline = false }: Props) {
  // Don't crash on undefined/null input — render empty silently.
  const safe = typeof children === 'string' ? children : '';
  if (!safe.trim()) return null;

  // ReactMarkdown wraps everything in <p> by default — for inline use
  // (e.g. inside a span) we strip the wrapping paragraph so it can sit
  // on the same line as surrounding text.
  const components = inline
    ? { p: ({ children }: any) => <>{children}</> }
    : undefined;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, remarkGfm]}
      rehypePlugins={[rehypeKatex]}
      components={components}
    >
      {safe}
    </ReactMarkdown>
  );
}
