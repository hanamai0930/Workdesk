"""Read-only projection of saved collaboration records. Never dispatches work."""

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time

from task_context import TaskContext, ROLE_NAMES


STAGES={"proposal":"提案","review":"复核","revision":"修订",
        "interrupt":"中断检查","variation":"补充检查"}
CLIENTS={"codex":"GPT / Codex","kimi":"Kimi Work"}
ROLES={"proposal":"方案负责人","review":"批判与复核者","revision":"方案修订者",
       "interrupt":"检查参与者","variation":"检查参与者"}
STATES={"ENDED_OBSERVED":"已观测结束","IN_FLIGHT":"尚无结束记录",
        "STOP_UNCONFIRMED":"停止未确认","SEND_FAILED":"发送失败","ENDED_UNVERIFIED":"自报结束，未独立核对"}
EVENTS={"claim_observed":"领取任务","material_delivered":"读取材料",
        "candidate_received":"提交候选","operator_round_started":"启动回合",
        "operator_round_observation":"回合观测","operator_dispatch_closed":"关闭派发",
        "operator_continuation_completed_for_review":"交接收尾",
        "deadline_elapsed":"达到时限","incomplete_branch_stopped":"不完整结果",
        "a6_closeout":"保存记录"}


def stamp(epoch):
    try:
        return datetime.fromtimestamp(float(epoch),timezone(timedelta(hours=8))).strftime("%m/%d %H:%M")
    except (TypeError,ValueError,OverflowError,OSError):
        return "时间未知"


def object_json(value):
    if not isinstance(value,str) or len(value)>131072:
        raise ValueError("记录字段格式不支持")
    result=json.loads(value)
    if not isinstance(result,dict):
        raise ValueError("记录字段格式不支持")
    return result


def list_json(value):
    if not isinstance(value,str) or len(value)>131072:
        raise ValueError("引用字段格式不支持")
    result=json.loads(value)
    if not isinstance(result,list) or any(not isinstance(item,str) for item in result):
        raise ValueError("引用字段格式不支持")
    return result


def checked_text(value):
    if not isinstance(value,str) or len(value.encode("utf-8"))>65536:
        raise ValueError("正文超过显示范围或格式不支持")
    return value


@dataclass
class Material:
    ref_id: str
    label: str
    revision: int | None
    text: str | None
    sha256: str | None
    expected_sha256: str | None = None
    delivered: bool = False

    @property
    def status(self):
        if self.text is None or not self.sha256:
            return "未找到来源正文"
        if sha256(self.text.encode("utf-8")).hexdigest()!=self.sha256:
            return "正文校验不一致"
        if self.expected_sha256 and self.expected_sha256!=self.sha256:
            return "引用版本不一致"
        return "正文校验一致"

    @property
    def delivery_status(self):
        return "有对应交付记录" if self.delivered else "未找到对应交付记录"


@dataclass
class Step:
    slot_id: str
    task_id: str
    label: str
    client: str
    instruction: str
    revision: int
    state: str | None
    candidate: dict | None
    round_id: str | None
    connection_id: str | None = None
    session_id: str | None = None
    session_associated: bool = False
    materials: list[Material] = field(default_factory=list)
    round_count: int = 0
    assigned_role: str | None = None
    role_source_ref: str | None = None
    round_source_ref: str | None = None
    round_evidence_kind: str | None = None
    session_record_type: str = "历史"
    pending_inputs: list[str] = field(default_factory=list)

    @property
    def role(self):
        return ROLE_NAMES.get(self.assigned_role,self.assigned_role) if self.assigned_role else ROLES.get(self.slot_id,"未记录职责")

    @property
    def role_origin(self):
        if self.assigned_role:
            return "任务记录" if self.role_source_ref else "来源未记录"
        return "历史步骤推定"

    @property
    def session_status(self):
        return f"有{self.session_record_type}关联 · 当前未核对" if self.session_associated and self.session_id else f"无{self.session_record_type}会话关联"

    @property
    def candidate_status(self):
        if not self.candidate:
            return "暂无候选"
        return "候选已回收" if self.candidate["hash_matches"] else "候选校验不一致"

    @property
    def round_status(self):
        return STATES.get(self.state,"状态未知" if self.state else "暂无回合记录")


