import json
import threading
from unittest.mock import Mock,patch
from urllib.error import URLError

from support import LocalFlowCase,BASE
from researchos.workdesk.client import Client
from researchos.workdesk.paths import atomic_json
from researchos.workdesk.server import Api
from researchos.workdesk.store import uid


class ConnectionSmoke(LocalFlowCase):
    def test_old_owner_blocked_but_stop_and_reload_remain_available(self):
        client=Client(BASE,self.loc.data)
        old={'_health':{'program_base':str(BASE.parent/'ResearchOS'),'implementation':'old'}}
        client.ensure=Mock(return_value=old);client.raw_request=Mock(return_value={'receipt':{}})
        with self.assertRaisesRegex(ValueError,'本次操作未发送'):
            client.request('command',{'kind':'task.start','payload':{}})
        client.raw_request.assert_not_called()
        client.request('command',{'kind':'task.stop','payload':{}})
        client.request('command',{'kind':'service.reload','payload':{}})
        self.assertEqual(client.raw_request.call_count,2)
        current={'_health':{'program_base':str(BASE),'implementation':client.expected_implementation}}
        client.ensure=Mock(return_value=current)
        client.request('command',{'kind':'task.create','payload':{}})
        self.assertEqual(client.raw_request.call_count,3)

    def test_transport_unknown_is_not_automatically_replayed(self):
        client=Client(BASE,self.loc.data);state={'port':12345,'viewer':'viewer','control':'control'}
        with patch('researchos.workdesk.client.urlopen',side_effect=URLError('synthetic transport failure')) as send:
            with self.assertRaises(URLError):client.raw_request(state,'command',{'kind':'service.reload','payload':{}})
            self.assertEqual(send.call_count,1)

    def test_http_owner_metadata_and_settings_roundtrip(self):
        server=Api(self.s);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
        try:
            atomic_json(self.loc.data/'backend.json',{'port':server.server_port,'instance':self.s.instance,
                        'control':server.control,'viewer':server.viewer})
            client=Client(BASE,self.loc.data);settings=client.request('settings')
            self.assertEqual(settings['health']['program_base'],str(BASE))
            self.assertEqual(client.discover()['_health']['implementation'],self.s.implementation)
            self.assertEqual(client.request('ui-state')['chats'],[])
            self.assertEqual(client.request('work-files?project='+self.project_id)['files'][0]['file'],'note.txt')
            receipt=client.request('command',{'id':uid(),'kind':'project.create','payload':{'root':str(self.project)}})
            self.assertEqual(receipt['receipt']['project_id'],self.project_id)
            self.assertEqual(self.calls,[])
        finally:server.shutdown();server.server_close();thread.join(timeout=1)
