import { useEffect, useRef, useState } from 'react';
import { Archive, Download, Ellipsis, Eye, Pencil, Plus, Trash2 } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { NativeSelect } from '../components/ui/native-select';
import './quick-documents.css';

type PageDraft = { title: string; body: string };
type PresentationDraft = { title: string; slides: PageDraft[] };
type SavedResource = { id: string; revision: number; kind: 'page' | 'file'; title: string };
type SavedRevision = { edit: number; item: SavedResource };
type DraftRow<T> = { id: string; created: number; updated?: number; draft: T; edit: number; saved: SavedRevision | null; copyOf?: string };
type SaveListener = (item: SavedResource) => void;
type ResourcePayload =
  | { title: string; kind: 'page'; body: string }
  | { title: string; kind: 'file'; file: { name: string; mime: string; data: string } };
type ResourceSupport = { command(kind: string, payload: unknown): Promise<{ item: unknown }> };
const blankPage: PageDraft = { title: '', body: '' };
const blankPresentation: PresentationDraft = { title: '', slides: [blankPage] };
const markdownPlugins = [remarkGfm];
const pptxMime = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
const fileLimit = 8 * 1024 * 1024;
const pageBodyLimit = 262144;

function isPage(value: unknown): value is PageDraft {
  return value !== null && typeof value === 'object' && 'title' in value && 'body' in value &&
    typeof value.title === 'string' && typeof value.body === 'string';
}

function isPresentation(value: unknown): value is PresentationDraft {
  return value !== null && typeof value === 'object' && 'title' in value && 'slides' in value &&
    typeof value.title === 'string' && Array.isArray(value.slides) && value.slides.length > 0 &&
    value.slides.every(isPage);
}

function isSavedResource(value: unknown): value is SavedResource {
  return value !== null && typeof value === 'object' && 'id' in value && 'revision' in value &&
    'kind' in value && 'title' in value && typeof value.id === 'string' && typeof value.revision === 'number' &&
    Number.isSafeInteger(value.revision) && (value.kind === 'page' || value.kind === 'file') && typeof value.title === 'string';
}

function isSavedRevision(value: unknown): value is SavedRevision {
  return value !== null && typeof value === 'object' && 'edit' in value && 'item' in value &&
    typeof value.edit === 'number' && Number.isSafeInteger(value.edit) && isSavedResource(value.item);
}