@dataclass
class History:
    event_id: int
    time: str
    action: str
    client: str
    slot_id: str | None
    kind: str
    data: dict


@dataclass
class Snapshot:
    path: Path
    batch_id: str
    dispatch_enabled: bool | None
    steps: list[Step]
    history: list[History]
    loaded_at: str = field(default_factory=lambda: datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"))
    source_kind: str = "local_history"
    capabilities: tuple[str,...] = ("view_records","preview_materials","export_candidate")
    task_contexts: dict[str,TaskContext] = field(default_factory=dict)
    observation: dict = field(default_factory=dict)

    @property
    def source_label(self):
        if self.source_kind=="local_observation":
            return "任务观察 · 只读 · "+self.observation_freshness
        return {"local_history":"本地历史 · 只读","component_fixture":"自有测试数据 · 非实时"}.get(self.source_kind,"未识别来源 · 只读")

    @property
    def observation_freshness(self):
        saved=self.observation.get("freshness","时间未知")
        if saved!="最近更新" or "loaded_monotonic" not in self.observation:
            return saved
        age=self.observation["age_seconds_at_load"]+max(0,time.monotonic()-self.observation["loaded_monotonic"])
        return "已过期" if age>self.observation["valid_for_seconds"] else saved

    def connection_summary(self):
        records=self.observation.get("connections",[])
        if not records:
            return "任务通信：没有观测记录；当前连接未知"
        elapsed=max(0,time.monotonic()-self.observation.get("loaded_monotonic",time.monotonic()))
        lines=[]
        for item in records:
            age=item.get("age_seconds_at_load")
            state=("未观测协议通信" if age is None else "保存的通信已结束，当前连接未知（不表示任务结束）" if item["transport_closed_at"] is not None else
                   "通信时间异常" if age < -5 else "近期无通信，当前未知" if age+elapsed>30 else "近期有协议通信")
            if self.observation_freshness!="最近更新":
                state+=" · 观察来源"+self.observation_freshness
            lines.append(f"{item['client']}：{state}；原生会话认证未知")
        return "任务通信观测\n"+"\n".join(lines)

    @property
    def task_ids(self):
        return list(dict.fromkeys([step.task_id for step in self.steps]+list(self.task_contexts)))

    @property
    def candidate_count(self):
        return sum(step.candidate is not None for step in self.steps)

    def task_name(self,task_id):
        first=next((s for s in self.steps if s.task_id==task_id),None)
        context=self.context_for(task_id)
        title=" ".join((context.title or (first.instruction if first else "")).split())
        return (title[:28]+"…") if len(title)>28 else title or "未命名任务"

    def context_for(self,task_id):
        context=self.task_contexts.get(task_id)
        if context and context.task_id!=task_id:
            raise ValueError("任务分工记录与所选任务不一致")
        return context or TaskContext(task_id)


class RecordReader:
    def __init__(self,root):
        self.root=Path(root).resolve()

    def sources(self):
        if not self.root.exists():
            return []
        sources=[]
        # Only immediate batch directories; never include synthetic fixtures.
        for child in self.root.iterdir():
            path=child/"center.sqlite3"
            if child.is_dir() and path.is_file():
                try:
                    path.resolve().relative_to(self.root)
                    sources.append(path.resolve())
                except ValueError:
                    continue
        return sorted(sources,key=lambda p:(p.stat().st_mtime_ns,p.parent.name),reverse=True)[:30]

    def load(self,path):
        path=Path(path).resolve()
        path.relative_to(self.root)
        if path.name!="center.sqlite3" or path.parent.parent!=self.root or path.stat().st_size>33554432:
            raise ValueError("记录位置或大小不支持")
        # The saved bridge uses a writable connection and BEGIN IMMEDIATE
        # even for query_task. The UI never imports/starts that service and
        # opens its saved database explicitly read-only instead.
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=.15)) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            control_row=db.execute("SELECT value FROM meta WHERE key='control'").fetchone()
            control=object_json(control_row[0]) if control_row else {}
            bindings={r["connection_id"]:dict(r) for r in db.execute("SELECT * FROM bindings LIMIT 100")}
            rounds=list(db.execute("SELECT round_id,slot_id,state,started FROM rounds ORDER BY started DESC,round_id DESC LIMIT 1000"))
            latest={}
            for row in rounds:
                latest.setdefault(row["slot_id"],row)
            round_slots={r["round_id"]:r["slot_id"] for r in rounds}
            submissions={}
            for row in db.execute("SELECT * FROM submissions LIMIT 100"):
                value=dict(row)
                checked_text(value["text"])
                value["hash_matches"]=sha256(value["text"].encode("utf-8")).hexdigest()==value["sha256"]
                submissions[row["slot_id"]]=value
            steps=[]
            batch_ids=set()
            slots=[dict(r) for r in db.execute("SELECT * FROM slots ORDER BY rowid LIMIT 100")]
            for row in slots:
                batch_ids.add(row["batch_id"])
                recent=latest.get(row["slot_id"])
                binding=bindings.get(row["connection_id"],{})
                steps.append(Step(row["slot_id"],row["task_id"],STAGES.get(row["slot_id"],f"步骤 {len(steps)+1}"),
                                  CLIENTS.get(binding.get("client"),"客户端未知"),checked_text(row["instructions"]),
                                  row["revision"],recent["state"] if recent else None,
                                  submissions.get(row["slot_id"]),recent["round_id"] if recent else None,
                                  row["connection_id"],binding.get("session_id"),binding.get("verified")==1,
                                  round_count=sum(r["slot_id"]==row["slot_id"] for r in rounds)))
            if len(batch_ids)>1:
                raise ValueError("记录含多个批次，暂不合并显示")
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            material_rows={r["material_id"]:dict(r) for r in db.execute("SELECT * FROM materials LIMIT 100")} if "materials" in tables else {}
            for value in material_rows.values():
                checked_text(value["text"])
            reads=list(db.execute("SELECT claim_id,ref_id,sha256 FROM reads LIMIT 1000")) if "reads" in tables else []
            claims=list(db.execute("SELECT claim_id,round_id FROM claims LIMIT 1000")) if "claims" in tables else []
            steps_by_slot={s.slot_id:s for s in steps}
            for step,row in zip(steps,slots):
                candidate=step.candidate
                refs={}
                if candidate:
                    # Old minimal records may contain an empty list. It is
                    # missing evidence, never a successful version check.
                    raw_refs=candidate["input_refs"]
                    if not isinstance(raw_refs,str) or len(raw_refs)>131072:
                        raise ValueError("候选引用字段格式不支持")
                    refs=json.loads(raw_refs)
                    if not isinstance(refs,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in refs.items()):
                        refs={}
                claim_ids=({candidate["claim_id"]} if candidate and candidate.get("claim_id") else
                           {c["claim_id"] for c in claims if c["round_id"]==step.round_id})
                if row.get("material_id"):
                    value=material_rows.get(row["material_id"],{})
                    step.materials.append(Material(row["material_id"],"任务材料",value.get("revision"),
                                                   value.get("text"),value.get("sha256"),refs.get(row["material_id"])))
                for parent_id in list_json(row.get("parents","[]")):
                    parent=steps_by_slot.get(parent_id)
                    value=parent.candidate if parent and parent.candidate else {}
                    ref_id=value.get("submission_id",parent_id)
                    step.materials.append(Material(ref_id,(parent.label if parent else "上游步骤")+"候选",
                                                   parent.revision if parent else None,value.get("text"),
                                                   value.get("sha256"),refs.get(ref_id)))
                for material in step.materials:
                    material.delivered=any(r["claim_id"] in claim_ids and r["ref_id"]==material.ref_id and
                                           r["sha256"]==material.sha256 for r in reads)
                if candidate:
                    expected={m.ref_id:m.sha256 for m in step.materials}
                    candidate["input_status"]=("无输入引用记录" if not refs else
                        "部分来源缺失" if not expected or any(m.text is None for m in step.materials) else
                        "引用版本不一致" if refs!=expected else
                        "来源正文不一致" if any(m.status!="正文校验一致" for m in step.materials) else "输入引用一致")
            history=[]
            for row in db.execute("SELECT id,at,connection_id,kind,data FROM events ORDER BY id DESC LIMIT 500"):
                data=object_json(row["data"])
                slot=data.get("slot_id") or round_slots.get(data.get("round_id"))
                # Delivery events carry a claim, not a slot. Retain the task
                # association instead of attributing them to every task.
                if not slot and data.get("claim_id"):
                    round_id=next((c["round_id"] for c in claims if c["claim_id"]==data["claim_id"]),None)
                    slot=round_slots.get(round_id)
                binding=bindings.get(row["connection_id"],{})
                history.append(History(row["id"],stamp(row["at"]),EVENTS.get(row["kind"],"其他记录"),
                                       CLIENTS.get(binding.get("client"),"中心记录"),slot,row["kind"],data))
        enabled=control.get("dispatch_enabled")
        return Snapshot(path,next(iter(batch_ids),path.parent.name),enabled if isinstance(enabled,bool) else None,steps,history)


