[Setup]
AppName=Kneipen-Schlägerei
AppVersion=1.5.1
AppPublisher=Shinpai-AI
AppPublisherURL=https://github.com/Shinpai-AI/Kneipe
DefaultDirName={commonappdata}\Kneipe
DefaultGroupName=Kneipen-Schlägerei
OutputBaseFilename=Kneipe-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=installer-build\kneipe.ico
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\kneipe.ico
; Komplettes Verzeichnis bei Deinstallation löschen
UninstallFilesDir={app}

[Files]
Source: "installer-build\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Dirs]
Name: "{app}"; Permissions: users-modify

[Icons]
Name: "{group}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"
Name: "{commondesktop}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"

[Run]
; Firewall-Regeln anlegen (python.exe UND pythonw.exe!)
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Kneipe-python"" dir=in action=allow program=""{app}\python\python.exe"" enable=yes profile=private,public"; Flags: runhidden waituntilterminated; StatusMsg: "Firewall-Regel wird angelegt..."
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Kneipe-pythonw"" dir=in action=allow program=""{app}\python\pythonw.exe"" enable=yes profile=private,public"; Flags: runhidden waituntilterminated
; Server einmal starten damit DB/Vault initialisiert werden (kurz laufen lassen)
Filename: "{app}\python\python.exe"; Parameters: "-c ""import subprocess,time,sys,os; os.environ['PYTHONIOENCODING']='utf-8'; p=subprocess.Popen([sys.executable, 'server.py'], cwd=r'{app}', env=os.environ); time.sleep(8); p.terminate()"""; Flags: runhidden waituntilterminated; StatusMsg: "Server wird initialisiert..."; WorkingDir: "{app}"
; Programm starten (Tray + Server)
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; Description: "Kneipen-Schlägerei starten"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallRun]
; Server beenden vor Deinstallation
Filename: "taskkill"; Parameters: "/F /IM pythonw.exe"; Flags: runhidden waituntilterminated
Filename: "taskkill"; Parameters: "/F /IM python.exe"; Flags: runhidden waituntilterminated
; Firewall-Regeln entfernen
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Kneipe-python"""; Flags: runhidden waituntilterminated
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Kneipe-pythonw"""; Flags: runhidden waituntilterminated

[UninstallDelete]
; Kompletten Installationsordner löschen (inkl. db, vault, logs)
Type: filesandordirs; Name: "{app}"
