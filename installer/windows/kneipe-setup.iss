[Setup]
AppName=Kneipen-Schlägerei
AppVersion=1.5.1
AppPublisher=Shinpai-AI
DefaultDirName={commonappdata}\Kneipe
DefaultGroupName=Kneipen-Schlägerei
OutputBaseFilename=Kneipe-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=installer-build\kneipe.ico
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\kneipe.ico

[Files]
Source: "installer-build\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
; Schreibrechte für den Kneipe-Ordner setzen (Server braucht db, vault, logs)
[Dirs]
Name: "{app}"; Permissions: users-modify

[Icons]
Name: "{group}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"
Name: "{commondesktop}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"

[Run]
; Firewall-Regel anlegen (Python darf auf Port 4567 lauschen)
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Kneipen-Schlägerei"" dir=in action=allow program=""{app}\python\python.exe"" enable=yes profile=private,public"; Flags: runhidden waituntilterminated; StatusMsg: "Firewall-Regel wird angelegt..."
; Programm starten
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; Description: "Kneipen-Schlägerei starten"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallRun]
; Firewall-Regel beim Deinstallieren entfernen
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Kneipen-Schlägerei"""; Flags: runhidden waituntilterminated
