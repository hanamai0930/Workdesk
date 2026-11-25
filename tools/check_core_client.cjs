/* Local platform client contract check. No server or model request. */
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ts = require('../workdesk-ui/node_modules/typescript');

function load(name, additions) {
  const source = fs.readFileSync(path.join(__dirname, '../workdesk-ui/frontend/core', name), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}});
  const exports = {};
  vm.runInNewContext(compiled.outputText, {exports, AbortController, structuredClone, crypto, ...additions}, {filename: name});
  return exports;
}

(async () => {
  const checks = [];
  const requests = [];
  let answer = () => { throw new TypeError('local transport disconnected'); };
  const {coreClient: client} = load('client.ts', {fetch: async (url, options) => {
    requests.push({url, options});
    return answer(url, options);
  }});
  const identity = 'b0b4ca60-fac2-4dce-a2b9-8b01225feedd';
  const payload = {project_id: 'selected-project'};
  const pending = client.command('tool.run', payload, identity);
  payload.project_id = 'changed-after-send';
  await assert.rejects(pending, error => error.unknown && error.commandId === identity);
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(requests[0].options.body).payload.project_id, 'selected-project');
  assert.equal(client.uncertainCommands()[0].id, identity);
  checks.push('one frozen command is sent once; transport loss retains its original identity');

  answer = () => new Response(JSON.stringify({state: 'UNKNOWN_OR_NOT_COMMITTED', no_resend: true}));
  await client.receipt(identity);
  assert.equal(client.uncertainCommands().length, 1);
  assert.equal(requests.filter(row => row.options.method === 'POST').length, 1);
  answer = () => new Response(JSON.stringify({state: 'RECORDED', receipt: {state: 'QUEUED'}}));
  await client.receipt(identity);
  assert.equal(client.uncertainCommands().length, 0);
  checks.push('receipt queries never resend a command and clear uncertainty only after a recorded result');

  answer = () => new Response(JSON.stringify({error: 'version changed'}), {status: 409});
  await assert.rejects(client.command('tool.run', {}, crypto.randomUUID()), error => error.status === 409 && !error.unknown);
  assert.equal(client.uncertainCommands().length, 0);
  const reads = client.createReadScope();
  const old = reads.begin(), current = reads.begin();
  assert(old.signal.aborted && !old.isCurrent() && current.isCurrent());
  reads.cancel();
  assert(current.signal.aborted && !current.isCurrent());
  checks.push('explicit rejection is separate from transport uncertainty; new reads cancel and invalidate old reads');

  const saved = new Map();
  const sessionStorage = {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)};
  const instance = {schema: 1, protocol: 2, instance_id: 'development-instance', data_root: 'development-library'};
  const makeClient = () => load('client.ts', {sessionStorage, fetch: async url => {
    if (url === 'api/v1/instance') return new Response(JSON.stringify(instance));
    throw new TypeError('local transport disconnected');
  }}).coreClient;
  const firstWindow = makeClient();
  await firstWindow.loadIdentity();
  await assert.rejects(firstWindow.command('tool.run', {}, identity));
  const reloaded = makeClient();
  await reloaded.loadIdentity();
  assert.equal(reloaded.uncertainCommands()[0].id, identity);
  instance.data_root = 'another-library';
  await reloaded.loadIdentity();
  assert.equal(reloaded.uncertainCommands().length, 0);
  checks.push('refresh retains pending command identity, scoped to the stable instance and active library');

  const scripts = [];
  const loader = load('legacy-loader.ts', {document: {
    createElement: () => ({remove() {}}),
    head: {append(script) { scripts.push(script.src); queueMicrotask(() => script.onload()); }},
  }});
  await loader.loadLegacyModule('chat');
  assert.equal(scripts.length, 0);
  await Promise.all([loader.loadLegacyModule('projects'), loader.loadLegacyModule('projects')]);
  assert.equal(scripts.filter(name => name === 'projects-page.js').length, 1);
  await Promise.all([loader.loadLegacyModule('work-items'), loader.loadLegacyModule('results')]);
  assert.equal(scripts.filter(name => name === 'results-page.js').length, 1);
  assert(scripts.indexOf('cooperation-toolbar.js') < scripts.indexOf('work-items-page.js'));
  assert.equal(scripts.includes('libraries-page.js'), false);
  checks.push('route loading reuses scripts, preserves dependencies and leaves unrelated controllers unloaded');
  console.log(JSON.stringify({checks, real_model_requests: 0}, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
