"""Read B observer snapshots without starting their producer or any task.

task-view.json is a display interchange, not an authenticated control protocol.
Only a future explicitly configured host observer should publish production
records. Reading a recent file never proves a client/MCP/session connection.
"""

from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import time

from task_context import TaskContext, RoleAssignment, EvidenceRecord
from task_records import Snapshot, Step, Material, History, RecordReader, stamp


FILENAME="task-view.json"
MAX_BYTES=2*1024*1024


def object_value(value):
    if type(value) is not dict:
        raise ValueError("快照对象格式不支持")
    return value


def string_value(value,limit=256,optional=False):
    if optional and value is None:
        return None
    if not isinstance(value,str) or len(value.encode("utf-8"))>limit or (not optional and not value.strip()):
        raise ValueError("快照文字字段格式或长度不支持")
    return value


def list_value(value,limit=100):
    if type(value) is not list or len(value)>limit:
        raise ValueError("快照列表格式或数量不支持")
    return value


def epoch_value(value):
    if type(value) not in (int,float) or value<0 or value>253402214400 or not math.isfinite(value):
        raise ValueError("快照时间格式不支持")
    return value


def revision_value(value):
    if type(value) is not int or value<1:
        raise ValueError("修订编号格式不支持")
    return value


def refs_value(value):
    value=object_value(value)
    if len(value)>16:
        raise ValueError("输入引用过多")
    for key,digest in value.items():
        string_value(key)
        string_value(digest,64)
        if len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("输入引用哈希格式不支持")
    return value


