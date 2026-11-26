// Observe the same official Web profile that owns the browser login. No model
// request, token export, second core, or credential-file polling is involved.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';

export const inject = ['deepseekAccount', 'profileContext'];

export function apply(ctx) {
  ctx.effect(() => {
    const abort = new AbortController();
    const publish = value => {
      if (!abort.signal.aborted) process.stdout.write(JSON.stringify({
        workdeskAuth: 1, event_token: process.env.WORKDESK_AUTH_EVENT_TOKEN, ...value,
      }) + '\n');
    };
    const pending = setImmediate(async () => {
      try {
        await ctx.get('loader')?.await();
        const account = ctx.deepseekAccount;
        const require = createRequire(ctx.profileContext.installAnchor);
        const modulePath = require.resolve('@deepseek-ai/dsh-deepseek-account');
        const version = JSON.parse(readFileSync(join(dirname(modulePath), '..', 'package.json'), 'utf8')).version;
        const client = {version, locale: 'zh-CN', timezoneOffsetSeconds: -new Date().getTimezoneOffset() * 60};
        for await (const update of account.watchState(abort.signal)) {
          if (abort.signal.aborted) break;
          const state = update.state;
          const phase = state.attempt?.phase ?? null;
          if (state.status === 'signed-out') {
            publish({auth_state: 'UNAUTHENTICATED', phase, verification: 'OFFICIAL_DSH_ACCOUNT_STATE'});
          } else if (phase === 'succeeded') {
            publish({auth_state: 'AUTHENTICATED', phase, verification: 'OFFICIAL_DSH_SIGN_IN_SUCCEEDED'});
          } else {
            // Presence by itself is not server validation. The official free
            // account-profile endpoint supplies proof without any generation.
            const profile = await account.getProfile(client);
            publish({auth_state: profile?.status === 'ready' ? 'AUTHENTICATED' : 'UNKNOWN', phase,
                     verification: profile?.status === 'ready' ? 'OFFICIAL_DSH_PROFILE_READY' : 'OFFICIAL_DSH_PROFILE_UNCONFIRMED'});
          }
        }
      } catch (error) {
        if (!abort.signal.aborted) publish({auth_state: 'UNKNOWN', verification: 'OFFICIAL_DSH_ACCOUNT_OBSERVATION_FAILED',
                                         error_class: typeof error?.code === 'string' ? error.code : 'NativeAccountReadFailed'});
      }
    });
    return () => { abort.abort(); clearImmediate(pending); };
  }, 'workdesk-official-login-observer');
}
