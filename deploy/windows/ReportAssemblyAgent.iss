#ifndef BundleDirectory
  #error BundleDirectory must point to a prepared native Windows bundle directory
#endif
#define ProductName "AI 报告整编工作台"
#define ProductVersion "1.0.0"

[Setup]
AppId={{D41D2A72-7E3E-44A9-AD77-E705E8CA21B4}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppPublisher=Report Assembly Agent
DefaultDirName={autopf}\ReportAssemblyAgent
DefaultGroupName={#ProductName}
UninstallDisplayName={#ProductName}
UninstallDisplayIcon={sys}\imageres.dll,3
OutputBaseFilename=ReportAssemblyAgent-Setup-0824-x64
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
MinVersion=10.0.10240
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UsePreviousGroup=no
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
SetupLogging=yes
Uninstallable=yes
CloseApplications=no
RestartApplications=no
ChangesEnvironment=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#BundleDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "Install.cmd"

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "http://127.0.0.1:8000"
Name: "{autodesktop}\{#ProductName}"; Filename: "http://127.0.0.1:8000"; Tasks: desktopicon

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File \"{app}\uninstall-native.ps1\" -InstallRoot \"{app}\" -DataRoot \"{commonappdata}\ReportAssemblyAgent\""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\profile"
Type: filesandordirs; Name: "{app}\app-source"
Type: filesandordirs; Name: "{app}\postgresql"
Type: filesandordirs; Name: "{app}\qdrant"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\payload"

[Run]
Filename: "http://127.0.0.1:8000"; Description: "打开 AI 报告整编工作台"; Flags: postinstall shellexec skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在初始化数据库、创建后台服务并进行健康检查，请稍候...';
    Params := '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\install-native.ps1') + '" -InstallRoot "' +
      ExpandConstant('{app}') + '" -DataRoot "' +
      ExpandConstant('{commonappdata}\ReportAssemblyAgent') + '" -InstallerManagedPayload';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      RaiseException('无法启动安装初始化脚本：' + SysErrorMessage(ResultCode));
    if ResultCode <> 0 then
      RaiseException('安装初始化失败。请检查 %ProgramData%\ReportAssemblyAgent\logs 和安装日志；程序文件已保留，用户数据未自动清除。');
  end;
end;
