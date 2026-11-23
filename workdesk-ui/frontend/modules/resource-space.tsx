import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowLeft, ArrowUpRight, BookOpen, ClipboardCopy, Clock3, Download, Ellipsis, File as FileIcon, FilePlus2,
  FileSpreadsheet, FileText, FolderOpen, Grid2X2, Image, Library, Link2, List,
  Pencil, Plus, Presentation, RotateCcw, Search, Trash2, Upload, X,
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Textarea } from '../components/ui/textarea';
import { Template } from '../components/template';
import './resource-space.css';

type Category = 'page' | 'presentation' | 'spreadsheet' | 'image' | 'link' | 'file';
type Kind = 'page' | 'link' | 'file';
type Collection = { id: string; title: string; description: string };
type Item = {
  id: string; title: string; kind: Kind; category: Category; revision: number; note?: string; tags?: string[];
  collection_id?: string | null; updated: number; trashed: boolean; trashed_at?: number | null;
  body?: string; url?: string; filename?: string; bytes?: number; preview?: 'image' | 'pdf' | 'text' | null;
};
type Catalog = { items: Item[]; collections: Collection[] };
type StoredFile = { name: string; mime: string; data: string; sha256: string; preview?: Item['preview'] };
type EditorState = { kind: Kind | 'collection'; item?: Item; storageKey?: string };
type EditorText = { title: string; body: string; note: string; tags: string; url: string; selected: string; revision?: number };
type Support = {
  request<T>(route: string): Promise<T>;
  command<T>(kind: string, data: object): Promise<T>;
};

const support = () => (window as Window & { WorkdeskSupport?: Support }).WorkdeskSupport!;
const MAX_FILE = 8 * 1024 * 1024;
const MAX_TEXT_PREVIEW = 256 * 1024;
const categories = {
  page: { label: '页面', Icon: FileText },
  presentation: { label: '幻灯片', Icon: Presentation },
  spreadsheet: { label: '表格', Icon: FileSpreadsheet },
  image: { label: '图片', Icon: Image },
  link: { label: '链接', Icon: Link2 },
  file: { label: '文件', Icon: FileIcon },
};
const destinations = [
  { route: '', label: '全部资料', Icon: Library },
  { route: 'library', label: '资料库', Icon: FolderOpen },
  { route: 'knowledge', label: '知识库', Icon: BookOpen },
  { route: 'recent', label: '最近', Icon: Clock3 },
  { route: 'trash', label: '回收站', Icon: Trash2 },
];
const viewDescriptions = new Map([
  ['', '收录值得长期保留、以后会用到的资料。任务交付可从原项目查看。'],
  ['library', '尚未归入知识库的笔记、参考文件和链接。'],
  ['knowledge', '按主题整理笔记、参考资料和经验。'],
]);

function href(view = '', id = '') {
  return '#resources' + (view ? '/' + view : '') + (id ? '/' + encodeURIComponent(id) : '');
}

function filteredHref(view: string, id: string, query: string, category: string) {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (category) params.set('type', category);
  return href(view, id) + (params.size ? (view ? '?' : '/?') + params : '');
}

function confirmEditor(message: string, discard = false) {
  const dialogs = (window as Window & { WorkdeskDialogs?: { confirm(value: object): Promise<boolean> } }).WorkdeskDialogs;
  const action = discard ? '舍弃暂存' : '关闭编辑';
  return dialogs ? dialogs.confirm({ title: action, message, confirmLabel: action, tone: discard ? 'danger' : 'default' }) : Promise.resolve(window.confirm(message));
}

function decodeId(value: string) {
  try { return decodeURIComponent(value); }
  catch { return value; }
}

function dateLabel(seconds: number) {
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(seconds * 1000);
}