function useLocalDrafts<T extends { title: string }>(kind: string, empty: T, validate: (value: unknown) => value is T) {
  const legacyKey = 'workdesk.sidebar.draft.' + kind;
  const prefix = legacyKey + '.v2.';
  const selectionKey = legacyKey + '.selected';
  const deletedPrefix = legacyKey + '.deleted.';
  function fresh(id: string, created: number, draft = empty): DraftRow<T> {
    return { id, created, draft, edit: 0, saved: null };
  }
  function readRows() {
    const rows = new Map<string, DraftRow<T>>();
    let selected = 'legacy-0';
    const damaged = new Map<string, string>();
    const blockedIds = new Set<string>();
    function read(key: string) {
      const raw = localStorage.getItem(key);
      if (raw === null) return { raw, value: undefined };
      try { return { raw, value: JSON.parse(raw) }; }
      catch { return { raw, value: undefined }; }
    }
    // Read v2 first so even an unreadable key is reserved. A recovered legacy
    // entry must never write into the unreadable row's original storage slot.
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(prefix)) continue;
      const { raw, value: row } = read(key);
      if (raw === null) continue;
      const id = key.slice(prefix.length);
      if (localStorage.getItem(deletedPrefix + id) !== null) continue;
      if (id && row?.id === id && validate(row.draft) && Number.isSafeInteger(row.edit) && row.edit >= 0 &&
          Number.isFinite(row.created) && (row.saved === null || isSavedRevision(row.saved)) &&
          (row.copyOf === undefined || typeof row.copyOf === 'string') && (row.updated === undefined || Number.isFinite(row.updated))) {
        rows.set(id, row);
      } else {
        damaged.set(key, raw);
        blockedIds.add(id);
      }
    }
    function legacyId(index: number) {
      let id = 'legacy-' + index;
      while (blockedIds.has(id)) id += '-recovered';
      return id;
    }
    const { raw, value: old } = read(legacyKey);
    if (raw !== null) {
      const drafts: unknown[] = validate(old) ? [old] : Array.isArray(old?.drafts) ? old.drafts : [];
      if (!drafts.length) damaged.set(legacyKey, raw);
      drafts.forEach((draft, index) => {
        if (!validate(draft)) { damaged.set(legacyKey, raw); return; }
        const row = fresh(legacyId(index), index, draft);
        if (localStorage.getItem(deletedPrefix + row.id) !== null) return;
        if (Number.isSafeInteger(old?.edits?.[index])) row.edit = old.edits[index];
        if (isSavedRevision(old?.saves?.[index])) row.saved = old.saves[index];
        if (!rows.has(row.id)) rows.set(row.id, row);
      });
      if (Number.isInteger(old?.active)) selected = legacyId(old.active);
    }
    if (!rows.size) {
      const row = localStorage.getItem(deletedPrefix + legacyId(0)) !== null ? fresh(crypto.randomUUID(), Date.now()) : fresh(legacyId(0), 0);
      rows.set(row.id, row);
    }
    selected = sessionStorage.getItem(selectionKey) || localStorage.getItem(selectionKey) || selected;
    const list = [...rows.values()].sort((a,b) => a.created - b.created || a.id.localeCompare(b.id));
    return { rows: list, damaged, id: rows.has(selected) ? selected : list[0].id };
  }
  const [initial] = useState(() => {
    try { return { ...readRows(), failed: false }; }
    catch { return { rows: [fresh(crypto.randomUUID(), Date.now())], id: '', damaged: new Map<string, string>(), failed: true }; }
  });
  const [rows, setRows] = useState(initial.rows);
  const [requested, setRequested] = useState(initial.id || initial.rows[0].id);
  const [activeId, setActiveId] = useState(requested);
  const [damaged, setDamaged] = useState(initial.damaged);
  const [ready, setReady] = useState(false);
  const [saveFailed, setSaveFailed] = useState(initial.failed);
  const [deleteError, setDeleteError] = useState('');
  const [leaseGeneration, setLeaseGeneration] = useState(0);
  const lease = useRef<{ original: string; writable: string } | null>(null);
  const rowsRef = useRef(rows);
  function publish(list: DraftRow<T>[]) { rowsRef.current = list; setRows(list); }
  function remember(id: string) {
    try { sessionStorage.setItem(selectionKey,id); localStorage.setItem(selectionKey,id); } catch { setSaveFailed(true); }
  }
  function merge(row: DraftRow<T>, base = rowsRef.current) {
    const existing = base.some(x => x.id === row.id);
    return existing ? base.map(x => x.id === row.id ? row : x) : [...base, row];
  }
  useEffect(() => {
    let cancelled = false;
    let release!: () => void;
    const lifetime = new Promise<void>(resolve => { release = resolve; });
    async function hold(writable: string) {
      if (cancelled) return;
      // A lock is acquired before publishing a writable ID and covers every
      // synchronous edit, including the final keystroke before navigation.
      try {
        const snapshot = readRows();
        setDamaged(snapshot.damaged);
        if (snapshot.damaged.has(prefix + writable)) writable = 'private-' + crypto.randomUUID();
        const disk = snapshot.rows;
        const row = disk.find(x => x.id === requested) || rowsRef.current.find(x => x.id === requested);
        if (row) publish(merge(row, disk));
      } catch { setSaveFailed(true); }
      lease.current = { original: requested, writable };
      setActiveId(requested); setReady(true);
      await lifetime;
    }
    async function acquire() {
      if (!navigator.locks) {
        // This instance alone creates/writes this UUID. Other unsupported
        // clients always fork too. Never overwrite a shared record unlocked.
        await hold('private-' + crypto.randomUUID());
        return;
      }
      await navigator.locks.request(prefix + requested, { ifAvailable: true }, async lock => {
        if (cancelled) return;
        if (lock && !requested.startsWith('private-')) return hold(requested);
        const copyId = crypto.randomUUID();
        await navigator.locks.request(prefix + copyId, () => hold(copyId));
      });
    }
    void acquire().catch(() => { if (!cancelled) void hold('private-' + crypto.randomUUID()); });
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== legacyKey && !event.key.startsWith(prefix) && !event.key.startsWith(deletedPrefix)) return;
      try {
        const snapshot = readRows();
        const disk = snapshot.rows;
        setDamaged(snapshot.damaged);
        // Keep the editor's visible version stable while another window edits it.
        let current = rowsRef.current.find(x => x.id === (lease.current?.writable || requested))
          || rowsRef.current.find(x => x.id === requested);
        const lock = lease.current;
        if (current && localStorage.getItem(deletedPrefix + current.id) !== null) {
          // A delete in another window must not erase the text still open here,
          // nor may a later keystroke recreate the deleted record's identity.
          const copy = { ...current, id: 'private-' + crypto.randomUUID(), created: Date.now(), updated: Date.now(),
            copyOf: current.draft.title || '已删除草稿', saved: null };
          localStorage.setItem(prefix + copy.id, JSON.stringify(copy));
          if (lock) lock.writable = copy.id;
          else setRequested(copy.id);
          current = copy; setActiveId(copy.id); remember(copy.id);
        }
        if (lock && snapshot.damaged.has(prefix + lock.writable)) lock.writable = 'private-' + crypto.randomUUID();
        publish(current ? merge(current, disk) : disk);
      } catch { setSaveFailed(true); }
    };
    window.addEventListener('storage', onStorage);
    return () => { cancelled = true; lease.current = null; release(); window.removeEventListener('storage',onStorage); };
  }, [requested, prefix, leaseGeneration]);
  function persist(row: DraftRow<T>, replaceOriginal = false) {
    let base = rowsRef.current;
    try {
      localStorage.setItem(prefix + row.id,JSON.stringify(row));
      if (replaceOriginal) base = readRows().rows;
      setSaveFailed(false);
    } catch { setSaveFailed(true); }
    publish(merge(row,base)); setActiveId(row.id); remember(row.id);
  }
  const active = Math.max(0, rows.findIndex(x => x.id === activeId));
  const row = rows[active];
  function setDraft(draft: T) {
    const lock = lease.current;
    if (!lock) return;
    const copy = lock.writable !== row.id;
    persist({ ...row, id: lock.writable, draft, edit: row.edit + 1, updated: Date.now(),
      ...(copy ? { copyOf: row.draft.title || '未命名草稿', created: Date.now(), saved: null } : {}) }, copy);
  }
  function select(id: string) {
    if (id === activeId) return;
    setReady(false); setActiveId(id); setRequested(id); remember(id);
    // A conflict copy can switch back to its original without changing the
    // prior requested ID. Reacquire by releasing the previous editor instance.
    if (id === requested && id !== lease.current?.writable) setLeaseGeneration(n => n + 1);
  }
  function newDraft() {
    const next = fresh(crypto.randomUUID(), Date.now());
    try { localStorage.setItem(prefix + next.id,JSON.stringify(next)); } catch { setSaveFailed(true); }
    publish(merge(next)); select(next.id);
  }
  async function deleteDraft() {
    const target = rowsRef.current.find(entry => entry.id === activeId);
    if (!target) return;
    const dialogs = (window as Window & { WorkdeskDialogs?: { confirm(value: object): Promise<boolean> } }).WorkdeskDialogs;
    const message = `删除“${target.draft.title.trim() || '未命名草稿'}”的本地草稿？已收录资料和已导出文件不受影响。其他窗口仍在编辑的内容会保留为独立副本。`;
    const confirmed = dialogs ? await dialogs.confirm({ title: '删除草稿', message, confirmLabel: '删除草稿', tone: 'danger' }) : window.confirm(message);
    if (!confirmed) return;
    const latest = rowsRef.current.find(entry => entry.id === target.id);
    if (activeId !== target.id || !latest || latest.edit !== target.edit) { setDeleteError('草稿已变化，请重新核对后删除。'); return; }
    try {
      localStorage.setItem(deletedPrefix + target.id, String(Date.now()));
      localStorage.removeItem(prefix + target.id);
      const snapshot = readRows();
      publish(snapshot.rows); setDamaged(snapshot.damaged); setReady(false);
      setActiveId(snapshot.id); setRequested(snapshot.id); remember(snapshot.id);
      setLeaseGeneration(value => value + 1); setDeleteError('');
    } catch { setDeleteError('无法删除本地草稿，请重试。'); }
  }
  function recordSave(id: string, save: SavedRevision) {
    const lock = lease.current;
    const savedRow = rowsRef.current.find(x => x.id === id);
    if (!savedRow || !lock || (id !== lock.writable && id !== lock.original)) return;
    // A receipt belongs to the content revision sent. Keep a stale result from
    // marking subsequent edits as already collected.
    const owner = rowsRef.current.find(x => x.id === lock.writable) || savedRow;
    const copy = owner.id !== lock.writable;
    persist({ ...owner, id: lock.writable, saved: save,
      ...(copy ? {copyOf: savedRow.draft.title || '未命名草稿', created: Date.now()} : {}) }, copy);
  }
  return { active, id: row.id, drafts: rows.map(x => x.draft), copies: rows.map(x => x.copyOf),
    updated: rows.map(x => x.updated || (x.created > 1000000000000 ? x.created : 0)), deleteDraft, deleteError,
    draft: row.draft, edit: row.edit, saved: row.saved, recordSave, setDraft,
    chooseDraft: (index: number) => select(rows[index].id), newDraft, saveFailed, ready, damaged };
}

