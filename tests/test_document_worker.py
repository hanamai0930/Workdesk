"""Regression cases for editable artifacts and checks bound to the delivered bytes."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

from researchos.workdesk.professional_tools.document import execute
from researchos.workdesk.professional_tools.office_render import office_executable


class DocumentWorkerTest(unittest.TestCase):
    def test_template_preserves_unmodified_parts_and_persists_recalculated_values(self):
        try:
            office_executable()
        except FileNotFoundError:
            self.skipTest('requires local LibreOffice')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / 'template.xlsx'
            wb = Workbook()
            sheet = wb.active
            sheet.title = 'Budget'
            sheet['B2'], sheet['B3'], sheet['B4'] = 10, 80, '=SUM(B2:B3)'
            sheet['B2'].number_format = '0.00'
            wb.create_sheet('Notes')['A1'] = 'keep'
            wb.create_sheet('Summary')['A1'] = '=Budget!B4*2'
            wb.save(template)
            wb.close()
            original = template.read_bytes()
            request = dict(project_root=directory, input_files=[str(template)], output_dir=str(root/'out'),
                           parameters=dict(format='xlsx', action='edit', name='budget.xlsx', sheet='Budget',
                                           cells={'B2':120}, expected={'B4':200}))
            result = execute(request)
            final = root/'out/budget.xlsx'
            self.assertEqual(result['state'], 'COMPLETED')
            self.assertEqual(template.read_bytes(), original)
            self.assertTrue(all(c['state']=='PASSED' and c['sha256']==sha256(final.read_bytes()).hexdigest()
                                for c in result['checks']))
            with ZipFile(template) as before, ZipFile(final) as after:
                self.assertEqual(before.namelist(), after.namelist())
                for name in before.namelist():
                    if name not in ('xl/worksheets/sheet1.xml','xl/worksheets/sheet3.xml'):
                        self.assertEqual(before.read(name), after.read(name), name)
            wb = load_workbook(final)
            self.assertEqual(wb['Budget']['B2'].number_format, '0.00')
            self.assertEqual(wb['Budget']['B4'].value, '=SUM(B2:B3)')
            wb.close()
            wb = load_workbook(final, data_only=True)
            self.assertEqual(wb['Budget']['B4'].value, 200)
            self.assertEqual(wb['Summary']['A1'].value, 400)
            wb.close()
            # The previous deliverable already contains caches. Change only
            # a precedent: both untouched formulas must get fresh results.
            result = execute({**request, 'input_files':[str(final)], 'output_dir':str(root/'second'),
                              'parameters':{**request['parameters'],'cells':{'B3':100},'expected':{'B4':220}}})
            wb = load_workbook(result['artifacts'][0]['path'],data_only=True)
            self.assertEqual(wb['Budget']['B4'].value, 220)
            self.assertEqual(wb['Summary']['A1'].value, 440)
            wb.close()


    def test_replace_across_run_styles_and_preserve_remaining_text(self):
        from docx import Document
        from researchos.workdesk.professional_tools.document_pages import rewrite_office
        with TemporaryDirectory() as directory:
            template = Path(directory)/'source.docx'
            target = Path(directory)/'edited.docx'
            doc = Document()
            first = doc.add_paragraph()
            first.add_run('first bud').bold = True
            first.add_run('get remaining').italic = True
            last = doc.add_paragraph('bud')
            last.add_run().add_break()
            last.add_run('get')
            doc.save(template)
            self.assertEqual(rewrite_office(template,target,{'replace':{'from':'budget','to':'plan'}}),1)
            result = Document(target)
            self.assertEqual(result.paragraphs[0].text, 'first plan remaining')
            self.assertTrue(result.paragraphs[0].runs[0].bold)
            self.assertTrue(result.paragraphs[0].runs[1].italic)
            self.assertEqual(result.paragraphs[1].text, 'bud\nget')
