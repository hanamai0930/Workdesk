import { useEffect, useState, type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { NativeSelect } from '../components/ui/native-select';
import './settings-accounts.css';

const providers = { codex: 'Codex', deepseek: 'DeepSeek', qwen: 'Qwen' };
const modelFamilies = { codex: 'GPT', deepseek: 'DeepSeek', qwen: 'Qwen' };
type Provider = keyof typeof providers;
type Selection = { model: string; effort: string; tier: string };
type Model = {
  model: string;
  display_name?: string;
  selection_efforts?: string[];
  efforts: string[];
  tiers: string[];
  runnable: boolean;
};
type Profile = Selection & { id: Provider; runtime_present: boolean; model_catalog: { revision: string; models: Model[] } };
type Resources = {
  connections: { provider: Provider; is_default: boolean; available: boolean; enabled: boolean; quota_id: string }[];
  quota_sources: { id: string; kind: string; name: string; revision: number }[];
};
type Accounts = { profiles: Profile[]; execution_resources: Resources };
type Status = { text: string; error?: boolean };
type Bridge = {
  WorkdeskSettingsPreferences: {
    get(): { workProvider: Provider };
    save(key: 'workProvider', value: Provider): Promise<{ workProvider: Provider }>;
  };
  WorkdeskSupport: {
    request<T>(route: string, value?: object): Promise<T>;
    command(kind: string, value: object): Promise<unknown>;
  };
};

const effortLabels: Record<string, string> = {
  off: '关闭思考', none: '关闭思考', minimal: '极低', low: '低', medium: '中', high: '高',
  xhigh: '很高', max: '极高', ultra: '最高',
};
const tierLabels: Record<string, string> = { standard: '标准', fast: '快速' };
const providerIds = Object.keys(providers) as Provider[];
const selectedDefaults = ({ model, effort, tier }: Selection) => ({ model, effort, tier });

function AccountCard({ title, children }: { title?: string; children: ReactNode }) {
  return <Card className="wd-settings-card gap-0 py-0 shadow-none">
    {title && <h3 className="wd-settings-card-title">{title}</h3>}
    {children}
  </Card>;
}

function AccountRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className="wd-settings-row">
    <div className="wd-settings-copy"><span>{label}</span></div>
    <div className="wd-settings-value">{children}</div>
  </div>;
}

function SelectOptions({ values, labels, selection }: { values: string[]; labels: Record<string, string>; selection: string }) {
  return <>
    {!selection && <option value="" disabled>暂无可选项</option>}
    {selection && !values.includes(selection) && <option value={selection} disabled>{labels[selection] || selection} · 暂不可用</option>}
    {values.map(value => <option key={value} value={value}>{labels[value] || value}</option>)}
  </>;
}

function ModelDefaults({ profile, onSave, busy }: { profile: Profile; onSave: (selection: Selection) => void; busy: boolean }) {
  const [selection, setSelection] = useState(() => selectedDefaults(profile));
  const models = profile.model_catalog.models.filter(model => model.runnable);
  const current = models.find(model => model.model === selection.model);
  const efforts = current?.selection_efforts ?? current?.efforts ?? [];
  const tiers = current?.tiers ?? [];
  const disabled = busy || !profile.runtime_present || !models.length;
  const valid = !!current && efforts.includes(selection.effort) && tiers.includes(selection.tier);
  const changeModel = (model: string) => {
    const entry = models.find(candidate => candidate.model === model);
    if (!entry) return;
    const nextEfforts = entry.selection_efforts ?? entry.efforts;
    const choose = (values: string[], value: string, fallback: string) =>
      values.includes(value) ? value : values.includes(fallback) ? fallback : values[0] ?? '';
    setSelection({ model, effort: choose(nextEfforts, selection.effort, profile.effort),
      tier: choose(entry.tiers, selection.tier, profile.tier) });
  };
  return <AccountCard title="新工作的默认设置">
    <form className="wd-account-defaults" onSubmit={event => { event.preventDefault(); if (valid && !disabled) onSave(selection); }}>
      <div className="wd-account-fields">
        <label>默认模型<NativeSelect aria-label="默认模型" value={selection.model} disabled={disabled}
          onChange={event => changeModel(event.currentTarget.value)}>
          <SelectOptions values={models.map(entry => entry.model)} labels={Object.fromEntries(models.map(entry => [entry.model, modelFamilies[profile.id] + ' · ' + entry.model]))} selection={selection.model}/>
        </NativeSelect></label>
        <label>思考设置<NativeSelect aria-label="思考设置" value={selection.effort} disabled={disabled || !efforts.length}
          onChange={event => setSelection({ ...selection, effort: event.currentTarget.value })}>
          <SelectOptions values={efforts} labels={effortLabels} selection={selection.effort}/>
        </NativeSelect></label>
        {!!tiers.length && (tiers.length > 1 || !tiers.includes(selection.tier)) && <label>速度<NativeSelect aria-label="速度" value={selection.tier} disabled={disabled}
          onChange={event => setSelection({ ...selection, tier: event.currentTarget.value })}>
          <SelectOptions values={tiers} labels={tierLabels} selection={selection.tier}/>
        </NativeSelect></label>}
      </div>
      <div className="wd-account-actions"><Button type="submit" className="wd-settings-button wd-settings-primary" disabled={disabled || !valid}>保存默认设置</Button></div>
    </form>
  </AccountCard>;
}