function sizeLabel(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`;
}

function localLink(value?: string) {
  try {
    const url = new URL(value || '');
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('文件读取失败，请重新选择。'));
    reader.readAsDataURL(file);
  });
}

function fileBlob(file: StoredFile) {
  const bytes = Uint8Array.from(atob(file.data), byte => byte.charCodeAt(0));
  return new Blob([bytes], { type: file.mime || 'application/octet-stream' });
}

function Visual({ category }: { category: Category }) {
  const { Icon } = categories[category] || categories.file;
  return <span className="wd-resource-visual" data-category={category}><Icon size={20} aria-hidden="true"/></span>;
}

function Markdown({ text }: { text: string }) {
  return text.trim()
    ? <div className="wd-resource-markdown markdown"><Template html={window.WorkdeskLegacy.markdown(text)}/></div>
    : <p className="wd-resource-muted">页面还没有正文。</p>;
}

function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return <section className="wd-resource-empty"><div className="wd-resource-empty-icon">{icon}</div><h2>{title}</h2>{children}</section>;
}

function ResourceDialog({ state, collectionId, collections, onClose, onSave }: {
  state: EditorState; collectionId: string; collections: Collection[];
  onClose: () => void; onSave: (kind: string, values: object) => Promise<void>;
}) {
  const { kind, item } = state;
  const draftKey = state.storageKey ? 'workdesk.resource.editor.' + JSON.stringify([state.storageKey, item?.id || 'new-' + kind, item ? '' : collectionId]) : '';
  const [initial] = useState(() => {
    const fallback: EditorText = { title: item?.title || '', body: item?.body || '', note: item?.note || '', tags: item?.tags?.join('、') || '', url: item?.url || '', selected: item?.collection_id || collectionId, revision: item?.revision };
    if (!draftKey) return { text: fallback, restored: false };
    try {
      const saved = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
      if (saved && ['title','body','note','tags','url','selected'].every(key => typeof saved[key] === 'string') &&
          (saved.revision === undefined || Number.isSafeInteger(saved.revision))) return { text: saved as EditorText, restored: true };
    } catch { /* A failed local draft read does not change the saved resource. */ }
    return { text: fallback, restored: false };
  });
  const ref = useRef<HTMLDialogElement>(null);
  const [title, setTitle] = useState(initial.text.title);
  const [body, setBody] = useState(initial.text.body);
  const [note, setNote] = useState(initial.text.note);
  const [tags, setTags] = useState(initial.text.tags);
  const [url, setUrl] = useState(initial.text.url);
  const [selected, setSelected] = useState(initial.text.selected);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [draftSaved, setDraftSaved] = useState(false);
  const saved = useRef(false);
  const text: EditorText = { title, body, note, tags, url, selected, revision: initial.text.revision };
  const dirty = initial.restored || title !== (item?.title || '') || body !== (item?.body || '') || note !== (item?.note || '') ||
    tags !== (item?.tags?.join('、') || '') || url !== (item?.url || '') || selected !== (item?.collection_id || collectionId);
  const heading = item ? '编辑资料' : { page: '新建页面', file: '收录文件', link: '收录链接', collection: '新建知识库' }[kind];
  const submitLabel = item || kind === 'collection' ? '保存' : '收录';

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);
  useEffect(() => {
    if (!draftKey || saved.current || !dirty) return;
    try { sessionStorage.setItem(draftKey, JSON.stringify(text)); setDraftSaved(true); }
    catch { setDraftSaved(false); }
  }, [draftKey, title, body, note, tags, url, selected, dirty]);

  async function close() {
    if (busy) return;
    let preserved = false;
    if (dirty && draftKey) {
      try { sessionStorage.setItem(draftKey, JSON.stringify(text)); preserved = true; setDraftSaved(true); }
      catch { setDraftSaved(false); }
    }
    if (file && !await confirmEditor(preserved ? '文字已在本窗口暂存。关闭后文件需要重新选择，仍要关闭吗？' : '关闭后需要重新选择文件，未暂存的文字也会丢弃。仍要关闭吗？')) return;
    if (!file && dirty && !preserved && !await confirmEditor('输入尚未暂存，关闭将丢弃这些修改。仍要关闭吗？')) return;
    onClose();
  }

  async function commit(kind: string, values: object) {
    await onSave(kind, values);
    saved.current = true;
    if (draftKey) { try { sessionStorage.removeItem(draftKey); } catch { /* The completed save remains in the resource catalog. */ } }
  }

  async function discard() {
    if (busy || !await confirmEditor('舍弃本窗口暂存的文字和本次修改？已收录的资料不受影响。', true)) return;
    saved.current = true;
    if (draftKey) { try { sessionStorage.removeItem(draftKey); } catch { setError('无法清除暂存，请保留需要的文字后重试。'); saved.current = false; return; } }
    onClose();
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      if (kind === 'collection') {
        await commit('collection-create', { title: title.trim(), description: note.trim() });
        return;
      }
      const values = {
        title: title.trim() || file?.name || '', note: note.trim(), collection_id: selected || null,
        tags: [...new Set(tags.split(/[、,，\n]/).map(tag => tag.trim()).filter(Boolean))],
        ...(kind === 'page' ? { body } : {}),
        ...(kind === 'link' ? { url: localLink(url)?.href || '' } : {}),
      };
      if (kind === 'link' && !values.url) throw new Error('请输入有效的 http 或 https 链接。');
      if (item) {
        await commit('update', { id: item.id, revision: initial.text.revision ?? item.revision, changes: values });
        return;
      }
      if (kind !== 'file') {
        await commit('create', { kind, ...values });
        return;
      }
      if (!file || !file.size) throw new Error('请选择一个非空文件。');
      if (file.size > MAX_FILE) throw new Error('每个文件最多 8 MB。');
      await commit('create', { kind, ...values, file: { name: file.name, mime: file.type, data: await readFile(file) } });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败。');
    } finally {
      setBusy(false);
    }
  }

  return <dialog ref={ref} className="wd-resource-dialog" aria-labelledby="wd-resource-dialog-title" onCancel={event => {
    event.preventDefault();
    void close();
  }}>
    <header><div><h2 id="wd-resource-dialog-title">{heading}</h2><p className="wd-resource-draft-status" role="status">{draftSaved ? '文字已在本窗口暂存' : dirty ? '文字尚未暂存' : draftKey ? '输入文字后自动暂存' : '关闭前会确认未保存输入'}{kind === 'file' && !item ? ' · 文件需重新选择' : ''}</p></div><Button type="button" variant="ghost" size="icon-sm" aria-label="关闭" disabled={busy} onClick={() => void close()}><X/></Button></header>
    <form onSubmit={save} className="wd-resource-form" aria-busy={busy}>
      <label>{kind === 'collection' ? '名称' : '标题'}<Input required={kind !== 'file' || !!item} maxLength={160} value={title} onChange={event => setTitle(event.target.value)} placeholder={kind === 'file' && !item ? '默认使用文件名' : undefined}/></label>
      {kind === 'file' && !item && <label>文件<Input type="file" required onChange={event => setFile(event.currentTarget.files?.[0] || null)}/><small>单个文件最多 8 MB。</small></label>}
      {kind === 'link' && <label>网址<Input type="url" placeholder="https://" required maxLength={4000} value={url} onChange={event => setUrl(event.target.value)}/></label>}
      {kind === 'page' && <section className="wd-resource-writing" aria-label="页面正文">
        <div className="wd-resource-filter" aria-label="编辑与预览"><button type="button" aria-pressed={!preview} onClick={() => setPreview(false)}>Markdown</button><button type="button" aria-pressed={preview} onClick={() => setPreview(true)}>预览</button></div>
        {preview ? <Markdown text={body}/> : <Textarea aria-label="Markdown 正文" rows={9} maxLength={262144} value={body} onChange={event => setBody(event.target.value)} placeholder="用 Markdown 写下内容…"/>}
      </section>}
      <label>{kind === 'collection' ? '描述' : '备注'}<Textarea aria-label={kind === 'collection' ? '描述' : '备注'} rows={2} maxLength={kind === 'collection' ? 1000 : 4000} value={note} onChange={event => setNote(event.target.value)}/></label>
      {kind !== 'collection' && <div className="wd-resource-form-row">
        <label>知识库<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">放在资料库</option>{collections.map(collection => <option key={collection.id} value={collection.id}>{collection.title}</option>)}</select></label>
        <label>标签<Input maxLength={1560} value={tags} placeholder="用逗号分隔" onChange={event => setTags(event.target.value)}/></label>
      </div>}
      {error && <p className="wd-resource-error" role="alert">{error}</p>}
      {initial.restored && item && initial.text.revision !== item.revision && <p className="wd-resource-error" role="alert">已恢复旧版本的文字草稿。资料已在别处更新，请先复制需要保留的文字，再舍弃暂存、重新编辑当前资料并合并。</p>}
      <footer>{dirty && <Button type="button" variant="ghost" className="wd-resource-discard" disabled={busy} onClick={() => void discard()}>舍弃暂存</Button>}<Button type="button" variant="outline" disabled={busy} onClick={() => void close()}>{dirty && draftSaved ? '关闭并保留暂存' : '关闭编辑'}</Button><Button type="submit" disabled={busy}>{busy ? '正在保存…' : submitLabel}</Button></footer>
    </form>
  </dialog>;
}

function FilePreview({ item }: { item: Item }) {
  const [file, setFile] = useState<StoredFile | null>(null);
  const [objectUrl, setObjectUrl] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const text = useMemo(() => file?.preview === 'text'
    ? new TextDecoder().decode(Uint8Array.from(atob(file.data.slice(0, Math.ceil(MAX_TEXT_PREVIEW / 3) * 4)), byte => byte.charCodeAt(0)).subarray(0, MAX_TEXT_PREVIEW))
    : '', [file]);

  useEffect(() => {
    if (file?.preview !== 'image') return;
    const url = URL.createObjectURL(fileBlob(file));
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  async function read(download: boolean) {
    setLoading(true);
    setError('');
    try {
      const stored = file || (await support().request<{ file: StoredFile }>('resource-space/file?' + new URLSearchParams({ id: item.id }))).file;
      if (!download) {
        setFile(stored);
        return;
      }
      const url = URL.createObjectURL(fileBlob(stored));
      const link = document.createElement('a');
      link.href = url;
      link.download = stored.name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '文件打开失败。');
    } finally {
      setLoading(false);
    }
  }

  return <section className="wd-resource-detail-card wd-resource-file" aria-label="文件预览">
    <header><Visual category={item.category}/><div><h2>{item.filename || item.title}</h2><p>{item.bytes !== undefined && sizeLabel(item.bytes)}</p></div><div className="wd-resource-file-actions">
      {item.preview && !file && <Button type="button" variant="outline" onClick={() => void read(false)} disabled={loading}>预览</Button>}
      <Button type="button" variant="outline" onClick={() => void read(true)} disabled={loading}><Download size={15}/>下载文件</Button>
    </div></header>
    {file?.preview === 'image' && objectUrl && <img src={objectUrl} alt={item.title}/>}
    {file?.preview === 'pdf' && /^[a-f0-9]{64}$/.test(file.sha256) && <iframe src={'api/resource-space-pdf?' + new URLSearchParams({ id: item.id, sha256: file.sha256 })} title={item.title + ' PDF 预览'}/>}
    {file?.preview === 'text' && <>{(item.bytes || 0) > MAX_TEXT_PREVIEW && <p>预览前 256 KB，下载可查看全文。</p>}{/\.md$/i.test(file.name) ? <Markdown text={text}/> : <pre>{text}</pre>}</>}
    {loading && <p className="wd-resource-muted" role="status">正在读取文件…</p>}
    {error && <p className="wd-resource-error" role="alert">{error}</p>}
  </section>;
}

export function ResourceSpace({ routeKey, onNewPresentation }: { routeKey?: string; onNewPresentation?: () => void }) {
  const rawRoute = routeKey || location.hash;
  const [path, rawParams = ''] = rawRoute.split('?');
  const params = new URLSearchParams(rawParams);
  const categoryParam = params.get('type') || '';
  const [view = '', id = ''] = path.replace(/^#/, '').split('/').slice(1);
  const identity = decodeId(id);
  const showingItem = view === 'item' && !!id;
  const inTrash = view === 'trash';
  const collectionId = view === 'knowledge' ? identity : '';
  const showingCollections = view === 'knowledge' && !id;
  const [catalog, setCatalog] = useState<Catalog>({ items: [], collections: [] });
  const [detail, setItem] = useState<Item | null>(null);
  const item = showingItem && detail?.id === identity ? detail : null;
  const [query, setQuery] = useState(params.get('q') || '');
  const [category, setCategory] = useState<Category | ''>((Object.hasOwn(categories, categoryParam) ? categoryParam : '') as Category | '');
  const [layout, setLayout] = useState<'cards' | 'list'>('cards');
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [copyError, setCopyError] = useState('');
  const [storageKey, setStorageKey] = useState('');
  const [loadedFor, setLoadedFor] = useState('');
  const newMenu = useRef<HTMLDetailsElement>(null);
  const backTo = useRef(href());
  const listingKey = view + '/' + identity;
  const listHref = filteredHref(view, id, query, category);

  useEffect(() => {
    let cancelled = false;
    support().request<{ storage?: { library?: string } }>('settings').then(data => {
      if (!cancelled && data.storage?.library) setStorageKey(data.storage.library);
    }).catch(() => { /* Until the library is known, closing an editor requires confirmation. */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const origin = params.get('from');
    if (showingItem && origin && /^#resources(?:\/(?:library|knowledge(?:\/[^/?#]+)?|recent|trash)?\/?)?(?:\?[^#]*)?$/.test(origin)) backTo.current = origin;
    else if (!showingItem) backTo.current = rawRoute ? '#' + rawRoute.replace(/^#/, '') : href();
    setEditor(null);
    setQuery(showingItem ? '' : params.get('q') || '');
    setCategory((!showingItem && Object.hasOwn(categories, categoryParam) ? categoryParam : '') as Category | '');
    setNotice('');
    setCopyError('');
    if (newMenu.current) newMenu.current.open = false;
  }, [rawRoute]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ view: inTrash ? 'trash' : 'active' });
    if (query.trim() && !showingItem && !showingCollections) params.set('q', query.trim());
    if (collectionId) params.set('collection_id', collectionId);
    setBusy(true);
    setError('');
    const timer = setTimeout(() => {
      const open = showingItem ? support().request<{ item: Item }>('resource-space/item?' + new URLSearchParams({ id: identity })) : Promise.resolve(null);
      Promise.all([support().request<Catalog>('resource-space?' + params), open]).then(([listing, detail]) => {
        if (cancelled) return;
        setCatalog(listing);
        setItem(detail?.item || null);
        setLoadedFor(listingKey);
      }).catch(cause => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '资料读取失败。');
      }).finally(() => { if (!cancelled) setBusy(false); });
    }, query ? 180 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [inTrash, query, collectionId, revision, identity, showingItem, showingCollections]);

  function begin(kind: EditorState['kind'], editing?: Item) {
    if (newMenu.current) newMenu.current.open = false;
    setEditor({ kind, item: editing, storageKey });
  }

  function filter(nextQuery: string, nextCategory: Category | '') {
    setQuery(nextQuery); setCategory(nextCategory);
    const next = filteredHref(view, id, nextQuery, nextCategory);
    history.replaceState(history.state, '', next);
    backTo.current = next;
  }

  function itemHref(id: string) { return href('item', id) + '?' + new URLSearchParams({ from: listHref }); }

  async function save(kind: string, values: object) {
    const origin = location.hash;
    const result = await support().command<{ collection?: Collection; item?: Item }>('resource-space.' + kind, values);
    if (location.hash !== origin) return;
    setEditor(null);
    setNotice('已保存。');
    setRevision(current => current + 1);
    if (result.collection) location.hash = href('knowledge', result.collection.id);
    else if (kind === 'create' && result.item) location.hash = href('item', result.item.id);
  }

  async function changeTrash(record: Item) {
    const origin = location.hash;
    setError('');
    setActionBusy(true);
    try {
      await support().command('resource-space.trash', { id: record.id, revision: record.revision, trashed: !record.trashed });
      if (location.hash !== origin) return;
      setNotice(record.trashed ? '资料已恢复。' : '已移入回收站。');
      setRevision(current => current + 1);
    } catch (cause) {
      if (location.hash === origin) setError(cause instanceof Error ? cause.message : '资料状态更新失败。');
    } finally {
      setActionBusy(false);
    }
  }

  const collection = catalog.collections.find(row => row.id === collectionId);
  const activeItems = catalog.items.filter(row => (view !== 'library' || !row.collection_id) && (!category || row.category === category));
  const rows = view === 'recent' ? activeItems.slice(0, 20) : activeItems;
  const collectionRows = query.trim() ? catalog.collections.filter(row => (row.title + ' ' + row.description).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : catalog.collections;
  const title = showingItem ? item?.title || '资料详情' : collection?.title || destinations.find(destination => destination.route === view)?.label || '全部资料';
  const link = localLink(item?.url);
  const reference = item?.kind === 'page' ? item.body : link?.href;
  const description = collection?.description || viewDescriptions.get(view);

  async function copyReference() {
    if (!item || !reference) return;
    const origin = location.hash;
    setNotice('');
    setCopyError('');
    try {
      await navigator.clipboard.writeText([`参考资料：${item.title}`, reference].join('\n\n'));
      if (location.hash === origin) setNotice('已复制引用，可以粘贴到需要使用这份资料的对话或任务。');
    } catch {
      if (location.hash === origin) setCopyError('无法写入剪贴板，可以选择正文或网址复制。');
    }
  }

  return <section className="wd-resource-space">
    <aside className="wd-resource-index" aria-label="资料空间导航">
      <header><Library size={20} aria-hidden="true"/><span>资料空间</span></header>
      <nav aria-label="资料分类">
        {destinations.map(({ route, label, Icon }) => <a key={route} href={href(route)} aria-current={!showingItem && route === view && !collectionId ? 'page' : undefined}><Icon aria-hidden="true"/>{label}</a>)}
      </nav>
      <section className="wd-resource-collections" aria-label="我的知识库">
        <header><h2>我的知识库</h2><Button type="button" variant="ghost" size="icon-sm" aria-label="新建知识库" onClick={() => begin('collection')}><Plus/></Button></header>
        <nav aria-label="知识库列表">{catalog.collections.map(row => <a key={row.id} href={href('knowledge', row.id)} aria-current={collectionId === row.id ? 'page' : undefined}><BookOpen aria-hidden="true"/><span title={row.title}>{row.title}</span></a>)}</nav>
      </section>
    </aside>
    <div className="wd-resource-content" aria-busy={busy}>
      <header className="wd-resource-header">
        <div>
          {showingItem && <a href={backTo.current} className="wd-resource-back"><ArrowLeft size={15}/>返回资料</a>}
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {!showingItem && !inTrash && <div className="wd-resource-heading-actions">
          <details ref={newMenu} className="wd-resource-menu"><summary><Plus size={16}/>新建</summary>
            <div>
              <button type="button" onClick={() => begin('page')}><FilePlus2/>新建页面</button>
              {onNewPresentation && <button type="button" onClick={() => { if (newMenu.current) newMenu.current.open = false; onNewPresentation(); }}><Presentation/>新建演示文稿</button>}
              <button type="button" onClick={() => begin('link')}><Link2/>收录链接</button>
              <button type="button" onClick={() => begin('file')}><Upload/>收录文件</button>
              <button type="button" onClick={() => begin('collection')}><BookOpen/>新建知识库</button>
            </div>
          </details>
        </div>}
        {showingItem && item && <div className="wd-resource-heading-actions">
          {!item.trashed && reference?.trim() && <Button type="button" variant="outline" onClick={() => void copyReference()}><ClipboardCopy size={15}/>复制引用</Button>}
          {!item.trashed && <Button type="button" variant="outline" onClick={() => begin(item.kind, item)}><Pencil size={15}/>编辑</Button>}
          <Button type="button" variant="ghost" onClick={() => void changeTrash(item)} disabled={actionBusy}>{item.trashed ? <RotateCcw size={15}/> : <Trash2 size={15}/>
          }{item.trashed ? '恢复资料' : '移入回收站'}</Button>
        </div>}
      </header>

      {!showingItem && <div className="wd-resource-toolbar">
        <label className="wd-resource-search"><Search size={17} aria-hidden="true"/><input type="search" maxLength={300} aria-label={showingCollections ? '搜索知识库' : '搜索资料'} value={query} onChange={event => filter(event.target.value, category)} placeholder={showingCollections ? '搜索知识库' : '搜索资料'}/></label>
        {(query || category) && <Button type="button" variant="ghost" size="sm" onClick={() => filter('', '')}>清除筛选</Button>}
        {!showingCollections && <><div className="wd-resource-filter" aria-label="资料类型"><button type="button" aria-pressed={!category} onClick={() => filter(query, '')}>全部</button>{Object.entries(categories).map(([name, config]) => <button type="button" key={name} aria-pressed={category === name} onClick={() => filter(query, name as Category)}>{config.label}</button>)}</div>
          <div className="wd-resource-layout" aria-label="显示方式"><Button type="button" variant="ghost" size="icon-sm" aria-label="卡片视图" aria-pressed={layout === 'cards'} onClick={() => setLayout('cards')}><Grid2X2/></Button><Button type="button" variant="ghost" size="icon-sm" aria-label="列表视图" aria-pressed={layout === 'list'} onClick={() => setLayout('list')}><List/></Button></div></>}
      </div>}

      {error && <div className="wd-resource-error" role="alert"><span>{error}</span><Button type="button" variant="outline" onClick={() => setRevision(current => current + 1)}>刷新</Button></div>}
      {copyError && <p className="wd-resource-error" role="alert">{copyError}</p>}
      {notice && <p className="wd-resource-notice" role="status">{notice}</p>}
      {busy && <p className="wd-resource-muted" role="status">{loadedFor === listingKey ? '正在更新资料…' : '正在读取资料…'}</p>}
      {!busy && !error && showingItem && item && <div className="wd-resource-detail">
        <div className="wd-resource-item-info"><span>{categories[item.category]?.label || '文件'}</span><time dateTime={new Date(item.updated * 1000).toISOString()}>更新于 {dateLabel(item.updated)}</time>{item.trashed && <span>回收站</span>}</div>
        {item.note && <p className="wd-resource-item-note">{item.note}</p>}
        {item.tags?.length ? <div className="wd-resource-tags">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div> : null}
        {item.kind === 'page' && <section className="wd-resource-detail-card"><Markdown text={item.body || ''}/></section>}
        {item.kind === 'file' && <FilePreview key={item.id} item={item}/>}
        {item.kind === 'link' && <section className="wd-resource-detail-card wd-resource-link"><Visual category="link"/>{link ? <a href={link.href} target="_blank" rel="noopener noreferrer">{link.href}<ArrowUpRight size={15}/></a> : <p>网址不可用。</p>}</section>}
      </div>}

      {loadedFor === listingKey && !showingItem && showingCollections && (collectionRows.length ? <div className="wd-resource-grid">
        {collectionRows.map(row => <article key={row.id} className="wd-resource-card"><a href={href('knowledge', row.id)}><Visual category="page"/><strong>{row.title}</strong><small>{row.description || '知识库'}</small></a></article>)}
      </div> : !busy && !error && <Empty icon={<BookOpen size={26}/>} title={query.trim() ? '没有匹配的知识库' : '还没有知识库'}>{query.trim() ? <Button type="button" variant="outline" onClick={() => filter('', '')}>清除筛选</Button> : <Button type="button" variant="outline" onClick={() => begin('collection')}><Plus size={15}/>新建知识库</Button>}</Empty>)}

      {loadedFor === listingKey && !showingItem && !showingCollections && (rows.length ? <div className={'wd-resource-grid wd-resource-' + layout}>
        {rows.map(row => <article className="wd-resource-card" key={row.id}>
          <a href={itemHref(row.id)}><Visual category={row.category}/><span className="wd-resource-card-text"><strong>{row.title}</strong><small>{categories[row.category]?.label || '文件'} · {dateLabel(row.trashed_at || row.updated)}</small></span></a>
          <details className="wd-resource-card-menu"><summary aria-label={'更多操作：' + row.title}><Ellipsis aria-hidden="true" size={18}/></summary><div><button type="button" disabled={actionBusy} onClick={() => void changeTrash(row)}>{row.trashed ? <RotateCcw size={15}/> : <Trash2 size={15}/>} {row.trashed ? '恢复资料' : '移入回收站'}</button></div></details>
        </article>)}
      </div> : !busy && !error && <Empty icon={inTrash ? <Trash2 size={26}/> : <FolderOpen size={26}/>} title={query.trim() || category ? '没有匹配的资料' : inTrash ? '回收站为空' : '还没有收录资料'}>
        {(query.trim() || category) && <Button type="button" variant="outline" onClick={() => filter('', '')}>清除筛选</Button>}
        {!query.trim() && !category && !inTrash && <div className="wd-resource-empty-actions"><Button type="button" variant="outline" onClick={() => begin('page')}><FilePlus2 size={15}/>新建页面</Button><Button type="button" variant="ghost" onClick={() => begin('file')}><Upload size={15}/>收录文件</Button></div>}
      </Empty>)}
    </div>
    {editor && <ResourceDialog state={editor} collectionId={collectionId} collections={catalog.collections} onClose={() => setEditor(null)} onSave={save}/>}
  </section>;
}
