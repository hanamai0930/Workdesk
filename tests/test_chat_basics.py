import base64
import io
import zipfile
from unittest.mock import patch

import test_chat_api as fixture
from support import LocalFlowCase
from researchos.workdesk.chat_context import CONTEXT_WINDOW,status


class ChatBasicFlows(LocalFlowCase):
    def setUp(self):
        super().setUp();self.api=self.s.ui_features.chats.api;self.bodies=[]
        self.s.profiles.credentials=lambda:{'deepseek':fixture.Secret()}
        def stream(secret,body,stop,opened):
            self.bodies.append(body)
            yield {'choices':[{'delta':{'content':'local reply'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':7,'completion_tokens':2,'total_tokens':9}}
            yield {'done':True}
        self.api.transport=stream
    create=fixture.ChatAPIFlows.create
    row=fixture.ChatAPIFlows.row
    wait=fixture.ChatAPIFlows.wait

    def attachment(self,name,raw):
        return self.command('chat.api-attach',{'name':name,'data':base64.b64encode(raw).decode()})['attachment']

    def ask(self,c,content='read the attached material',**kw):
        return self.command('chat.api-send',{'id':c['id'],'revision':c['revision'],'content':content,'model':'deepseek-flash',
            'thinking':False,'billing':'official_api',**kw})

    def test_attachment_context_search_and_delete_without_agents(self):
        f=self.attachment('notes.txt','fixture attachment fact'.encode());c=self.create();searches=[]
        def search(secret,q,stop,opened):
            searches.append(q);return [{'title':'Fixture source','url':'https://example.com/reference','summary':'fixture public fact'},
                {'title':'unsafe','url':'javascript:alert(1)','summary':'must not become link'}]
        self.api.search_transport=search
        self.ask(c,attachments=[f['id']],search=True,search_query='fixture query',thinking=True);c=self.wait(c['id'])
        body=self.bodies[0];self.assertIn('fixture attachment fact',body['messages'][-1]['content'])
        self.assertIn('fixture public fact',body['messages'][-1]['content']);self.assertNotIn('tools',body)
        self.assertEqual(body['thinking']['type'],'enabled');self.assertEqual(searches,['fixture query'])
        self.assertEqual(len(c['messages'][-1]['sources']),1);self.assertEqual(c['messages'][0]['attachments'][0]['name'],'notes.txt')
        # Thinking is a per-turn switch; all completed text/files/search remain.
        self.ask(c,'follow up',thinking=False);c=self.wait(c['id'])
        self.assertIn('fixture attachment fact',self.bodies[1]['messages'][1]['content'])
        self.assertIn('fixture public fact',self.bodies[1]['messages'][1]['content'])
        self.assertEqual(self.bodies[1]['thinking']['type'],'disabled');self.assertEqual(searches,['fixture query'])
        other=self.create()
        with self.assertRaises(ValueError):self.ask(other,attachments=[f['id']])
        self.command('chat.delete',{'chats':[{'id':c['id'],'revision':c['revision']}],'confirm':True})
        with self.s.store.read() as db:self.assertEqual(self.s.collaboration.all(db,'ui_chat_attachment'),[])
        self.assertEqual(self.calls,[])

    def test_image_model_capability_and_file_only_message(self):
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(16,16),'white').save(out,format='PNG')
        f=self.attachment('tiny.png',out.getvalue());c=self.create()
        with self.assertRaises(ValueError):self.ask(c,model='deepseek-v4-pro',attachments=[f['id']])
        self.ask(c,'',attachments=[f['id']]);c=self.wait(c['id'])
        self.assertEqual(self.bodies[0]['messages'][-1]['content'][1]['type'],'image_url')
        self.assertTrue(self.bodies[0]['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_document_extraction_rejects_silent_loss(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>DOCX fixture text</w:t></w:r></w:p></w:body></w:document>')
        f=self.attachment('doc.docx',out.getvalue());c=self.create();self.ask(c,attachments=[f['id']]);self.wait(c['id'])
        self.assertIn('DOCX fixture text',self.bodies[0]['messages'][-1]['content'])
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        from pypdf import PdfWriter
        w=PdfWriter();page=w.add_blank_page(width=200,height=200)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 100 Td (PDF fixture text) Tj ET')
        page[NameObject('/Contents')]=w._add_object(stream);pdf_text=io.BytesIO();w.write(pdf_text)
        f2=self.attachment('readable.pdf',pdf_text.getvalue());c=self.create();self.ask(c,attachments=[f2['id']]);self.wait(c['id'])
        self.assertIn('PDF fixture text',self.bodies[-1]['messages'][-1]['content'])
        from pypdf import PdfWriter
        pdf=io.BytesIO();w=PdfWriter();w.add_blank_page(width=72,height=72);w.write(pdf)
        for name,raw in [('scan.pdf',pdf.getvalue()),('bad.png',b'not image'),('run.exe',b'not executable')]:
            with self.assertRaises(ValueError):self.attachment(name,raw)
        self.assertEqual(len(self.bodies),2)

    def test_epub_spine_text_is_attached_in_order_and_invalid_books_fail_locally(self):
        package = ('<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="b" href="sub/b%20two.xhtml" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
        entries = {
            'META-INF/container.xml': '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
            'OEBPS/book.opf': package,
            'OEBPS/a.xhtml': '<html><body><p>第二章</p></body></html>',
            'OEBPS/sub/b two.xhtml': '<html><head><title>跳过标题</title></head><body><p>第<b>一</b>章</p>'
                                     '<script>跳过脚本</script><img src="https://example.invalid/image"/></body></html>',
        }
        def attach(changes):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as book:
                for name, value in (entries | changes).items():
                    book.writestr(name, value)
            return self.attachment('book.epub', stream.getvalue())

        file = attach({})
        chat = self.create()
        self.ask(chat, attachments=[file['id']])
        self.wait(chat['id'])
        text = self.bodies[0]['messages'][-1]['content']
        self.assertIn('第一章\n\n第二章', text)
        self.assertNotIn('跳过', text)
        encrypted = '<encryption><CipherReference URI="OEBPS/a.xhtml"/></encryption>'
        for changes, reason in [
            ({'META-INF/encryption.xml': encrypted}, '加密'),
            ({'OEBPS/book.opf': package.replace('a.xhtml', 'https://example.invalid/a.xhtml')}, '书内'),
            ({'OEBPS/book.opf': package.replace('application/xhtml+xml', 'image/png')}, '没有可提取'),
            ({'OEBPS/book.opf': '<!DOCTYPE package [<!ENTITY x "t">]>' + package}, '声明'),
            ({'OEBPS/a.xhtml': 'x' * (16 * 1024 * 1024 + 1)}, '解压后过大'),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                attach(changes)

    def test_context_full_length_and_thresholds_no_trim(self):
        self.assertEqual(self.api.describe()['context_window'],1048576)
        self.assertEqual([status(int(CONTEXT_WINDOW*x))['warning'] for x in (.79,.81,.96)],[0,80,95])
        c=self.create();large='long message '*10000
        self.ask(c,large);c=self.wait(c['id']);self.assertEqual(self.bodies[0]['messages'][-1]['content'],large.strip())
        with patch('researchos.workdesk.chat_api.count',return_value=CONTEXT_WINDOW):
            with self.assertRaises(ValueError):self.ask(c,'must not discard history')
        self.assertEqual(len(self.bodies),1);self.assertEqual(len(self.row(c['id'])['messages']),2)

    def test_search_failure_has_no_model_fallback_or_retry(self):
        calls=[]
        def failed(secret,q,stop,opened):calls.append(q);raise ValueError('fixture search unavailable')
        self.api.search_transport=failed;c=self.create();self.ask(c,search=True);c=self.wait(c['id'])
        self.assertEqual(calls,['read the attached material']);self.assertEqual(self.bodies,[])
        self.assertEqual(c['messages'][-1]['state'],'FAILED_NO_SEND');self.assertIn('fixture search unavailable',c['messages'][-1]['error'])
