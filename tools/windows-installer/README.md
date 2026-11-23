# Windows 安装包构建

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
