import { useEffect, useState } from 'react';
import JSZip from 'jszip';
import './workbook-preview.css';

const ROW_LIMIT = 120;
const COLUMN_LIMIT = 32;
const PART_LIMIT = 4 * 1024 * 1024;
const previewLimit = '这份工作簿超过预览范围，可以下载原件查看。';

async function xmlPart(zip: JSZip, path: string) {
  const entry = zip.file(path);
  if (!entry) throw new Error('工作簿缺少预览所需的数据。');
  // JSZip exposes this streaming API in the browser; its declarations only list the Node stream.
  const chunked = entry as JSZip.JSZipObject & { internalStream(type: 'uint8array'): JSZip.JSZipStreamHelper<Uint8Array> };
  const stream = chunked.internalStream('uint8array');
  const source = await new Promise<string>((resolve, reject) => {
    const decoder = new TextDecoder();
    const pieces: string[] = [];
    let size = 0;
    stream.on('error', reject).on('data', chunk => {
      size += chunk.length;
      if (size > PART_LIMIT) {
        stream.pause();
        reject(new Error(previewLimit));
        return;
      }
      pieces.push(decoder.decode(chunk, { stream: true }));
    }).on('end', () => resolve(pieces.join('') + decoder.decode())).resume();
  });
  if (/<!DOCTYPE/i.test(source)) throw new Error('此工作簿使用了预览不支持的文档格式。');
  const xml = new DOMParser().parseFromString(source, 'application/xml');
  if (xml.getElementsByTagName('parsererror').length) throw new Error('工作簿内容无法解析。');
  return xml;
}

function elements(xml: Element | Document, tag: string) {
  return Array.from(xml.getElementsByTagNameNS('*', tag));
}

function richText(xml: Element) {
  return elements(xml, 't').filter(node => node.parentElement?.localName !== 'rPh')
    .map(node => node.textContent ?? '').join('');
}

function partPath(target: string) {
  const base = new URL('https://package.invalid/xl/workbook.xml');
  const part = new URL(target, base);
  if (part.origin !== base.origin || part.search || part.hash) return '';
  return decodeURIComponent(part.pathname.slice(1));
}

async function openBook(bytes: Uint8Array) {
  if (bytes.length > 16 * 1024 * 1024) throw new Error(previewLimit);
  const zip = await JSZip.loadAsync(bytes);
  if (Object.keys(zip.files).length > 2048) throw new Error(previewLimit);
  const [book, rels] = await Promise.all([xmlPart(zip, 'xl/workbook.xml'), xmlPart(zip, 'xl/_rels/workbook.xml.rels')]);
  const relationships = elements(rels, 'Relationship').filter(rel => rel.getAttribute('TargetMode') !== 'External');
  const sheets = elements(book, 'sheet').filter(sheet => (sheet.getAttribute('state') || 'visible') === 'visible').flatMap(sheet => {
    const id = Array.from(sheet.attributes).find(attr => attr.localName === 'id')?.value;
    const rel = relationships.find(item => item.getAttribute('Id') === id && item.getAttribute('Type')?.endsWith('/worksheet'));
    const path = rel && partPath(rel.getAttribute('Target') || '');
    return path ? [{name: sheet.getAttribute('name') || '工作表', path}] : [];
  });
  if (!sheets.length) throw new Error('这份工作簿没有可预览的工作表。');
  const stringsRel = relationships.find(rel => rel.getAttribute('Type')?.endsWith('/sharedStrings'));
  const strings = stringsRel ? elements(await xmlPart(zip, partPath(stringsRel.getAttribute('Target') || '')), 'si').map(richText) : [];
  return {zip, sheets, strings};
}

function colName(index: number): string {
  return (index > 26 ? colName(Math.floor((index - 1) / 26)) : '') + String.fromCharCode(65 + (index - 1) % 26);
}

type Book = Awaited<ReturnType<typeof openBook>>;
type Cell = {value: string; formula: string; numeric: boolean};
type Grid = {cells: Map<string, Cell>; rows: number; cols: number; limited: boolean};

