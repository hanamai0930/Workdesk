; Build inputs are supplied by ISCC as /DProgramRoot, /DStageRoot and /DDependencyRoot.
#ifdef ReleaseBuild
#ifdef Win10Compatibility
#define AppTitle "Workdesk 1.0.1（Windows 10/11 兼容版）"
#define InstallerName "Workdesk-Setup-Win10-Win11-x64"
#define InstallerDescription "Workdesk 1.0.1 x64 Windows 10/11 Compatibility Installer"
#define AppVersionValue "1.0.1"
#define FileVersionValue "1.0.1.0"
#define MinimumWindowsVersion "10.0.17763"
#else
#define AppTitle "Workdesk 1.0"
#define InstallerName "Workdesk-Setup-x64"
#define InstallerDescription "Workdesk 1.0 x64 Installer"
#define AppVersionValue "1.0.0"
#define FileVersionValue "1.0.0.0"
#define MinimumWindowsVersion "10.0.22000"
#endif
#define AppIdValue "Workdesk-Windows-x64"
#else
#ifdef Win10Compatibility
#error Win10Compatibility requires ReleaseBuild.
#endif
#define AppTitle "Workdesk 1.0（安装测试版）"
#define AppIdValue "Workdesk-1_0-Installer-Test"
#define InstallerName "Workdesk-Setup-x64-Test"
#define InstallerDescription "Workdesk 1.0 x64 Installer Test"
#define AppVersionValue "1.0.0"
#define FileVersionValue "1.0.0.0"
#define MinimumWindowsVersion "10.0.22000"
#endif
#define WebViewInstaller "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

