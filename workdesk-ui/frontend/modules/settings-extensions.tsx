import { useEffect, useState, useSyncExternalStore } from 'react';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { NativeSelect } from '../components/ui/native-select';
import { ManagementForm } from './management';

type ManagementRequest = {
  id: string;
  state: string;
  text: string;
};

type ManagementBridge = {
  render(): Promise<void>;
  snapshot(): { data?: { requests: ManagementRequest[] }; error?: string };
  subscribe(listener: () => void): () => void;
};

type ExtensionEntry = {
  id: string;
  revision: string;
  name: string;
  enabled: boolean;
};

type ExtensionCatalog = {
  capabilities: (ExtensionEntry & { kind: string })[];
  extensions: ExtensionEntry[];
  checked_tools: Record<string, unknown[]>;
  scenes: {
    categories: { id: string; name: string }[];
    presets: (ExtensionEntry & { scene: string; default: boolean })[];
  };
};

type Support = {
  request<T>(route: string): Promise<T>;
  command<T>(kind: string, payload: object): Promise<T>;
};

const extensionTabs = [
  ['skills', 'Skills'], ['mcp', 'MCP 连接'], ['scenes', '场景与预设'],
] as const;

function support() {
  return (window as Window & { WorkdeskSupport?: Support }).WorkdeskSupport!;
}

function SettingsLink({ href, label }: { href: string; label: string }) {
  return <Button asChild variant="outline" className="wd-settings-button"><a href={href}>{label}</a></Button>;
}

function extensionEditor(tab: typeof extensionTabs[number][0], entry?: ExtensionEntry) {
  const action = tab === 'skills' ? 'view' : 'edit';
  return `#extensions/${tab}/${entry ? `${action}/${encodeURIComponent(entry.id)}` : 'add'}`;
}

