// Workdesk transport for the installed official DaemonClient. The daemon owns
// the Agent loop, permission decisions, session history and background tasks.
import { realpath } from 'node:fs/promises';
import readline from 'node:readline';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { startDaemon } from './qwen_daemon_host.mjs';
import { stageQueryJournal } from './qwen_daemon_storage.mjs';

const permissionModes = ['plan', 'default', 'auto-edit', 'auto', 'yolo'];
const effortModes = ['off', 'thinking', 'none', 'default', 'low', 'medium', 'high', 'xhigh', 'max'];
const terminalJobs = new Set(['completed', 'failed', 'cancelled']);
const controlMutations = new Set(['setSessionModel', 'setSessionConfigOption', 'setSessionApprovalMode',
  'interrupt', 'sessionTaskCancel', 'rewindSession', 'branchSession', 'adoptSettledQuery']);
const sessionlessControls = new Set(['verifyAttempt', 'getSessionTurnIndexPage', 'workspaceProviders', 'adoptSettledQuery']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 200;
const pause = (ms, signal) => delay(ms, undefined, { signal });

function controlError(error, config, operation, afterAcknowledgement = false) {
  const text = String(error?.message ?? 'Qwen Daemon operation failed')
    .replaceAll(config.token || '\0', '[redacted]').slice(0, 1200);
  if (text.startsWith('DELIVERY_UNKNOWN:')) return text;
  if (controlMutations.has(operation?.method) && (afterAcknowledgement || error?.status >= 500 ||
      error instanceof TypeError || /time(?:d ?out|out)|abort|connection|network|fetch failed/i.test(text))) {
    return 'DELIVERY_UNKNOWN: ' + text;
  }
  return text;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  // Consumption can begin after early SSE delivery. Keep its rejection available.
  promise.catch(() => {});
  return { promise, resolve, reject };
}

export async function executeDaemon(config, commands, emit, open = startDaemon) {
  let host;
  let run;
  let result;
  let operationResult;
  let staged;
  let closed = false;
  try {
    if (!config.official || !uuid.test(config.session_id) || !validId(config.origin_id) ||
        !config.model || !effortModes.includes(config.effort)) {
      throw Error('Invalid Workdesk Daemon session or model identity');
    }
    if (config.operation?.method === 'adoptSettledQuery') staged = await stageQueryJournal(config);
    host = await open(config);
    run = new DaemonRun(config, host.client, emit);
    if (!sessionlessControls.has(config.operation?.method)) await run.initialize();
    if (config.operation) {
      operationResult = await run.control(config.operation);
    } else {
      // The reader is also what handles approval votes while the turn runs.
      run.receive(commands);
      result = await run.execute();
    }
    await run.close();
    await host.stop();
    closed = true;
    if (config.operation?.method === 'adoptSettledQuery' || config.operation?.method === 'branchSession') {
      const verification = await open(config);
      try {
        const verifier = new DaemonRun(config, verification.client, emit);
        const actual = config.operation.method === 'branchSession' ? operationResult.thread.id : config.session_id;
        const origin = operationResult.source_id;
        const proof = await verifier.persistedIdentity(actual, origin);
        if (!proof) throw Error('Native session source was not observed after daemon restart');
        if (config.operation.method === 'adoptSettledQuery') {
          const history = await verifier.transcriptDigest(proof.workspace, actual);
          if (history.digest !== operationResult.history_digest || history.events !== operationResult.history_events) {
            throw Error('Native Query history changed while binding its settled session');
          }
          await staged.verifySource();
          operationResult = { ...operationResult, original_journal_digest: staged.digest };
        }
        operationResult = { ...operationResult, verified_after_restart: true };
      } finally {
        await verification.stop();
      }
    }
    if (config.operation) emit('native_control_result', { id: config.operation.id, result: operationResult });
    if (result) {
      emit('message', result);
      emit('closed', { session_id: config.session_id, turn_id: result.turn_id, interface: 'Daemon' });
      emit('native_sdk_lifecycle', { phase: 'CLOSE_RETURNED', session_id: config.session_id,
        turn_id: result.turn_id, interface: 'Daemon' });
    }
    return true;
  } catch (error) {
    const message = controlError(error, config, config.operation, operationResult !== undefined);
    if (config.operation) {
      emit('native_control_result', { id: config.operation.id, error: message });
    } else {
      if (!run?.sent) emit('native_pre_send_failure', { session_id: config.session_id, prompt_sent: false,
        interface: 'Daemon', message });
      emit('error', { message });
    }
    return false;
  } finally {
    run?.abort.abort();
    if (host && !closed) {
      // On ambiguous execution, abandon the owned process without fabricating
      // a terminal receipt. The outer Workdesk job also records the child exit.
      try { await host.stop(); } catch { /* failure is already unresolved */ }
    }
  }
}

class DaemonRun {
  constructor(config, client, emit) {
    this.config = config;
    this.client = client;
    this.emit = emit;
    this.abort = new AbortController();
    this.terminal = deferred();
    this.handshake = deferred();
    this.lost = deferred();
    this.buffer = [];
    this.requests = new Map();
    this.stopJobs = new Set();
    this.toolCalls = new Map();
    this.fragments = [];
    this.sent = false;
    this.receivingTurnEvents = false;
    this.interrupted = false;
    this.stopping = false;
    this.sessionId = config.session_id;
  }

  async checkWorkspace(cwd) {
    const normalize = value => process.platform === 'win32' ? value.toLowerCase() : value;
    const actual = typeof cwd === 'string' && normalize(await realpath(cwd));
    if (!actual || actual !== normalize(await realpath(this.config.workspace))) {
      throw Error('Official Qwen Daemon workspace binding differs');
    }
  }

  require(...features) {
    for (const feature of features) {
      if (!this.features.has(feature)) throw Error('Installed Qwen Daemon capability missing: ' + feature);
    }
  }

  async initialize() {
    const config = this.config;
    const caps = await this.client.capabilities();
    await this.checkWorkspace(caps.workspaceCwd);
    this.features = new Set(caps.features);
    this.version = caps.qwenCodeVersion;
    this.require('session_permission_vote', 'session_tasks');

    if (config.resume) {
      this.session = await this.client.resumeSession(this.sessionId, { workspaceCwd: config.workspace });
    } else {
      this.require('session_id_override', 'session_scope_override', 'session_startup_config');
      if (this.config.operation) throw Error('A control cannot create a replacement session');
      this.session = await this.client.createOrAttachSession({
        sessionId: this.sessionId,
        workspaceCwd: config.workspace,
        sessionScope: 'thread',
        sourceType: 'workdesk',
        sourceId: config.origin_id,
        startupConfig: { modelServiceId: config.model },
        ...(config.permission_mode && { approvalMode: config.permission_mode }),
      });
    }
    if (this.session.sessionId !== this.sessionId || !validId(this.session.clientId) ||
        this.session.sourceType !== 'workdesk' || this.session.sourceId !== config.origin_id ||
        this.session.partial || this.session.replayError ||
        (!config.resume && (this.session.attached || this.session.sourcePersisted !== true))) {
      throw Error('Qwen Daemon did not prove the exact Workdesk session identity');
    }
    await this.checkWorkspace(this.session.workspaceCwd);
    if (this.session.currentCwd) await this.checkWorkspace(this.session.currentCwd);
    this.clientId = this.session.clientId;
    if (config.resume && config.settled_resume !== true) throw Error('Workdesk settled-session proof is required');
    await this.requireIdle(config.resume === true);
    if (config.operation) return;
    const startup = this.session.startupConfigApplied;
    const model = await this.selectModel(config.model, startup);
    const effort = await this.selectEffort(config.effort);
    const mode = config.permission_mode ? await this.setMode(config.permission_mode) : null;
    await this.scene();
    this.emit('native_configuration_receipt', { session_id: this.sessionId, interface: 'Daemon',
      model: config.model, model_service_id: model, cwd: config.workspace, effort, approval_mode: mode,
      resume: config.resume, source_persisted: this.session.sourcePersisted === true, qwen_code_version: this.version });
    this.emit('model_selection_receipt', { model: config.model, effort: config.effort,
      interface_acknowledged: true, switch_method: 'DaemonClient.setSessionModel', effect: 'BEFORE_FIRST_PROMPT_OF_THIS_ATTEMPT',
      session_id_observed_before_prompt: this.sessionId, resume_requested: config.resume });
    this.emit('message', { type: 'system', subtype: 'init', session_id: this.sessionId,
      model: config.model, cwd: config.workspace, parent_tool_use_id: null, native_interface: 'Daemon',
      qwen_code_version: this.version });
  }

  async selectModel(requested, startup) {
    const view = await this.client.workspaceProviders();
    await this.checkWorkspace(view.workspaceCwd);
    const rows = view.providers.filter(p => p.authType === 'openai').flatMap(p => p.models);
    const matching = rows.filter(row => row.baseModelId === requested &&
      row.baseUrl === this.config.url && row.envKey === 'OPENAI_API_KEY');
    if (matching.length !== 1 || !validId(matching[0].modelId)) {
      throw Error('Qwen Daemon model, endpoint or credential source differs');
    }
    const modelId = matching[0].modelId;
    if (startup && this.session.modelApplied !== true) throw Error('Qwen startup model was not applied');
    if (startup && startup.modelServiceId !== modelId) throw Error('Qwen startup model differs');
    const result = await this.client.setSessionModel(this.sessionId, modelId, this.clientId);
    const selected = result?._meta?.qwenModelSwitch;
    if (!selected || selected.authType !== 'openai' || selected.baseUrl !== this.config.url ||
        ![modelId, requested].includes(selected.modelId)) throw Error('Qwen model switch was not verified');
    return modelId;
  }

  async selectEffort(requested) {
    if (!effortModes.includes(requested)) throw Error('Unknown Qwen effort');
    const value = requested === 'off' ? 'none' : requested === 'thinking' ? 'default' : requested;
    const result = await this.client.setSessionConfigOption(this.sessionId, 'reasoning_effort', value, this.clientId);
    if (result.persisted !== false ||
        !result.configOptions?.some(option => option.id === 'reasoning_effort' && option.currentValue === value)) {
      throw Error('Qwen Daemon reasoning setting was not verified');
    }
    this.emit('native_effort_receipt', { session_id: this.sessionId, requested,
      status: { applied: true, selection: value, persisted: false } });
    return value;
  }

  async setMode(mode) {
    this.require('session_approval_mode_control');
    if (!permissionModes.includes(mode)) throw Error('Unknown Qwen permission mode');
    const result = await this.client.setSessionApprovalMode(this.sessionId, mode,
      { clientId: this.clientId, persist: false });
    if (result.sessionId !== this.sessionId || result.mode !== mode || result.persisted !== false) {
      throw Error('Qwen session permission mode was not verified');
    }
    return mode;
  }

  async scene() {
    const skills = this.config.scene_skills || [];
    const mcp = Object.entries(this.config.scene_mcp || {});
    if (!skills.length && !mcp.length) return;
    this.require('session_resources');
    const expectedSkills = await Promise.all(skills.map(async skill => ({ ...skill, path: await realpath(skill.path) })));
    const deadline = performance.now() + 8000;
    for (;;) {
      const catalog = await this.client.sessionResources(this.sessionId, this.clientId);
      if (catalog.sessionId !== this.sessionId) throw Error('Qwen scene resources belong to another session');
      await this.checkWorkspace(catalog.workspaceCwd);
      const nativeSkills = catalog.skills.skills.filter(item => item.modelInvocable && !item.disabledReason);
      const missing = [];
      for (const skill of expectedSkills) {
        const installed = nativeSkills.find(item => item.name === skill.name && item.installedPath);
        if (!installed || await realpath(installed.installedPath) !== skill.path) missing.push(skill.name);
      }
      const servers = catalog.mcp.servers.map(server => ({ name: server.name, status: server.status,
        mcp_status: server.mcpStatus, config_origin: server.configOrigin }));
      for (const [name, expected] of mcp) {
        const native = catalog.mcp.servers.find(server => server.name === name);
        if (native?.mcpStatus !== 'connected' || native.disabled || native.configOrigin !== 'system_settings' ||
            !Object.entries(expected).every(([key, value]) => JSON.stringify(native.config?.[key]) === JSON.stringify(value))) {
          missing.push(name);
        }
      }
      if (!missing.length || performance.now() >= deadline) {
        this.emit('scene_capability_receipt', { source: 'NATIVE_DAEMON_SESSION_RESOURCES', session_id: this.sessionId,
          skills: nativeSkills.map(skill => skill.name), skill_catalog: 'SKILLS', servers, missing });
        if (missing.length) throw Error('Selected Qwen scene capabilities unavailable: ' + missing.join(', '));
        return;
      }
      await pause(300, this.abort.signal);
    }
  }

  async execute() {
    const stream = this.readEvents();
    const signal = this.abort.signal;
    try {
      await Promise.race([this.handshake.promise, this.lost.promise,
        pause(10_000, signal).then(() => { throw Error('Qwen Daemon event stream did not attach'); })]);
      if (this.stopping) throw Error('Stopped before admitting the Qwen Daemon prompt');
      this.emit('native_prompt_send_intent', { session_id: this.sessionId, interface: 'Daemon' });
      this.sent = true;
      const admitted = await this.client.promptNonBlocking(this.sessionId, {
        prompt: [{ type: 'text', text: this.config.prompt }], eventDetailMode: 'full',
      }, signal, this.clientId);
      if (!validId(admitted?.promptId) || !Number.isSafeInteger(admitted.lastEventId) ||
          (this.session.eventEpoch && admitted.eventEpoch !== this.session.eventEpoch)) {
        throw Error('Official Qwen prompt admission lacks exact identity or event epoch');
      }
      this.turnId = admitted.promptId;
      this.emit('native_turn_attached', { session_id: this.sessionId, turn_id: this.turnId, interface: 'Daemon' });
      while (this.buffer.length) await this.event(this.buffer.shift());
      this.receivingTurnEvents = true;
      if (this.stopping) await this.interrupt(true);
      const terminal = await Promise.race([this.terminal.promise, this.lost.promise]);
      await this.drain();
      return terminal;
    } finally {
      this.abort.abort();
      await stream;
    }
  }

  async readEvents() {
    try {
      for await (const event of this.client.subscribeEvents(this.sessionId, {
        clientId: this.clientId, signal: this.abort.signal, lastEventId: 0,
        epoch: this.session.eventEpoch, onSseStreamAccepted: () => this.handshake.resolve(),
      })) {
        if (event.v !== 1) continue;
        if (['stream_error', 'state_resync_required', 'client_evicted', 'session_died', 'session_recording_degraded'].includes(event.type)) {
          throw Error('Qwen native stream requires reconciliation: ' + event.type);
        }
        if (!this.sent) continue;
        if (!this.receivingTurnEvents) {
          if (this.buffer.length >= 2048) throw Error('Too many events before Qwen prompt admission');
          this.buffer.push(event);
        } else {
          await this.event(event);
        }
      }
      if (!this.abort.signal.aborted) throw Error('Qwen native event stream ended before close');
    } catch (error) {
      if (!this.abort.signal.aborted) this.lost.reject(error);
    }
  }

  async event(event) {
    const data = event.data || {};
    if (data.sessionId && data.sessionId !== this.sessionId) return;
    if (event.type === 'permission_request') {
      if (data.sessionId === this.sessionId) await this.registerPermission(data);
      return;
    }
    if (event.type === 'permission_resolved' || event.type === 'permission_already_resolved') {
      const request = this.requests.get(data.requestId);
      if (request?.voting && event.type === 'permission_resolved' &&
          request.outcome.outcome === data.outcome?.outcome && request.outcome.optionId === data.outcome?.optionId) {
        request.resolved = true;
        this.confirmReply(data.requestId, request);
        return;
      }
      if (this.requests.delete(data.requestId)) {
        this.emit('native_request_cancelled', { id: data.requestId, reason: 'native_resolution' });
      }
      return;
    }
    if ((event.promptId ?? data.promptId) !== this.turnId) return;
    if (event.type === 'session_update' && data.update) {
      this.update(data.update);
    } else if (event.type === 'turn_complete' || event.type === 'turn_error') {
      if (data.sessionId !== this.sessionId || this.terminalReceived) return;
      this.terminalReceived = true;
      const reason = data.stopReason;
      const subtype = event.type === 'turn_error' ? 'error' :
        ['cancelled', 'canceled', 'interrupted'].includes(reason) ? 'interrupted' :
          reason === 'end_turn' ? 'success' : 'error';
      const result = { type: 'result', subtype, session_id: this.sessionId,
        turn_id: this.turnId, parent_tool_use_id: null, is_error: subtype === 'error',
        stop_reason: reason || null, result: this.fragments.join(''),
        ...(subtype === 'error' && { error: String(data.message || reason || 'Qwen turn failed').slice(0, 1200) }) };
      this.emit('native_turn_terminal', { session_id: this.sessionId, turn_id: this.turnId, outcome: subtype,
        stop_reason: result.stop_reason, interface: 'Daemon' });
      this.terminal.resolve(result);
    }
  }

  update(update) {
    const type = update.sessionUpdate;
    const content = update.content;
    const meta = update._meta || {};
    if (type === 'agent_message_chunk' && content?.type === 'text' &&
        !meta.backgroundTurn && meta.qwenDiscreteMessage !== true) {
      this.fragments.push(content.text);
      this.emitMessage('assistant', [{ type: 'text', text: content.text }]);
    }
    if (type === 'tool_call' && validId(update.toolCallId) && !this.toolCalls.has(update.toolCallId)) {
      this.toolCalls.set(update.toolCallId, update.title);
      this.emitMessage('assistant', [{ type: 'tool_use', id: update.toolCallId,
        name: update.title || update.kind || 'native_tool', input: update.rawInput || {} }]);
    }
    if (type === 'tool_call_update' && this.toolCalls.has(update.toolCallId) &&
        ['completed', 'failed'].includes(update.status)) {
      this.toolCalls.delete(update.toolCallId);
      this.emitMessage('user', [{ type: 'tool_result', tool_use_id: update.toolCallId,
        is_error: update.status === 'failed', content: update.rawOutput ?? update.content ?? null }]);
    }
  }

  emitMessage(type, content) {
    this.emit('message', { type, session_id: this.sessionId, turn_id: this.turnId,
      parent_tool_use_id: null, message: { role: type, content } });
  }

  async status() {
    const status = await this.client.sessionStatus(this.sessionId, this.clientId);
    if (status.sessionId !== this.sessionId) throw Error('Qwen status session differs');
    await this.checkWorkspace(status.workspaceCwd);
    return status;
  }

  async requireIdle(settledRestore) {
    if (settledRestore && (this.session.hasActivePrompt !== false ||
        this.session.hasRunningBackgroundTasks !== false || this.session.backgroundTurn)) {
      throw Error('Native restored session lacks explicit foreground and background state');
    }
    const deadline = performance.now() + 18_000;
    let status;
    for (;;) {
      status = await this.status();
      if (status.clientCount !== 1 || status.hasActivePrompt !== false || status.hasRunningBackgroundTasks === true ||
          status.backgroundTurn || status.pendingInteractionCount !== 0 || status.pendingInteractions?.length ||
          (settledRestore && status.hasRunningBackgroundTasks !== false)) {
        throw Error('Native session still holds work or lacks exact idle checks');
      }
      if (status.activeWorkState === 'idle') break;
      if (!settledRestore || status.activeWorkState !== 'unknown' || performance.now() >= deadline) {
        throw Error('Authoritative Qwen idle state was not observed');
      }
      await pause(600, this.abort.signal);
    }
    const jobs = await this.tasks();
    const pending = await this.client.getPendingPrompts(this.sessionId, { clientId: this.clientId });
    if (!Array.isArray(pending.pendingPrompts) || pending.pendingPrompts.length ||
        jobs.some(task => !task.isHistorical && !terminalJobs.has(task.status))) {
      throw Error('Native session has an unresolved prompt or background task');
    }
    return status;
  }

  async registerPermission(data) {
    const { requestId } = data;
    if (!validId(requestId) || this.requests.has(requestId) || !Array.isArray(data.options)) return;
    const pending = (await this.status()).pendingInteractions?.find(item => item.requestId === requestId);
    if (!pending) return;
    const questions = pending.kind === 'user_question' ? pending.questions.map(q => ({
      id: q.answerKey, question: q.question, header: q.header, options: q.options,
      multiSelect: q.multiSelect, isSecret: q.isSecret === true,
    })) : null;
    if (questions && (!questions.length || questions.some(q => !validId(q.id) || q.isSecret) ||
        new Set(questions.map(q => q.id)).size !== questions.length)) {
      await this.client.respondToSessionPermission(this.sessionId, requestId,
        { outcome: { outcome: 'cancelled' } }, this.clientId);
      return;
    }
    this.requests.set(requestId, { options: data.options, questions });
    this.emit('native_request', { id: requestId,
      method: questions ? 'sdk/tool/requestUserInput' : 'sdk/tool/requestApproval',
      params: { session_id: this.sessionId, tool_name: data.toolCall?.title || 'Qwen tool',
        input: data.toolCall?.rawInput || {}, ...(questions && { questions }) } });
  }

  async reply(command) {
    const request = this.requests.get(command.id);
    if (!request || request.voting || this.stopping) return;
    const answer = command.response || {};
    let response = { outcome: { outcome: 'cancelled' } };
    const option = request.options.find(item => item.kind ===
      (answer.decision === 'decline' ? 'reject_once' : 'allow_once'));
    if (option && validId(option.optionId) && (answer.decision === 'accept' || answer.decision === 'decline')) {
      response = { outcome: { outcome: 'selected', optionId: option.optionId } };
    }
    if (request.questions && answer.answers && option && validId(option.optionId)) {
      const keys = request.questions.map(q => q.id);
      if (Object.keys(answer.answers).length !== keys.length ||
          !keys.every(key => Array.isArray(answer.answers[key]?.answers))) throw Error('Invalid exact Qwen answers');
      response = { outcome: { outcome: 'selected', optionId: option.optionId },
        answers: Object.fromEntries(keys.map(key => [key, answer.answers[key].answers.join(', ')])) };
    }
    request.voting = true;
    request.outcome = response.outcome;
    const ok = await this.client.respondToSessionPermission(this.sessionId, command.id, response, this.clientId);
    if (this.requests.get(command.id) !== request) return;
    if (ok) {
      request.voteAccepted = true;
      this.confirmReply(command.id, request);
    } else {
      this.requests.delete(command.id);
      this.emit('native_request_cancelled', { id: command.id, reason: 'official_vote_no_longer_available' });
    }
  }

  confirmReply(id, request) {
    if (request.voteAccepted && request.resolved && this.requests.delete(id)) {
      this.emit('native_reply_applied', { id });
    }
  }

  async receive(commands) {
    try {
      for await (const command of commands) {
        if (this.abort.signal.aborted) return;
        try {
          if (command.action === 'interrupt') await this.interrupt(true);
          else if (command.action === 'native.reply') await this.reply(command);
          else if (command.action === 'native.control') await this.operation(command.operation);
        } catch (error) {
          this.emit('control_error', { message: String(error?.message || 'Qwen control failed').slice(0, 500) });
        }
      }
    } catch (error) {
      this.lost.reject(error);
    }
  }

  async interrupt(includeBackground = false) {
    this.stopping = true;
    if (!this.turnId) return;
    if (!this.interrupted) {
      this.interrupted = true;
      const result = await this.client.removePendingPrompt(this.sessionId, this.turnId, { clientId: this.clientId });
      this.emit('interrupt_receipt', { received: result.removed === true, session_id: this.sessionId,
        turn_id: this.turnId, stop_confirmed: false });
    }
    if (includeBackground) {
      this.stopBackground = true;
      await this.cancelBackground(await this.tasks());
    }
  }

  async cancelBackground(tasks) {
    for (const task of tasks) {
      if (task.isHistorical || terminalJobs.has(task.status) || !validId(task.id) ||
          !['agent', 'shell', 'monitor', 'workflow'].includes(task.kind)) continue;
      const key = JSON.stringify([task.kind, task.id]);
      if (this.stopJobs.has(key)) continue;
      this.stopJobs.add(key);
      try {
        const result = await this.client.sessionTaskCancel(this.sessionId, task.id, task.kind, this.clientId);
        this.emit('native_background_cancel_receipt', { session_id: this.sessionId, turn_id: this.turnId,
          task_id: task.id, kind: task.kind, cancellation_requested: result.cancelled === true });
      } catch {
        this.emit('native_background_cancel_receipt', { session_id: this.sessionId, turn_id: this.turnId,
          task_id: task.id, kind: task.kind, cancellation_requested: null });
      }
    }
  }

  async tasks() {
    const base = await this.client.sessionTasks(this.sessionId, this.clientId);
    const flow = await this.client.sessionWorkflowTasks(this.sessionId, this.clientId);
    if (base.sessionId !== this.sessionId || flow.sessionId !== this.sessionId) throw Error('Qwen task owner differs');
    return [...base.tasks, ...flow.tasks.filter(task => task.kind === 'workflow')];
  }

  async drain() {
    // A foreground reply does not settle a daemon that still owns native work.
    // Keep the same daemon and controls alive until Qwen reports no live tasks.
    let recorded;
    for (;;) {
      const status = await this.status();
      const tasks = await this.tasks();
      if (this.stopBackground) await this.cancelBackground(tasks);
      const remaining = tasks.filter(task => !task.isHistorical && !terminalJobs.has(task.status));
      const settled = status.activeWorkState === 'idle' && !status.hasActivePrompt &&
        !status.hasRunningBackgroundTasks && !status.backgroundTurn && !status.pendingInteractionCount && !remaining.length;
      const projection = { tasks: tasks.filter(task => !task.isHistorical).slice(-100).map(task => this.jobView(task)), settled };
      const serialized = JSON.stringify(projection);
      if (serialized !== recorded) {
        recorded = serialized;
        this.emit('native_background_observation', { session_id: this.sessionId, turn_id: this.turnId, ...projection });
      }
      if (settled) return;
      await Promise.race([this.lost.promise, pause(600, this.abort.signal)]);
    }
  }

  jobView(task) {
    return { id: task.id, kind: task.kind, label: task.label, status: task.status,
      isBackgrounded: task.isBackgrounded === true, historical: task.isHistorical === true,
      ...(task.toolUseId && { tool_use_id: task.toolUseId }),
      ...(task.parentAgentId && { parent_agent_id: task.parentAgentId }) };
  }

  async close() {
    if (this.clientId) await this.client.closeSession(this.sessionId, this.clientId);
  }

  async operation(op) {
    try {
      const result = await this.control(op);
      this.emit('native_control_result', { id: op.id, result });
    } catch (error) {
      this.emit('native_control_result', { id: op.id,
        error: controlError(error, this.config, op) });
    }
  }

  async control(op) {
    const p = op.params || {};
    const client = this.client, sid = this.sessionId, cid = this.clientId;
    switch (op.method) {
      case 'sessionSupportedCommands': return client.sessionSupportedCommands(sid, cid);
      case 'workspaceMcp': {
        const result = await client.sessionResources(sid, cid);
        if (result.sessionId !== sid) throw Error('Qwen MCP owner differs');
        return result.mcp.servers.map(server => ({ name: server.name, status: server.status,
          mcp_status: server.mcpStatus, transport: server.transport, disabled: server.disabled }));
      }
      case 'workspaceProviders': {
        const view = await client.workspaceProviders();
        await this.checkWorkspace(view.workspaceCwd);
        return view.providers.map(provider => ({ authType: provider.authType,
          models: provider.models.map(model => ({ id: model.modelId, name: model.name, current: model.isCurrent })) }));
      }
      case 'sessionContextUsage': return client.sessionContextUsage(sid, { detail: !!p.showDetails }, cid);
      case 'sessionStats': return client.sessionStats(sid, cid);
      case 'setSessionModel': {
        if (p.model !== this.config.model) throw Error('Workdesk reserves the configured model for this attempt');
        return { model_id: await this.selectModel(p.model) };
      }
      case 'setSessionConfigOption': return { selection: await this.selectEffort(p.effort) };
      case 'setSessionApprovalMode': return { mode: await this.setMode(p.mode) };
      case 'interrupt': await this.interrupt(); return { requested: this.interrupted, stop_confirmed: false };
      case 'sessionTasks': return { data: (await this.tasks()).map(task => this.jobView(task)) };
      case 'sessionTaskCancel': {
        const task = (await this.tasks()).find(row => row.id === p.job_id && row.kind === p.kind &&
          !row.isHistorical && !terminalJobs.has(row.status));
        if (!task) throw Error('Exact active Qwen background task is not present');
        const result = await client.sessionTaskCancel(sid, task.id, task.kind, cid);
        return { task_id: task.id, kind: task.kind, cancellation_requested: result.cancelled === true };
      }
      case 'sessionAgents': {
        const result = await client.sessionAgents(sid, cid);
        if (result.sessionId !== sid) throw Error('Qwen agent list owner differs');
        return result.tasks.map(task => this.jobView(task));
      }
      case 'verifyAttempt': return this.verifyAttempt(p.turnId);
      case 'adoptSettledQuery': return this.adoptSettledQuery();
      case 'getSessionTurnIndexPage': {
        const request = { limit: 30 };
        if (p.cursor !== undefined) {
          const match = typeof p.cursor === 'string' && p.cursor.match(/^(0|[1-9]\d{0,8}):([1-9]|[12]\d|30)$/);
          if (!match || typeof p.snapshot !== 'string' || !p.snapshot) throw Error('Invalid Qwen history cursor');
          Object.assign(request, { start: Number(match[1]), limit: Number(match[2]), snapshot: p.snapshot });
        }
        const workspace = await this.persistedWorkspace();
        if (!workspace) throw Error('Exact persisted Qwen session is unavailable');
        const page = await workspace.getSessionTurnIndexPage(sid, request);
        if (page.sessionId !== sid || !Number.isSafeInteger(page.start)) throw Error('Qwen history session differs');
        const previousSize = Math.min(page.start, 30);
        return { data: page.turns.map(turn => ({ ...turn, id: turn.promptId ?? turn.turnId })),
          snapshot: page.snapshot, nextCursor: previousSize ? `${page.start - previousSize}:${previousSize}` : null };
      }
      case 'getRewindSnapshots': {
        const { snapshots } = await client.getRewindSnapshots(sid);
        return { data: snapshots.map(snapshot => ({ ...snapshot, id: snapshot.promptId })) };
      }
      case 'rewindSession': {
        const snapshots = (await client.getRewindSnapshots(sid)).snapshots;
        if (!validId(p.beforeTurnId) || !snapshots.some(snapshot => snapshot.promptId === p.beforeTurnId)) {
          throw Error('Exact native iteration snapshot unavailable');
        }
        const result = await client.rewindSession(sid, p.beforeTurnId, { clientId: cid, rewindFiles: false });
        return { ...result, prompt_id: p.beforeTurnId, working_files_rewound: false };
      }
      case 'branchSession': {
        if (p.lastTurnId || p.atRecordId) throw Error('History fork currently supports the native session head');
        const origin = p.fork_origin_id ?? op.fork_origin_id;
        if (!uuid.test(origin) || origin === this.config.origin_id) throw Error('Missing Workdesk branch identity');
        const result = await client.branchSession(sid, {}, cid);
        if (!uuid.test(result.sessionId) || result.sessionId === sid || result.forkedFrom?.sessionId !== sid) {
          throw Error('DELIVERY_UNKNOWN: native Qwen branch source differs');
        }
        try {
          if (result.clientId) await client.closeSession(result.sessionId, result.clientId);
          const child = new DaemonRun({ ...this.config, session_id: result.sessionId, origin_id: origin }, client, this.emit);
          await child.attributeOwnSession();
          await child.close();
        } catch {
          throw Error('DELIVERY_UNKNOWN: native Qwen branch returned without verified ownership settlement');
        }
        return { thread: { id: result.sessionId }, source_session_id: sid,
          source_type: 'workdesk', source_id: origin };
      }
      default: throw Error('Unsupported Qwen Daemon control operation');
    }
  }

  async verifyAttempt(turnId) {
    const unknown = { session_id: this.sessionId, turn_id: turnId, outcome: 'UNKNOWN',
      background_state: 'UNKNOWN', source: 'NATIVE_PERSISTED_READ_ONLY' };
    if (!validId(turnId)) return { ...unknown, reason: 'TURN_ID_UNAVAILABLE' };
    const workspace = (await this.persistedIdentity(this.sessionId, this.config.origin_id))?.workspace;
    if (!workspace) return { ...unknown, reason: 'EXACT_PERSISTED_SESSION_NOT_OBSERVED' };
    const index = await workspace.getSessionTurnIndexPage(this.sessionId, { limit: 100 });
    if (index.sessionId !== this.sessionId || !index.turns?.some(turn => turn.promptId === turnId)) {
      return { ...unknown, reason: 'EXACT_PERSISTED_PROMPT_NOT_OBSERVED' };
    }
    return { ...unknown, persisted_prompt_observed: true,
      reason: 'PERSISTED_INDEX_ATTESTS_HISTORY_ONLY' };
  }

  async persistedWorkspace() {
    return (await this.persistedIdentity(this.sessionId, this.config.origin_id))?.workspace ?? null;
  }

  async persistedIdentity(sessionId, originId) {
    const caps = await this.client.capabilities();
    await this.checkWorkspace(caps.workspaceCwd);
    const workspace = this.client.workspaceByCwd(this.config.workspace);
    let cursor;
    do {
      const page = await workspace.listWorkspaceSessionsPage({
        pageSize: 100, cursor, ...(originId && { sourceType: 'workdesk', sourceId: originId }),
      });
      const session = page.sessions?.find(item => item.sessionId === sessionId &&
        (!originId || item.sourceType === 'workdesk' && item.sourceId === originId));
      if (session) {
        await this.checkWorkspace(session.workspaceCwd);
        return { workspace, session };
      }
      if (!page.nextCursor || page.nextCursor === cursor) return null;
      cursor = page.nextCursor;
    } while (typeof cursor === 'string');
    return null;
  }

  async attributeOwnSession() {
    const config = this.config;
    const restored = await this.client.resumeSession(this.sessionId, { workspaceCwd: config.workspace,
      sourceType: 'workdesk', sourceId: config.origin_id });
    this.session = restored;
    if (restored.sessionId !== this.sessionId || restored.sourceType !== 'workdesk' ||
        restored.sourceId !== config.origin_id || restored.sourcePersisted !== true ||
        restored.partial || restored.replayError || !validId(restored.clientId)) {
      throw Error('DELIVERY_UNKNOWN: official restore has no exact source attestation');
    }
    await this.checkWorkspace(restored.workspaceCwd);
    if (restored.currentCwd) await this.checkWorkspace(restored.currentCwd);
    this.clientId = restored.clientId;
    try {
      await this.requireIdle(true);
    } catch {
      throw Error('DELIVERY_UNKNOWN: attributed Qwen session idle state was not verified');
    }
  }

  async adoptSettledQuery() {
    if (this.config.settled_resume !== true) throw Error('Workdesk did not prove settled Query ownership');
    const proof = await this.persistedIdentity(this.sessionId);
    if (!proof || proof.session.sourceType || proof.session.sourceId ||
        proof.session.hasActivePrompt || proof.session.hasRunningBackgroundTasks || proof.session.backgroundTurn) {
      throw Error('Exact unassigned, settled Qwen Query history is unavailable');
    }
    const history = await this.transcriptDigest(proof.workspace, this.sessionId);
    if (!history.events) throw Error('Native Query transcript contains no replayable history');
    await this.attributeOwnSession();
    return { session_id: this.sessionId, source_type: 'workdesk', source_id: this.config.origin_id, settled: true,
      history_digest: history.digest, history_events: history.events,
      source_persisted: true, verified_after_restart: false };
  }

  async transcriptDigest(workspace, sessionId) {
    const { createHash } = await import('node:crypto');
    const hash = createHash('sha256');
    let cursor;
    let events = 0;
    do {
      const page = await workspace.getSessionTranscriptPage(sessionId, { limit: 100, cursor });
      if (page.sessionId !== sessionId || page.partial || page.replayError || !Array.isArray(page.events)) {
        throw Error('Native Qwen history could not be verified completely');
      }
      for (const event of page.events) hash.update(JSON.stringify(event) + '\n');
      events += page.events.length;
      if (!page.hasMore) return { events, digest: hash.digest('hex') };
      if (!page.nextCursor || page.nextCursor === cursor) throw Error('Native Qwen history cursor did not advance');
      cursor = page.nextCursor;
    } while (events <= 50_000);
    throw Error('Native Qwen history exceeds the bounded exact transfer check');
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const input = readline.createInterface({ input: process.stdin });
  const lines = input[Symbol.asyncIterator]();
  const emit = (type, data) => process.stdout.write(JSON.stringify({ type, data }) + '\n');
  try {
    const config = JSON.parse((await lines.next()).value);
    async function* commands() { for await (const line of lines) yield JSON.parse(line); }
    if (!await executeDaemon(config, commands(), emit)) process.exitCode = 1;
  } finally {
    input.close();
    process.stdin.pause();
  }
}