[Setup]
AppId={#AppIdValue}
AppName=Workdesk
AppVersion={#AppVersionValue}
AppVerName={#AppTitle}
AppPublisher=Workdesk
DefaultDirName={localappdata}\Programs\Workdesk
DefaultGroupName=Workdesk
PrivilegesRequired=lowest
MinVersion={#MinimumWindowsVersion}
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputBaseFilename={#InstallerName}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
DisableDirPage=no
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayName={#AppTitle}
UninstallDisplayIcon={app}\Workdesk.exe
VersionInfoDescription={#InstallerDescription}
VersionInfoProductName=Workdesk
VersionInfoVersion={#FileVersionValue}
SetupLogging=yes

[Languages]
Name: "zhcn"; MessagesFile: "{#DependencyRoot}\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
zhcn.AddDesktopIcon=创建桌面快捷方式
en.AddDesktopIcon=Create a desktop shortcut
zhcn.OpenWorkdesk=打开 Workdesk
en.OpenWorkdesk=Open Workdesk
zhcn.OpenGuide=打开 Workdesk 使用指南
en.OpenGuide=Open the Workdesk guide
zhcn.GuideShortcut=Workdesk 使用指南
en.GuideShortcut=Workdesk User Guide
zhcn.OccupiedPath=请选择一个空的程序文件夹，或选择此安装程序上次使用的位置。
en.OccupiedPath=Choose an empty program folder or the location used by this installer previously.
zhcn.BusyApp=Workdesk 后台仍在运行，或程序文件暂时无法更换。请先处理在途工作，重启 Windows，然后重新运行安装或卸载程序。
en.BusyApp=Workdesk is still running or its files are locked. Finish any active work, restart Windows, and run setup or uninstall again.
zhcn.RuntimeFailed=安装 WebView2 Runtime 失败。请记录错误代码，再重试 Workdesk 安装。
en.RuntimeFailed=WebView2 Runtime setup failed. Keep the error code and try Workdesk setup again.
#ifdef ReleaseBuild
zhcn.VisualCppMissing=请从微软安装最新的 Visual C++ v14 x64 运行库，然后重新运行 Workdesk 安装程序。%n%nhttps://aka.ms/vc14/vc_redist.x64.exe
en.VisualCppMissing=Install the latest Microsoft Visual C++ v14 x64 Redistributable, then run Workdesk setup again.%n%nhttps://aka.ms/vc14/vc_redist.x64.exe
zhcn.VisualCppOpen=现在打开微软下载说明吗？
en.VisualCppOpen=Open the Microsoft download instructions now?
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:AddDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#DependencyRoot}\{#WebViewInstaller}"; Flags: dontcopy nocompression
Source: "{#ProgramRoot}\Workdesk.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\release.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProgramRoot}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs
Source: "{#ProgramRoot}\src\*"; DestDir: "{app}\src"; Flags: ignoreversion recursesubdirs
Source: "{#ProgramRoot}\workdesk-ui\*"; DestDir: "{app}\workdesk-ui"; Flags: ignoreversion recursesubdirs
Source: "{#StageRoot}\指南\*"; DestDir: "{app}\指南"; Flags: ignoreversion recursesubdirs
#ifdef ReleaseBuild
Source: "{#StageRoot}\第三方许可与源码\*"; DestDir: "{app}\第三方许可与源码"; Flags: ignoreversion recursesubdirs
#endif

[Icons]
Name: "{group}\Workdesk"; Filename: "{app}\Workdesk.exe"; WorkingDir: "{app}"
Name: "{group}\{cm:GuideShortcut}"; Filename: "{app}\指南\Workdesk使用指南.html"
Name: "{userdesktop}\Workdesk"; Filename: "{app}\Workdesk.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Workdesk.exe"; Description: "{cm:OpenWorkdesk}"; Flags: postinstall nowait skipifsilent unchecked
Filename: "{app}\指南\Workdesk使用指南.html"; Description: "{cm:OpenGuide}"; Flags: postinstall shellexec nowait skipifsilent unchecked

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdValue}_is1';

function RuntimeInRegistry(Root: Integer): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, WebViewKey, 'pv', Version) and
    (Trim(Version) <> '') and (Version <> '0.0.0.0');
end;

function WebViewReady: Boolean;
begin
  Result := RuntimeInRegistry(HKLM32) or RuntimeInRegistry(HKCU64) or RuntimeInRegistry(HKCU32);
end;

function DirectoryIsEmpty(const Directory: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then
      begin
        Result := False;
        Exit;
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
end;

function DestinationAllowed(const Directory: String): Boolean;
var
  Previous, Chosen, DataRoot: String;
begin
  Chosen := AddBackslash(Directory);
  DataRoot := AddBackslash(GetEnv('USERPROFILE') + '\.workdesk-release');
  if CompareText(Copy(Chosen, 1, Length(DataRoot)), DataRoot) = 0 then
  begin
    Result := False;
    Exit;
  end;
  Result := not DirExists(Directory) or DirectoryIsEmpty(Directory);
  if not Result then
    Result := RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Previous) and
      (CompareText(AddBackslash(Previous), Chosen) = 0) and
      FileExists(Chosen + 'release.json');
end;

function ProgramFileUnlocked(const FileName: String): Boolean;
var
  FileStream: TFileStream;
begin
  Result := True;
  if not FileExists(FileName) then
    Exit;
  try
    FileStream := TFileStream.Create(FileName, fmOpenReadWrite or fmShareExclusive);
    FileStream.Free;
  except
    Result := False;
  end;
end;

function ProgramFilesUnlocked: Boolean;
begin
  Result := ProgramFileUnlocked(ExpandConstant('{app}\runtime\python\pythonw.exe')) and
    ProgramFileUnlocked(ExpandConstant('{app}\runtime\python\python.exe'));
end;

#ifdef ReleaseBuild
function HasVisualCppFile(const Name: String): Boolean;
var
  HighVersion, LowVersion: Cardinal;
begin
  Result := GetVersionNumbers(ExpandConstant('{sys}\' + Name), HighVersion, LowVersion);
  if Result then
    Result := (HighVersion > ((14 shl 16) + 44)) or
      ((HighVersion = ((14 shl 16) + 44)) and ((LowVersion shr 16) >= 35208));
end;

function VisualCppReady: Boolean;
begin
  Result := HasVisualCppFile('msvcp140.dll') and HasVisualCppFile('vcruntime140.dll') and
    HasVisualCppFile('vcruntime140_1.dll');
end;
#endif

function NextButtonClick(PageID: Integer): Boolean;
begin
  Result := True;
  if (PageID = wpSelectDir) and not WizardSilent then
  begin
    Result := DestinationAllowed(WizardDirValue);
    if not Result then
      MsgBox(CustomMessage('OccupiedPath'), mbError, MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if not DestinationAllowed(ExpandConstant('{app}')) then
  begin
    Result := CustomMessage('OccupiedPath');
    Exit;
  end;
  if not ProgramFilesUnlocked then
  begin
    Result := CustomMessage('BusyApp');
    Exit;
  end;
#ifdef ReleaseBuild
  if not VisualCppReady then
  begin
    Result := CustomMessage('VisualCppMissing');
    if not WizardSilent then
      if MsgBox(Result + #13#10#13#10 + CustomMessage('VisualCppOpen'), mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('open', 'https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist',
          '', '', SW_SHOWNORMAL, ewNoWait, ExitCode);
    Exit;
  end;
  Log('Microsoft Visual C++ v14 x64 Redistributable is present.');
#endif
  if WebViewReady then
  begin
    Log('Microsoft Edge WebView2 Runtime is present.');
    Exit;
  end;
  try
    ExtractTemporaryFile('{#WebViewInstaller}');
    if not Exec(ExpandConstant('{tmp}\{#WebViewInstaller}'), '/silent /install',
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := CustomMessage('RuntimeFailed') + ' (' + IntToStr(ExitCode) + ')'
    else if not WebViewReady then
    begin
      NeedsRestart := (ExitCode = 3010) or (ExitCode = 1641);
      Result := CustomMessage('RuntimeFailed') + ' (' + IntToStr(ExitCode) + ')';
    end;
  except
    Result := CustomMessage('RuntimeFailed') + ' ' + GetExceptionMessage;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := ProgramFilesUnlocked;
  if not Result then
    SuppressibleMsgBox(CustomMessage('BusyApp'), mbError, MB_OK, IDOK);
end;
