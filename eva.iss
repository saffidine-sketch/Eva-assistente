; Installer di Eva (Inno Setup 6). Si costruisce con crea_installer.bat
#define NomeApp "Eva"
#define Versione "2.2"

[Setup]
AppId={{8F3A6C52-1B7D-4E39-A5C4-2D9E7B10F6A1}
AppName={#NomeApp}
AppVersion={#Versione}
AppPublisher=Eva
DefaultDirName={localappdata}\Programs\{#NomeApp}
DefaultGroupName={#NomeApp}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=Eva_Setup_{#Versione}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Eva.exe
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
LicenseFile=LICENSE

[Languages]
Name: "italiano"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul Desktop"; GroupDescription: "Icone:"; Flags: unchecked
Name: "avvioauto"; Description: "Avvia Eva ogni volta che accendo il computer"; GroupDescription: "Avvio:"; Flags: unchecked

[Files]
Source: "build_installer\dist\Eva\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "eva_avvio_automatico.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "guardia_eva.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEGGIMI.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "aiuto_eva.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Eva"; Filename: "{app}\Eva.exe"
Name: "{autoprograms}\Eva - cambia nome e chiave"; Filename: "{app}\Eva.exe"; Parameters: "--impostazioni"
Name: "{autoprograms}\Eva - guida"; Filename: "{app}\aiuto_eva.txt"
Name: "{autodesktop}\Eva"; Filename: "{app}\Eva.exe"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\eva_avvio_automatico.ps1"" attiva"; Flags: runhidden; Tasks: avvioauto
Filename: "{app}\Eva.exe"; Description: "Avvia Eva adesso"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\eva_avvio_automatico.ps1"" disattiva"; Flags: runhidden; RunOnceId: "EvaAvvioAutoOff"

; Impostazioni, rubrica, registri e promemoria stanno in %APPDATA%\Eva: la disinstallazione NON li cancella.
