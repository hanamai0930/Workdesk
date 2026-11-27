export type InstanceIdentity = {
  schema: number; channel: string; instance_id: string; process_instance: string;
  program_root: string; user_root: string; data_root: string;
  source_revision: string; source_dirty: boolean; implementation_revision: string; protocol: number;
  core_engine?: string; storage_schema?: number; owner_epoch?: number; legacy_bridge_protocol?: number;
};

export class CoreRequestError extends Error {
  constructor(message: string, public status?: number, public commandId?: string, public unknown = false) {
    super(message); this.name = 'CoreRequestError';
  }
}

type CommandContext = {id: string; kind: string};
const uncertain = new Map<string, CommandContext>();
let identity: InstanceIdentity | null = null;

function pendingKey() {
  return identity ? 'workdesk.core.pending:' + identity.instance_id + ':' + identity.data_root : '';
}

function savePending() {
  const key = pendingKey();
  if (!key || typeof sessionStorage === 'undefined') return;
  try { sessionStorage.setItem(key, JSON.stringify([...uncertain.values()])); } catch { /* The live window still retains the command identities. */ }
}

function restorePending() {
  const key = pendingKey();
  if (!key || typeof sessionStorage === 'undefined') return;
  try {
    const values = JSON.parse(sessionStorage.getItem(key) || '[]');
    if (Array.isArray(values)) for (const value of values) {
      if (typeof value?.id === 'string' && typeof value?.kind === 'string') uncertain.set(value.id, {id: value.id, kind: value.kind});
    }
  } catch { /* Corrupt local display state never authorizes another send. */ }
}

function updatePending(command: CommandContext, retain: boolean, scope: string) {
  if (scope === pendingKey()) {
    if (retain) uncertain.set(command.id, command); else uncertain.delete(command.id);
    savePending();
    return;
  }
  if (!scope || typeof sessionStorage === 'undefined') return;
  try {
    const saved = JSON.parse(sessionStorage.getItem(scope) || '[]');
    const rows = Array.isArray(saved) ? saved.filter(value => value?.id !== command.id) : [];
    if (retain) rows.push(command);
    sessionStorage.setItem(scope, JSON.stringify(rows));
  } catch { /* An old instance response must never change the newly selected library. */ }
}

const protectedCommands: Readonly<Record<string,string>> = {
  'v1/chat-api-key': 'chat.api-key.save',
  'v1/chat-api-key/reuse': 'chat.api-key.reuse',
  'v1/chat-api-key/select': 'chat.api-key.select',
};

function commandContext(route: string, options?: RequestInit): CommandContext | undefined {
  if ((route !== 'v1/command' && !protectedCommands[route]) || typeof options?.body !== 'string') return;
  try {
    const value = JSON.parse(options.body);
    const kind = protectedCommands[route] || value.kind;
    if (typeof value.id === 'string' && typeof kind === 'string') return {id: value.id, kind};
  } catch { /* Invalid bodies are rejected by the service. */ }
}

function url(route: string) {
  if (!route || route.startsWith('/') || route.includes('://') || route.split('?')[0].split('/').includes('..')) {
    throw new CoreRequestError('读取位置无效。');
  }
  return 'api/' + route;
}

async function response(route: string, options?: RequestInit) {
  return fetch(url(route), {cache: 'no-store', credentials: 'omit', ...options});
}

async function request<T = any>(route: string, options?: RequestInit): Promise<T> {
  const command = commandContext(route, options);
  const scope = pendingKey();
  if (command) updatePending(command, true, scope);
  let res: Response;
  let data: any;
  try {
    res = await response(route, options);
    data = await res.json();
  } catch (error) {
    if (command) {
      updatePending(command, true, scope);
      throw new CoreRequestError('操作回执未知，请核对原操作；不会自动重发。', undefined, command.id, true);
    }
    throw error;
  }
  if (!res.ok) {
    const unknown = !!command && (data.state === 'UNKNOWN' || data.no_resend === true ||
      (!!protectedCommands[route] && res.status >= 500 && data.explicit_rejection !== true));
    if (command) updatePending(command, unknown, scope);
    throw new CoreRequestError(data.error || '记录读取失败，当前状态未知。', res.status, command?.id, unknown);
  }
  if (command) updatePending(command, false, scope);
  return data as T;
}

async function loadIdentity(signal?: AbortSignal) {
  const value = await request<InstanceIdentity>('v1/instance', {signal});
  if (value.schema !== 1 || ![1, 2].includes(value.protocol) ||
      typeof value.instance_id !== 'string' || typeof value.data_root !== 'string') {
    throw new CoreRequestError('实例身份或协议不受支持。');
  }
  if (identity && (identity.instance_id !== value.instance_id || identity.data_root !== value.data_root)) uncertain.clear();
  identity = value;
  restorePending();
  return value;
}

async function command<T = any>(kind: string, payload: unknown, id = crypto.randomUUID()) {
  const frozen = structuredClone({id, kind, payload});
  return request<{receipt: T; command_id: string; replayed: boolean}>('v1/command', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(frozen),
  });
}

async function receipt(id: string) {
  const value = await request('v1/receipt?id=' + encodeURIComponent(id));
  if (value.state === 'RECORDED') { uncertain.delete(id); savePending(); }
  return value;
}

/** Each new read invalidates earlier responses; commands are deliberately not cancelled. */
function createReadScope() {
  let generation = 0;
  let controller: AbortController | undefined;
  return {
    begin() {
      controller?.abort(); controller = new AbortController();
      const current = ++generation;
      return {signal: controller.signal, isCurrent: () => current === generation && !controller?.signal.aborted};
    },
    cancel() { ++generation; controller?.abort(); },
  };
}

export const coreClient = {
  request, command, receipt, loadIdentity, createReadScope,
  identity: () => identity,
  uncertainCommands: () => [...uncertain.values()],
  async readText(route: string, options?: RequestInit) {
    const res = await response(route, options);
    if (!res.ok) throw new CoreRequestError('成果读取失败，请重新核对文件版本。', res.status);
    return res.text();
  },
};

export type CoreClient = typeof coreClient;
