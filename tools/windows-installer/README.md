# Windows 安装包构建

## v1.2 伙伴选装版

运行 `Workdesk-Setup-1.2.1-x64.exe` 后，安装向导可打开伙伴管理窗口。直接勾选名称会从官方源安装，也可用“导入本地补充包…”一次选择多份 `.wdpa`。文件选择器兼容同版本的 `.wdpartner`。八个官方端各自选装，包体和预计占用在选择时显示。开始菜单中的“Workdesk 1.2 → 伙伴管理”可再次打开此窗口。

安装向导显示当前伙伴、安装阶段和可计算的进度。伙伴下载失败后，可以重试或改用本地包。应用内的“账号与模型 → 工作端管理”也提供联网安装和本地包导入。安装成功后，设置页收起此前的失败提示。

从 v1.2.0 更新时，先在开始菜单停止 Workdesk 1.2，再运行新版安装程序，沿用已有的安装位置。现有工作端和资料随原安装实例继续使用。

安装版有独立的程序和资料目录。首次使用前可从已停止的 1.0.1 安装版导入旧库，保留升级前备份；账号及 API Key 在新版重新连接。升级及卸载前可从开始菜单选择“停止 Workdesk 1.2”。卸载保留实例资料，在原程序位置重新安装可继续使用。

构建使用以下入口，运行组件和发行产物在仓库以外准备。

- `build_release_runtime.py` 汇集冻结的 Go 核心、界面及明确指定的运行组件。为每次发行准备干净的程序目录。
- `build_partner_preview_packs.py` 生成伙伴包及校验清单。`partner-preview/build.ps1` 将该清单嵌入伙伴管理程序，Go 核心的 `bundle_pins.go` 固定同一组包和发布版本。
- `build_12.py` 按 `Workdesk12.iss` 将程序、选装窗口、WebView2 离线安装器、依赖许可和 Poppler 对应源码合并为主安装程序。
- `check_12.py --root <发行工作目录> --legacy-fixture <旧库测试副本>` 使用最终 EXE 验证导入、启动、伙伴安装、覆盖安装和卸载。该检查使用尚无已登记 1.2 安装的 Windows 测试账户。

## v1.0.1 通用兼容版

`Workdesk.iss` 提供原 1.0.0 安装配置，以及 `ReleaseBuild + Win10Compatibility` 的 1.0.1 通用兼容配置。兼容版文件名为 `Workdesk-Setup-Win10-Win11-x64.exe`，最低系统为 Windows 10 1809（17763）x64，也允许 Windows 11 x64。

## 构建输入

运行组件、安装器编译器、WebView2 离线安装组件及第三方许可和源码材料单独准备，不提交到源码仓库。

- `ProgramRoot`：已整理的程序目录，包含 `Workdesk.exe`、`src`、`runtime`、`workdesk-ui`。
- `StageRoot`：安装版 `release.json`、离线指南，以及 `第三方许可与源码`。
- `DependencyRoot`：WebView2 x64 离线安装器与 `ChineseSimplified.isl`。

使用 Inno Setup 7 编译：

```powershell
ISCC.exe /DReleaseBuild /DWin10Compatibility /DProgramRoot="C:\WorkdeskBuild\program" /DStageRoot="C:\WorkdeskBuild\stage" /DDependencyRoot="C:\WorkdeskBuild\dependencies" tools/windows-installer/Workdesk.iss
```

`build_win10.py` 是维护现有发行基线的配方：复用 `build/installer-release-20261123` 中的已审计运行组件，合入 `build/cooperation-repair-20261123` 的明确修复清单，再生成匹配指南与源码归档。它需要这些本地构建材料，不能替代首次准备组件。指南渲染额外需要 `markdown-it-py`。

```powershell
python tools/windows-installer/build_win10.py
```

构建先在独立输出目录完成，再移动完整 EXE 到 `分发`。程序目录、标记和源码归档同步更新，保留原有基线。

## 验证

`check_installation.py` 会在新目录安装并启动空资料库，再卸载和核对资料保留。它拒绝覆盖当前用户已登记的 Workdesk 安装及已有开始菜单组。运行前需准备该工具引用的首启验证材料；源码构建和这些完整安装验证是不同步骤。

```powershell
python tools/windows-installer/check_installation.py 分发/Workdesk-Setup-Win10-Win11-x64.exe build/installer-win10-20261123 --official-release --expected-version 1.0.1 --expected-installer Workdesk-Setup-Win10-Win11-x64.exe --expected-channel win10-validation
```

兼容范围与当前实测记录见 [安装指南](../../docs/INSTALL.md)。新包包含合作条件等待修复；已有 1.0.0 用户也可使用 [独立修复程序](../windows-repair/README.md)。

## v1.2 伙伴选装预览

在 Windows x64 上用开发组件库构建本地补充包，然后生成独立的选装窗口。目录与源码分开，包中仅含固定版本的组件文件、已登记运行依赖及校验清单。

```powershell
python tools/windows-installer/build_partner_preview_packs.py --components <组件库> --out <预览目录>/packages
./tools/windows-installer/partner-preview/build.ps1 -Catalog <预览目录>/packages/catalog.json
```

打开生成的 Workdesk-Setup-Preview.exe 可以选择八个伙伴，导入本地 .wdpartner 伙伴补充包 并核对下载量和新增占用。包体大小取压缩文件的实际字节数，安装后占用按全新安装计算，共享运行依赖按版本及摘要去重。本轮交互止于确认清单。正式在线下载、安装和日用资料升级属于发布安装器。预览用本地补充包保留在构建目录。

`.wdpartner` v1 以九字节 ASCII 标识 `WDPARTNER` 和版本字节 `0x01` 开头，其后为 gzip 压缩的 PAX tar 数据。包内根目录提供 `partner.json` 和 `component-files.json`，程序文件位于 `component/`，随附依赖位于 `dependencies/`。预览从内嵌目录读取认可的版本、字节数和整包 SHA-256，校验通过后将该伙伴计为本地来源。
