# 保存进展与同步 GitHub

本机日常继续使用工程根目录的 Workdesk。完成一项实质进展或紧急修复，并完成相关检查后，双击根目录 **GitHub同步.cmd**，输入一句进展说明即可。

工具先把开发进展提交到本地 Git，再按 `tools/github-public-files.json` 的公开清单同步源码到 GitHub。重复运行没有改动时不会产生空提交。安装包、版本标签和 Release 由既定的网盘发布流程单独处理。

也可以从项目根目录运行：

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

换电脑需要先安装 Git、Python 3.12 和 GitHub CLI，并使用 `gh auth login` 登录；仓库地址与目标分支在公开文件清单中配置。同步工具使用现有登录，不保存密码或令牌到源码。
