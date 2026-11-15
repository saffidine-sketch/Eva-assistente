; Installer di Eva (Inno Setup 6). Si costruisce con crea_installer.bat
#define NomeApp "Eva"
#define Versione "2.3"

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
; All'avvio dell'installer compare la scelta della lingua (anche quando si aggiorna): la lingua scelta diventa la lingua di Eva
ShowLanguageDialog=yes
UsePreviousLanguage=no

[Languages]
Name: "italiano"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "francese"; MessagesFile: "compiler:Languages\French.isl"
Name: "spagnolo"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "inglese"; MessagesFile: "compiler:Default.isl"
Name: "portoghese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[CustomMessages]
; CodiceLinguaEva = lingua scritta in impostazioni_eva.json (it, fr, es, en)
italiano.CodiceLinguaEva=it
francese.CodiceLinguaEva=fr
spagnolo.CodiceLinguaEva=es
inglese.CodiceLinguaEva=en
portoghese.CodiceLinguaEva=pt
italiano.IconaDesktop=Crea un'icona sul Desktop
francese.IconaDesktop=Créer une icône sur le Bureau
spagnolo.IconaDesktop=Crear un icono en el Escritorio
inglese.IconaDesktop=Create a Desktop icon
portoghese.IconaDesktop=Criar um ícone na Área de trabalho
italiano.GruppoIcone=Icone:
francese.GruppoIcone=Icônes :
spagnolo.GruppoIcone=Iconos:
inglese.GruppoIcone=Icons:
portoghese.GruppoIcone=Ícones:
italiano.AvvioAuto=Avvia Eva ogni volta che accendo il computer
francese.AvvioAuto=Lancer Eva à chaque démarrage de l'ordinateur
spagnolo.AvvioAuto=Iniciar Eva cada vez que enciendo el ordenador
inglese.AvvioAuto=Start Eva every time I turn on the computer
portoghese.AvvioAuto=Iniciar a Eva toda vez que eu ligar o computador
italiano.GruppoAvvio=Avvio:
francese.GruppoAvvio=Démarrage :
spagnolo.GruppoAvvio=Inicio:
inglese.GruppoAvvio=Startup:
portoghese.GruppoAvvio=Inicialização:
italiano.AvviaOra=Avvia Eva adesso
francese.AvviaOra=Lancer Eva maintenant
spagnolo.AvviaOra=Iniciar Eva ahora
inglese.AvviaOra=Start Eva now
portoghese.AvviaOra=Iniciar a Eva agora
italiano.CambiaNomeChiave=Eva - cambia nome e chiave
francese.CambiaNomeChiave=Eva - changer le nom et la clé
spagnolo.CambiaNomeChiave=Eva - cambiar nombre y clave
inglese.CambiaNomeChiave=Eva - change name and key
portoghese.CambiaNomeChiave=Eva - mudar nome e chave
italiano.Guida=Eva - guida
francese.Guida=Eva - guide
spagnolo.Guida=Eva - guía
inglese.Guida=Eva - guide
portoghese.Guida=Eva - guia

[Tasks]
Name: "desktopicon"; Description: "{cm:IconaDesktop}"; GroupDescription: "{cm:GruppoIcone}"; Flags: unchecked
Name: "avvioauto"; Description: "{cm:AvvioAuto}"; GroupDescription: "{cm:GruppoAvvio}"; Flags: unchecked

[Files]
Source: "build_installer\dist\Eva\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "eva_avvio_automatico.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "guardia_eva.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEGGIMI.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "aiuto_eva.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Eva"; Filename: "{app}\Eva.exe"
Name: "{autoprograms}\{cm:CambiaNomeChiave}"; Filename: "{app}\Eva.exe"; Parameters: "--impostazioni"
Name: "{autoprograms}\{cm:Guida}"; Filename: "{app}\aiuto_eva.txt"
Name: "{autodesktop}\Eva"; Filename: "{app}\Eva.exe"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\eva_avvio_automatico.ps1"" attiva"; Flags: runhidden; Tasks: avvioauto
Filename: "{app}\Eva.exe"; Description: "{cm:AvviaOra}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\eva_avvio_automatico.ps1"" disattiva"; Flags: runhidden; RunOnceId: "EvaAvvioAutoOff"

; Impostazioni, rubrica, registri e promemoria stanno in %APPDATA%\Eva: la disinstallazione NON li cancella.

[Code]
{ La lingua scelta nell'installer viene scritta in %APPDATA%\Eva\impostazioni_eva.json (chiave "lingua"). Al primo avvio
  Eva la legge (vox_lingue.lingua_salvata) e saluta già in quella lingua. Le altre chiavi del file (nome, parole extra...)
  restano com'erano: si cambia solo "lingua". Il file si tratta come byte, così gli accenti non si rovinano. }
procedure SalvaLinguaEva;
var
  Percorso, Cod, Prima, Resto: String;
  Raw: AnsiString;
  p, q, r: Integer;
begin
  Cod := CustomMessage('CodiceLinguaEva');
  Percorso := ExpandConstant('{userappdata}\Eva\impostazioni_eva.json');
  ForceDirectories(ExtractFilePath(Percorso));
  Raw := '';
  if FileExists(Percorso) then
    LoadStringFromFile(Percorso, Raw);
  p := Pos('"lingua"', String(Raw));
  if p > 0 then
  begin
    { c'è già: sostituisco solo il valore tra le virgolette che seguono i due punti }
    Prima := Copy(String(Raw), p + 8, MaxInt);
    q := Pos(':', Prima);
    r := 0;
    if q > 0 then
    begin
      Resto := Copy(Prima, q + 1, MaxInt);
      r := Pos('"', Resto);
    end;
    if (q > 0) and (r > 0) and (Pos('"', Copy(Resto, r + 1, MaxInt)) > 0) then
    begin
      { posizione assoluta (in byte) dell'apertura e della chiusura del valore }
      p := p + 8 + q + r - 1;                       { apertura }
      Resto := Copy(String(Raw), p + 1, MaxInt);
      q := Pos('"', Resto);                         { chiusura }
      Delete(Raw, p + 1, q - 1);
      Insert(AnsiString(Cod), Raw, p + 1);
    end
    else
      p := -1;
  end
  else
    p := -1;
  if p = -1 then
  begin
    p := Pos('{', String(Raw));
    if p = 0 then
      Raw := '{' + #13#10 + '  "lingua": "' + AnsiString(Cod) + '"' + #13#10 + '}' + #13#10
    else if Pos('"', Copy(String(Raw), p + 1, MaxInt)) > 0 then
      Insert(#13#10 + '  "lingua": "' + AnsiString(Cod) + '",', Raw, p + 1)
    else
      Insert(#13#10 + '  "lingua": "' + AnsiString(Cod) + '"' + #13#10, Raw, p + 1);
  end;
  if not SaveStringToFile(Percorso, Raw, False) then
    Log('Non riesco a scrivere ' + Percorso);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SalvaLinguaEva;
end;
