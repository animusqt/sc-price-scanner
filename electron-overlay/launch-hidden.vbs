Option Explicit

Dim shell, fso, appFolder, electronPath, result, scannerPath

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appFolder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appFolder

electronPath = appFolder & "\node_modules\electron\dist\electron.exe"
If Not fso.FileExists(electronPath) Then
    result = shell.Run("cmd.exe /d /s /c npm.cmd ci", 0, True)
    If result <> 0 Then
        MsgBox "Could not install the overlay dependencies. Make sure Node.js and npm are installed, then try again.", _
            vbExclamation, "SC Price Scanner"
        WScript.Quit result
    End If
End If

If Not fso.FileExists(electronPath) Then
    MsgBox "Electron was not installed successfully. Try running npm ci in the electron-overlay folder.", _
        vbExclamation, "SC Price Scanner"
    WScript.Quit 1
End If

scannerPath = fso.GetParentFolderName(appFolder) & "\scanner-engine\dist\SC-Price-Scanner-Bridge.exe"
If Not fso.FileExists(scannerPath) Then
    MsgBox "The overlay will open, but scanning will not work until the scanner bridge is built." & vbCrLf & _
        "Build it with: python scanner-engine\build_scanner.py", _
        vbInformation, "SC Price Scanner"
End If

shell.Run Chr(34) & electronPath & Chr(34) & " .", 0, False