function DraftPicker({ drafts, copies, active, title, onSelect, onNew, onDelete, updated, disabled }: {
  drafts: { title: string }[]; copies: (string | undefined)[]; active: number; title: string; disabled?: boolean;
  onSelect: (index: number) => void; onNew: () => void; onDelete: () => void; updated: number[];
}) {
  const names = drafts.map(draft => draft.title.trim() || '未命名' + title);
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (disabled && menu.current) menu.current.open = false; }, [disabled]);
  function closeMenu() { if (menu.current) menu.current.open = false; }
  return <div className="wd-quick-document-picker">
    <NativeSelect value={active} aria-label={'继续' + title + '草稿'} disabled={disabled}
      onChange={event => { closeMenu(); onSelect(Number(event.currentTarget.value)); }}>
      {names.map((name, index) => <option key={index} value={index}>
        {names.indexOf(name) === names.lastIndexOf(name) ? name : name + ' · 草稿 ' + (index + 1)}{copies[index] && ' · 并行编辑副本'}
      </option>)}
    </NativeSelect>
    <div className="wd-quick-draft-actions">
    <Button size="sm" variant="outline" aria-label={'新建' + title + '草稿'} onClick={() => { closeMenu(); onNew(); }} disabled={disabled}>
      <Plus aria-hidden="true"/>新建{title}
    </Button>
    <details ref={menu} className="wd-quick-draft-menu" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) closeMenu(); }}
      onKeyDown={event => { if (event.key==='Escape' && menu.current?.open) { event.preventDefault(); event.stopPropagation(); closeMenu(); menu.current.querySelector('summary')?.focus(); } }}>
      <summary aria-label={title+'草稿更多操作'} aria-disabled={disabled || undefined} onClick={event => { if (disabled) event.preventDefault(); }}><Ellipsis aria-hidden="true"/>更多</summary>
      <div><Button size="sm" variant="ghost" className="wd-quick-delete-draft" aria-label={'删除当前' + title + '草稿'} onClick={() => { closeMenu(); onDelete(); }} disabled={disabled}><Trash2 aria-hidden="true"/>删除草稿</Button></div>
    </details>
    </div>
    <small className="wd-quick-draft-time">{updated[active] ? <>本地暂存于 <time dateTime={new Date(updated[active]).toISOString()}>{new Intl.DateTimeFormat('zh-CN', { month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit' }).format(updated[active])}</time> · </> : null}修改标题即可重命名</small>
  </div>;
}

function downloadName(title: string, fallback: string, extension: string) {
  const suffix = '.' + extension;
  const trimmed = title.trim();
  const stem = trimmed.toLowerCase().endsWith(suffix) ? trimmed.slice(0, -suffix.length) : trimmed;
  const name = stem
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, '-')
    .replace(/[. ]+$/, '')
    .slice(0, 100);
  return (name || fallback) + suffix;
}

function DraftRecovery({ damaged }: { damaged: Map<string, string> }) {
  if (!damaged.size) return null;
  function download() {
    const content = JSON.stringify({ format: 'workdesk-damaged-drafts-v1', records: Object.fromEntries(damaged) }, null, 2);
    downloadText(content, 'application/json;charset=utf-8', 'Workdesk-草稿异常原始记录.json');
  }
  return <div className="wd-quick-document-status">
    <p role="status">有 {damaged.size} 条草稿存储记录无法完整读取。已保留原始内容，可下载供恢复。其他草稿可继续编辑。</p>
    <Button variant="outline" size="sm" onClick={download}><Download aria-hidden="true"/>下载异常原始记录</Button>
  </div>;
}

function downloadMarkdown(draft: PageDraft) {
  const title = draft.title.trim();
  const content = (title ? '# ' + title + '\n\n' : '') + draft.body;
  downloadText(content, 'text/markdown;charset=utf-8', downloadName(title, '未命名页面', 'md'));
}

function downloadText(content: string, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function buildPresentation(draft: PresentationDraft) {
  const { default: PowerPoint } = await import('pptxgenjs');
  const pptx = new PowerPoint();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.title = draft.title.trim() || '未命名演示文稿';
  for (const entry of draft.slides) {
    const slide = pptx.addSlide();
    slide.background = { color: 'F4F7F5' };
    slide.addText(entry.title, {
      x: .75, y: .55, w: 11.8, h: 1.15, fontFace: 'Microsoft YaHei',
      fontSize: 32, bold: true, color: '254E41', margin: 0, fit: 'shrink',
    });
    slide.addText(entry.body, {
      x: .75, y: 1.95, w: 11.8, h: 4.9, fontFace: 'Microsoft YaHei',
      fontSize: 21, color: '22332D', margin: 0, valign: 'top', fit: 'shrink',
    });
  }
  return pptx;
}

async function downloadPresentation(draft: PresentationDraft) {
  const pptx = await buildPresentation(draft);
  await pptx.writeFile({ fileName: downloadName(draft.title, '未命名演示文稿', 'pptx') });
}

function readBase64(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('无法读取演示文稿。'));
    reader.onload = () => {
      const value = typeof reader.result === 'string' ? reader.result.split(',', 2)[1] : '';
      if (value) resolve(value);
      else reject(new Error('无法读取演示文稿。'));
    };
    reader.readAsDataURL(blob);
  });
}

