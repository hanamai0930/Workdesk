// One authenticated, API-only Qwen Code daemon owned by this worker process.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import readline from 'node:readline';
import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { prepareDaemonStorage } from './qwen_daemon_storage.mjs';

export async function startDaemon(config) {
  const storage = await prepareDaemonStorage(config);
  const { DaemonClient } = await import(pathToFileURL(join(config.sdk, 'dist/index.mjs')).href);
  const token = randomBytes(32).toString('hex');
  const privateSettings = config.model ? await startupSettings(config) : null;
  const child = spawn(process.execPath, [join(config.sdk, 'dist/cli/cli.js'), 'serve',
    '--hostname', '127.0.0.1', '--port', '0', '--workspace', config.workspace,
    '--require-auth', '--no-open', '--no-web', '--no-token-qr'], {
    cwd: config.workspace,
    env: { ...process.env, QWEN_SERVER_TOKEN: token, QWEN_CODE_NO_RELAUNCH: '1',
      QWEN_RUNTIME_DIR: storage.directory, QWEN_CODE_DISABLE_CRON: '1', QWEN_SERVE_NO_PERSISTENT_REGISTRATION: '1',
      ...(config.model && { OPENAI_API_KEY: config.token, OPENAI_BASE_URL: config.url, OPENAI_MODEL: config.model }),
      ...(privateSettings && { QWEN_CODE_SYSTEM_SETTINGS_PATH: join(privateSettings, 'settings.json') }) },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  let exited = false;
  let client;
  const closed = new Promise(resolve => {
    child.once('close', () => { exited = true; resolve(); });
  });
  const output = readline.createInterface({ input: child.stdout });
  const deadline = performance.now() + 20_000;
  let timeout;
  const address = new Promise((resolve, reject) => {
    timeout = setTimeout(() => reject(Error('Qwen daemon startup timed out')), 20_000);
    child.once('error', () => reject(Error('Qwen daemon could not start')));
    child.once('close', () => reject(Error('Qwen daemon exited during startup')));
    output.on('line', line => {
      const match = line.match(/^qwen serve listening on http:\/\/127\.0\.0\.1:(\d{1,5}) \(mode=/);
      const port = match && Number(match[1]);
      if (port && port <= 65535) resolve(`http://127.0.0.1:${port}`);
    });
  });
  const stop = async () => {
    client?.dispose();
    output.close();
    if (!exited) child.kill();
    await Promise.race([closed, delay(3000, undefined, { ref: false })]);
    if (!exited) throw Error('Qwen daemon exit was not observed');
    if (privateSettings) await rm(privateSettings, { recursive: true, force: true });
  };
  try {
    const baseUrl = await address;
    client = new DaemonClient({ baseUrl, token, fetchTimeoutMs: 5000 });
    while (!exited && performance.now() < deadline) {
      try {
        const health = await client.health();
        if (health.status === 'ok' && !exited) return { client, stop };
      } catch {
        // Native announcement can precede the authenticated health listener.
        // Poll only the originally announced address and this child's token.
      }
      await delay(125);
    }
    throw Error('Authenticated Qwen daemon did not become healthy');
  } catch (error) {
    await stop();
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function startupSettings(config) {
  // The Daemon model selector only resolves registered models. The SDK Query
  // path could select an environment-only model that is missing from that list.
  const systemPath = process.env.QWEN_CODE_SYSTEM_SETTINGS_PATH ||
    (process.platform === 'win32' ? 'C:/ProgramData/qwen-code/settings.json' :
      process.platform === 'darwin' ? '/Library/Application Support/QwenCode/settings.json' :
        '/etc/qwen-code/settings.json');
  let settings = {};
  try {
    settings = JSON.parse((await readFile(systemPath, 'utf8')).replace(/^\uFEFF/, ''));
  } catch (error) {
    if (error.code !== 'ENOENT') throw Error('Qwen system settings could not be read safely');
  }
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
    throw Error('Qwen system settings root is invalid');
  }
  settings.security ??= {};
  settings.security.auth ??= {};
  const auth = settings.security.auth;
  if (auth.enforcedType && auth.enforcedType !== 'openai') throw Error('Qwen native auth policy does not allow the Workdesk connection');
  auth.selectedType = 'openai';
  settings.modelProviders ??= {};
  const providers = settings.modelProviders.openai ?? [];
  if (!Array.isArray(providers)) throw Error('Qwen OpenAI provider settings are invalid');
  const existing = providers.find(model => model.id === config.model);
  if (existing && (existing.baseUrl !== config.url || existing.envKey !== 'OPENAI_API_KEY')) {
    throw Error('Qwen system model conflicts with the Workdesk connection');
  }
  if (!existing) {
    providers.push({ id: config.model, name: config.model, baseUrl: config.url,
      envKey: 'OPENAI_API_KEY',
      capabilities: config.daemon_model_capabilities ??
        { reasoning: { thinking: true, toggleOnly: true, disableField: 'enable_thinking', defaultEnabled: true } } });
  }
  settings.modelProviders.openai = providers;
  if (Object.keys(config.scene_mcp || {}).length) {
    settings.mcpServers = { ...settings.mcpServers, ...config.scene_mcp };
  }
  // Only Workdesk owns this loopback daemon. Suppress native server channel
  // autostart here while keeping the user's own Qwen daemon settings intact.
  settings.serve = { ...settings.serve, channels: [] };
  const encoded = JSON.stringify(settings);
  if ((config.token && encoded.includes(config.token)) || hasInlineCredentials(settings)) {
    throw Error('Inline credential in Qwen system overlay; use a native environment reference');
  }
  const dir = await mkdtemp(join(tmpdir(), 'workdesk-qwen-daemon-'));
  try {
    await writeFile(join(dir, 'settings.json'), encoded, { flag: 'wx', mode: 0o600 });
    return dir;
  } catch (error) {
    await rm(dir, { recursive: true, force: true });
    throw error;
  }
}

function hasInlineCredentials(value, field = '') {
  if (typeof value === 'string') {
    if (!value || /^\$(?:[A-Z_][A-Z0-9_]*|\{[A-Z_][A-Z0-9_]*\})$/i.test(value)) return false;
    if (/api[_-]?key|password|credential|secret|authorization|(?:access|refresh|bearer)[_-]?token|^token$/i.test(field) ||
        /^(?:bearer|basic)\s+/i.test(value)) return true;
    if (/^https?:\/\//i.test(value)) {
      const url = new URL(value);
      return !!(url.username || url.password || [...url.searchParams.keys()]
        .some(key => /token|key|signature|secret|password/i.test(key)));
    }
    return false;
  }
  return value !== null && typeof value === 'object' &&
    Object.entries(value).some(([key, item]) => hasInlineCredentials(item, key));
}
