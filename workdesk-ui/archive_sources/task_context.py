"""Display records for task roles and evidence, never dispatch qualifications.

This is B's view contract, not C's storage schema or an authorization model.
Adapters supply explicit records; candidate text and client names are not parsed
to create decisions, role changes, acceptance, or eligibility.
"""

from dataclasses import dataclass, field


ROLE_NAMES={"lead":"负责人","execute":"执行者","review":"审查者"}
EVIDENCE_NAMES={"opinion":"方案与意见","technical_decision":"技术决定",
                "readiness":"开工核对","human_decision":"Human 决议"}


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    participant: str
    model_name: str | None = None
    model_identity: str | None = None
    client: str | None = None
    session_ref: str | None = None
    source_ref: str | None = None

    @property
    def label(self):
        return ROLE_NAMES.get(self.role,"其他职责")

    @property
    def display_participant(self):
        return self.participant or "未记录处理者"


@dataclass(frozen=True)
class EvidenceRecord:
    summary: str = ""
    source_ref: str | None = None
    version: str | None = None
    state: str = "unknown"

    def display_status(self,task_version):
        if self.state=="stale":
            return "旧记录，待核对"
        if self.state not in ("unknown","recorded"):
            return "来源状态未识别"
        if self.state!="recorded" or not self.source_ref:
            return "仅有说明，缺少来源" if self.summary else "未记录"
        if task_version is not None and self.version is None:
            return "适用版本未记录"
        if task_version is not None and self.version!=task_version:
            return "版本不一致，待核对"
        return "有来源记录" if task_version is not None else "有来源记录，版本未核对"


@dataclass
class TaskContext:
    task_id: str
    title: str = ""
    version: str | None = None
    assignments: list[RoleAssignment] = field(default_factory=list)
    review_requirement: str = "unknown"
    review_mode: str = "unknown"
    review_source_ref: str | None = None
    evidence: dict[str,EvidenceRecord] = field(default_factory=dict)
    source_note: str = "正式任务分工与决定记录未接入"

    def role_rows(self):
        rows=[]
        for role,label in ROLE_NAMES.items():
            matches=[a for a in self.assignments if a.role==role]
            rows.extend((label,a.display_participant+("（来源缺失）" if not a.source_ref else "")) for a in matches)
            if not matches:
                rows.append((label,"未记录"))
        rows.extend((a.label,a.display_participant) for a in self.assignments if a.role not in ROLE_NAMES)
        return rows

    def combined_roles(self):
        groups={}
        for assignment in self.assignments:
            if assignment.model_identity:
                groups.setdefault(assignment.model_identity,[]).append(assignment)
        notices=[]
        for assignments in groups.values():
            labels=list(dict.fromkeys(a.label for a in assignments))
            if len(labels)>1:
                notices.append(f"{assignments[0].model_name or assignments[0].display_participant}：{'、'.join(labels)}")
        return notices

    def review_status(self):
        requirement={"independent":"要求独立审查","self_allowed":"允许自审",
                     "unknown":"审查要求未记录"}.get(self.review_requirement,"审查要求未识别")
        reviewers=[a for a in self.assignments if a.role=="review"]
        workers=[a for a in self.assignments if a.role in ("lead","execute")]
        if self.review_mode not in ("unknown","self","independent"):
            actual="审查方式未识别"
        elif self.review_mode=="unknown" or not self.review_source_ref:
            actual="审查来源未记录"
        elif self.review_mode=="self":
            actual="记录为自审"
        else:
            actual="来源声明为独立审查，未核验"
        same=any(a.model_identity and a.model_identity==b.model_identity for a in reviewers for b in workers)
        if same:
            actual+="；审查者与设计/执行者有相同模型标识"
        elif not reviewers or not workers or any(not a.model_identity for a in reviewers+workers):
            actual+="；身份资料不足"
        if self.review_requirement=="independent" and self.review_mode=="self" and self.review_source_ref:
            actual+="；与独立审查要求不符"
        return requirement+"\n"+actual

    def evidence_record(self,kind):
        return self.evidence.get(kind,EvidenceRecord())

    def pages(self,historical_steps=()):
        roles="任务分工（按任务记录，不按客户端固定）\n\n"
        for label,participant in self.role_rows():
            roles+=f"{label}：{participant}\n"
        roles+=f"\n{self.source_note}\n\n审查要求与来源\n{self.review_status()}\n"
        combined=self.combined_roles()
        roles+=("\n同一模型兼任（按来源标识）\n"+"\n".join(combined) if combined else
                "\n没有记录可确认的兼任关系；这不证明审查独立。")
        if historical_steps:
            roles+="\n\n历史步骤记录（不是当前正式任务分工，不证明实际参与）\n"
            for step in historical_steps:
                roles+=f"{step.label}：{step.role} · {step.client}\n"
        roles+="\n分工变化不会增加权限；本页面只展示来源，不执行接管。"
        records="分开查看四类依据\n\n"
        for kind,label in EVIDENCE_NAMES.items():
            record=self.evidence_record(kind)
            records+=f"{label}：{record.display_status(self.version)}\n{record.summary or '没有对应记录'}\n\n"
        records+=("方案意见、技术选择、开工核对与 Human 决议不能互相替代。\n"
                  "候选收到或回合结束不生成这些记录。本界面不判定开工资格、不写入 Human 接受。\n"
                  "接管、恢复决策与额度控制仍由阶段 C 落实。")
        technical=(f"任务标识：{self.task_id}\n任务版本：{self.version or '未记录'}\n"
                   f"分工来源说明：{self.source_note}\n审查来源：{self.review_source_ref or '未记录'}\n\n")
        for assignment in self.assignments:
            technical+=(f"职责：{assignment.label}（{assignment.role}）\n处理者：{assignment.display_participant}\n"
                        f"模型：{assignment.model_name or '未记录'}\n模型来源标识：{assignment.model_identity or '未记录'}\n"
                        f"客户端：{assignment.client or '未记录'}\n会话来源：{assignment.session_ref or '未记录'}\n"
                        f"分工来源：{assignment.source_ref or '未记录'}\n\n")
        for kind,label in EVIDENCE_NAMES.items():
            record=self.evidence_record(kind)
            technical+=(f"{label}\n来源标识：{record.source_ref or '未记录'}\n"
                        f"适用版本：{record.version or '未记录'}\n来源状态：{record.state}\n\n")
        technical+="模型标识相同可提示兼任；不同标识或客户端不证明身份认证或审查独立。来源字符串只用于查看，不打开或执行。"
        return {"任务分工":roles,"决定与依据":records,"来源详情":technical}
