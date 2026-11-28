import { lazy, Suspense, useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { coreClient } from '../core/client';
import { PreviewContents } from './quick-files';

const WorkbookPreview = lazy(async () => ({ default: (await import('./workbook-preview')).WorkbookPreview }));

export type ResultFile = {
  name: string; path: string; round: number; route: string; sha256: string;
  source: 'job' | 'snapshot'; mime?: string;
};
type ReadFile = { bytes: Uint8Array<ArrayBuffer>; mime: string };

async function readResult(file: ResultFile, signal: AbortSignal): Promise<ReadFile> {
  let bytes: Uint8Array<ArrayBuffer>;
  let mime = file.mime || 'application/octet-stream';
  if (file.source === 'job') {
    const response = await fetch('api/' + file.route, { signal, cache: 'no-store', credentials: 'omit',
      headers: { Accept: 'application/octet-stream', Range: 'bytes=0-' } });
    if (!response.ok) throw Error('此版本暂时无法读取。');
    bytes = new Uint8Array(await response.arrayBuffer());
  } else {
    const result = await coreClient.request<{ data: string; sha256: string; mime?: string; kind?: string; image?: string }>(file.route, { signal });
    if (result.sha256 !== file.sha256) throw Error('文件与所选版本不一致。');
    bytes = Uint8Array.from(atob(result.data), char => char.charCodeAt(0));
    const imageType = result.kind === 'image' && result.image?.match(/^data:(image\/(?:png|jpeg|gif|webp));base64,/i)?.[1];
    mime = result.mime || imageType || mime;
  }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
  if (digest !== file.sha256) throw Error('文件与所选版本不一致。');
  return { bytes, mime };
}

type Loaded = ReadFile & { url: string; text?: string };
export function TaskFilePreview({ file }: { file: ResultFile }) {
  const [loaded, setLoaded] = useState<Loaded>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const workbook = /\.xlsx$/i.test(file.name);
  useEffect(() => {
    const controller = new AbortController();
    let url = '';
    setLoaded(undefined);
    setError('');
    readResult(file, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(new Blob([result.bytes], { type: result.mime }));
      let text: string | undefined;
      if (!workbook && !/^(image\/|application\/pdf)/i.test(result.mime)) {
        // Decode a bounded sample. Binary files retain the download action.
        const sample = result.bytes.subarray(0, 410000);
        if (!sample.includes(0)) {
          try { text = new TextDecoder('utf-8', { fatal: true }).decode(sample, { stream: sample.length < result.bytes.length }); }
          catch { /* This is not a UTF-8 document. */ }
        }
      }
      setLoaded({ ...result, url, text });
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '读取成果失败。'); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [file, retry, workbook]);
  const image = loaded && /^image\/(png|jpeg|gif|webp)$/.test(loaded.mime);
  return <section className="wd-file-preview" aria-label="文件预览">
    <header><span>第 {file.round} 轮</span>{loaded && <a href={loaded.url} download={file.name}><Download size={14}/>下载原件</a>}</header>
    {error ? <p role="alert">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>重试</button></p> :
      !loaded ? <p role="status">正在读取文件…</p> :
      workbook ? <Suspense fallback={<p role="status">正在打开工作簿…</p>}><WorkbookPreview bytes={loaded.bytes}/></Suspense> :
      <div className="wk-quick-file-preview"><PreviewContents name={file.name} pdf={loaded.url}
        value={image ? { kind: 'image', image: loaded.url } : loaded.mime === 'application/pdf' ? { kind: 'pdf' } :
          loaded.text !== undefined ? { kind: 'text', content: loaded.text } : { kind: 'binary' }}/></div>}
  </section>;
}
