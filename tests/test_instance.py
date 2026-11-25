"""Installation boundaries use disposable paths and never open the daily store."""
import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from researchos.workdesk.instance import load_profile,configure_environment,validate_locations
from researchos.workdesk.paths import Locations
from researchos.workdesk.client import Client


class InstanceTests(unittest.TestCase):
    def test_pending_login_prevents_idle_stop_before_any_state_change(self):
        from researchos.workdesk.setup_account import AccountSetup
        from researchos.workdesk.service import Service
        account=AccountSetup(None)
        self.assertFalse(account.active())
        account._login=SimpleNamespace(state='PENDING')
        self.assertTrue(account.active())
        with self.assertRaisesRegex(ValueError,'账号登录'):
            Service._reload(SimpleNamespace(account_setup=account),None,{})
        account._login.state='CANCELLED'
        self.assertFalse(account.active())
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='workdesk-instance-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        subprocess.run(['git','init','--quiet',str(self.repo)],check=True)
        self.config=self.root/'instance.json'
        for key,value in [('workdesk.channel','development'),('workdesk.instanceConfig',str(self.config))]:
            subprocess.run(['git','-C',str(self.repo),'config','--local',key,value],check=True)
        self.profile={'schema':1,'channel':'development','instance_id':str(uuid4()),'development_root':str(self.root),
            'program_root':str(self.repo),'user_root':str(self.root/'user'),'data_root':str(self.root/'user/libraries/main'),
            'python_executable':sys.executable,'ui_port':18766,'sync_branch':'codex/v2-dev',
            'protected_roots':[str(self.root/'daily')]}
        self.save()
    def save(self):self.config.write_text(json.dumps(self.profile),encoding='utf-8')
    def test_explicit_instance_overrides_inherited_daily_environment_and_refuses_wrong_roots(self):
        # Replace the mapping without round-tripping unrelated empty Windows
        # variables (Git's empty environment config values must remain present).
        with patch('os.environ',{**os.environ,'WORKDESK_HOME':str(self.root/'daily'),'WORKDESK_DATA_DIR':str(self.root/'daily/db')}):
            profile=configure_environment(self.repo)
            loc=Locations.resolve(self.repo)
            self.assertEqual(loc.user,Path(profile['user_root']))
            self.assertEqual(loc.data,Path(profile['data_root']))
            self.assertEqual(loc.python,Path(sys.executable))
            with self.assertRaisesRegex(ValueError,'登记范围'):
                Locations.resolve(self.repo,self.root/'daily/db',user=loc.user)
        self.assertFalse((self.root/'daily').exists())
        self.assertFalse((self.root/'user').exists())
    def test_missing_profile_and_wrong_publish_channel_cannot_fall_back(self):
        self.config.unlink()
        with self.assertRaisesRegex(ValueError,'未回落'):Locations.resolve(self.repo)
        self.profile['sync_branch']='main';self.save()
        with self.assertRaisesRegex(ValueError,'codex/v2-dev'):load_profile(self.repo)
        self.assertFalse((self.root/'user').exists())
    def test_location_pointer_checks_protection_and_keeps_a_valid_restore_target(self):
        pointer=self.root/'user/config/locations.json';pointer.parent.mkdir(parents=True)
        pointer.write_text(json.dumps({'schema':1,'active_data':str(self.root/'daily/db')}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'登记范围'):load_profile(self.repo)
        restored=self.root/'user/libraries/restored'
        pointer.write_text(json.dumps({'schema':1,'active_data':str(restored)}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'缺失'):load_profile(self.repo)
        restored.mkdir(parents=True)
        with closing(sqlite3.connect(restored/'tasks.sqlite3')) as db:
            db.execute('CREATE TABLE sample(value)');db.commit()
        self.assertEqual(Locations.resolve(self.repo).data,restored)
        self.assertFalse((self.root/'daily').exists())
    def test_wrong_control_owner_is_rejected_without_spawning_or_sending(self):
        loc=Locations.resolve(self.repo);loc.data.mkdir(parents=True)
        state={'port':18767,'instance':'process-a','control':'x'*43,'viewer':'y'*43}
        (loc.data/'backend.json').write_text(json.dumps(state),encoding='utf-8')
        client=Client.__new__(Client);client.locations=loc
        health={'app':'workdesk-control-v1','protocol':1,'instance':'process-a','profile_id':'other-profile',
                'program_base':str(self.repo),'data_root':str(loc.data)}
        with patch('researchos.workdesk.client.urlopen',return_value=io.BytesIO(json.dumps(health).encode())), \
             patch('researchos.workdesk.client.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'归属不符'):client.discover()
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
