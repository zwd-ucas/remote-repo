; Inno Setup script for Story Dubbing (Windows). Packages the payload (runtime/ + node/)
; into a per-user installer (no admin needed); the program dir is read-only and all
; user data + downloaded models go to %USERPROFILE%\StoryDubbing.
;
; Driven by env vars set in CI: APP_VERSION, APP_VARIANT (cpu|cuda), PAYLOAD_DIR.

#define MyAppName "Story Dubbing Workbench"
#define MyAppVersion GetEnv("APP_VERSION")
#define Variant GetEnv("APP_VARIANT")
#define PayloadDir GetEnv("PAYLOAD_DIR")
#define Launcher "runtime\pythonw.exe"
#define LaunchArgs "-m videotrans.story_pipeline.desktop"

[Setup]
AppId={{8F3C2A41-7C1E-4E9A-9B6E-STORYDUBBING}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Story Dubbing
DefaultDirName={localappdata}\Programs\StoryDubbing
DefaultGroupName=Story Dubbing
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=StoryDubbing-{#MyAppVersion}-win-{#Variant}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName} ({#Variant})

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Story Dubbing"; Filename: "{app}\{#Launcher}"; Parameters: "{#LaunchArgs}"; WorkingDir: "{app}"
Name: "{group}\卸载 Story Dubbing"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Story Dubbing"; Filename: "{app}\{#Launcher}"; Parameters: "{#LaunchArgs}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#Launcher}"; Parameters: "{#LaunchArgs}"; WorkingDir: "{app}"; Description: "立即启动 Story Dubbing"; Flags: nowait postinstall skipifsilent
