"""Targeted updates and independent value checks for spreadsheet artifacts."""
import csv
from io import StringIO
from math import isclose, isfinite
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries, get_column_letter

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def entries(values):
    if not isinstance(values, dict) or len(values) > 20000:
        raise ValueError('单元格应为坐标和值组成的对象，最多 20000 项。')
    for key, val in values.items():
        if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', key):
            raise ValueError('单元格坐标无效：'+key)
        row, col = coordinate_to_tuple(key)
        if row > 1048576 or col > 16384 or not isinstance(val, (str, int, float, bool, type(None))):
            raise ValueError('不支持的坐标或数值：'+key)
        if isinstance(val, float) and not isfinite(val):
            raise ValueError('单元格数值必须有限。')
        yield key, val, row, col


def csv_file(path, template, params):
    text = template.read_text(encoding='utf-8-sig') if template else params.get('content','')
    rows = list(csv.reader(StringIO(text, newline='')))
    slots = sum(len(row) for row in rows)
    for key, val, row, col in entries(params.get('cells', {})):
        if col > 10000 or row > 100000:
            raise ValueError('CSV 展开大小超限。')
        while len(rows) < row:
            rows.append([])
        needed = max(0, col-len(rows[row-1]))
        slots += needed
        if slots > 2_000_000:
            raise ValueError('CSV 展开超过 200 万个单元格。')
        rows[row-1].extend([''] * needed)
        rows[row-1][col-1] = '' if val is None else str(val)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        csv.writer(stream).writerows(rows)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        if list(csv.reader(stream)) != rows:
            raise ValueError('CSV 回读不一致。')
    return [('表格回读', 'PASSED', f'{len(rows)} 行')]


def xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def write_zip(source, output, changes):
    with ZipFile(source) as src, ZipFile(output, 'w') as dst:
        for item in src.infolist():
            dst.writestr(item, changes[item.filename] if item.filename in changes else src.read(item))


def update_sheet(raw, cells):
    tree = xml(raw)
    data = tree.find(NS+'sheetData')
    rows = {int(r.get('r')):r for r in data.findall(NS+'row')}
    bounds = tree.find(NS+'dimension')
    extent = list(range_boundaries(bounds.get('ref'))) if bounds is not None else None
    for key, val, ri, ci in entries(cells):
        row = rows.get(ri)
        if row is None:
            row = etree.Element(NS+'row',r=str(ri))
            at = next((i for i,r in enumerate(data) if r.tag==NS+'row' and int(r.get('r'))>ri),len(data))
            data.insert(at,row)
            rows[ri]=row
        cell = row.find(f"{NS}c[@r='{key}']")
        if cell is None:
            cell=etree.Element(NS+'c',r=key)
            at=next((i for i,c in enumerate(row) if c.tag!=NS+'c' or coordinate_to_tuple(c.get('r'))[1]>ci),len(row))
            row.insert(at,cell)
        f=cell.find(NS+'f')
        if f is not None and f.get('t') in {'shared','array','dataTable'}:
            raise ValueError('选中单元格属于共享或数组公式，需由支持该模板的工具修改。')
        for child in list(cell):
            if child.tag in (NS+'is', NS+'v', NS+'f'):
                cell.remove(child)
        cell.attrib.pop('t',None)
        if isinstance(val,bool):
            cell.set('t','b')
            etree.SubElement(cell,NS+'v').text='1' if val else '0'
        elif isinstance(val,str) and val.startswith('='):
            etree.SubElement(cell,NS+'f').text=val[1:]
        elif isinstance(val,str):
            cell.set('t','inlineStr')
            t=etree.SubElement(etree.SubElement(cell,NS+'is'),NS+'t')
            t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            t.text=val
        elif val is not None:
            etree.SubElement(cell,NS+'v').text=str(val)
        if extent:
            extent=[min(extent[0],ci),min(extent[1],ri),max(extent[2],ci),max(extent[3],ri)]
    if extent:
        c,r,c2,r2=extent
        bounds.set('ref',f'{get_column_letter(c)}{r}:{get_column_letter(c2)}{r2}')
    return etree.tostring(tree, encoding='utf-8')


def same(a,b):
    if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
        return isclose(a,b,rel_tol=1e-10,abs_tol=1e-10)
    return type(a)==type(b) and a==b