class ObservationReader:
    def __init__(self,root,clock=time.time):
        self.root=Path(root).resolve()
        self.clock=clock
        self.identities={}

    def sources(self):
        if not self.root.exists():
            return []
        result=[]
        for child in self.root.iterdir():
            path=child/FILENAME
            if child.is_dir() and path.is_file():
                resolved=path.resolve()
                if resolved.is_relative_to(self.root) and resolved.parent.parent==self.root:
                    result.append(resolved)
        return sorted(result,key=lambda p:(p.stat().st_mtime_ns,p.parent.name),reverse=True)[:30]

    def load(self,path):
        path=Path(path).resolve()
        if path.name!=FILENAME or path.parent.parent!=self.root or not path.is_relative_to(self.root):
            raise ValueError("观察来源位置不支持")
        # A bounded read also catches replacement with a larger file between
        # discovery and load. The reader opens no path named by the payload.
        with path.open("rb") as stream:
            raw=stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError("观察快照超过显示范围")
        def invalid_constant(value):
            raise ValueError("观察快照包含非法数字")
        def unique_object(pairs):
            result={}
            for key,value in pairs:
                if key in result:
                    raise ValueError("观察快照包含重复字段")
                result[key]=value
            return result
        view=object_value(json.loads(raw.decode("utf-8"),parse_constant=invalid_constant,object_pairs_hook=unique_object))
        if type(view.get("version")) is not int or view["version"]!=1:
            raise ValueError("观察快照版本不支持")
        batch=string_value(view["batch_id"])
        if batch!=path.parent.name:
            raise ValueError("观察批次与来源目录不一致")
        project=object_value(view["project"])
        project_id=string_value(project["id"])
        project_name=string_value(project["name"],512)
        identity=(batch,project_id)
        if path in self.identities and self.identities[path]!=identity:
            raise ValueError("观察来源项目身份已变化，请核对新的来源")
        phase=string_value(view["phase"])
        producer=object_value(view["producer"])
        producer_kind=string_value(producer["kind"])
        if producer_kind not in ("host_observer","model_report","component_fixture"):
            raise ValueError("观察来源类型不支持")
        producer_name=string_value(producer["name"])
        producer_ref=string_value(producer.get("source_ref"),1024,optional=True)
        updated=epoch_value(view["updated_at"])
        ttl=view["valid_for_seconds"]
        if type(ttl) is not int or not 5<=ttl<=3600:
            raise ValueError("观察有效期格式不支持")
        age=self.clock()-updated
        freshness="时间异常" if age < -5 else "已过期" if age>ttl else "最近更新"
        contexts={}
        for item in list_value(view["tasks"]):
            item=object_value(item)
            task_id=string_value(item["id"])
            if task_id in contexts:
                raise ValueError("观察快照包含重复任务")
            assignments=[]
            for a in list_value(item.get("assignments",[]),20):
                a=object_value(a)
                assignments.append(RoleAssignment(string_value(a["role"]),string_value(a["participant"]),
                    string_value(a.get("model_name"),optional=True),string_value(a.get("model_identity"),optional=True),
                    string_value(a.get("client"),optional=True),string_value(a.get("session_ref"),1024,optional=True),
                    string_value(a.get("source_ref"),1024,optional=True)))
            evidence={}
            for kind,record in object_value(item.get("evidence",{})).items():
                if kind not in ("opinion","technical_decision","readiness","human_decision"):
                    raise ValueError("决定依据类别不支持")
                record=object_value(record)
                evidence[kind]=EvidenceRecord(string_value(record.get("summary",""),8192,optional=True),
                    string_value(record.get("source_ref"),1024,optional=True),
                    string_value(record.get("version"),optional=True),string_value(record.get("state","unknown")))
            contexts[task_id]=TaskContext(task_id,string_value(item["title"],1024),
                string_value(item.get("version"),optional=True),assignments,
                string_value(item.get("review_requirement","unknown")),string_value(item.get("review_mode","unknown")),
                string_value(item.get("review_source_ref"),1024,optional=True),evidence,
                "来自本地观察快照，来源与当前会话未在工作台认证")
        materials={}
        for item in list_value(view.get("materials",[])):
            item=object_value(item)
            ref_id=string_value(item["id"])
            if ref_id in materials:
                raise ValueError("观察快照包含重复材料")
            materials[ref_id]=dict(id=ref_id,label=string_value(item["label"]),revision=revision_value(item["revision"]),
                text=string_value(item["text"],65536),sha256=string_value(item["sha256"],64))
        steps=[]
        seen_slots=set()
        candidates={}
        rows=list_value(view.get("steps",[]))
        for item in rows:
            item=object_value(item)
            slot=string_value(item["id"])
            task_id=string_value(item["task_id"])
            if task_id not in contexts or slot in seen_slots:
                raise ValueError("观察步骤任务不匹配或标识重复")
            seen_slots.add(slot)
            candidate=None
            if item.get("candidate") is not None:
                c=object_value(item["candidate"])
                submission_id=string_value(c["id"])
                if submission_id in candidates or submission_id in materials:
                    raise ValueError("候选引用标识重复")
                text=string_value(c["text"],65536)
                digest=string_value(c["sha256"],64)
                candidate={"submission_id":submission_id,"text":text,"sha256":digest,
                    "input_refs":json.dumps(refs_value(c.get("input_refs",{}))),"received":epoch_value(c["received_at"]),
                    "hash_matches":sha256(text.encode("utf-8")).hexdigest()==digest}
                candidates[submission_id]=dict(label=string_value(item["label"])+"候选",
                    revision=revision_value(item["revision"]),text=text,sha256=digest)
            round_record=object_value(item.get("round",{}))
            state=string_value(round_record.get("state"),optional=True)
            source_kind=round_record.get("evidence_kind")
            source_ref=string_value(round_record.get("source_ref"),1024,optional=True)
            if state=="ENDED_OBSERVED" and not (producer_kind=="host_observer" and source_ref and
                    source_kind in ("native_event","operator_ui_observation")):
                state="ENDED_UNVERIFIED"
            steps.append(Step(slot,task_id,string_value(item["label"]),string_value(item.get("client","客户端未记录")),
                string_value(item.get("instruction","未记录任务说明"),65536),revision_value(item["revision"]),state,candidate,
                string_value(round_record.get("id"),optional=True),string_value(item.get("connection_ref"),optional=True),
                string_value(item.get("session_ref"),1024,optional=True),bool(item.get("session_ref")),
                assigned_role=string_value(item.get("role"),optional=True),
                role_source_ref=string_value(item.get("role_source_ref"),1024,optional=True),
                round_count=1 if round_record.get("id") else 0,
                round_source_ref=source_ref,round_evidence_kind=string_value(source_kind,optional=True),
                session_record_type="观察",
                pending_inputs=[string_value(p,512) for p in list_value(item.get("pending_inputs",[]),16)]))
        for step,item in zip(steps,rows):
            inputs=refs_value(item.get("inputs",{}))
            deliveries=list_value(item.get("deliveries",[]),16)
            for delivery in deliveries:
                object_value(delivery)
                string_value(delivery.get("ref_id"))
                string_value(delivery.get("sha256"),64)
                string_value(delivery.get("source_ref"),1024,optional=True)
            for ref_id,expected in inputs.items():
                value=materials.get(ref_id) or candidates.get(ref_id) or {}
                delivered=producer_kind=="host_observer" and any(
                    object_value(d).get("ref_id")==ref_id and d.get("sha256")==value.get("sha256") and
                    string_value(d.get("source_ref"),1024,optional=True) for d in deliveries)
                step.materials.append(Material(ref_id,value.get("label","来源缺失"),value.get("revision"),
                    value.get("text"),value.get("sha256"),expected,bool(delivered)))
            if step.candidate:
                actual=json.loads(step.candidate["input_refs"])
                step.candidate["input_status"]=("无输入引用记录" if not actual else
                    "引用版本不一致" if actual!=inputs else
                    "部分来源缺失" if any(m.text is None for m in step.materials) else
                    "来源正文不一致" if any(m.status!="正文校验一致" for m in step.materials) else "输入引用一致")
        history=[]
        seen_events=set()
        for item in list_value(view.get("events",[]),500):
            item=object_value(item)
            event_id=item["id"]
            if type(event_id) is not int or event_id<1 or event_id in seen_events:
                raise ValueError("观察事件标识不支持")
            seen_events.add(event_id)
            slot=string_value(item.get("step_id"),optional=True)
            if slot is not None and slot not in seen_slots:
                raise ValueError("观察事件引用未知步骤")
            history.append(History(event_id,stamp(epoch_value(item["at"])),string_value(item["label"]),
                string_value(item.get("client","观察来源")),slot,string_value(item.get("kind","observation")),
                object_value(item.get("data",{}))))
        connections=[]
        seen_connections=set()
        for item in list_value(view.get("connections",[]),20):
            item=object_value(item)
            connection_id=string_value(item["id"])
            if connection_id in seen_connections:
                raise ValueError("观察快照包含重复连接")
            seen_connections.add(connection_id)
            last_seen=item.get("protocol_observed_at")
            closed=item.get("transport_closed_at")
            if last_seen is not None:
                epoch_value(last_seen)
            if closed is not None:
                epoch_value(closed)
                if last_seen is None or closed>last_seen:
                    raise ValueError("通信结束与最近观测时间不一致")
            if item.get("native_chat_identity_authenticated",False) is not False:
                raise ValueError("当前显示协议不支持声明原生会话已认证")
            connections.append({"id":connection_id,"client":string_value(item["client"]),
                "session_ref":string_value(item.get("session_ref"),1024,optional=True),
                "session_source_ref":string_value(item.get("session_source_ref"),1024,optional=True),
                "protocol_observed_at":last_seen,"transport_closed_at":closed,
                "age_seconds_at_load":self.clock()-last_seen if last_seen is not None else None,
                "native_chat_identity_authenticated":False})
        observation={"project_id":project_id,"project_name":project_name,"phase":phase,
            "producer":producer_name,"producer_kind":producer_kind,"producer_ref":producer_ref,
            "updated_at":stamp(updated),"freshness":freshness,"valid_for_seconds":ttl,
            "age_seconds_at_load":age,"loaded_monotonic":time.monotonic(),
            "connections":connections,
            "note":"快照可读取不证明任务服务、客户端或会话已连接；来源声明尚未认证"}
        snapshot=Snapshot(path,batch,None,steps,sorted(history,key=lambda e:e.event_id,reverse=True),
            source_kind="component_fixture" if producer_kind=="component_fixture" else "local_observation",
            task_contexts=contexts,observation=observation)
        self.identities[path]=identity
        return snapshot


class CompositeTaskSource:
    poll_interval_ms=5000

    def __init__(self,history_root,observation_root):
        self.history=RecordReader(history_root)
        self.observations=ObservationReader(observation_root)

    def sources(self):
        return self.observations.sources()+self.history.sources()

    def load(self,path):
        return self.observations.load(path) if Path(path).name==FILENAME else self.history.load(path)

    def can_auto_refresh(self,path):
        return Path(path).name==FILENAME
