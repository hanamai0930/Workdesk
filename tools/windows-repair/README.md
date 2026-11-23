# Workdesk 1.0 合作任务修复程序

本工具将明确列出的程序文件打入单个 `Workdesk-Repair-x64.exe`。运行不依赖额外 Python/Node；使用 Windows 11 自带的 .NET Framework。

用户运行后，程序从当前用户安装记录定位 Workdesk，也可手动选择含 `Workdesk.exe` 的安装目录。程序只接受正式 1.0.0 安装版的发布标识与启动器哈希，对每个更新文件核对原版/已修复哈希。清单不允许写入用户资料、账号文件、任务库或项目文件，也不更换原 `release.json`。

修复前保存工作并关闭 Workdesk。程序文件仍被占用时拒绝更新，提示重启 Windows 后先运行修复程序；不强制终止后台、工作端或任务进程。准备完成后锁定启动器、Python 与当前桌面宿主文件，防止更新期间启动旧程序。

原程序文件备份到安装目录 `.workdesk-repair/<补丁标识>/<时间与随机标识>/`。更新使用同目录临时文件和原子替换。失败时恢复已改文件，并保留事务记录；重复运行会识别已完成修复。最后回执保存为安装目录 `.workdesk-repair/last-repair.json`。

## 构建

`files.json` 为相对路径字符串数组。仅允许业务 Python、前端静态脚本/样式及构建输出，新增文件使用空基线哈希；已存在但不同于原发布版或本补丁的文件会使整次修复被拒绝。

```powershell
python tools/windows-repair/build.py --baseline build/installer-release-20261123/p --release-manifest build/installer-release-20261123/stage/release.json --payload build/cooperation-repair-20261123/payload --files build/cooperation-repair-20261123/files.json --work build/windows-repair-20261123/package --output build/windows-repair-20261123/Workdesk-Repair-x64.exe
```

构建使用系统的 x64 C# 编译器；嵌入 ZIP、manifest 与每个文件的 SHA-256。清单、构建回执和最终 EXE 哈希保留在 `--work`。

## 隔离验证

```powershell
python tools/windows-repair/check.py --baseline build/installer-release-20261123/p --release-manifest build/installer-release-20261123/stage/release.json --package build/windows-repair-20261123/package --exe build/windows-repair-20261123/Workdesk-Repair-x64.exe --evidence build/windows-repair-20261123/final-check
```

检查只创建隔离程序目录，覆盖检查/应用/重复运行、中文空格路径、错误目录、被修改的基线、运行时占用和更新中异常回滚。账号、任务与项目哨兵保持逐字节一致；不会修改本机真实安装。

静默验证接口为 `--check --directory <程序目录> --result <结果JSON>` 或 `--apply --directory <程序目录> --result <结果JSON>`。默认无参数打开中文修复窗口。事务失败注入只由单独编译的私有测试入口调用，正式修复入口不开放失败注入参数。
