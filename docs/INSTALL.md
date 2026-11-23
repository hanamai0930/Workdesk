# 安装 Workdesk

适用于 **Workdesk 1.0.0 · Windows 11 x64**。安装文件为 `Workdesk-Setup-x64.exe`，从 [v1.0.0 发行页](https://github.com/hanamai0930/Workdesk/releases/tag/v1.0.0)获取。

## 安装与首次启动

1. 下载并运行 **Workdesk-Setup-x64.exe**，选择程序位置。默认安装到 `%LOCALAPPDATA%\Programs\Workdesk`，供当前 Windows 用户使用。
2. 点击 **安装**。系统缺少 WebView2 Runtime 时，安装程序会调用随附的微软离线安装组件。
3. 安装结束后，从开始菜单打开 **Workdesk**，按提示连接自己的模型账号或 API Key，再进入工作区。
4. 从开始菜单打开 **Workdesk 使用指南**，可离线查看后续操作。

**首次运行提示。** 此版本的安装程序和 Workdesk 启动器未做数字签名。若 Windows SmartScreen 显示拦截，请先核对文件来源与下文的 SHA-256。确认来自本仓库发行页后，可按 Windows 提供的选项继续。受管理的电脑如禁止运行，请联系管理员。

如果安装程序提示缺少运行库，请安装微软最新的 [Visual C++ v14 x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)，然后重新运行安装程序。运行库的系统安装可能需要管理员权限。[微软下载说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

## 核对安装文件

`v1.0.0` 安装文件大小为 **1,864,826,462 字节**。SHA-256 如下，与发行页一致。

```text
362ec811a0c3b742d08517d07782fbb95a31b2ece34ef18d08099011ce4bd903
```

在下载目录打开 PowerShell，运行以下命令，并比较返回的 Hash。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\Workdesk-Setup-x64.exe'
```

## 资料、更新与卸载

安装版的用户资料默认位于 `%USERPROFILE%\.workdesk-release`，实际资料库位置可在设置中查看。项目原件保存在你选择的项目文件夹。

更新或卸载前，先完成或停止在途任务，处理待核对状态并创建备份。关闭窗口后重启 Windows，让后台服务停止。

- **更新**时运行新版安装程序，选择原安装位置，再启动核对资料与连接。
- **卸载**时打开 **Windows 设置 → 应用 → 已安装的应用**，找到 **Workdesk 1.0**，选择 **卸载**。程序和快捷方式会移除，用户资料和项目原件保留。

安装目录中的 **第三方许可与源码** 提供 Workdesk 及随包组件的许可、源码和构建材料。Workdesk 自有源码采用 [MIT 许可](../LICENSE)，第三方组件适用各自条款，见[第三方声明](../THIRD_PARTY_NOTICES.md)。

## 常见问题

### 提示未知发布者或 Windows 已保护你的电脑

当前版本未签名。请先核对来源与本页 SHA-256，再按 Windows 提供的选项继续。组织设备的运行权限由管理员管理。

### 安装提示缺少 Visual C++ 运行库

安装微软的 **Visual C++ v14 x64** 版本后重试。安装程序检查的是 x64 组件，最低版本为 14.44.35208。下载入口见本页的安装说明。

### 桌面窗口打不开

保留启动弹窗中的错误信息。若提示缺少 WebView2，可从 [Microsoft 官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download-section)取得 Evergreen Standalone Installer x64 并安装。启动诊断及其他错误处理见[用户指南的常见问题](USER_GUIDE.md#chapter-13)。

### 关闭窗口后任务为什么还在运行

关闭窗口会关闭界面，后台服务继续运行。完全退出前先处理在途工作，再关闭窗口并重启 Windows。

### 账号、Key 和用量怎样处理

Workdesk 使用你连接的账号或 API Key，请求由选中的模型服务或工作端处理。计费按对应提供方的规则结算。连接、数据位置和密钥保护见[用户指南](USER_GUIDE.md)。

仍有问题时，请通过 [GitHub Issues](https://github.com/hanamai0930/Workdesk/issues/new/choose)提供 Workdesk 与 Windows 版本、复现步骤及报错。提交前移除密钥、账号信息与私有项目内容。
