import { Children, isValidElement, useEffect, useState, type ComponentProps, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { createRoot, type Root } from 'react-dom/client';
import './safe-markdown.css';

type MarkdownNode = { type: string; value?: string; url?: string; children?: MarkdownNode[] };
const blockContainers = new Set(['root', 'blockquote', 'listItem', 'footnoteDefinition']);

/** Keep HTML/XML as source text; never pass it through as active HTML. */
function preserveHtmlSource() {
  return (tree: MarkdownNode) => {
    function visit(parent: MarkdownNode) {
      if (!parent.children) return;
      parent.children = parent.children.map(node => {
        if (node.type === 'html') {
          // A literal block preserves line breaks and provides the same exact-text
          // copy control as fenced code. Inline tags remain readable inline text.
          return blockContainers.has(parent.type)
            ? { type: 'code', lang: 'text', value: node.value || '' }
            : { type: 'text', value: node.value || '' };
        }
        visit(node);
        return node;
      });
    }
    visit(tree);
  };
}

const plugins = [remarkGfm, preserveHtmlSource];

/** Application-only marker. Its single attribute contains escaped plain text. */
export function markdownTemplate(value: unknown) {
  const source = String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]!));
  return `<workdesk-markdown data-source="${source}"></workdesk-markdown>`;
}

export function safeMarkdownLink(value?: string) {
  if (!value || !/^https?:\/\//i.test(value) || /[\u0000-\u001f\u007f]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch { return undefined; }
}

function plainText(children: ReactNode): string {
  return Children.toArray(children).map(child => typeof child === 'string' || typeof child === 'number'
    ? String(child) : isValidElement<{ children?: ReactNode }>(child) ? plainText(child.props.children) : '').join('');
}

function CodeBlock({ children }: ComponentProps<'pre'>) {
  const source = plainText(children);
  const child = Children.toArray(children).find(item => isValidElement(item));
  const className = isValidElement<{ className?: string }>(child) ? child.props.className : undefined;
  const language = className?.match(/(?:^|\s)language-([^\s]+)/)?.[1] || '代码';
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  useEffect(() => { setCopyState('idle'); }, [source]);
  useEffect(() => {
    if (copyState !== 'copied') return;
    const timer = window.setTimeout(() => setCopyState('idle'), 2500);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  return <div className="wd-markdown-code">
    <div className="wd-markdown-code-heading"><span>{language}</span><button type="button" onClick={async () => {
      try { await navigator.clipboard.writeText(source); setCopyState('copied'); }
      catch { setCopyState('failed'); }
    }}>{copyState === 'copied' ? '已复制代码' : '复制代码'}</button></div>
    <pre tabIndex={0}>{children}</pre>
    {copyState === 'failed' && <p className="wd-markdown-copy-status" role="status">复制暂不可用，可选中代码复制。</p>}
  </div>;
}

const components = {
  a: ({ children, href }: ComponentProps<'a'>) => {
    const target = safeMarkdownLink(href);
    return target ? <a href={target} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
  },
  img: ({ alt }: ComponentProps<'img'>) => <span className="wd-markdown-image">{alt ? `[图片：${alt}]` : '[图片未自动加载]'}</span>,
  table: ({ children }: ComponentProps<'table'>) => <div className="wd-markdown-table" role="region" aria-label="表格" tabIndex={0}><table>{children}</table></div>,
  pre: CodeBlock,
};

type FileLinks = ReadonlyMap<string, () => void>;

function linkFileMentions(files: FileLinks) {
  const names = [...files.keys()].sort((a,b) => b.length - a.length);
  const escaped = names.map(name => name.replace(/[.*+?^$\{\}()|[\]\\]/g, '\\$&'));
  const mention = new RegExp('(?<![\\w.\\-])(' + escaped.join('|') + ')(?![\\w.\\-])', 'gu');
  return () => (tree: MarkdownNode) => {
    function visit(parent: MarkdownNode) {
      if (!parent.children || ['link','linkReference','image','code'].includes(parent.type)) return;
      parent.children = parent.children.flatMap(node => {
        if (node.type === 'inlineCode' && node.value && files.has(node.value))
          return [{ type: 'link', url: '', children: [node] }];
        if (node.type !== 'text' || !node.value) { visit(node); return [node]; }
        return node.value.split(mention).filter(Boolean).map((part): MarkdownNode =>
          files.has(part) ? { type: 'link', url: '', children: [{ type: 'text', value: part }] } : { type: 'text', value: part });
      });
    }
    visit(tree);
  };
}

export function SafeMarkdown({ text, fileLinks }: { text: string; fileLinks?: FileLinks }) {
  const withFiles = fileLinks?.size ? {
    plugins: [...plugins, linkFileMentions(fileLinks)],
    components: { ...components, a: (props: ComponentProps<'a'>) => {
      const name = plainText(props.children);
      const open = !props.href && fileLinks.get(name);
      return open ? <button type="button" className="wd-talk-file-link" data-preview-file={name} onClick={open}>{props.children}</button>
        : components.a(props);
    } },
  } : { plugins, components };
  return <div className="wd-safe-markdown" data-safe-markdown>
    <ReactMarkdown skipHtml remarkPlugins={withFiles.plugins} components={withFiles.components}
      urlTransform={(value, key) => key === 'href' ? safeMarkdownLink(value) || '' : ''}>
      {text.replace(/^\uFEFF/, '')}
    </ReactMarkdown>
  </div>;
}

/** Legacy controllers also insert application templates into ordinary DOM nodes. */
export function registerMarkdownElement() {
  if (typeof window === 'undefined' || !window.customElements || window.customElements.get('workdesk-markdown')) return;
  class MarkdownElement extends HTMLElement {
    static observedAttributes = ['data-source'];
    private markdownRoot: Root | null = null;
    private queued = false;
    connectedCallback() { this.queueRender(); }
    attributeChangedCallback() { this.queueRender(); }
    private queueRender() {
      if (this.queued) return;
      this.queued = true;
      queueMicrotask(() => {
        this.queued = false;
        if (!this.isConnected) return;
        this.markdownRoot ??= createRoot(this);
        this.markdownRoot.render(<SafeMarkdown text={this.getAttribute('data-source') || ''}/>);
      });
    }
    disconnectedCallback() {
      // A parent React commit can remove this compatibility node. Unmount only
      // after that commit, and retain the root if the same node was reattached.
      queueMicrotask(() => {
        if (this.isConnected) return;
        this.markdownRoot?.unmount();
        this.markdownRoot = null;
      });
    }
  }
  window.customElements.define('workdesk-markdown', MarkdownElement);
}
