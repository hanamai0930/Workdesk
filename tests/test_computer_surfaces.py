"""Exercise computer/1 against one owned webpage and one temporary Win32 window."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from uuid import uuid4

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'src'))
from researchos.workdesk.professional_tools.computer_surface import execute


class SurfaceChecks(unittest.TestCase):
    def test_browser_grants(self):
        visits=[]
        class Page(BaseHTTPRequestHandler):
            def do_GET(self):
                visits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=UTF-8')
                self.end_headers()
                self.wfile.write(b'<title>Owned fixture</title><body>Stage B<input id="note"><a href="/next" id="next">Next</a><img src="/not-granted"></body>')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Page)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin='http://127.0.0.1:'+str(server.server_port)
        for act,kwargs in [('read',{}),('fill',{'element_id':'note','text':'hello'}),('click',{'element_id':'next','destination_url':origin+'/next'}),('download',{})]:
            with self.subTest(act=act), TemporaryDirectory() as tmp:
                execute(dict(action='browser.'+act,origin=origin,url=origin+'/',**kwargs),Path(tmp))
                receipt=json.loads((Path(tmp)/'surface-receipt.json').read_text(encoding='utf8'))
                if act=='click':self.assertEqual(receipt['destination_url'],origin+'/next')
                if act=='download':self.assertIn(b'Stage B',(Path(tmp)/'download.bin').read_bytes())
                if act=='read':self.assertIn('Stage B',receipt['text'])
        with TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            execute(dict(action='browser.read',origin=origin,url='http://localhost/'),Path(tmp))
        self.assertNotIn('/not-granted',visits)

    @unittest.skipUnless(sys.platform=='win32','Windows desktop needed')
    def test_bound_window(self):
        import win32gui as ui
        title='Workdesk Surface '+uuid4().hex
        script=r"""
import win32gui as g,win32api as a,win32con as c,sys
inst=a.GetModuleHandle(None)
def proc(hwnd,msg,wp,lp):
    if msg==c.WM_COMMAND and wp&65535==8:
        g.SetWindowText(g.GetDlgItem(hwnd,7),'button-received')
        return 0
    if msg==c.WM_DESTROY:
        g.PostQuitMessage(0)
        return 0
    return g.DefWindowProc(hwnd,msg,wp,lp)
wc=g.WNDCLASS()
wc.hInstance=inst
wc.lpszClassName='WorkdeskOwnedSurface'
wc.lpfnWndProc=proc
g.RegisterClass(wc)
hwnd=g.CreateWindowEx(c.WS_EX_TOOLWINDOW|c.WS_EX_NOACTIVATE,wc.lpszClassName,sys.argv[1],c.WS_OVERLAPPEDWINDOW,-8000,-8000,300,200,0,0,inst,None)
for cls,code,number in [('EDIT','',7),('BUTTON','Check',8)]:
    g.CreateWindowEx(0,cls,code,c.WS_CHILD|c.WS_VISIBLE,2,number*5,200,18,hwnd,number,inst,None)
g.ShowWindow(hwnd,c.SW_SHOWNOACTIVATE)
print('ready',flush=True)
g.PumpMessages()
"""
        child=subprocess.Popen([sys.executable,'-c',script,title],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(),b'ready')
            with TemporaryDirectory() as tmp:
                root=Path(tmp)
                def call(action,**kwargs):
                    execute(dict(action='desktop.'+action,**kwargs),root)
                    return json.loads((root/'surface-receipt.json').read_text(encoding='utf8'))
                receipt=call('inspect',window_title=title)
                target=receipt['target']
                edit,button=[next(c for c in receipt['controls'] if c['control_id']==n) for n in (7,8)]
                call('set',target=target,control_id=7,control_hwnd=edit['control_hwnd'],text='local sample')
                with self.assertRaises(ValueError):call('invoke',target={**target,'process_start':'0'},control_id=8,control_hwnd=button['control_hwnd'])
                call('invoke',target=target,control_id=8,control_hwnd=button['control_hwnd'])
                from researchos.workdesk.professional_tools.computer_windows import send
                import ctypes
                buffer=ctypes.create_unicode_buffer(100)
                send(edit['control_hwnd'],0x000d,100,ctypes.addressof(buffer))
                self.assertEqual(buffer.value,'button-received')
                call('screenshot',target=target)
                self.assertTrue((root/'window.png').read_bytes().startswith(b'\x89PNG'))
                ui.PostMessage(target['hwnd'],0x0010,0,0)
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()

if __name__=='__main__':unittest.main()
