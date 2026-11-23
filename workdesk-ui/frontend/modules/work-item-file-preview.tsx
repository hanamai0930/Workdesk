import type { ComponentProps } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { FileText, X } from 'lucide-react';
import { WorkspaceExpandButton } from './quick-workspace';

type PreviewFile = {
  kind: 'text' | 'image' | 'pdf' | 'binary';
  content?: string;
  image?: string;
};

type Props = {
  name: string;
  run: string;
  file?: PreviewFile;
  pdfSrc?: string;
  error?: string;
  canLocate: boolean;
  expanded?: boolean;
  onToggleExpanded?: () => void;
};

const markdownPlugins = [remarkGfm];
const documentComponents = {
  a: ({ children, href }: ComponentProps<'a'>) => <span title={href}>{children}</span>,
  img: ({ alt }: ComponentProps<'img'>) => alt ? <span>{alt}</span> : null,
  table: ({ children }: ComponentProps<'table'>) =>
    <div className="wi-file-preview-table" tabIndex={0}><table>{children}</table></div>,
};

function FileContents({ file, name, pdfSrc }: { file: PreviewFile; name: string; pdfSrc?: string }) {
  if (file.kind === 'text') {
    const content = file.content ?? '';
    if (/\.(md|markdown|mdown|mdx|txt)$/i.test(name)) {
      return <article className="wi-file-preview-document">
        <Markdown skipHtml remarkPlugins={markdownPlugins} components={documentComponents}>
          {content.replace(/^\uFEFF/, '')}
        </Markdown>
      </article>;
    }
    return <pre className="wi-file-preview-code" tabIndex={0}><code>{content}</code></pre>;
  }
  if (file.kind === 'image') return <img className="wi-file-preview-image" src={file.image} alt={name}/>;
  if (file.kind === 'pdf') return <iframe className="wi-file-preview-pdf" src={pdfSrc} title={name}/>;
  return <p className="wi-file-preview-status">下载原件后可在对应应用中打开。</p>;
}

export default function WorkItemFilePreview({ name, run, file, pdfSrc, error, canLocate, expanded = false, onToggleExpanded }: Props) {
  const baseName = name.split('/').at(-1);
  return <>
    <header className="wi-file-preview-header">
      <div className="wi-file-preview-heading">
        <FileText size={17} aria-hidden="true"/>
        <h2 title={name}>{baseName}</h2>
        <div className="wi-file-preview-window-actions">
          {onToggleExpanded && <WorkspaceExpandButton expanded={expanded} onToggle={onToggleExpanded} className="wi-file-preview-close"/>}
        <button type="button" className="wi-file-preview-close" data-cooperation-preview-close
          aria-label="关闭文件预览" title="关闭预览（Esc）"><X size={17} aria-hidden="true"/></button>
        </div>
      </div>
      <div className="wi-file-preview-actions">
        <button type="button" disabled={!file} data-delivery-action="download" data-delivery-run={run}
          data-delivery-file={name}>下载原件</button>
        {canLocate && <button type="button" disabled={!file} data-delivery-action="locate"
          data-delivery-run={run} data-delivery-file={name}>在资源管理器中显示</button>}
      </div>
    </header>
    <div className="wi-file-preview-scroll" key={name}>
      {error ? <p className="wi-file-preview-status wi-file-preview-error" role="alert">{error}</p>
        : file ? <FileContents file={file} name={name} pdfSrc={pdfSrc}/>
        : <p className="wi-file-preview-status" role="status">正在读取保存的文件…</p>}
    </div>
  </>;
}
