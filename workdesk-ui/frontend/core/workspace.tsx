import {useEffect, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {coreClient as core, CoreRequestError, type InstanceIdentity} from './client';
import './workspace.css';

type Project = {id: string; name: string; root: string};
type RecordRow = {id: string; kind: string; title: string; project_id?: string; state?: string; revision?: number};
type Artifact = {path: string; sha256?: string; bytes?: number; mime?: string};
type LogSegment = {stream: string; segment: number; relative_path: string; bytes?: number; sha256?: string};
type Job = {id: string; state: string; project_id?: string; artifacts?: Artifact[]; logs?: LogSegment[]; summary?: unknown; error?: string};
type EventRow = {seq: number; at?: number; kind: string; task_id?: string};
type Page<T> = {cursor?: string | number; has_more?: boolean} & T;
const labels: Record<string, string> = {QUEUED: '排队中', RUNNING: '运行中', COMPLETED: '已完成', FAILED: '失败',
  CANCELLED: '已取消', STOP_REQUESTED: '正在停止', STOP_UNKNOWN: '停止状态待核对', ORPHANED_UNKNOWN: '重启后待核对'};
const live = new Set(['QUEUED', 'RUNNING', 'STOP_REQUESTED']);
const stateLabel = (value?: string) => value ? labels[value] || value : '已保存';
const errorText = (error: unknown) => error instanceof Error ? error.message : '读取失败，请重试。';
const artifactRoute = (job: string, file: string) => `v2/jobs/${encodeURIComponent(job)}/artifact?path=${encodeURIComponent(file)}`;

export default function CoreWorkspace({instance}: {instance: InstanceIdentity}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, selectProject] = useState('');
  const [projectPage, setProjectPage] = useState<Page<{}>>({});
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [recordPage, setRecordPage] = useState<Page<{}>>({});
  const [recordCursors, setRecordCursors] = useState<(string | number)[]>([0]);
  const [detail, setDetail] = useState<any>(null);
  const [detailLimit, setDetailLimit] = useState(12000);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [eventPage, setEventPage] = useState<Page<{}>>({});
  const [components, setComponents] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [unknownCommand, setUnknownCommand] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<{file: string; body: string} | null>(null);
  const [previewLimit, setPreviewLimit] = useState(12000);
  const [logPage, setLogPage] = useState<{job: string; log: LogSegment; next: number; total: number; hasMore: boolean} | null>(null);
  const eventCursor = useRef<string | number>(0);
  const life = useRef(0);
  const reads = useRef(core.createReadScope());
  const artifactReads = useRef(core.createReadScope());
  const logDecoder = useRef<TextDecoder | null>(null);
  const enabled = instance.core_engine === 'go' && instance.protocol === 2;

  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    const generation = ++life.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const current = () => life.current === generation && !controller.signal.aborted;
    const options = {signal: controller.signal};
    eventCursor.current = 0;
    setLoading(true); setError(''); setEvents([]); setDetail(null); setBusy(false);
    setUnknownCommand(core.uncertainCommands()[0]?.id || '');
    async function poll() {
      if (!current()) return;
      if (!document.hidden) {
        try {
          const [runtime, changes] = await Promise.all([
            core.request<{jobs: Job[]}>('v2/runtime', options),
            core.request<Page<{events: EventRow[]}>>('v2/events?' + new URLSearchParams({after: String(eventCursor.current), limit: '50'}), options),
          ]);
          if (!current()) return;
          setJobs(runtime.jobs || []);
          setEvents(prior => {
            const known = new Set(prior.map(row => row.seq));
            return [...prior, ...(changes.events || []).filter(row => !known.has(row.seq))].slice(-200);
          });
          if (changes.cursor !== undefined) eventCursor.current = changes.cursor;
          setEventPage(changes);
        } catch (error) { if (current()) setError(errorText(error)); }
      }
      if (current()) timer = setTimeout(poll, 1800);
    }
    Promise.all([
      core.request<Page<{projects: Project[]}>>('v2/projects?limit=50', options),
      core.request<Page<{records: RecordRow[]}>>('v2/records?limit=50', options),
      core.request<{components: any[]}>('v2/components', options),
    ]).then(([projectData, recordData, componentData]) => {
      if (!current()) return;
      setProjects(projectData.projects || []); setProjectPage(projectData);
      const preferred = document.documentElement.dataset.workdeskDefaultProject;
      selectProject(projectData.projects?.find(row => row.id === preferred)?.id || projectData.projects?.[0]?.id || '');
      setRecords(recordData.records || []); setRecordPage(recordData); setRecordCursors([0]);
      setComponents(componentData.components || []);
    }).catch(error => { if (current()) setError(errorText(error)); })
      .finally(() => { if (current()) { setLoading(false); void poll(); } });
    return () => { ++life.current; controller.abort(); clearTimeout(timer); reads.current.cancel(); artifactReads.current.cancel(); };
  }, [enabled, instance.instance_id, instance.data_root, instance.process_instance]);

  async function loadRecords(cursor: string | number, direction: 'next' | 'previous') {
    const read = reads.current.begin();
    setError('');
    try {
      const page = await core.request<Page<{records: RecordRow[]}>>('v2/records?' + new URLSearchParams({cursor: String(cursor), limit: '50'}), {signal: read.signal});
      if (!read.isCurrent()) return;
      setRecords(page.records || []); setRecordPage(page); setDetail(null);
      setRecordCursors(prior => direction === 'next' ? [...prior, cursor] : prior.slice(0, -1));
    } catch (error) { if (read.isCurrent()) setError(errorText(error)); }
  }

  async function loadDetail(row: RecordRow) {
    const read = reads.current.begin();
    setError(''); setDetail(null); setDetailLimit(12000);
    try {
      const value = await core.request(`v2/records/${encodeURIComponent(row.kind)}/${encodeURIComponent(row.id)}`, {signal: read.signal});
      if (read.isCurrent()) setDetail(value.record || value);
    } catch (error) { if (read.isCurrent()) setError(errorText(error)); }
  }

  async function moreProjects() {
    const read = reads.current.begin();
    try {
      const page = await core.request<Page<{projects: Project[]}>>('v2/projects?' + new URLSearchParams({cursor: String(projectPage.cursor || 0), limit: '50'}), {signal: read.signal});
      if (read.isCurrent()) { setProjects(prior => [...prior, ...(page.projects || [])]); setProjectPage(page); }
    } catch (error) { if (read.isCurrent()) setError(errorText(error)); }
  }

  async function execute(kind: string, payload: unknown) {
    if (busy || unknownCommand) return;
    const generation = life.current;
    setBusy(true); setError(''); setNotice('');
    try {
      await core.command(kind, payload);
      if (generation !== life.current) return;
      setNotice(kind === 'tool.run' ? '本地概要已加入队列。' : '停止请求已登记。');
      const value = await core.request<{jobs: Job[]}>('v2/runtime');
      if (generation === life.current) setJobs(value.jobs || []);
    } catch (error) {
      if (generation !== life.current) return;
      if (error instanceof CoreRequestError && error.unknown && error.commandId) setUnknownCommand(error.commandId);
      setError(errorText(error));
    } finally { if (generation === life.current) setBusy(false); }
  }

  async function reconcile() {
    setBusy(true);
    const generation = life.current;
    try {
      const result = await core.receipt(unknownCommand);
      if (generation !== life.current) return;
      if (result.state === 'RECORDED') { setUnknownCommand(''); setError(''); setNotice('原操作回执已核对。'); }
      else setNotice('原操作仍待核对，尚未重发。');
    } catch (error) { if (generation === life.current) setError(errorText(error)); }
    finally { if (generation === life.current) setBusy(false); }
  }

  async function showArtifact(job: Job, file: Artifact) {
    const read = artifactReads.current.begin();
    setPreview(null); setPreviewLimit(12000); setLogPage(null); logDecoder.current = null; setError('');
    try {
      const body = await core.readText(artifactRoute(job.id, file.path), {signal: read.signal});
      if (read.isCurrent()) setPreview({file: file.path, body});
    } catch (error) { if (read.isCurrent()) setError(errorText(error)); }
  }

  async function showLog(jobId: string, log: LogSegment, offset = 0) {
    const read = artifactReads.current.begin();
    setError('');
    if (!offset) { setPreview(null); setLogPage(null); setPreviewLimit(12000); logDecoder.current = new TextDecoder('utf-8'); }
    try {
      const page = await core.request<{data: string; next_offset: number; total_bytes: number; has_more: boolean}>(
        `v2/jobs/${encodeURIComponent(jobId)}/log?` + new URLSearchParams({stream: log.stream, segment: String(log.segment), offset: String(offset), limit: '65536'}),
        {signal: read.signal});
      if (!read.isCurrent()) return;
      if (typeof page.data !== 'string') throw new Error('日志读取结果无效。');
      const raw = Uint8Array.from(atob(page.data), character => character.charCodeAt(0));
      const text = (logDecoder.current ||= new TextDecoder('utf-8')).decode(raw, {stream: page.has_more});
      setPreview(prior => ({file: `${log.stream === 'stderr' ? '错误日志' : '运行日志'} · ${log.segment + 1}`, body: (offset ? prior?.body || '' : '') + text}));
      setLogPage({job: jobId, log, next: page.next_offset, total: page.total_bytes, hasMore: page.has_more});
      if (offset) setPreviewLimit(limit => limit + 12000);
    } catch (error) { if (read.isCurrent()) setError(errorText(error)); }
  }

  const detailBody = detail && [detail.goal, detail.body, detail.text, detail.content, detail.spec?.goal, detail.data?.goal]
    .find(value => typeof value === 'string');
  if (!enabled) return <section className="core-workspace"><h3>本地核心</h3><p>当前仍使用原核心，Go 核心接手后可在这里查看记录与运行本地概要工具。</p></section>;
  return <section className="core-workspace" data-core-workspace="">
    <div className="core-heading"><h3>本地工具与记录</h3><span>Go 核心 · 资料格式 {instance.storage_schema ?? '—'}</span></div>
    {loading && <p role="status">正在读取项目和记录…</p>}
    {error && <p className="core-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {unknownCommand && <div className="core-unknown"><p>待核对操作：<code>{unknownCommand}</code></p>
      <Button size="sm" variant="outline" disabled={busy} onClick={reconcile}>核对原操作</Button></div>}
    {!loading && <>
      <label className="core-project"><span>项目工作区</span><select value={project} onChange={event => selectProject(event.target.value)} disabled={busy}>
        {!projects.length && <option value="">暂无项目</option>}
        {projects.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
      </select></label>
      {project && <p className="core-path">{projects.find(row => row.id === project)?.root}</p>}
      {projectPage.has_more && <Button size="sm" variant="outline" onClick={moreProjects}>加载更多项目</Button>}
      <div className="core-tool"><div><strong>文本与 CSV 概要</strong><p>读取所选项目的材料，生成概要和文件清单。</p></div>
        <Button size="sm" disabled={!project || busy || !!unknownCommand} onClick={() => execute('tool.run', {project_id: project})}>生成本地概要</Button></div>
      <div className="core-jobs" aria-live="polite">{jobs.slice(0, 20).map(job => <article className="core-job" key={job.id}>
        <div className="core-heading"><strong>{stateLabel(job.state)}</strong><code title={job.id}>{job.id.slice(0, 8)}</code>
          {live.has(job.state) && <Button size="sm" variant="outline" disabled={busy || !!unknownCommand} onClick={() => execute('tool.cancel', {invocation_id: job.id})}>停止</Button>}</div>
        {job.error && <p className="core-error">{job.error}</p>}
        {job.artifacts?.map(file => <div className="core-artifact" key={file.path}><span title={file.path}>{file.path.split(/[\\/]/).at(-1)}</span>
          <Button size="sm" variant="ghost" onClick={() => showArtifact(job, file)}>查看</Button>
          <a href={'api/' + artifactRoute(job.id, file.path) + '&download=1'} download>下载</a></div>)}
        {!!job.logs?.length && <details><summary>运行日志</summary>{job.logs.map(log => <div className="core-artifact" key={log.stream + ':' + log.segment}>
          <span>{log.stream === 'stderr' ? '错误日志' : '运行日志'} · {log.segment + 1}</span>
          <Button size="sm" variant="ghost" onClick={() => showLog(job.id, log)}>查看日志</Button>
        </div>)}</details>}
      </article>)}</div>
      {preview && <section className="core-preview"><div className="core-heading"><strong>{preview.file.split(/[\\/]/).at(-1)}</strong>
        <Button size="sm" variant="ghost" onClick={() => { artifactReads.current.cancel(); setPreview(null); setLogPage(null); logDecoder.current = null; }}>收起</Button></div>
        <pre>{preview.body.slice(0, previewLimit)}</pre>{preview.body.length > previewLimit ? <Button size="sm" variant="outline" onClick={() => setPreviewLimit(limit => limit + 12000)}>继续显示正文</Button> : logPage?.hasMore && <Button size="sm" variant="outline" onClick={() => showLog(logPage.job, logPage.log, logPage.next)}>继续读取日志</Button>}</section>}
      <details className="core-records"><summary>现有记录 · 本页 {records.length} 条</summary>
        <ul>{records.map(row => <li key={row.kind + ':' + row.id}>{row.kind === 'partner_run' ? <a href={'#work/partners/run/' + encodeURIComponent(row.id)}>{row.title || row.id}</a> : <button type="button" onClick={() => loadDetail(row)}>{row.title || row.id}</button>}<span>{stateLabel(row.state)}</span></li>)}</ul>
        {!records.length && <p>暂无记录。</p>}
        <div className="core-paging"><Button size="sm" variant="outline" disabled={recordCursors.length < 2} onClick={() => loadRecords(recordCursors.at(-2)!, 'previous')}>上一页</Button>
          <span>第 {recordCursors.length} 页</span><Button size="sm" variant="outline" disabled={!recordPage.has_more || recordPage.cursor == null} onClick={() => loadRecords(recordPage.cursor!, 'next')}>下一页</Button></div>
        {detail && <article className="core-preview"><strong>{detail.title || detail.name || '记录详情'}</strong>
          {detailBody ? <><pre>{detailBody.slice(0, detailLimit)}</pre>{detailBody.length > detailLimit && <Button size="sm" variant="outline" onClick={() => setDetailLimit(limit => limit + 12000)}>继续显示正文</Button>}</> : <p>记录身份与版本已读取，正文保留在原记录中。</p>}</article>}
      </details>
      <details><summary>最近事件 · 已显示 {events.length} 条</summary><ol className="core-events">{events.map(row => <li key={row.seq}><span>{row.seq}</span><code>{row.kind}</code></li>)}</ol>
        {eventPage.has_more && <p>后续事件正在按页读取。</p>}</details>
      <details><summary>工作端组件 · {components.length} 项</summary><ul className="core-components">{components.map((row, index) => { const status = row.status || row.state; return <li key={row.component_id || row.id || index}><span>{row.name || row.component_id || row.id}</span><span>{status === 'AVAILABLE' ? row.version === 'UNVERIFIED' ? '待核对' : '已准备' : status === 'MISSING' ? '未准备' : status || '待核对'}</span></li>; })}</ul></details>
    </>}
  </section>;
}
