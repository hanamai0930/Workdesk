"""Bounded C ledger projection. Imports no harness, RPC, login or subprocess code."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import time


TERMINAL={"COMPLETED","FAILED","INTERRUPTED"}
STATE_LABELS={"RESERVED":"已登记开工意图","SEND_INTENT":"发送待核对","RUNNING":"已保存运行记录",
              "STOP_REQUESTED":"停止请求待确认","STOP_UNKNOWN":"停止状态未知","SEND_UNKNOWN":"执行状态未知",
              "COMPLETED":"协议已完成","FAILED":"失败","INTERRUPTED":"协议已中断"}
STATE_LABELS.update({'STOPPED_RUNTIME':'专属运行时已停止（回合协议状态另见详情）',
                     'FAILED_NO_SEND':'本地失败（未发送）','FAILED_LIMIT_RECONCILED':'轮数限制失败（已显式对账）'})
MAX_TEXT=262144


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def obj(text):
    if not isinstance(text,str) or len(text.encode("utf-8"))>MAX_TEXT:
        raise ValueError("记录字段超过显示上限")
    def invalid_constant(value):raise ValueError('不支持的 JSON 数值')
    result=json.loads(text,parse_constant=invalid_constant)
    if not isinstance(result,dict):raise ValueError("记录字段不是对象")
    return result


def stamp(epoch):
    from datetime import datetime,timezone,timedelta
    try:return datetime.fromtimestamp(float(epoch),timezone(timedelta(hours=8))).strftime("%m/%d %H:%M:%S")
    except (ValueError,TypeError,OverflowError,OSError):return "未知"


def protocol_label(row):
    if row.get("stop_requested") and row.get("state") not in TERMINAL:
        return "停止请求待确认 · "+STATE_LABELS.get(row.get("state"),"未知")
    return STATE_LABELS.get(row.get("state"),"状态未知")


@dataclass
class HarnessSnapshot:
    path:Path
    spec:dict
    spec_hash:str
    attempts:list
    events:list
    lifecycle:dict
    attempt_count:int
    loaded_at:float
    last_event:float|None
    closed:bool

    def attempt(self,identity):
        return next((a for a in self.attempts if a['id']==identity),None)

    def pages(self,identity):
        row=self.attempt(identity)
        if not row:raise ValueError("尝试已不在显示范围")
        events=[e for e in self.events if e['attempt_id']==identity]
        grants=[e['data'] for e in events if e['kind']=='authorization_reserved']
        grant=grants[-1] if grants else None
        matches=bool(grant and grant.get('authorized') is True and grant.get('spec_sha256')==self.spec_hash
                     and grant.get('batch_root')==str(self.path.parent))
        expiry=grant.get('expires_at') if grant else None
        expiry_status='已过期' if type(expiry) in (int,float) and expiry<=time.time() else '保存的期限尚未到达（不代表可新派发）' if type(expiry) in (int,float) else '期限未知'
        budget=self.spec.get('budget',{})
        local=self.lifecycle.get(identity,{})
        tool_label=('已确认本轮固定工具进程退出' if local.get('tool_state')=='EXIT_CONFIRMED' else
                    '有工具运行记录，当前存活未知' if local.get('tool_state')=='RUNNING' else '工具状态未知')
        material=self.spec.get('materials')
        if material is None:
            material={'prompts':self.spec.get('prompts',{}),'synthetic_phrase':self.spec.get('phrase'),
                      'tools':self.spec.get('tools',[]),'note':'合成材料；不是账户凭据'}
        final=[e['data'] for e in events if e['kind']=='agent_message_complete' and e['data'].get('phase')=='final_answer']
        candidate=row.get('candidate','')
        text_hash=hashlib.sha256(candidate.encode('utf-8')).hexdigest()
        checked=bool(final and final[-1].get('sha256')==text_hash and final[-1].get('text')==candidate)
        candidate_status=('最终正文与保存哈希一致' if checked else '正文与最终消息不一致' if final else '尚无最终正文校验记录')
        model=f"{self.spec.get('model','未知')} / {self.spec.get('effort','未知')} / {self.spec.get('service_tier','未知')}"
        overview=(f"{self.spec.get('title','未命名任务')}\n\n"
                  f"协议：{protocol_label(row)}\n工具：{tool_label}\n任意工具树：未知\n"
                  f"停止请求：{'已登记，不等于已停止' if row['stop_requested'] else '未登记'}\n"
                  f"模型配置：{model}\n负责人：未记录\n执行职责：{'执行者' if self.spec.get('role')=='executor' else self.spec.get('role','未记录')}\n审查者：未记录\n"
                  f"执行归属：{row['owner']}\n线程：{row.get('thread_id') or '未知'}\n回合：{row.get('turn_id') or '未知'}\n"
                  f"工作端：{self.spec.get('harness','未单独记录，见运行事件')}\n运行环境：{self.spec.get('environment','未单独记录，见准确规格')}\n"
                  f"尝试身份：{row['id']}\n请求身份：{row['request_id']}\n任务身份：{self.spec.get('task_id','未知')}\n"
                  f"记录来源：{self.path}\n最后事件：{stamp(self.last_event)}\n读取：{stamp(self.loaded_at)}\n"
                  "后台当前存活：未知。界面自动重读只读取本地记录。")
        authorization=(f"保存的开工授权：{'与任务指纹一致' if matches else '缺失或不一致'}\n"
                       f"接收方：{grant.get('recipient','未记录') if grant else '未记录'}\n"
                       f"Human 来源：{grant.get('human_source','未记录') if grant else '未记录'}\n"
                       f"期限：{stamp(expiry)} · {expiry_status}\n"
                       f"预算：累计登记 {self.attempt_count} / {budget.get('max_turns','未知')} 次\n"
                       f"包状态：{'已封闭，不再派发' if self.closed else '未见封闭记录；不代表允许派发'}\n"
                       f"单次上限：{budget.get('turn_seconds','未知')} 秒；总时限：{budget.get('total_seconds','未知')} 秒\n"
                       f"工具请求上限：{budget.get('tool_calls','未定义')}；API金额上限：{budget.get('api_cost_limit','未知')}\n"
                       "实际订阅扣量：未知。登记次数不是底层网络请求次数。\n"
                       "这里只呈现保存证据，不重新批准、扩充或刷新授权。\n\n"
                       f"任务指纹：{self.spec_hash}\n准确授权记录：\n{json.dumps(grant,ensure_ascii=False,indent=2)}")
        trace=[]
        for event in events:
            trace.append(f"{stamp(event['at'])} · {event['kind']}\n{json.dumps(event['data'],ensure_ascii=False,indent=2)}")
        self_checked=any(e['kind'] in {'pilot_self_check_passed','lifecycle_pilot_closed','workbench_ui_pilot_closed'} for e in self.events)
        return {"任务状态":overview,"授权与预算":authorization,
                "准确材料":json.dumps(material,ensure_ascii=False,indent=2),
                "工具过程与差异":"\n\n".join(trace) or '暂无事件（最多显示最近500条）',
                "结果与审查":f"{candidate_status}\n\n{candidate or '暂无保存的正文'}\n\n"
                    f"正文 SHA256：{text_hash}\n自审：{'有本包自有检查通过记录' if self_checked else '未见本包通过记录'}\n"
                    "独立审查：未记录\nHuman 接受：未记录\n协议完成与正文、工具退出、审查、Human 接受分别记录。"}


class HarnessReader:
    def __init__(self,root):self.root=Path(root).absolute()

    def validate_path(self,path):
        path=Path(path).absolute()
        try:parts=path.relative_to(self.root).parts
        except ValueError:raise ValueError('来源不在 C 台账目录内') from None
        if (path.name not in {'control.sqlite3','providers.sqlite3'} or len(parts) not in (2,3)
                or path.name=='providers.sqlite3' and len(parts)==3 and parts[1]!='qwen-closeout-smoke'
                or path.name=='control.sqlite3' and len(parts)==3 and parts[1] not in {'sample','live-smoke'}
                or any(p in {'.','..','codex-home','check-temp','check-fixtures'} for p in parts)):
            raise ValueError('不支持的 C 台账位置')
        for item in [path,*path.parents]:
            st=item.lstat()
            if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:
                raise ValueError('来源包含链接或重解析点')
            if item==self.root:break
        info=path.stat()
        if not path.is_file() or info.st_size>32*1024*1024 or info.st_nlink!=1:raise ValueError('台账过大、存在硬链接或不是普通文件')
        return path

    def sources(self):
        if not self.root.exists():return []
        found=[]
        for directory in sorted(self.root.iterdir()):
            if directory.name.startswith('check-') or not directory.is_dir():continue
            for path in [directory/'control.sqlite3',directory/'sample/control.sqlite3',directory/'live-smoke/control.sqlite3',directory/'providers.sqlite3',directory/'qwen-closeout-smoke/providers.sqlite3']:
                if path.is_file():
                    self.validate_path(path);found.append(path)
        return found[:100]

    @contextmanager
    def connect(self,path,write=False):
        path=self.validate_path(path)
        db=sqlite3.connect(path.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        if not write:db.execute('PRAGMA query_only=ON')
        started=time.monotonic()
        db.set_progress_handler(lambda: int(time.monotonic()-started>1),1000)
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            if write:db.commit()
        finally:db.close()

    def load(self,path):
        path=self.validate_path(path)
        if path.name=='providers.sqlite3':
            from provider_source import load_provider_snapshot
            return load_provider_snapshot(self,path)
        with self.connect(path) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'batch','attempts','events'}<=tables:raise ValueError('不是支持的 C 台账')
            batch=db.execute('SELECT spec,hash FROM batch LIMIT 2').fetchall()
            if len(batch)!=1:raise ValueError('任务规格数量不一致')
            spec=obj(batch[0]['spec']);spec_hash=digest(spec)
            if spec.get('schema')!=1 or batch[0]['hash']!=spec_hash:raise ValueError('任务版本或指纹不一致')
            if (not all(isinstance(spec.get(k),str) and spec[k] for k in ['task_id','title','model','effort'])
                    or not isinstance(spec.get('budget'),dict)
                    or any(type(spec['budget'].get(k)) not in (int,float) or spec['budget'][k]<=0
                           for k in ['max_turns','turn_seconds','total_seconds'])):
                raise ValueError('任务身份或预算字段不支持')
            if db.execute('SELECT 1 FROM attempts WHERE length(candidate)>? LIMIT 1',(MAX_TEXT,)).fetchone():raise ValueError('候选超出显示上限')
            if db.execute('SELECT 1 FROM events WHERE length(payload)>? LIMIT 1',(MAX_TEXT,)).fetchone():raise ValueError('事件超出显示上限')
            attempts=[dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY started DESC LIMIT 100')]
            count=db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            events=[{**dict(r),'data':obj(r['payload'])} for r in db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 500')][::-1]
            lifecycle={r['attempt_id']:dict(r) for r in db.execute('SELECT * FROM lifecycle LIMIT 100')} if 'lifecycle' in tables else {}
        for row in attempts:
            if not all(isinstance(row.get(k),str) and row[k] for k in ['id','owner','state','request_id']):raise ValueError('尝试身份不完整')
            if not isinstance(row.get('candidate'),str):raise ValueError('候选字段不支持')
        closed=any(e['kind'] in {'pilot_self_check_passed','lifecycle_pilot_closed','workbench_ui_pilot_closed'} for e in events)
        return HarnessSnapshot(path,spec,spec_hash,attempts,events,lifecycle,count,time.time(),events[-1]['at'] if events else None,closed)

    def request_stop(self,path,spec_hash,attempt_id,owner):
        """The only mutation. No service import, process launch, state reset or retry."""
        if self.validate_path(path).name=='providers.sqlite3':
            return {'registered':False,'reason':'当前 provider 批次仅观察，不在界面执行停止；保存状态见详情'}
        with self.connect(path,write=True) as db:
            batch=db.execute('SELECT spec,hash FROM batch').fetchone()
            if not batch or batch['hash']!=spec_hash or digest(obj(batch['spec']))!=spec_hash:raise ValueError('任务版本已变化，未登记停止')
            row=db.execute('SELECT * FROM attempts WHERE id=?',(attempt_id,)).fetchone()
            if not row or row['owner']!=owner:raise ValueError('尝试或执行归属已变化')
            if row['state'] in TERMINAL:return {'registered':False,'reason':'协议已有终态；未写入停止请求'}
            if row['stop_requested']:return {'registered':True,'reason':'已有停止请求；尚未据此确认停止'}
            db.execute('UPDATE attempts SET stop_requested=1 WHERE id=? AND owner=?',(attempt_id,owner))
            payload=canonical({'source':'Workdesk UI explicit stop','task_sha256':spec_hash,'attempt_id':attempt_id,'owner':owner,
                               'thread_id':row['thread_id'],'turn_id':row['turn_id'],'stop_confirmed':False})
            for kind in ['stop_requested','workdesk_ui_stop_requested']:
                db.execute('INSERT INTO events(at,attempt_id,kind,payload) VALUES(?,?,?,?)',(time.time(),attempt_id,kind,payload))
        return {'registered':True,'reason':'已登记停止请求，等待后台确认；当前连接未知'}
