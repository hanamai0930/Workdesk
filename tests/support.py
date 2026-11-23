"""Neutral, isolated fixtures. The synthetic engine never opens a provider SDK."""
from pathlib import Path
from uuid import uuid4
import tempfile
import threading
import time
import unittest
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'src'))
from researchos.workdesk.paths import Locations
from researchos.workdesk.service import Service
from researchos.workdesk.store import uid


class LocalFlowCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.project=self.root/'neutral-project';self.project.mkdir()
        (self.project/'note.txt').write_text('neutral original',encoding='utf-8')
        self.loc=Locations(BASE,self.root/'user/libraries/smoke',self.root/'user')
        self.calls=[];self.wait_for_stop=False;self.entered=threading.Event()
        self.s=Service(self.loc,engine=self.engine);self.s.collaboration.close()
        # This disposable home has no login. Use real catalog metadata while
        # bypassing authentication readiness only for the synthetic engine.
        self.s.profiles.validate=lambda provider,model,effort,tier:next(
            p for p in self.s.profiles.list() if p['id']==provider)
        # Backup implementation snapshots can exceed MAX_PATH on Windows.
        # Keep application identities ordinary; extend only this owned temp I/O path.
        def cleanup_fixture():
            from researchos.workdesk.workspace import io_path
            assert self.root.resolve().parent == Path(tempfile.gettempdir()).resolve()
            self.temp.name=str(io_path(self.root))
            self.temp.cleanup()
        self.addCleanup(cleanup_fixture);self.addCleanup(lambda:self.s.close())
        # A deliberately empty, fixture-only account reference. It is never
        # presented to Codex because every dispatch uses the synthetic engine.
        account=self.loc.user/'providers/codex/profiles'/uuid4().hex;account.mkdir(parents=True)
        (account/'auth.json').write_text('{}',encoding='utf-8')
        self.command('storage.credentials.select',{'codex_home':str(account)})
        self.project_id=self.command('project.create',{'root':str(self.project)})['project_id']

    def engine(self,service,task,attempt,tools,stop,resume,note):
        self.calls.append(attempt['id']);self.entered.set()
        if self.wait_for_stop:
            stop.wait(getattr(self,'wait_seconds',3))
            return {'state':'INTERRUPTED','answer':'local stop observed','known_tools_exited':True,
                    'owned_job':{'active_processes':0},'protocol':'LOCAL_SYNTHETIC_ENGINE_NO_MODEL'}
        for name in task['spec']['writable']:
            path=Path(attempt['workspace'])/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('neutral result',encoding='utf-8')
        return {'state':'COMPLETED','answer':'local sample delivered','known_tools_exited':True,
                'owned_job':{'active_processes':0},'protocol':'LOCAL_SYNTHETIC_ENGINE_NO_MODEL'}

    def configure_fixture_api(self):
        path=self.root/'fixture-keys.env'
        path.write_text('DEEPSEEK_API_KEY=fixture-deepseek\nQWEN_API_KEY=sk-fixture-qwen\n',encoding='utf-8')
        self.command('storage.credentials.select',{'api_env':str(path),'codex_home':str(self.loc.codex_home)})

    def command(self,kind,payload,identity=None):return self.s.command(kind,payload,identity or uid())['receipt']

    def definition(self):
        return {'provider':'codex','execution':'scoped','campaign':None,'goal':'Read and update the neutral fixture',
                'files':['note.txt'],'writable':['note.txt'],
                'budget':{'attempts':2,'seconds':30,'tools':1,'requests':0,'cny':0}}

    def draft(self):return self.command('task.create',{'project_id':self.project_id,**self.definition()})

    def start(self,row):
        self.command('task.approve',row);return self.command('task.start',row)

    def settled(self,identity):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            worker=self.s.workers.get(identity)
            if worker and not worker['thread'].is_alive():return self.s.store.attempt(identity)
            time.sleep(.02)
        self.fail('local synthetic operation did not settle')

    def scope(self,task_id):
        attempt=self.s.store.attempts(task_id)[-1]
        return {'task_id':task_id,'attempt_id':attempt['id'],'owner':attempt['owner']}

    def candidate(self,task_id):
        c=self.command('candidate.capture',self.scope(task_id))['candidate']
        self.command('candidate.judge',{'candidate_id':c['id'],'verdict':'pass','note':'Neutral local smoke fixture only; not a product Human acceptance.'})
        return c