export function SettingsExtensions() {
  const [catalog, setCatalog] = useState<ExtensionCatalog>();
  const [tab, setTab] = useState<typeof extensionTabs[number][0]>('skills');
  const [sceneId, setSceneId] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setError('');
    support().request<ExtensionCatalog>('extensions').then(data => {
      if (active) setCatalog(data);
    }).catch(() => {
      if (active) setError('无法读取扩展列表。');
    });
    return () => { active = false; };
  }, [refresh]);

  async function toggle(row: ExtensionEntry, type: 'capability' | 'extension') {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await support().command(`${type}.enable`, { id: row.id, revision: row.revision, enabled: !row.enabled });
      setCatalog(await support().request<ExtensionCatalog>('extensions'));
    } catch {
      setError('未确认启用状态，请刷新列表。');
    } finally {
      setBusy(false);
    }
  }

  const scenes = catalog?.scenes.categories || [];
  const selectedScene = scenes.find(scene => scene.id === sceneId) || scenes[0];
  const presets = catalog?.scenes.presets.filter(preset => preset.scene === selectedScene?.id) || [];
  const defaultPreset = presets.find(preset => preset.default && preset.enabled);
  const entries = tab === 'mcp' ? catalog?.extensions : catalog?.capabilities.filter(entry => entry.kind === 'skill');
  const panelTitle = tab === 'skills' ? '已导入 Skills' : tab === 'mcp' ? 'MCP 连接' : '场景';
  return <>
    <div className="wd-settings-section-header">
      <div className="wd-settings-theme" role="group" aria-label="扩展分类">
        {extensionTabs.map(([id, label]) => <Button key={id} variant="ghost" aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</Button>)}
      </div>
      <SettingsLink href={extensionEditor(tab)} label={tab === 'skills' ? '导入 Skill' : tab === 'mcp' ? '添加连接' : '新建预设'}/>
    </div>
    {error && <div className="wd-settings-row" role="alert"><span className="wd-settings-copy">{error}</span>
      <Button variant="outline" className="wd-settings-button" disabled={busy} onClick={() => setRefresh(count => count + 1)}>刷新列表</Button>
    </div>}
    <section className="wd-settings-group">
      <Card className="wd-settings-card gap-0 py-0 shadow-none">
        <h2 className="wd-settings-card-title">{panelTitle}</h2>
        {!catalog && !error && <p className="wd-settings-empty" role="status">正在读取扩展…</p>}
        {catalog && (tab === 'scenes' ? <>
          <div className="wd-settings-row">
            <div className="wd-settings-copy"><h3>选择场景</h3></div>
            <div className="wd-settings-value"><NativeSelect aria-label="选择场景" value={selectedScene?.id || ''} onChange={event => setSceneId(event.currentTarget.value)}>
              {scenes.map(scene => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
            </NativeSelect></div>
          </div>
          <div className="wd-settings-row">
            <div className="wd-settings-copy"><h3>默认预设</h3></div>
            <div className="wd-settings-value">{defaultPreset?.name || '工作端默认'}
              {defaultPreset && <SettingsLink href={extensionEditor('scenes', defaultPreset)} label="编辑预设"/>}
            </div>
          </div>
          <div className="wd-settings-actions"><SettingsLink href="#extensions/scenes" label="管理所有预设"/></div>
        </> : entries?.length ? entries.map(row => {
          const checked = tab === 'mcp' && Array.isArray(catalog.checked_tools?.[row.id]);
          return <div className="wd-settings-row" key={row.id}>
            <div className="wd-settings-copy"><h3>{row.name}</h3></div>
            <div className="wd-settings-value">
              {checked && <span>已检查</span>}
              <SettingsLink href={extensionEditor(tab, row)} label={tab === 'skills' ? '查看' : '管理'}/>
              <input className="wd-settings-switch" type="checkbox" role="switch" aria-label={`启用 ${row.name}`}
                checked={row.enabled} disabled={busy} onChange={() => void toggle(row, tab === 'mcp' ? 'extension' : 'capability')}/>
            </div>
          </div>;
        }) : <p className="wd-settings-empty">{tab === 'skills' ? '暂无 Skills。' : '暂无 MCP 连接。'}</p>)}
      </Card>
    </section>
    <section className="wd-settings-group">
      <Card className="wd-settings-card gap-0 py-0 shadow-none">
        <div className="wd-settings-row"><div className="wd-settings-copy"><h3>插件包</h3></div>
          <div className="wd-settings-value"><SettingsLink href="#extensions/plugins" label="管理插件"/></div>
        </div>
        <div className="wd-settings-row"><div className="wd-settings-copy"><h3>加载与操作记录</h3></div>
          <div className="wd-settings-value"><SettingsLink href="#extensions/records" label="查看记录"/></div>
        </div>
      </Card>
    </section>
  </>;
}

const requestStates: Record<string, string> = {
  PARSING: '正在解析', WAITING_INPUT: '等待补充', NEEDS_RECONCILIATION: '待核对', APPLIED: '已处理', FAILED: '处理失败', CANCELLED: '已取消',
};

export function SettingsManagementSummary() {
  const management = (window as Window & { WorkdeskManagement?: ManagementBridge }).WorkdeskManagement!;
  const state = useSyncExternalStore(management.subscribe, management.snapshot);
  const requests = state.data?.requests;
  useEffect(() => {
    void management.render();
  }, [management]);
  return <>
    <section className="wd-settings-group"><Card className="wd-settings-card gap-0 py-0 shadow-none">
      <h2 className="wd-settings-card-title">新请求</h2>
      <ManagementForm {...state} compact/>
    </Card></section>
    <section className="wd-settings-group"><Card className="wd-settings-card gap-0 py-0 shadow-none">
      <h2 className="wd-settings-card-title">最近请求</h2>
      {requests?.slice(0, 5).map(row => <div className="wd-settings-row" key={row.id}>
        <div className="wd-settings-copy"><h3>{row.text.length > 120 ? row.text.slice(0, 120) + '…' : row.text}</h3></div>
        <div className="wd-settings-value"><span>{requestStates[row.state] || '待查看'}</span>
          <SettingsLink href={'#management/' + encodeURIComponent(row.id)} label="查看"/>
        </div>
      </div>)}
      {!state.error && !requests?.length &&
        <p className="wd-settings-empty" role="status">{requests ? '暂无管理请求。' : '正在读取请求…'}</p>}
      {requests?.length ? <div className="wd-settings-actions"><SettingsLink href="#management" label="查看请求与详情"/></div> : null}
    </Card></section>
  </>;
}