def xlsx_file(path, template, p):
    from .office_render import convert
    checks=[]
    cells=p.get('cells',{})
    expected=dict((k,v) for k,v,_,_ in entries(p.get('expected',{})))
    with TemporaryDirectory() as tmp:
        scratch=Path(tmp)
        if not template:
            blank=Workbook()
            if p.get('sheet'):
                blank.active.title=p['sheet']
            template=scratch/'empty.xlsx'
            blank.save(template)
            blank.close()
        wb=load_workbook(template,read_only=True)
        try:
            name=p.get('sheet') or wb.active.title
            sheet=wb[name]
            sheet_paths={s.title:s._worksheet_path for s in wb.worksheets}
        finally:
            wb.close()
        with ZipFile(template) as src:
            raw=update_sheet(src.read(sheet_paths[name]),cells)
        write_zip(template,path,{sheet_paths[name]:raw})
        # Workbook templates may contain formulas outside the edited sheet.
        with ZipFile(path) as src:
            formula_sheets={title:part for title,part in sheet_paths.items()
                            if xml(src.read(part)).find('.//'+NS+'f') is not None}
        if formula_sheets:
            # A spreadsheet engine may trust existing cached values even after
            # precedent cells change. Invalidate *all* formula cells, including
            # those on other sheets, before opening the calculation copy.
            changes={}
            with ZipFile(path) as src:
                for part in formula_sheets.values():
                    tree=xml(src.read(part))
                    for f in tree.iter(NS+'f'):
                        cell=f.getparent()
                        v=cell.find(NS+'v')
                        if v is not None:
                            cell.remove(v)
                        cell.attrib.pop('t',None)
                    changes[part]=etree.tostring(tree,encoding='utf-8')
                workbook=xml(src.read('xl/workbook.xml'))
            calc_settings=workbook.find(NS+'calcPr')
            if calc_settings is None:
                calc_settings=etree.SubElement(workbook,NS+'calcPr')
            calc_settings.attrib.update({'calcMode':'auto','fullCalcOnLoad':'1','forceFullCalc':'1','calcId':'0'})
            calc_input=scratch/'recalculate.xlsx'
            write_zip(path,calc_input,{**changes,'xl/workbook.xml':etree.tostring(workbook,encoding='utf-8')})
            try:
                calc=convert(calc_input,scratch/'calc','xlsx')
            except FileNotFoundError:
                updated=scratch/'uncalculated.xlsx'
                write_zip(path,updated,changes)
                shutil.copyfile(updated,path)
                checks.append(('公式重算','PENDING','缺少 LibreOffice，未核实公式缓存值。'))
            else:
                values=load_workbook(calc,data_only=True)
                changes={}
                try:
                    with ZipFile(path) as src:
                        for title,part in formula_sheets.items():
                            tree=xml(src.read(part))
                            for f in tree.iter(NS+'f'):
                                cell=f.getparent()
                                cv=values[title][cell.get('r')]
                                v=cell.find(NS+'v')
                                if v is None:
                                    v=etree.SubElement(cell,NS+'v')
                                if cv.data_type=='e' or cv.value is None:
                                    raise ValueError(f'公式未取得有效结果：{title}!{cell.get("r")}')
                                if isinstance(cv.value,bool):
                                    cell.set('t','b')
                                    v.text=str(int(cv.value))
                                elif isinstance(cv.value,str):
                                    cell.set('t','str')
                                    v.text=cv.value
                                else:
                                    from datetime import datetime,date,time,timedelta
                                    from openpyxl.utils.datetime import to_excel
                                    val=to_excel(cv.value,values.epoch) if isinstance(cv.value,(datetime,date,time,timedelta)) else cv.value
                                    cell.set('t','n')
                                    v.text=str(val)
                            changes[part]=etree.tostring(tree,encoding='utf-8')
                finally:
                    values.close()
                updated=scratch/'cached.xlsx'
                write_zip(path,updated,changes)
                shutil.copyfile(updated,path)
                checks.append(('公式重算','PASSED',f'已重算 {len(changes)} 张含公式工作表并保存缓存。'))
    for only_values,targets in [(False,cells),(True,expected)]:
        wb=load_workbook(path,data_only=only_values,read_only=True)
        try:
            for key,val,_,_ in entries(targets):
                actual=wb[name][key].value
                if only_values and actual is None and any(s=='PENDING' for _,s,_ in checks):
                    continue
                if not same(actual,val):
                    raise ValueError(f'{name}!{key} 回读结果与指定值不一致。')
        finally:
            wb.close()
    checks.append(('表格回读','PASSED',f'已核对 {len(cells)} 个写入单元格。'))
    if expected and not any(s=='PENDING' for _,s,_ in checks):
        checks.append(('关键值','PASSED',f'已从交付文件回读核对 {len(expected)} 个预期值。'))
    return checks
