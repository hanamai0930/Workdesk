"""Desktop launch identity stays bound to one process and user root."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from researchos.workdesk import paths

SPEC=importlib.util.spec_from_file_location('desktop_host_identity',ROOT/'workdesk-ui/desktop_host.py')
desktop=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(desktop)


class DesktopHostIdentityTests(unittest.TestCase):
    def test_title_and_user_root_follow_only_the_new_process(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-desktop-identity-') as tmp:
            base=Path(tmp);binary=base/'runtime/workdesk-desktop/versions/test/Workdesk.Desktop.exe'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture')
            (binary.parent.parent.parent/'current.json').write_text(json.dumps({'executable':'versions/test/Workdesk.Desktop.exe'}),encoding='utf-8')
            user=base/'development-user'
            url='http://127.0.0.1:18766/s/local-entry-token/'
            for title in (None,'Workdesk · 开发'):
                with self.subTest(title=title),patch.object(desktop.subprocess,'Popen',return_value=Mock(pid=1234)) as spawn,patch.object(paths,'process_identity',return_value={'pid':1234,'creation':'5678','alive':True}):
                    self.assertTrue(desktop.launch(url,base,user,title=title))
                    command=spawn.call_args.args[0]
                    self.assertEqual(command[0],str(binary.resolve()))
                    self.assertEqual(command[command.index('--user-root')+1],str(user))
                    if title:self.assertEqual(command[command.index('--title')+1],title)
                    else:self.assertNotIn('--title',command)
                    raw=(user/'runtime/desktop/launch-identity.json').read_text(encoding='utf-8')
                    identity=json.loads(raw)
                    self.assertEqual(identity,{'pid':1234,'creation':'5678','executable':str(binary.resolve()),'user_root':str(user.resolve()),'title':title or 'Workdesk · 工作台'})
                    self.assertNotIn(url,raw)
                    self.assertNotIn('local-entry-token',raw)

    def test_unsupported_title_does_not_spawn(self):
        with patch.object(desktop.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):desktop.launch('http://127.0.0.1:18766/s/fixture/',ROOT,ROOT/'unused',title='Unknown product')
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