export function SettingsAccounts({preferenceBlocked=false,initialProvider}: {preferenceBlocked?:boolean;initialProvider?:string}) {
  const { WorkdeskSupport: support, WorkdeskSettingsPreferences: preferences } = window as unknown as Bridge;
  const [defaultProvider, setDefaultProvider] = useState<Provider>(() => preferences.get().workProvider ?? 'codex');
  const [provider, setProvider] = useState<Provider>(initialProvider && Object.prototype.hasOwnProperty.call(providers,initialProvider) ? initialProvider as Provider : defaultProvider);
  const [accounts, setAccounts] = useState<Accounts | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [checkState, setCheckState] = useState<Partial<Record<Provider, boolean>>>({});
  const [failedLoad, setFailedLoad] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    support.request<Accounts>('workspace?view=workbench').then(data => {
      if (active) setAccounts(data);
    }).catch(() => {
      if (active) setFailedLoad(true);
    });
    return () => { active = false; };
  }, [support, refresh]);

  const profile = accounts?.profiles.find(item => item.id === provider);
  const resources = accounts?.execution_resources;
  const connection = resources?.connections.find(item => item.provider === provider && item.is_default);
  const quota = resources?.quota_sources.find(item => item.id === connection?.quota_id);
  const accountSource = provider === 'codex' ? '订阅账号' : '官方 API Key';
  const connectionText = connection?.enabled === false ? '已停用' : checkState[provider] === false ? '检查未通过'
    : !connection?.available ? '未连接' : checkState[provider] === true ? '已验证' : '已连接';

  async function perform(action: () => Promise<void>, success: string, failure: string) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      setStatus({ text: success });
    } catch {
      setStatus({ text: failure, error: true });
    } finally {
      setBusy(false);
    }
  }

  function changeDefault(value: Provider) {
    void perform(async () => {
      const updated = await preferences.save('workProvider', value);
      setDefaultProvider(updated.workProvider);
    }, '默认工作端已保存。', '无法保存默认工作端，请重试。');
  }

  function verifyConnection() {
    void perform(async () => {
      try {
        await support.request('profile-check', { provider });
        setAccounts(await support.request<Accounts>('workspace?view=workbench'));
        setCheckState(previous => ({ ...previous, [provider]: true }));
      } catch (error) {
        setCheckState(previous => ({ ...previous, [provider]: false }));
        throw error;
      }
    }, '连接检查通过。', '连接检查未通过，请核对账号或 API Key。');
  }

  function saveModelDefaults(selection: Selection) {
    void perform(async () => {
      await support.command('profile.select', { provider, ...selection });
      setAccounts(previous => previous && { ...previous,
        profiles: previous.profiles.map(item => item.id === provider ? { ...item, ...selection } : item) });
    }, '默认设置已保存。', '保存结果暂不可确认，请重新打开此页核对。');
  }

  return <div className="wd-settings-accounts">
    <section className="wd-settings-group"><AccountCard>
      <AccountRow label="独立工作默认工作端">
        <NativeSelect aria-label="独立工作默认工作端" value={defaultProvider} disabled={busy || preferenceBlocked}
          onChange={event => changeDefault(event.currentTarget.value as Provider)}>
          {providerIds.map(id => <option key={id} value={id}>{providers[id]}</option>)}
        </NativeSelect>
      </AccountRow>
    </AccountCard></section>
    <section className="wd-settings-group">
      <div className="wd-settings-section-header"><h2>工作端</h2>
        <div className="wd-settings-theme" role="group" aria-label="选择工作端">
          {providerIds.map(id => <Button key={id} type="button" variant="ghost" aria-pressed={provider === id} disabled={busy}
            onClick={() => { setProvider(id); setStatus(null); }}>{providers[id]}</Button>)}
        </div>
      </div>
      {accounts ? <AccountCard title={providers[provider]}>
        <AccountRow label="连接"><span className="wd-account-state" data-connected={connection?.available && checkState[provider] !== false}>{connectionText}</span></AccountRow>
        <AccountRow label="账号来源">{accountSource}</AccountRow>
        <AccountRow label="验证连接">
          <Button type="button" variant="outline" className="wd-settings-button" disabled={busy || !profile?.runtime_present}
            onClick={verifyConnection}><Check aria-hidden="true"/>检查连接</Button>
        </AccountRow>
        <AccountRow label="连接管理">
          <Button asChild variant="outline" className="wd-settings-button"><a href="#connect">查看连接选项</a></Button>
        </AccountRow>
      </AccountCard> : <AccountCard>
        <div className="wd-settings-empty">{failedLoad ?
          <><p role="alert">暂时无法读取账号与模型。</p>
            <Button type="button" variant="outline" className="wd-settings-button" onClick={() => { setFailedLoad(false); setRefresh(count => count + 1); }}>重新读取</Button></>
          : <span role="status">正在读取工作端…</span>}</div>
      </AccountCard>}
    </section>
    {status && <p className="wd-account-feedback" role={status.error ? 'alert' : 'status'}>{status.text}</p>}
    {profile && <section className="wd-settings-group"><ModelDefaults key={[provider, profile.model, profile.effort, profile.tier, profile.model_catalog.revision].join(':')}
      profile={profile} busy={busy} onSave={saveModelDefaults}/></section>}
    {profile && <section className="wd-settings-group"><AccountCard title="成员与额度来源">
      <AccountRow label="独立工作">{providers[provider]}</AccountRow>
      <AccountRow label="额度来源">{!quota ? '暂无记录' : quota.revision ? quota.name : quota.kind === 'subscription' ? '订阅额度' : 'API 余额'}</AccountRow>
      <AccountRow label="其他成员与连接">
        <Button asChild variant="outline" className="wd-settings-button"><a href="#control/profiles">管理成员与连接</a></Button>
      </AccountRow>
    </AccountCard></section>}
  </div>;
}
