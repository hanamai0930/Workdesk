"""Constraint evidence and ambiguous recovery through actual command transactions."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from support import LocalFlowCase
from researchos.workdesk.store import canonical,uid


class ExecutionConstraintTests(LocalFlowCase):
    def send(self,**extra):
        r=self.command('independent.send',{'project_id':self.project_id,'goal':'Local constraint fixture',**extra})
        self.settled(r['launch_attempt']);return r

    def item(self,r):return self.s.work_items.catalog(identity=r['work_item']['id'])['work_items'][0]

    def control(self,r,action,identity=None):
        i=self.item(r);a=i['runtime']['runs'][0]
        return self.command('work-item.control',{'id':i['id'],'revision':i['revision'],'action':action,
            **{k:a[k] for k in ('dispatch_id','task_id','attempt_id','owner')}},identity)

    def unknown(self,r,*,local=True,turn='exact-turn'):
        a=self.s.store.attempt(r['launch_attempt'])
        with self.s.store.write() as db:
            result={**a['result'],'state':'SEND_UNKNOWN','remote_model_state':'UNKNOWN','known_tools_exited':local}
            db.execute('UPDATE attempts SET state=?,result=?,turn_id=? WHERE id=?',('SEND_UNKNOWN',canonical(result),turn,a['id']))
            db.execute("UPDATE tasks SET state='SEND_UNKNOWN' WHERE id=?",(r['task_id'],))
        fingerprint=sha256(json.dumps({'email':'fixture@example.invalid','account_id':'fixture-account'},sort_keys=True).encode()).hexdigest()
        self.s.store.event(r['task_id'],a['id'],'codex_session_account_bound',{'session_id':a['session_id'],'fingerprint':fingerprint})

    def rpc(self,*,email='fixture@example.invalid',status='completed',pages=2,repeat=False):
        calls=[];homes=[]
        class Rpc:
            process=SimpleNamespace(pid=0)
            def request(self,method,params,**kwargs):
                calls.append((method,params))
                if method=='account/read':return {'account':{'type':'chatgpt','email':email},'workspaceRouting':{'chatgptAccountId':'fixture-account'}}
                if method=='thread/turns/list':
                    count=sum(m=='thread/turns/list' for m,p in calls)
                    return {'data':[{'id':'different-turn','status':'completed'}] if count<pages else [{'id':'exact-turn','status':status}],
                        'nextCursor':'same' if repeat else str(count)}
                raise AssertionError('Reconciliation must not generate or restore: '+method)
            def close(self):pass
        def opened(loc,*args):homes.append(loc.codex_home);return Rpc()
        return calls,homes,patch('researchos.workdesk.adapters.open_codex',side_effect=opened),patch('researchos.workdesk.adapters.owned',return_value=SimpleNamespace(close=lambda:{'active_processes':0}))

    def test_dispatch_freezes_actual_caps_and_read_does_not_generate(self):
        r=self.send(access='full');run=self.item(r)['runtime']['runs'][0];c=run['constraints']
        self.assertEqual(c['source'],'DISPATCH_SNAPSHOT');self.assertEqual(c['access']['native_mode'],'danger-full-access / never')
        self.assertIsNone(c['deadline']);self.assertEqual(c['budget'],{})
        self.assertFalse(c['workspace_is_isolation']);self.assertEqual(c['hard_balance_enforcement'],'UNSUPPORTED')
        self.assertEqual(c['policy_evidence']['status'],'NO_RUNTIME_RECEIPT');self.assertEqual(len(self.calls),1)
        i=self.item(r);self.command('work-item.update',{'id':i['id'],'revision':i['revision'],'changes':{'constraints_text':'Hard cap claim in plain text'}})
        self.assertEqual(self.item(r)['runtime']['runs'][0]['constraints']['budget'],{})

    def test_historical_snapshot_is_labelled_reconstructed_and_receipt_separate(self):
        r=self.send()
        with self.s.store.write() as db:
            d=self.s.collaboration.get(db,'work_dispatch',r['work_item']['dispatch_id']);d.pop('constraints');self.s.collaboration.put(db,'work_dispatch',d)
        self.s.store.event(r['task_id'],r['launch_attempt'],'runtime_configuration',{'observed_sandbox':{'type':'workspaceWrite'}})
        c=self.item(r)['runtime']['runs'][0]['constraints']
        self.assertEqual(c['source'],'CURRENT_RECORD_RECONSTRUCTION');self.assertEqual(c['policy_evidence']['observed_sandbox']['type'],'workspaceWrite')

    def test_finite_task_snapshot_retains_task_and_chain_budget(self):
        r=self.start(self.draft());self.settled(r['launch_attempt']);c=self.item(r)['runtime']['runs'][0]['constraints']
        self.assertEqual(c['mode'],'FINITE');self.assertEqual(c['budget']['attempts'],2)
        self.assertEqual(c['tool_enforcement'],'WORKDESK_TOOL_GATE');self.assertIsNotNone(c['deadline'])

    def test_local_close_preserves_remote_unknown_and_blocks_every_send(self):
        r=self.send();self.unknown(r);identity=uid();reply=self.control(r,'close-local',identity)
        self.assertEqual(reply['remote_model_state'],'UNKNOWN');self.assertTrue(reply['no_resend'])
        self.assertEqual(reply,self.control(r,'close-local',identity))
        with self.assertRaisesRegex(ValueError,'远端'):self.command('independent.send',{**self.scope(r['task_id']),'goal':'No blind resend'})
        with self.assertRaisesRegex(ValueError,'远端'):self.command('native.control',{**self.scope(r['task_id']),'operation':'history'})
        run=self.item(r)['runtime']['runs'][0]
        self.assertEqual(run['remote_state'],'UNKNOWN');self.assertFalse(run['can_resume'])
        self.assertEqual(self.s.monitor.catalog()['runs'][0]['task_id'],r['task_id']);self.assertEqual(len(self.calls),1)

    def test_close_local_requires_exit_and_rejects_stale_ownership(self):
        r=self.send();self.unknown(r,local=False)
        with self.assertRaises(ValueError):self.control(r,'close-local')
        with self.assertRaises(ValueError):self.command('independent.recover',{**self.scope(r['task_id']),'owner':'wrong'})

    def test_exact_probe_uses_original_connection_pages_and_keeps_frozen_result(self):
        r=self.send();original=self.s.resources.task_context(self.s.store.task(r['task_id'])).codex_home
        other=self.loc.user/'providers/codex/profiles'/uuid4().hex;other.mkdir(parents=True);(other/'auth.json').write_text('{}')
        self.command('storage.credentials.select',{'codex_home':str(other)})
        self.unknown(r);f=self.loc.data/'tasks'/r['task_id']/'attempts'/r['launch_attempt']/'result.json';before=f.read_bytes()
        calls,homes,rpc,job=self.rpc()
        with rpc,job:reply=self.control(r,'probe')
        self.assertEqual(reply['recovered_state'],'COMPLETED');self.assertEqual(homes,[original])
        self.assertEqual(reply['codex_probe_pages'],2);self.assertTrue(reply['no_resend']);self.assertEqual(f.read_bytes(),before)
        self.assertEqual(reply['work_item_control']['action'],'probe')
        self.assertEqual(self.s.store.attempt(r['launch_attempt'])['result']['remote_model_state'],'UNKNOWN')
        self.assertTrue(self.item(r)['runtime']['runs'][0]['can_resume'])
        # Ordinary local checks retain conclusive exact-turn evidence.
        self.control(r,'reconcile');self.assertTrue(self.item(r)['runtime']['runs'][0]['can_resume'])
        # A later model/permission revision cannot invalidate the prior exact
        # terminal proof; its account/connection and turn remain unchanged.
        with self.s.store.write() as db:db.execute('UPDATE tasks SET spec_hash=? WHERE id=?',('next-configuration-revision',r['task_id']))
        self.assertTrue(self.item(r)['runtime']['runs'][0]['can_resume'])
        self.assertEqual(len(self.calls),1)
        next_turn=self.command('independent.send',{**self.scope(r['task_id']),'goal':'Explicit new instruction'})
        self.settled(next_turn['launch_attempt']);self.assertEqual(len(self.calls),2)

    def test_wrong_account_cannot_query_history_or_clear_unknown(self):
        r=self.send();self.unknown(r);calls,homes,rpc,job=self.rpc(email='different@example.invalid')
        with rpc,job:reply=self.control(r,'probe')
        self.assertIn('codex_probe_error',reply);self.assertEqual([m for m,p in calls],['account/read'])
        self.assertFalse(self.item(r)['runtime']['runs'][0]['can_resume']);self.assertEqual(len(self.calls),1)

    def test_unmatched_repeated_cursor_and_nonterminal_do_not_resolve(self):
        for options in ({'pages':99,'repeat':True},{'pages':99},{'status':'inProgress'}):
            with self.subTest(options=options):
                r=self.send();self.unknown(r);calls,homes,rpc,job=self.rpc(**options)
                with rpc,job:reply=self.control(r,'probe')
                self.assertIsNone(reply['protocol_terminal']);self.assertLessEqual(sum(m=='thread/turns/list' for m,p in calls),5)
                self.assertFalse(self.item(r)['runtime']['runs'][0]['can_resume'])

    def test_terminal_without_local_exit_does_not_unlock(self):
        r=self.send();self.unknown(r,local=False);calls,homes,rpc,job=self.rpc()
        with rpc,job:reply=self.control(r,'probe')
        self.assertEqual(reply['protocol_terminal'],'completed');self.assertNotIn('recovered_state',reply)
        self.assertFalse(self.item(r)['runtime']['runs'][0]['can_resume'])

    def test_unknown_native_operation_blocks_send_and_control_after_terminal_probe(self):
        r=self.send();self.unknown(r)
        with self.s.store.write() as db:self.s.collaboration.put(db,'native_control',{'id':'native-'+uid(),'task_id':r['task_id'],'attempt_id':r['launch_attempt'],'operation':'mcp.login','state':'DELIVERY_UNKNOWN','created':1})
        calls,homes,rpc,job=self.rpc()
        with rpc,job:self.control(r,'probe')
        self.assertFalse(self.item(r)['runtime']['runs'][0]['can_resume'])
        with self.assertRaises(ValueError):self.command('independent.send',{**self.scope(r['task_id']),'goal':'No bypass'})
        with self.assertRaises(ValueError):self.command('native.control',{**self.scope(r['task_id']),'operation':'history'})
        self.assertEqual(len(self.calls),1)

    def test_missing_turn_and_non_codex_have_no_probe_route(self):
        self.configure_fixture_api()
        for extra in ({},{'provider':'deepseek'},{'provider':'qwen'}):
            r=self.send(**extra);self.unknown(r,turn=None)
            with self.assertRaises(ValueError):self.control(r,'probe')
            self.control(r,'close-local');self.assertFalse(self.item(r)['runtime']['runs'][0]['can_resume'])