def step_details(snapshot,step):
    candidate=step.candidate
    text=(f"{snapshot.source_label}\n来源：{snapshot.path}\n批次：{snapshot.batch_id}\n"
          f"任务：{step.task_id}\n步骤：{step.slot_id} · {step.label}\n客户端：{step.client}\n"
          f"职责：{step.role}（{step.role_origin}，不是客户端固定职责）\n"
          f"职责来源：{step.role_source_ref or '未记录正式分工来源'}\n"
          f"会话：{step.session_status}\n历史会话标识：{step.session_id or '未记录'}\n"
          f"关联不是协议级认证；窗口接入不能证明任务连接。\n"
          f"任务修订：{step.revision}\n候选：{step.candidate_status}\n回合：{step.round_status}\n\n"
          f"回合观测来源：{step.round_source_ref or '未在步骤记录中提供'}\n观测方式：{step.round_evidence_kind or '未在步骤记录中提供'}\n\n"
          f"待齐材料：{'；'.join(step.pending_inputs) or '无待齐来源记录'}\n\n"
          f"记录中的任务说明\n{step.instruction}\n")
    if candidate:
        text+=(f"\n候选标识：{candidate['submission_id']}\n接收时间：{stamp(candidate['received'])}（UTC+8）\n"
               f"SHA-256：{candidate['sha256']}\n引用核对：{candidate.get('input_status','未核对')}\n引用记录：{candidate['input_refs']}\n\n"
               f"候选正文（文本查看，未作接受判断）\n{candidate['text']}")
    return text


