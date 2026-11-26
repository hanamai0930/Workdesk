// Read-only native selection adapter. No credential is returned and no Agent
// or generation request is created. The official SDK and Agent loop stay intact.
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

export const inject = ['llm', 'profileContext'];

export function apply(ctx) {
  ctx.effect(() => {
    let disposed = false;
    const pending = setImmediate(async () => {
      try {
        // The callback is outside this plugin's activation effect, so Loader
        // readiness cannot wait on itself. All official providers settle first.
        await ctx.get('loader')?.await();
        if (disposed) return;
        const state = await ctx.get('deepseekAccount')?.getState();
        if (state?.status !== 'credential-stored') {
          publish({state:'NOT_AUTHENTICATED', authenticated:false, models:[], selection:null});
          return;
        }
        const models = (await ctx.llm.listModels('deepseek-account')).map(model => ({
          id:model.id, name:model.name, provider:model.provider,
          ...(model.description ? {description:model.description} : {}),
        }));
        if (models.length === 0) {
          publish({state:'NOT_AUTHENTICATED', authenticated:false, models:[], selection:null,
                   reason:'The official account route advertises no usable models'});
          return;
        }
        const require = createRequire(ctx.profileContext.installAnchor);
        const boot = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-app-boot')).href);
        // Web login saves this profile's default; SDK is a separate profile.
        // Use the vendor's own profile reader/composer, never parse credentials.
        const web = boot.loadProfile('dsh', 'web', ctx.profileContext.installAnchor, ctx.profileContext.home);
        const entries = boot.composeEntries([...web.layers.map(layer => layer.patches), web.patches]);
        const flatten = rows => rows.flatMap(row => [row, ...(row.group && Array.isArray(row.config) ? flatten(row.config) : [])]);
        const config = flatten(entries).find(row => row.id === 'agent-default-model')?.config;
        let selection = null;
        let source = 'ACCOUNT_MODEL_SELECTION_REQUIRED';
        if (config?.provider === 'deepseek-account' && typeof config.model === 'string' && config.model.length > 0) {
          selection = {provider:'deepseek-account', model:config.model};
          if (typeof config.reasoningEffort === 'string') selection.reasoningEffort = config.reasoningEffort;
          source = models.some(model => model.id === selection.model)
            ? 'OFFICIAL_WEB_PROFILE_SAVED_ACCOUNT_DEFAULT' : 'SAVED_ACCOUNT_DEFAULT_NOT_ADVERTISED';
        } else {
          // Human authorizes the first real account model when no account
          // default is stored. Record the choice rather than claim a default.
          selection = {provider:'deepseek-account', model:models[0].id};
          source = 'FIRST_AVAILABLE_ACCOUNT_MODEL';
        }
        publish({state:source === 'SAVED_ACCOUNT_DEFAULT_NOT_ADVERTISED' ? 'MODEL_SELECTION_REQUIRED' : 'ACCOUNT_CATALOG_AVAILABLE',
                 authenticated:true, models, selection, source,
                 profile_source:'profiles/web/cordis.patch.yml#agent-default-model'});
      } catch (error) {
        // Native configuration errors can include secrets. Publish classification
        // only; the Workdesk parent keeps the official diagnostics separately.
        publish({state:'ACCOUNT_DISCOVERY_FAILED', authenticated:false, models:[], selection:null,
                 reason:typeof error?.code === 'string' ? error.code : 'Native account/default-model read failed'});
      }
    });
    const publish = params => {
      if (!disposed) process.stdout.write(JSON.stringify({jsonrpc:'2.0', method:'workdesk.accountSelection', params})+'\n');
    };
    return () => { disposed = true; clearImmediate(pending); };
  }, 'workdesk-read-only-account-selection');
}
