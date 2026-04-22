Option Explicit

Dim shell
Dim fileSystem
Dim basePath
Dim pythonwPath
Dim scriptPath
Dim command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

basePath = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = fileSystem.BuildPath(basePath, ".venv\Scripts\pythonw.exe")
scriptPath = fileSystem.BuildPath(basePath, "yarbis_desktop.py")

If Not fileSystem.FileExists(pythonwPath) Then
    MsgBox "No encontre .venv\Scripts\pythonw.exe. Revisa el entorno virtual antes de abrir Yarbis.", vbExclamation, "Yarbis"
    WScript.Quit 1
End If

If Not fileSystem.FileExists(scriptPath) Then
    MsgBox "No encontre yarbis_desktop.py en la carpeta del proyecto.", vbExclamation, "Yarbis"
    WScript.Quit 1
End If

shell.CurrentDirectory = basePath
command = """" & pythonwPath & """" & " " & """" & scriptPath & """"
shell.Run command, 0, False
