# 保存进展与同步 GitHub

本机日常继续使用工程根目录的 Workdesk。完成一项实质进展或紧急修复，并完成相关检查后，双击根目录 **GitHub同步.cmd**，输入一句进展说明即可。

工具先把开发进展提交到本地 Git，再按 `tools/github-public-files.json` 的公开清单同步源码到 GitHub。重复运行没有改动时不会产生空提交。安装包、版本标签和 Release 由既定的网盘发布流程单独处理。

## 日用与开发通道

同一个同步入口根据源码仓库的本机实例配置确定目标，开始时显示源码目录、本地分支和公开分支：

| 通道 | 公开目标 | 工作副本 |
| --- | --- | --- |
| 日用 | 公开清单中的 `main` | 日用工程自己的 `.maintenance/github-publish` |
| 开发 | `codex/v2-dev` | 开发仓库自己的 `.maintenance/github-publish` |

开发仓库在本机 Git 配置中登记 `workdesk.channel=development` 和 `workdesk.instanceConfig`，后者指向仓库外的开发实例配置。开发标记存在而配置缺失、损坏或公开目标错误时，同步停止，不回退为日用。开发仓库中双击原 **GitHub同步.cmd** 也使用开发目标。可选 `--branch` 只核对预期目标，不覆盖通道；例如开发侧使用 `--branch main` 会在保存或上传前拒绝。

首次公开开发分支不存在时，工具从实际公开 `main` 建立自己的过滤工作副本，再以普通推送创建 `codex/v2-dev`。后续沿用远端变更保护，不覆盖其他人的提交。内部源码仓库保留开发资料，其提交不能直接推送到公开仓库。

开发实例的 `auth_root` 可以显式引用本机已有 `.maintenance` 认证目录。工具只引用其中的 GitHub CLI 和 `gh-config`，不复制认证文件；开发公开工作副本与锁仍归开发仓库。显式认证配置缺失时停止，不换用其他账号。

### Git HTTPS 受阻时的开发传输

Git 网络不可用、同一显式账号的 GitHub API 已确认可用时，开发侧可显式使用：

```powershell
python tools/sync_github.py --transport github-api --message "保存开发进展" --no-input
```

也可在本机开发实例配置中登记 `sync_transport: "github-api"`，让原同步入口沿用这一明确选择；未配置时仍使用 Git。参数可显式选择传输，但始终不能改变所属通道和公开分支。

此选项只允许已登记开发实例的 `codex/v2-dev`，日用通道拒绝使用。首次建立公开副本时，只从认证维护目录中现有的干净公开过滤副本本地克隆，并核对其提交与 API 查询的公开 `main` 完全相同；不复制认证，不从内部源码 Git 克隆公开历史。公开开发分支已有但本地副本缺失时，先恢复或正常获取它，工具不会从旧 `main` 覆盖。

公开文件仍由同一清单过滤并在独立公开副本中原生提交。API 上传 blob、tree、commit 时逐项比对本地 Git 摘要，移动分支前重读远端，已有分支仅允许非强制快进，新建分支若被其他人抢先建立则停止。公开 `main` 不作为写入目标。上传中断时保留本地提交；分支回执未知时先核对再重试。

接口依据：[Git 提交对象](https://docs.github.com/en/rest/git/commits#create-a-commit)、[Git 树对象](https://docs.github.com/en/rest/git/trees#create-a-tree)、[非强制更新引用](https://docs.github.com/en/rest/git/refs#update-a-reference)。

## 常用命令

从所属源码仓库根目录运行：

```powershell
python tools/sync_github.py --message "修复合作任务恢复入口" --no-input
```

只查看拟同步文件，不提交、不联网：

```powershell
python tools/sync_github.py --check
```

需要先检查公开候选时，可使用 `--no-push --no-input` 保存并准备公开提交，检查后再运行普通同步命令上传。

## 同步范围

- 本地 Git 保存工程源码、检查工具和内部开发文档。
- GitHub 公开产品源码、指定检查、维护工具和用户文档。新增产品源码会按清单中的目录和后缀规则纳入；新增公开文档或工具需补入清单。
- 账号、密钥、个人资料、内部接续文档、运行组件、构建目录和分发程序不上传。公开版延续现有的首字母工作端图标及可移植 Python 入口。

本机 `.maintenance/github-publish` 保存公开工作副本，`.maintenance/tools/gh` 和 `.maintenance/gh-config` 提供 GitHub 工具与本机认证。它们均不纳入源码提交。请保留这些目录。

## 同步失败时

网络或认证失败会保留本地提交，恢复后再次运行即可补推。GitHub 出现新的提交、公开工作副本存在手动修改或发生合并冲突时，工具会停止并提示处理，不强制覆盖。

换电脑需要先安装 Git、Python 3.12 和 GitHub CLI，并使用 `gh auth login` 登录；仓库地址和日用目标在公开文件清单中配置，开发目标由本机实例通道确定。同步工具使用现有登录，不保存密码或令牌到源码。