def export_candidate(step,path):
    """Explicit Human save action. Preserve UTF-8 bytes, including LF."""
    if not step.candidate or not step.candidate["hash_matches"]:
        raise ValueError("候选缺失或正文校验不一致，不能导出")
    data=step.candidate["text"].encode("utf-8")
    if sha256(data).hexdigest()!=step.candidate["sha256"]:
        raise ValueError("候选在查看期间发生变化，请刷新后重试")
    Path(path).write_bytes(data)


def observation_pages(snapshot,kind,task_id,object_id=None):
    """Pure projection shared by every observer; no bridge or control imports."""
    steps=[s for s in snapshot.steps if s.task_id==task_id]
    step=next((s for s in steps if s.slot_id==object_id),None)
    technical=(f"{snapshot.source_label}\n来源：{snapshot.path}\n批次：{snapshot.batch_id}\n任务：{task_id}\n"
               f"快照读取时间：{snapshot.loaded_at}（UTC+8）\n来源类型：{snapshot.source_kind}\n"
               f"记录中的派发状态：{snapshot.dispatch_enabled}（不是当前连接状态）\n"
               f"本界面能力：{', '.join(snapshot.capabilities)}\n\n"
               "显示范围：最近 500 条事件、最近 1000 个回合；回合数是所读历史记录数，不是预算。\n"
               "实时服务、任务派发、暂停、取消、恢复及 Human 接受写入均未接入。\n"
               "历史预算不是 B 的预算；本轮没有真实协作模型请求。\n")
    if snapshot.observation:
        technical+="\n观察来源元数据（不是当前连接证明）\n"+json.dumps(snapshot.observation,ensure_ascii=False,indent=2)+"\n"
        technical+="观察快照只提供每步骤的一个回合来源，不提供完整回合历史或预算计数。\n"
        technical+="\n"+snapshot.connection_summary()+"\n"
    if kind=="event":
        event=next((e for e in snapshot.history if str(e.event_id)==str(object_id)),None)
        if not event:
            raise LookupError("这条交接记录已不在当前显示范围")
        return {"交接记录":f"{event.time}（UTC+8） · {event.action}\n客户端：{event.client}\n\n"+json.dumps(asdict(event),ensure_ascii=False,indent=2),"技术详情":technical}
    if task_id not in snapshot.task_ids or (kind in ("step","materials") and step is None):
        raise LookupError("未找到原任务或步骤")
    context=snapshot.context_for(task_id)
    if kind=="roles":
        pages=context.pages(steps)
        pages["来源详情"]=technical+"\n"+pages["来源详情"]
        return pages
    if kind=="materials":
        pages={"交接概要":f"步骤：{step.label}\n职责：{step.role}\n客户端：{step.client}\n会话：{step.session_status}\n"
               f"任务修订：{step.revision}\n\n只读材料预览，尚未发送任何新消息。\n"
               "交付记录只证明保存的传输事件，不证明语义理解；对应候选的领取记录（无候选时对应最新回合）。\n"}
        for i,material in enumerate(step.materials,1):
            pages[f"{i} · {material.label}"]=(f"{material.label}\n版本：{material.revision if material.revision is not None else '未记录'}\n"
                f"{material.status} · {material.delivery_status}\n\n"
                +(material.text if material.text is not None else "记录中未找到来源正文，不猜测或读取其他文件。"))
        if not step.materials:
            pages["材料说明"]="这份历史没有记录材料来源；不能据此声称已交接。"
        pages["技术详情"]=technical+"\n\n".join(
            f"{m.label}\n来源引用：{m.ref_id}\n来源 SHA-256：{m.sha256 or '未记录'}\n候选引用 SHA-256：{m.expected_sha256 or '未记录'}"
            for m in step.materials)
        return pages
    if kind in ("review","results"):
        pages={"概览":("候选审查" if kind=="review" else "成果查看")+f" · {snapshot.source_label}\n\n"
               "提案、批评和修订均为候选文本。没有 Human 接受记录，未写入 Research State。\n"
               "导出是复制当前候选字节，不构成接受或执行；正文校验不一致时禁止导出。\n"}
        for i,item in enumerate(steps,1):
            if item.candidate:
                pages[f"{i} · {item.label}候选"]=(f"{item.label} · {item.role}\n客户端：{item.client}\n"
                    f"候选：{item.candidate_status} · {item.candidate.get('input_status','未核对')}\n"
                    f"最新回合：{item.round_status}\nHuman 接受：无接受记录\n\n"
                    f"候选正文（文本查看，未作接受判断）\n{item.candidate['text']}")
        if len(pages)==1:
            pages["候选说明"]="当前任务暂无候选成果。"
        if kind=="review":
            pages["审查来源"]=context.pages(steps)["任务分工"]
        pages["技术详情"]=technical+"\n\n".join(step_details(snapshot,s) for s in steps)
        return pages
    if kind=="step":
        return {"任务与候选":step_details(snapshot,step),"技术详情":technical}
    summary=(f"任务：{snapshot.task_name(task_id)}\n{snapshot.source_label}\nHuman 接受：无接受记录\n"
             +snapshot.connection_summary()+"\n\n")
    for item in steps:
        summary+=(f"{item.label} · {item.role}\n客户端：{item.client}\n会话：{item.session_status}\n"
                  f"候选：{item.candidate_status}\n最新回合：{item.round_status}\n历史回合数：{item.round_count}\n\n")
    pages={"任务概览":summary,**context.pages(steps)}
    pages["技术详情"]=technical+"\n\n".join(step_details(snapshot,s) for s in steps)
    return pages
