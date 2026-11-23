# 安装 Workdesk

本页对应 **Workdesk 1.0.1（Windows 10/11 兼容版）**。完整安装包已包含合作条件等待修复。

## 下载安装包

新版安装包改用百度网盘分发，分享链接和提取码将在 [Release](https://github.com/hanamai0930/Workdesk/releases) 更新时公布。当前 GitHub 附件仍为历史 v1.0.0，本轮不新增 EXE 附件。

| 文件 | 适用情况 |
| --- | --- |
| `Workdesk-Setup-Win10-Win11-x64.exe` | 首次安装，或将现有安装版更新到 1.0.1；同一文件兼容 Win10/Win11 x64 |
| `Workdesk-Repair-x64.exe` | 已安装 1.0.0，仅修复合作任务等待条件后无法继续的问题；保留原版本标识与资料 |

完整 1.0.1 安装包已包含独立修复内容，无须再运行修复程序。

[历史 v1.0.0 发行页](https://github.com/hanamai0930/Workdesk/releases/tag/v1.0.0)保留原 `Workdesk-Setup-x64.exe`，该旧安装器只允许 Win11 x64；Win10 用户应使用新的通用兼容包。

## 兼容范围

| 系统 | 1.0.1 兼容目标 |
| --- | --- |
| Windows 11 | 各版本 x64 |
| Windows 10 | 1809（build 17763）及以后 x64，含对应 LTSC 2019、LTSC 2021 |
| 排除 | Win10 1507、1511、1607、1703、1709、1803及对应早期 LTSB；32 位、ARM；Windows 8.1 及更早系统 |

随包官方工作端使用的伪控制台 API 从 Win10 1809 起提供，因此安装下限为 17763。[微软的系统要求](https://learn.microsoft.com/en-us/windows/console/resizepseudoconsole)

已在 Win11 本机完成安装、首启、卸载与资料保留检查；Win10 实机验收待确认，旧 Win10 和 LTSC 暂列兼容目标。

## 安装与首次启动

1. 下载并运行 **Workdesk-Setup-Win10-Win11-x64.exe**，选择程序位置。默认安装到 `%LOCALAPPDATA%\Programs\Workdesk`，供当前 Windows 用户使用。
2. 点击 **安装**。系统缺少 WebView2 Runtime 时，安装程序会调用随附的微软离线安装组件。
3. 安装结束后，从开始菜单打开 **Workdesk**，连接自己的模型账号或 API Key，再进入工作区。
4. 从开始菜单打开 **Workdesk 使用指南**，可离线查看后续操作。

当前安装版要求 **Visual C++ v14 x64 ≥ 14.44.35208.0**，建议安装微软提供的[最新版本](https://aka.ms/vc14/vc_redist.x64.exe)。安装程序提示缺少组件时，安装后重新运行 Workdesk 安装包。运行库的系统安装可能需要管理员权限。[微软下载说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

**首次运行提示。** 安装程序、修复程序和 Workdesk 启动器未做数字签名。若 Windows SmartScreen 显示拦截，请先核对来源和下文的 SHA-256，再按 Windows 提供的选项继续。受管理电脑的运行权限由管理员管理。

## 核对安装文件

### 1.0.1 通用兼容安装包

文件名：`Workdesk-Setup-Win10-Win11-x64.exe`。大小：**1,865,249,905 字节**。

```text
096db6f9cfd45df211d41af94e8f1606fd175a383890df856e347abdf8ca5835
```

在下载目录运行以下命令，并比较返回的 Hash：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\Workdesk-Setup-Win10-Win11-x64.exe'
```

### 1.0.0 独立修复程序

文件名：`Workdesk-Repair-x64.exe`。大小：**549,888 字节**。

```text
920dea47c91d1fa590af9917a29197f8a40f1dcf37a46691ca17e36f57b4f170
```

## 更新、修复与卸载

安装版的用户资料默认位于 `%USERPROFILE%\.workdesk-release`，实际资料库位置可在设置中查看。项目原件保存在你选择的项目文件夹。

更新或卸载前，先完成或停止在途任务，处理待核对状态并创建备份。关闭窗口后重启 Windows，让后台服务停止。

- **更新到 1.0.1**：运行完整兼容安装包，选择原安装位置，再启动核对资料与连接。
- **仅修复 1.0.0**：运行独立修复程序，核对安装位置，点击 **开始修复**。若仍提示占用，重启 Windows 后先运行修复程序，再打开 Workdesk。
- **卸载**：打开 **Windows 设置 → 应用**，找到 **Workdesk** 并选择卸载。程序和快捷方式会移除，用户资料和项目原件保留。

独立修复程序会备份原文件，更新失败时恢复；重复运行会识别已有修复。它只修改明确列出的程序文件，不重发任务，不延长派发授权。

安装目录中的 **第三方许可与源码** 提供 Workdesk 及随包组件的许可、源码和构建材料。Workdesk 自有源码采用 [MIT 许可](../LICENSE)，第三方组件适用各自条款，见[第三方声明](../THIRD_PARTY_NOTICES.md)。

## 常见问题

### 提示不支持当前 Windows 版本

旧 v1.0.0 安装器只允许 Win11。Win10 请使用 **Workdesk-Setup-Win10-Win11-x64.exe**，并核对系统为 Win10 1809及以上 x64。完整范围见上文。

### 合作任务显示等待补充条件

在原合作聊天点击 **处理补充条件**，逐项填写资源、环境、人工配合或后续阶段核对说明，点击 **保存处理说明**。处理全部条件后，选择 **按补充说明重新规划并继续**。需要工具时仍可使用 Skill/MCP 预设。

重新规划使用 1 次准备回合，保留原任务、成员、权限、累计上限与截止；补充说明会交给负责人核对，实际缺项仍须在相应阶段处理。操作细节见[用户指南](USER_GUIDE.md#chapter-6)。

### 桌面窗口打不开

保留启动弹窗中的错误信息。若提示缺少 WebView2，可从 [Microsoft 官方页面](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download-section)取得 Evergreen Standalone Installer x64 并安装。诊断路径见[用户指南](USER_GUIDE.md#chapter-13)。

### 关闭窗口后任务为什么还在运行

关闭窗口会关闭界面，后台服务继续运行。完全退出前先处理在途工作，再关闭窗口并重启 Windows。

### 账号、Key 和用量怎样处理

Workdesk 使用你连接的账号或 API Key，请求由选中的模型服务或官方工作端处理，计费按对应提供方的规则结算。连接、数据位置和密钥保护见[用户指南](USER_GUIDE.md)。

仍有问题时，请通过 [GitHub Issues](https://github.com/hanamai0930/Workdesk/issues/new/choose)提供 Workdesk 与 Windows 版本、复现步骤及报错。提交前移除密钥、账号信息与私有项目内容。
