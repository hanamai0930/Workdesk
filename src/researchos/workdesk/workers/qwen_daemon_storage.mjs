// Installed Qwen Code 0.25.2 runtime storage. Only settled, exact Query
// journals are staged here. The official daemon reads and validates them
// before Workdesk binds the new runtime, and the old runtime stays read-only.
import { createHash } from 'node:crypto';
import { lstat, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';

const MAX_JOURNAL_BYTES = 64 * 1024 * 1024;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value;
const hash = value => createHash('sha256').update(value).digest('hex');
const knownSystemRecords = new Set(['attribution_snapshot', 'ui_telemetry']);

async function absent(path) {
  try { await lstat(path); return false; }
  catch (error) { if (error.code === 'ENOENT') return true; throw error; }
}

async function plain(path) {
  if (normalize(await realpath(path)) !== normalize(resolve(path))) {
    throw Error('Qwen runtime storage path resolves outside its declared identity');
  }
}

export async function prepareDaemonStorage(config) {
  if (!isAbsolute(config.daemon_runtime_dir || '')) {
    throw Error('Workdesk-owned Qwen Daemon runtime directory is required');
  }
  await mkdir(config.daemon_runtime_dir, { recursive: true });
  await plain(config.daemon_runtime_dir);
  const workspace = normalize(await realpath(config.workspace));
  const scheduled = join(config.daemon_runtime_dir, 'tmp', hash(workspace), 'scheduled_tasks.json');
  if (!await absent(scheduled)) {
    throw Error('Qwen runtime has a scheduled task store; use an empty Workdesk-owned runtime');
  }
  return { workspace, directory: resolve(config.daemon_runtime_dir) };
}

async function readJournal(path) {
  await plain(path);
  const info = await lstat(path);
  if (!info.isFile() || info.nlink !== 1 || !info.size || info.size > MAX_JOURNAL_BYTES) {
    throw Error('Legacy Qwen journal is not a supported standalone file');
  }
  const raw = await readFile(path);
  if (raw.length !== info.size) throw Error('Legacy Qwen journal changed while reading');
  return raw;
}

function checkJournal(raw, config, workspace) {
  const lines = new TextDecoder('utf-8', { fatal: true }).decode(raw).split(/\r?\n/);
  if (lines.at(-1) !== '') throw Error('Legacy Qwen journal has an incomplete final frame');
  lines.pop();
  let parent = null;
  let assistant = false;
  let user = false;
  for (const line of lines) {
    let record;
    try { record = JSON.parse(line); }
    catch { throw Error('Legacy Qwen journal contains an unsupported frame'); }
    if (record.sessionId !== config.session_id || !uuid.test(record.uuid) ||
        record.parentUuid !== parent || typeof record.cwd !== 'string' ||
        normalize(resolve(record.cwd)) !== workspace || record.version !== '0.25.2' ||
        record.forkedFrom || record.daemonPromptId) {
      throw Error('Legacy Qwen journal identity, workspace or version differs');
    }
    const type = record.type;
    if (type === 'system') {
      if (!knownSystemRecords.has(record.subtype)) throw Error('Legacy Qwen journal has unsupported runtime records');
    } else if (type === 'user' || type === 'assistant') {
      const message = record.message;
      if (typeof message?.content !== 'string' &&
          (!Array.isArray(message?.parts) ||
            message.parts.some(part => typeof part.text !== 'string' || Object.keys(part).some(key => key !== 'text' && key !== 'thought')))) {
        throw Error('Legacy Qwen journal contains tool or attachment state requiring separate migration');
      }
      if (type === 'user') user = true;
      else assistant = true;
    } else {
      throw Error('Legacy Qwen journal has unsupported linked history');
    }
    parent = record.uuid;
  }
  if (!user || !assistant) throw Error('Legacy Qwen journal lacks completed conversation history');
}

export async function stageQueryJournal(config) {
  if (config.settled_resume !== true || !uuid.test(config.session_id)) {
    throw Error('Workdesk did not bind the settled Qwen Query journal');
  }
  const runtime = await prepareDaemonStorage(config);
  const oldRoot = config.query_runtime_dir || process.env.QWEN_RUNTIME_DIR ||
    process.env.QWEN_HOME || join(homedir(), '.qwen');
  if (!isAbsolute(oldRoot) || normalize(resolve(oldRoot)) === normalize(runtime.directory)) {
    throw Error('Independent legacy Qwen runtime directory is required');
  }
  const project = runtime.workspace.replace(/[^a-zA-Z0-9]/g, '-');
  const sourceFolder = join(oldRoot, 'projects', project, 'chats');
  const source = join(sourceFolder, config.session_id + '.jsonl');
  for (const suffix of ['.ledger.jsonl', '.runtime.json']) {
    if (!await absent(join(sourceFolder, config.session_id + suffix))) {
      throw Error('Legacy Qwen journal has a sidecar requiring separate migration');
    }
  }
  const raw = await readJournal(source);
  checkJournal(raw, config, runtime.workspace);
  const targetFolder = join(runtime.directory, 'projects', project, 'chats');
  await mkdir(targetFolder, { recursive: true });
  await plain(targetFolder);
  const target = join(targetFolder, config.session_id + '.jsonl');
  try {
    await writeFile(target, raw, { flag: 'wx', mode: 0o600 });
  } catch (error) {
    if (error.code === 'EEXIST') throw Error('A Qwen journal under this identity is already staged; inspect before retrying');
    throw error;
  }
  const digest = hash(raw);
  const verifySource = async () => {
    if (hash(await readJournal(source)) !== digest) throw Error('Legacy Qwen journal changed during relocation');
  };
  await verifySource();
  return { digest, verifySource };
}
