; Workdesk 1.2 per-user installer. Build with build_12.py.
#define ProductId "Workdesk-1.2-Windows-x64"
#define OfflineWebView "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

[Setup]
AppId={#ProductId}
AppName=Workdesk 1.2
AppVersion=1.2.1
AppVerName=Workdesk 1.2.1
AppPublisher=Workdesk
DefaultDirName={localappdata}\Programs\Workdesk-1.2
DefaultGroupName=Workdesk 1.2
UninstallDisplayName=Workdesk 1.2.1
UninstallDisplayIcon={app}\Workdesk.exe
VersionInfoVersion=1.2.1.0
VersionInfoDescription=Workdesk 1.2.1 Windows x64 Installer
VersionInfoProductName=Workdesk
OutputDir={#OutputRoot}
OutputBaseFilename=Workdesk-Setup-1.2.1-x64
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "zhcn"; MessagesFile: "{#DependencyRoot}\ChineseSimplified.isl"

[Tasks]
Name: "import101"; Description: "从已停止的 Workdesk 1.0.1 安装版复制资料"; GroupDescription: "升级资料"; Flags: unchecked; Check: HasOldData
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式"; Flags: unchecked

[Files]
Source: "{#ProgramRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ToolsRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "{#NoticesRoot}\*"; DestDir: "{app}\第三方许可与源码"; Flags: ignoreversion recursesubdirs
Source: "{#DependencyRoot}\{#OfflineWebView}"; Flags: dontcopy nocompression

[Icons]
Name: "{group}\Workdesk 1.2"; Filename: "{app}\Workdesk.exe"; WorkingDir: "{app}"
Name: "{group}\伙伴管理"; Filename: "{app}\Workdesk-Partners.exe"; WorkingDir: "{app}"
Name: "{group}\停止 Workdesk 1.2"; Filename: "{app}\Workdesk.exe"; Parameters: "--stop"; WorkingDir: "{app}"
Name: "{group}\导入 1.0.1 资料"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-I -B -X utf8 ""{app}\tools\release_upgrade.py"" import --notify"; WorkingDir: "{app}"; Check: HasOldData
Name: "{userdesktop}\Workdesk 1.2"; Filename: "{app}\Workdesk.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Workdesk-Partners.exe"; Parameters: "--package-dir ""{src}\partners"""; WorkingDir: "{app}"; Description: "选择要安装的工作伙伴"; Flags: postinstall skipifsilent; Check: ImportSucceeded
Filename: "{app}\Workdesk.exe"; WorkingDir: "{app}"; Description: "打开 Workdesk 1.2"; Flags: postinstall nowait skipifsilent; Check: ImportSucceeded

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#ProductId}_is1';
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
var
  ImportFailed: Boolean;

function HasOldData: Boolean;
var Root: String;
begin
  Root := GetEnv('WORKDESK_RELEASE_DATA_HOME');
  if Root = '' then Root := AddBackslash(GetEnv('USERPROFILE')) + '.workdesk-release';
  Root := AddBackslash(Root);
  Result := FileExists(Root + 'config\locations.json') or FileExists(Root + 'libraries\main\tasks.sqlite3');
end;

function ImportSucceeded: Boolean;
begin
  Result := not ImportFailed;
end;

function IsEmptyDirectory(const Directory: String): Boolean;
var Entry: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Directory) + '*', Entry) then
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin Result := False; Exit; end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
end;

function Attributes(Name: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function HasReparseParent(Directory: String): Boolean;
var Parent: String; Attr: Cardinal;
begin
  Result := True;
  repeat
    Attr := Attributes(Directory);
    if (Attr <> $FFFFFFFF) and ((Attr and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Directory);
    if Parent = Directory then Break;
    Directory := Parent;
  until Directory = '';
  Result := False;
end;

function Within(const Candidate, Root: String): Boolean;
begin
  Result := Pos(UpperCase(AddBackslash(Root)), UpperCase(AddBackslash(Candidate))) = 1;
end;

function DestinationAllowed(Directory: String): Boolean;
var Previous, Home: String;
begin
  Result := False;
  Directory := RemoveBackslashUnlessRoot(ExpandFileName(Directory));
  Home := AddBackslash(GetEnv('USERPROFILE'));
  if (Length(Directory) <= 3) or (Pos('\\', Directory) = 1) or HasReparseParent(Directory) then Exit;
  if Within(Directory, Home + '.workdesk') or Within(Directory, Home + '.workdesk-release') or
     Within(Directory, ExpandConstant('{localappdata}\Workdesk-v1.2')) or
     Within(Directory, 'C:\Workdesk') or Within(Directory, 'C:\Workdesk-dev\repo') or
     Within(Directory, 'C:\Workdesk-dev\user') then Exit;
  if not DirExists(Directory) then begin Result := not FileExists(Directory); Exit; end;
  if IsEmptyDirectory(Directory) then begin Result := True; Exit; end;
  Result := RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', Previous) and
    (CompareText(AddBackslash(Directory),AddBackslash(Previous)) = 0) and
    FileExists(AddBackslash(Directory) + 'Workdesk.exe') and FileExists(AddBackslash(Directory) + 'release.json');
end;

function AppStopped(const Folder: String): Boolean;
var Code: Integer; Previous, Exe: String;
begin
  Result := True;
  if not RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', Previous) then Exit;
  Result := CompareText(AddBackslash(Folder),AddBackslash(Previous)) = 0;
  if not Result then Exit;
  Exe := AddBackslash(Folder) + 'Workdesk.exe';
  Result := FileExists(Exe) and Exec(Exe, '--check-stopped', Folder, SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function WebViewReady: Boolean;
var V: String;
begin
  Result := (RegQueryStringValue(HKLM32,WebViewKey,'pv',V) or
             RegQueryStringValue(HKLM64,WebViewKey,'pv',V) or
             RegQueryStringValue(HKCU32,WebViewKey,'pv',V) or
             RegQueryStringValue(HKCU64,WebViewKey,'pv',V)) and (Trim(V) <> '') and (V <> '0.0.0.0');
end;

function NextButtonClick(PageID: Integer): Boolean;
begin
  Result := True;
  if (PageID = wpSelectDir) and not DestinationAllowed(WizardDirValue) then begin
    MsgBox('请选择空目录，或已有 Workdesk 1.2 的安装目录。',mbError,MB_OK);
    Result := False;
  end;
end;

function HasVisualCppFile(const Name: String): Boolean;
var HighVersion, LowVersion: Cardinal;
begin
  Result := GetVersionNumbers(ExpandConstant('{sys}\' + Name), HighVersion, LowVersion);
  if Result then Result := (HighVersion > ((14 shl 16) + 44)) or
    ((HighVersion = ((14 shl 16) + 44)) and ((LowVersion shr 16) >= 35208));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Folder: String;
begin
  Result := '';
  Folder := ExpandConstant('{app}');
  if not DestinationAllowed(Folder) then begin Result := '安装目标目录不适用。'; Exit; end;
  if not AppStopped(Folder) then begin Result := '已有 Workdesk 1.2 安装。请在原位置更新，并先从开始菜单安全停止该实例。'; Exit; end;
  if not (HasVisualCppFile('msvcp140.dll') and HasVisualCppFile('vcruntime140.dll') and
          HasVisualCppFile('vcruntime140_1.dll')) then begin
    Result := '请先安装微软 Visual C++ v14 x64 运行库，再运行此安装程序。' + #13#10 +
      'https://aka.ms/vc14/vc_redist.x64.exe';
    Exit;
  end;
  if WebViewReady then Exit;
  ExtractTemporaryFile('{#OfflineWebView}');
  if not Exec(ExpandConstant('{tmp}\{#OfflineWebView}'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) or not WebViewReady then
  begin
    NeedsRestart := (Code = 3010) or (Code = 1641);
    Result := 'WebView2 安装未完成，错误码 ' + IntToStr(Code) + '。';
  end;
end;

function GetNotifyArgument: String;
begin
  if WizardSilent then Result := '' else Result := ' --notify';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('import101') then
  begin
    ImportFailed := not Exec(ExpandConstant('{app}\runtime\python\python.exe'),
      '-I -B -X utf8 "' + ExpandConstant('{app}\tools\release_upgrade.py') + '" import' +
      GetNotifyArgument, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0);
    if ImportFailed then Log('Workdesk 1.0.1 import failed; the original data is retained.');
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if ImportFailed then Result := 4 else Result := 0;
end;

function InitializeUninstall: Boolean;
begin
  Result := AppStopped(ExpandConstant('{app}'));
  if not Result then SuppressibleMsgBox('请先通过开始菜单停止 Workdesk 1.2，再卸载。', mbError, MB_OK, IDOK);
end;
