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
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
PrivilegesRequired=admin
MinVersion=10.0.10240
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UsePreviousGroup=no
WizardStyle=modern
Compression=lzma2/fast
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
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall-native.ps1"" -InstallRoot ""{app}"" -DataRoot ""{commonappdata}\ReportAssemblyAgent"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopIRAComponents"

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
Filename: "http://127.0.0.1:8000"; Description: "打开 AI 报告整编工作台"; Flags: postinstall shellexec skipifsilent runasoriginaluser; Check: InitializationSucceeded

[Code]
var
  InitializationFailed: Boolean;
  InitializationError: String;

function InitializationSucceeded: Boolean;
begin
  Result := not InitializationFailed;
end;

procedure OnInitializationOutput(const S: String; const Error, FirstLine: Boolean);
var
  Step: Integer;
  Description: String;
begin
  Log(S);
  if Error then Exit;
  if Copy(S, 1, 9) = 'IRA_FILE|' then
  begin
    WizardForm.StatusLabel.Caption := '校验离线文件：' + Copy(S, 10, Length(S));
    Exit;
  end;
  if Copy(S, 1, 13) <> 'IRA_PROGRESS|' then Exit;
  Step := StrToIntDef(Copy(S, 14, Length(S)), -1);
  case Step of
    0: Description := '校验离线安装文件';
    1: Description := '准备私有运行目录';
    2: Description := '解压 PostgreSQL';
    3: Description := '解压 Qdrant';
    4: Description := '解压 FFmpeg';
    5: Description := '安装 Python 依赖';
    6: Description := '初始化数据库';
    7: Description := '注册后台服务';
    8: Description := '检查服务是否可用';
    9: Description := '安装完成';
  else
    Exit;
  end;
  WizardForm.StatusLabel.Caption := Description + '（阶段 ' + IntToStr(Step + 1) + '/10）';
  WizardForm.ProgressGauge.Position := (Step + 1) * 10;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '准备初始化应用，请稍候...';
    WizardForm.ProgressGauge.Max := 100;
    WizardForm.ProgressGauge.Position := 0;
    Params := '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\install-native.ps1') + '" -InstallRoot "' +
      ExpandConstant('{app}') + '" -DataRoot "' +
      ExpandConstant('{commonappdata}\ReportAssemblyAgent') + '" -InstallerManagedPayload';
    try
      if not ExecAndLogOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
        ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode,
        @OnInitializationOutput) then
      begin
        InitializationFailed := True;
        InitializationError := '无法启动初始化脚本：' + SysErrorMessage(ResultCode);
      end
      else if ResultCode <> 0 then
      begin
        InitializationFailed := True;
        InitializationError := '初始化脚本退出码：' + IntToStr(ResultCode);
      end;
    except
      InitializationFailed := True;
      InitializationError := '初始化过程异常：' + GetExceptionMessage;
    end;
    if InitializationFailed then
      Log('Initialization failed: ' + InitializationError);
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and InitializationFailed then
  begin
    WizardForm.FinishedHeadingLabel.Caption := '安装未完成';
    WizardForm.FinishedLabel.Caption := InitializationError + #13#10 + #13#10 +
      '请查看 ' + ExpandConstant('{app}\install-native.log') +
      '。程序文件已保留，服务尚未确认可用。';
    WizardForm.RunList.Visible := False;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if InitializationFailed then Result := 4 else Result := 0;
end;