async function readSheet(book: Book, path: string): Promise<Grid> {
  const xml = await xmlPart(book.zip, path);
  const grid: Grid = {cells: new Map(), rows: 26, cols: 8, limited: false};
  for (const cell of elements(xml, 'c')) {
    const address = cell.getAttribute('r') || '';
    const match = /^([A-Z]{1,3})(\d+)$/.exec(address);
    if (!match) continue;
    const row = Number(match[2]);
    const col = Array.from(match[1]).reduce((n, char) => n * 26 + char.charCodeAt(0) - 64, 0);
    if (row > ROW_LIMIT || col > COLUMN_LIMIT) {grid.limited = true; continue;}
    const type = cell.getAttribute('t');
    const raw = elements(cell, 'v')[0]?.textContent ?? '';
    let value = raw;
    if (type === 's') value = book.strings[Number(raw)] ?? '';
    if (type === 'inlineStr') value = richText(cell);
    if (type === 'b') value = raw === '1' ? 'TRUE' : 'FALSE';
    const formula = elements(cell, 'f')[0]?.textContent;
    grid.cells.set(address, {value, formula: formula ? '=' + formula : '', numeric: !type || type === 'n'});
    grid.cols = Math.max(grid.cols, col);
    grid.rows = Math.max(grid.rows, row);
  }
  return grid;
}

function useRead<T>(read: () => Promise<T>, key: object | string) {
  const [result, setResult] = useState<{key: typeof key; data?: T; error?: string}>();
  useEffect(() => {
    let active = true;
    read().then(data => {if (active) setResult({key, data});}, err => {
      if (active) setResult({key, error: err?.message?.includes('工作簿') ? err.message : '无法读取这份工作簿，可以下载原件查看。'});
    });
    return () => {active = false;};
  }, [key]);
  return result?.key === key ? result : undefined;
}

function Sheet({book, path}: {book: Book; path: string}) {
  const read = useRead(() => readSheet(book, path), path);
  const [selection, select] = useState('A1');
  const grid = read?.data;
  if (!grid) return <p role="status">{read?.error || '正在读取工作表…'}</p>;
  const cell = grid.cells.get(selection);
  const cols = Array.from({length: grid.cols}, (_, i) => colName(i + 1));
  return <>
    <div className="wd-sheet-formula"><span>{selection}</span><i aria-hidden="true">ƒx</i><output aria-label="单元格内容">{cell?.formula || cell?.value}</output></div>
    <div className="wd-sheet-scroll" tabIndex={0}>
      <table aria-label="工作表内容"><thead><tr><th></th>{cols.map(col => <th key={col} scope="col">{col}</th>)}</tr></thead>
        <tbody>{Array.from({length: grid.rows}, (_, i) => <tr key={i}><th scope="row">{i + 1}</th>{cols.map(col => {
          const addr = col + (i + 1);
          const entry = grid.cells.get(addr);
          return <td key={addr} data-cell={addr} aria-label={addr} data-selected={addr === selection} data-number={entry?.numeric}
            onClick={() => select(addr)} onFocus={() => select(addr)} tabIndex={addr === selection ? 0 : -1}
            onKeyDown={event => {
              const offset = {ArrowUp:[-1,0], ArrowDown:[1,0], ArrowLeft:[0,-1], ArrowRight:[0,1]}[event.key];
              if (!offset) return;
              event.preventDefault();
              const table = event.currentTarget.closest('table');
              const target = table?.rows[i + 1 + offset[0]]?.cells[event.currentTarget.cellIndex + offset[1]];
              if (target?.tagName === 'TD') target.focus();
            }}>{entry?.value || entry?.formula}</td>;
        })}</tr>)}</tbody>
      </table>
    </div>
    {grid.limited && <small>预览显示前 {ROW_LIMIT} 行、{COLUMN_LIMIT} 列。</small>}
  </>;
}

function Sheets({book}: {book: Book}) {
  const [index, setIndex] = useState(0);
  return <div className="wd-sheet-preview">
    <Sheet key={index} book={book} path={book.sheets[index].path}/>
    <div className="wd-sheet-tabs" role="tablist" aria-label="工作表">
      {book.sheets.map(({name, path}, i) => <button type="button" key={path} role="tab" aria-selected={i === index} onClick={() => setIndex(i)}>{name}</button>)}
    </div>
  </div>;
}

export function WorkbookPreview({bytes}: {bytes: Uint8Array}) {
  const result = useRead(() => openBook(bytes), bytes);
  return result?.data ? <Sheets book={result.data}/> : <p role="status">{result?.error || '正在读取工作簿…'}</p>;
}
