/* Real development Go UI acceptance. Creates one local material-summary job only. */
const {chromium} = require('C:/Users/naoki/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const root = path.resolve(__dirname, '..');
const output = process.env.WORKDESK_CORE_UI_OUTPUT || 'C:/Workdesk-dev/artifacts/stage1/ui-core';
const state = JSON.parse(fs.readFileSync(process.env.WORKDESK_CORE_UI_STATE || 'C:/Workdesk-dev/user/runtime/ui/instance-core.json', 'utf8'));
assert.equal(state.core_engine, 'go');
assert.equal(state.channel, 'development');
assert.equal(state.program_root.replaceAll('\\', '/').toLowerCase(), root.replaceAll('\\', '/').toLowerCase());
const origin = 'http://127.0.0.1:' + state.port;
const entry = origin + '/s/' + state.token + '/';
const jobAt = process.argv.indexOf('--job');
const reuseJob = jobAt >= 0 ? process.argv[jobAt + 1] : '';
const allowHTTPDownload = process.argv.includes('--http-download-fallback');
if (jobAt >= 0) assert(/^[a-f0-9-]{36}$/.test(reuseJob || ''), 'Pass the original job identity when resuming');
const redact = value => String(value).split(state.token).join('[redacted]');
const report = {checks: [], errors: [], external_requests: [], commands: [], real_model_requests: 0,
  instance_id: state.instance_id, url: origin + '/s/[redacted]/', synthetic: false};
let browser, page, jobId;

(async () => {
  fs.mkdirSync(output, {recursive: true});
  const prior = path.join(output, 'check.json'), initial = path.join(output, 'check-initial-attempt.json');
  if (fs.existsSync(prior) && !fs.existsSync(initial)) fs.copyFileSync(prior, initial);
  browser = await chromium.launch({headless: true, channel: 'msedge'});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, acceptDownloads: true});
  page = await context.newPage();
  page.setDefaultTimeout(30000);
  let v2Polls = 0, loadedRecords = [], loadedJobs = [];
  page.on('pageerror', error => report.errors.push(redact(error.message)));
  page.on('response', async response => {
    const url = new URL(response.url());
    if (url.pathname.endsWith('/api/v2/records') && response.ok()) {
      try { loadedRecords = (await response.json()).records || []; } catch { /* The page reports failed reads. */ }
    }
    if (url.pathname.endsWith('/api/v2/runtime') && response.ok()) {
      try { loadedJobs = (await response.json()).jobs || []; } catch { /* The panel reports failed reads. */ }
    }
  });
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      if (url.origin !== origin) { report.external_requests.push(redact(url.origin + url.pathname)); return route.abort(); }
      if (/\/api\/v2\/(runtime|events)$/.test(url.pathname)) v2Polls++;
      if (request.method() === 'POST' && url.pathname.endsWith('/api/v1/command')) {
        const body = request.postDataJSON();
        report.commands.push({id: body.id, kind: body.kind});
        if (body.kind !== 'tool.run') {
          report.errors.push('Unrequested command blocked: ' + body.kind);
          return route.abort();
        }
      }
    }
    return route.continue();
  });

  await page.goto(entry, {waitUntil: 'networkidle'});
  await page.locator('[data-instance-channel="development"]').waitFor();
  await page.waitForFunction(() => document.documentElement.dataset.uiRuntime?.startsWith('react19'));
  await page.getByRole('textbox', {name: '工作目标', exact: true}).waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-work-loading]') &&
    !document.querySelector('.wk-composer')?.textContent?.includes('正在同步工作配置'));
  assert.equal(await page.title(), 'Workdesk · 开发');
  await page.screenshot({path: path.join(output, 'home.png')});
  report.checks.push('Go production homepage keeps the development identity and normal Work entry');

  await page.locator('[data-instance-channel="development"] button').click();
  const dialog = page.locator('#wd-instance-dialog');
  await dialog.locator('[data-core-workspace]').waitFor();
  await dialog.getByText('正在读取项目和记录…', {exact: true}).waitFor({state: 'hidden'});
  const projectSelect = dialog.getByRole('combobox', {name: '项目工作区', exact: true});
  const selectedProject = await projectSelect.inputValue();
  assert.equal(selectedProject, await page.locator('html').getAttribute('data-workdesk-default-project'));
  assert.match(await dialog.locator('.core-path').innerText(), /C:\\Workdesk-dev\\repo/i);
  assert.equal(await dialog.getByText('Go 核心 · 资料格式 2', {exact: true}).isVisible(), true);
  await page.screenshot({path: path.join(output, 'core-panel.png')});
  report.checks.push('Development details open the Go panel and select the registered development project');

  if (reuseJob) jobId = reuseJob;
  else {
    const sent = page.waitForResponse(response => response.url().endsWith('/api/v1/command') &&
      response.request().method() === 'POST' && response.request().postDataJSON().kind === 'tool.run');
    await dialog.getByRole('button', {name: '生成本地概要', exact: true}).click();
    const submitted = await sent;
    assert.equal(submitted.status(), 200);
    const receipt = await submitted.json();
    jobId = receipt.receipt.invocation_id;
  }
  assert(jobId);
  report.tool = {invocation_id: jobId, project_id: selectedProject, reused: !!reuseJob};
  const job = dialog.locator('.core-job:has(code[title="' + jobId + '"])');
  await job.getByText('已完成', {exact: true}).waitFor();
  const text = job.locator('.core-artifact').filter({hasText: '材料概要.txt'});
  await text.getByRole('button', {name: '查看', exact: true}).click();
  await dialog.locator('.core-preview pre').filter({hasText: '项目材料概要'}).waitFor();
  assert.match(await dialog.locator('.core-preview pre').innerText(), /C:\\Workdesk-dev\\repo/);
  await page.screenshot({path: path.join(output, 'summary-preview.png')});
  const csv = job.locator('.core-artifact').filter({hasText: '材料清单.csv'});
  const downloaded = page.waitForEvent('download');
  await csv.getByRole('link', {name: '下载', exact: true}).click();
  const download = await downloaded;
  report.download = {suggested_filename: redact(download.suggestedFilename()), failure: await download.failure()};
  const csvPath = path.join(output, '材料清单.csv');
  if (report.download.failure && allowHTTPDownload) {
    const href = await csv.getByRole('link', {name: '下载', exact: true}).getAttribute('href');
    const response = await context.request.get(new URL(href, entry).href);
    assert.equal(response.status(), 200);
    assert.match(response.headers()['content-disposition'], /^attachment;/);
    fs.writeFileSync(csvPath, await response.body());
    report.download.transport = 'authenticated-http';
    report.limitations = ['Browser attachment download was cancelled by the local automated browser; the same UI link was verified through authenticated HTTP transfer.'];
  } else {
    assert.equal(report.download.failure, null);
    await download.saveAs(csvPath);
    report.download.transport = 'browser';
  }
  const csvBytes = fs.readFileSync(csvPath);
  assert.match(csvBytes.toString('utf8').replace(/^\uFEFF/, ''), /^file,bytes,lines,sha256\r?\n/);
  Object.assign(report.tool, {csv_sha256: crypto.createHash('sha256').update(csvBytes).digest('hex'), download: '材料清单.csv'});
  report.checks.push('One real local Python job completes; TXT opens in the panel and transferred CSV has valid columns');

  const logGroup = job.locator('details').filter({has: page.locator('summary', {hasText: '运行日志'})});
  if (await logGroup.count()) {
    await logGroup.locator('summary').click();
    const firstLog = logGroup.locator('.core-artifact').filter({hasText: '运行日志 · 1'});
    const loaded = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/log') &&
      new URL(response.url()).searchParams.get('stream') === 'stdout');
    await firstLog.getByRole('button', {name: '查看日志', exact: true}).click();
    const logResponse = await loaded;
    assert.equal(logResponse.status(), 200);
    const log = await logResponse.json(), chunk = Buffer.from(log.data, 'base64');
    const manifest = loadedJobs.find(row => row.id === jobId)?.logs?.find(row => row.stream === 'stdout' && row.segment === 0);
    assert(manifest && manifest.chunk_bytes >= chunk.length);
    assert.equal(manifest.chunk_sha256[0], crypto.createHash('sha256').update(chunk).digest('hex'));
    await dialog.locator('.core-preview pre').filter({hasText: 'COMPLETED'}).waitFor();
    report.log = {stream: 'stdout', segment: 0, chunk_bytes: chunk.length, has_more: log.has_more, chunk_sha256_verified: true};
    await page.screenshot({path: path.join(output, 'log-preview.png')});
    report.checks.push('The stdout log opens through a bounded first chunk and its bytes and digest match the displayed result');
  } else if (!reuseJob) assert.fail('New local tools must publish the new segmented log metadata');

  await dialog.locator('.core-records > summary').click();
  await dialog.locator('.core-records li').first().waitFor();
  assert(loadedRecords.some(row => row.kind === 'work_item'), 'The existing stage 0 work item must remain indexed');
  const old = loadedRecords.find(row => row.kind === 'work_item');
  await dialog.locator('.core-records').getByRole('button', {name: old.title, exact: true}).click();
  await dialog.locator('.core-records .core-preview').waitFor();
  report.old_record = {id: old.id, kind: old.kind, revision: old.revision};
  await page.screenshot({path: path.join(output, 'existing-record.png')});
  report.checks.push('The pre-migration work item remains visible and its detail loads only when opened');

  await dialog.getByRole('button', {name: '关闭实例详情', exact: true}).click();
  await page.waitForTimeout(300);
  const closedCount = v2Polls;
  await page.waitForTimeout(2300);
  assert.equal(v2Polls, closedCount, 'Closed details must stop runtime and event polling');
  await page.goto(entry + '#projects', {waitUntil: 'networkidle'});
  await page.getByRole('heading', {name: '项目与工作区', exact: true}).waitFor();
  assert.equal(await dialog.isVisible(), false);
  const pageCount = v2Polls;
  await page.waitForTimeout(2000);
  assert.equal(v2Polls, pageCount, 'A different page must not restart the closed panel');
  report.checks.push('Closing details and navigating away stops runtime and event polling');

  await page.setViewportSize({width: 560, height: 900});
  await page.locator('[data-instance-channel="development"] button').click();
  await dialog.locator('[data-core-workspace]').waitFor();
  await dialog.getByText('正在读取项目和记录…', {exact: true}).waitFor({state: 'hidden'});
  assert(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth));
  const bounds = await dialog.boundingBox();
  assert(bounds.x >= 0 && bounds.x + bounds.width <= 561 && bounds.y >= 0 && bounds.y + bounds.height <= 901);
  await page.screenshot({path: path.join(output, 'core-panel-narrow.png')});
  await dialog.getByRole('button', {name: '关闭实例详情', exact: true}).click();
  assert.equal(report.commands.filter(row => row.kind === 'tool.run').length, reuseJob ? 0 : 1);
  assert.deepEqual(report.errors, []);
  assert.deepEqual(report.external_requests, []);
  report.checks.push('The 560px details panel remains inside the viewport without horizontal overflow');
  fs.writeFileSync(path.join(output, 'check.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(async error => {
  report.errors.push(redact(error.stack || error.message));
  if (page) await page.screenshot({path: path.join(output, 'failure.png')}).catch(() => {});
  fs.writeFileSync(path.join(output, 'check.json'), JSON.stringify(report, null, 2));
  console.error(redact(error.stack || error.message));
  process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