function resourceTitle(title: string, fallback: string) {
  const result = title.trim() || fallback;
  if ([...result].length > 160) throw new Error('资料标题最多 160 个字符。');
  return result;
}

function pagePayload(draft: PageDraft): ResourcePayload {
  if (draft.body.length > pageBodyLimit && [...draft.body].length > pageBodyLimit) {
    throw new Error('页面正文最多 262,144 个字符。');
  }
  return { title: resourceTitle(draft.title, '未命名页面'), kind: 'page', body: draft.body };
}

async function presentationPayload(draft: PresentationDraft): Promise<ResourcePayload> {
  const title = resourceTitle(draft.title, '未命名演示文稿');
  const pptx = await buildPresentation(draft);
  const blob = await pptx.write({ outputType: 'blob', compression: true });
  if (!(blob instanceof Blob)) throw new Error('无法导出演示文稿。');
  if (blob.size > fileLimit) throw new Error('演示文稿超过资料空间单个文件 8 MB 的上限。');
  return {
    title,
    kind: 'file',
    file: { name: downloadName(draft.title, '未命名演示文稿', 'pptx'), mime: pptxMime, data: await readBase64(blob) },
  };
}

function useResourceSave({ id: active, edit, saved, recordSave }: {
  id: string;
  edit: number;
  saved: SavedRevision | null;
  recordSave: (index: string, save: SavedRevision) => void;
}, prepare: () => ResourcePayload | Promise<ResourcePayload>, onSaved?: SaveListener) {
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const [failure, setFailure] = useState<{ draft: string; message: string; canSaveCopy: boolean } | null>(null);
  const [savedNotice, setSavedNotice] = useState<SavedResource | null>(null);
  const listener = useRef(onSaved);
  listener.current = onSaved;
  useEffect(() => {
    if (savedNotice) listener.current?.(savedNotice);
  }, [savedNotice]);
  const current = saved?.edit === edit ? saved.item : null;
  async function save(asCopy = false) {
    if (submitting.current || current) return;
    const support = (window as Window & { WorkdeskSupport?: ResourceSupport }).WorkdeskSupport;
    submitting.current = true;
    setPending(true);
    setFailure(null);
    try {
      if (!support) throw new Error('当前无法连接资料空间。');
      const payload = await prepare();
      const prior = saved?.item;
      const receipt = !asCopy && payload.kind === 'page' && prior?.kind === 'page'
        ? await support.command('resource-space.update', {
          id: prior.id, revision: prior.revision, changes: { title: payload.title, body: payload.body },
        })
        : await support.command('resource-space.create', payload);
      if (!isSavedResource(receipt?.item) || receipt.item.kind !== payload.kind) {
        throw new Error('未收到资料空间的保存记录。');
      }
      const { id, revision, kind, title } = receipt.item;
      const item = { id, revision, kind, title };
      recordSave(active, { edit, item });
      setSavedNotice(item);
    } catch (error) {
      const message = error instanceof Error ? error.message : '收录到资料空间失败。';
      setFailure({ draft: active, message, canSaveCopy: !asCopy && message.startsWith('资料已更改') });
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }
  return { current, pending, failure: failure?.draft === active ? failure : null, save };
}

function ResourceSaveButton({ resource, disabled }: { resource: ReturnType<typeof useResourceSave>; disabled: boolean }) {
  return <Button variant="default" size="sm" className="wd-quick-resource-save" disabled={disabled || resource.pending || !!resource.current}
    onClick={() => void resource.save()}>
    <Archive aria-hidden="true"/>{resource.pending ? '正在收录…' : resource.current ? '已收录到资料空间' : '收录到资料空间'}
  </Button>;
}

function ResourceSaveFeedback({ resource }: { resource: ReturnType<typeof useResourceSave> }) {
  if (resource.failure) return <div className="wd-quick-document-save-feedback">
    <p className="wd-quick-document-status" role="alert">{resource.failure.message}</p>
    {resource.failure.canSaveCopy &&
      <Button variant="outline" size="sm" onClick={() => void resource.save(true)}>另存为新资料</Button>}
  </div>;
  if (!resource.current) return null;
  return <p className="wd-quick-document-status" role="status">
    {resource.current.kind === 'page' ? '页面已保存，可继续修改草稿。' : '此版本的 PowerPoint 已保存，可继续编辑草稿。'}
    <a href={'#resources/item/' + encodeURIComponent(resource.current.id)}>查看已保存的资料</a>
  </p>;
}

function PreviewButton({ preview, onClick }: { preview: boolean; onClick: () => void }) {
  return <Button variant="outline" size="sm" aria-pressed={preview} onClick={onClick}>
    {preview ? <Pencil aria-hidden="true"/> : <Eye aria-hidden="true"/>}
    {preview ? '编辑' : '预览'}
  </Button>;
}

function QuickPage({ onSaved }: { onSaved?: SaveListener }) {
  const local = useLocalDrafts('page', blankPage, isPage);
  const { drafts, active, draft, setDraft, chooseDraft, newDraft, saveFailed } = local;
  const resource = useResourceSave(local, () => pagePayload(draft), onSaved);
  const [preview, setPreview] = useState(false);
  const hasContent = Boolean(draft.title.trim() || draft.body.trim());
  if (!local.ready) return <p role="status">正在打开草稿…</p>;
  return <div className="wd-quick-document">
    {local.copies[active] && <p role="status">“{local.copies[active]}”同时在另一处打开。本窗口正在编辑单独的草稿副本，可在草稿列表切换。</p>}
    <DraftPicker drafts={drafts} copies={local.copies} updated={local.updated} active={active} title="页面" disabled={resource.pending}
      onDelete={() => { void local.deleteDraft().then(() => setPreview(false)); }} onSelect={index => {
      chooseDraft(index);
      setPreview(false);
    }} onNew={() => {
      newDraft();
      setPreview(false);
    }}/>
    <div className="wd-quick-document-tools">
      <ResourceSaveButton resource={resource} disabled={!hasContent}/>
      <div className="wd-quick-document-secondary">
      <PreviewButton preview={preview} onClick={() => setPreview(!preview)}/>
      <Button variant="outline" size="sm" disabled={!hasContent} onClick={() => downloadMarkdown(draft)}>
        <Download aria-hidden="true"/>导出 Markdown
      </Button>
      </div>
    </div>
    <ResourceSaveFeedback resource={resource}/>
    <DraftRecovery damaged={local.damaged}/>
    {saveFailed && <p className="wd-quick-document-status" role="status">无法保存草稿，可以先导出文件。</p>}
    {local.deleteError && <p className="wd-quick-document-status" role="alert">{local.deleteError}</p>}
    {preview ? <article className="wd-quick-page-preview">
      {draft.title.trim() && <h1>{draft.title.trim()}</h1>}
      <Markdown skipHtml remarkPlugins={markdownPlugins}
        components={{ img: ({ alt }) => alt ? <span>{alt}</span> : null }}>{draft.body}</Markdown>
      {!hasContent && <p className="wd-quick-document-status">页面为空。</p>}
    </article> : <>
      <Input type="text" aria-label="页面标题" placeholder="页面标题" value={draft.title}
        onChange={event => setDraft({ ...draft, title: event.currentTarget.value })}/>
      <textarea aria-label="Markdown 正文" placeholder="输入 Markdown 正文" value={draft.body}
        onChange={event => setDraft({ ...draft, body: event.currentTarget.value })}/>
    </>}
  </div>;
}

function QuickPresentation({ onSaved }: { onSaved?: SaveListener }) {
  const local = useLocalDrafts('presentation', blankPresentation, isPresentation);
  const { drafts, active, draft, setDraft, chooseDraft, newDraft, saveFailed } = local;
  const resource = useResourceSave(local, () => presentationPayload(draft), onSaved);
  const [selected, setSelected] = useState(0);
  const [preview, setPreview] = useState(false);
  const [pending, setPending] = useState(false);
  const [exportFailed, setExportFailed] = useState(false);
  const slide = draft.slides[Math.min(selected, draft.slides.length - 1)];
  const hasContent = draft.slides.some(item => item.title.trim() || item.body.trim());
  function editSlide(key: keyof PageDraft, value: string) {
    setDraft({ ...draft, slides: draft.slides.map((item, index) => index === selected ? { ...item, [key]: value } : item) });
  }
  function addSlide() {
    setSelected(draft.slides.length);
    setDraft({ ...draft, slides: [...draft.slides, blankPage] });
    setPreview(false);
  }
  function deleteSlide() {
    setSelected(Math.min(selected, draft.slides.length - 2));
    setDraft({ ...draft, slides: draft.slides.filter((_, index) => index !== selected) });
  }
  async function exportFile() {
    setPending(true);
    setExportFailed(false);
    try {
      await downloadPresentation(draft);
    } catch {
      setExportFailed(true);
    } finally {
      setPending(false);
    }
  }
  function resetEditor() {
    setSelected(0);
    setPreview(false);
    setExportFailed(false);
  }
  if (!local.ready) return <p role="status">正在打开草稿…</p>;
  return <div className="wd-quick-document">
    {local.copies[active] && <p role="status">“{local.copies[active]}”同时在另一处打开。本窗口正在编辑单独的草稿副本，可在草稿列表切换。</p>}
    <DraftPicker drafts={drafts} copies={local.copies} updated={local.updated} active={active} title="演示文稿" disabled={pending || resource.pending}
      onDelete={() => { void local.deleteDraft().then(resetEditor); }} onSelect={index => {
      chooseDraft(index);
      resetEditor();
    }} onNew={() => {
      newDraft();
      resetEditor();
    }}/>
    <div className="wd-quick-document-tools">
      <ResourceSaveButton resource={resource} disabled={!hasContent || pending}/>
      <div className="wd-quick-document-secondary">
      <PreviewButton preview={preview} onClick={() => setPreview(!preview)}/>
      <Button variant="outline" size="sm" disabled={!hasContent || pending || resource.pending} onClick={() => void exportFile()}>
        <Download aria-hidden="true"/>{pending ? '正在导出…' : '导出 PowerPoint'}
      </Button>
      </div>
    </div>
    <ResourceSaveFeedback resource={resource}/>
    <DraftRecovery damaged={local.damaged}/>
    {saveFailed && <p className="wd-quick-document-status" role="status">无法保存草稿，可以先导出文件。</p>}
    {local.deleteError && <p className="wd-quick-document-status" role="alert">{local.deleteError}</p>}
    {exportFailed && <p className="wd-quick-document-status" role="alert">演示文稿导出失败，请重试。</p>}
    <Input type="text" aria-label="演示文稿名称" placeholder="演示文稿名称" value={draft.title}
      onChange={event => setDraft({ ...draft, title: event.currentTarget.value })}/>
    <div className="wd-quick-document-slides">
      <div className="wd-quick-document-slide-list" role="group" aria-label="幻灯片">
        {draft.slides.map((item, index) => <Button key={index} variant={index === selected ? 'secondary' : 'ghost'} size="sm"
          aria-pressed={index === selected} onClick={() => setSelected(index)}>
          {index + 1}<span>{item.title.trim() || '无标题'}</span>
        </Button>)}
      </div>
      <Button variant="outline" size="icon-sm" aria-label="添加幻灯片" title="添加幻灯片" onClick={addSlide}>
        <Plus aria-hidden="true"/>
      </Button>
      <Button variant="ghost" size="icon-sm" aria-label="删除当前幻灯片" title="删除当前幻灯片"
        disabled={draft.slides.length < 2} onClick={deleteSlide}><Trash2 aria-hidden="true"/></Button>
    </div>
    {preview ? <><p className="wd-quick-document-status">幻灯片 {Math.min(selected + 1, draft.slides.length)} / {draft.slides.length}</p><article className="wd-quick-slide-preview" aria-label="当前幻灯片预览">
      {slide.title && <h2>{slide.title}</h2>}
      <p>{slide.body}</p>
    </article></> : <>
      <Input type="text" aria-label="幻灯片标题" placeholder="幻灯片标题" value={slide.title}
        onChange={event => editSlide('title', event.currentTarget.value)}/>
      <textarea aria-label="幻灯片正文" placeholder="输入幻灯片正文" value={slide.body}
        onChange={event => editSlide('body', event.currentTarget.value)}/>
    </>}
  </div>;
}

export function QuickDocuments({ kind, onSaved }: { kind: 'page' | 'presentation'; onSaved?: SaveListener }) {
  return kind === 'page' ? <QuickPage onSaved={onSaved}/> : <QuickPresentation onSaved={onSaved}/>;
}
