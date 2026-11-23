"""An immutable local credential reference for one official execution route."""
from dataclasses import dataclass

from .paths import local_credential_path, legacy
from .store import digest


@dataclass(frozen=True)
class ConnectionContext:
    source: object
    provider: str
    reference: str
    file_revision: str | None

    def __getattr__(self,name):return getattr(self.source,name)

    @property
    def credential_revision(self):
        return digest({'provider':self.provider,'reference':self.reference,'file_revision':self.file_revision})

    @property
    def codex_home(self):
        if self.provider!='codex':raise ValueError('此连接不提供Codex认证目录。')
        return self.source.validate_codex_home(self.reference)

    @property
    def api_env(self):
        if self.provider=='codex':return None
        return local_credential_path(self.reference)

    def credential_references(self):
        return {'schema':1,'revision':self.credential_revision,'api_env':self.reference if self.provider!='codex' else None,
                'api_env_sha256':self.file_revision,'codex_home':self.reference if self.provider=='codex' else None}

    def credentials(self):
        if self.provider=='codex':
            self.codex_home
            return {}
        reader=legacy(self.source,'provider-harness','credentials')
        if not isinstance(self.file_revision,str) or len(self.file_revision)!=64:raise ValueError('连接缺少准确凭据文件版本。')
        try:all_credentials=reader.load_credentials(self.api_env,expected_sha256=self.file_revision)
        except reader.CredentialError:raise ValueError('选定连接的凭据文件已变化或不可用；未回退其它连接。') from None
        secret=all_credentials.get(self.provider)
        if not secret or self.provider=='qwen' and secret.billing_kind!='general_api':raise ValueError('选定连接没有所需工作端的有效引用。')
        return {self.provider:secret}

    def validate(self):
        if self.provider=='codex':self.codex_home
        else:self.credentials()
        return self


def context(locations,connection):
    ref=connection['credential_reference']
    return ConnectionContext(locations,connection['provider'],ref['reference'],ref.get('file_revision'))
