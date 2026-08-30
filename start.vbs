Option Explicit
Dim fso, sh, base, pyw, url, pid, i
base = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

pyw = FindPython()
If pyw = "" Then
    MsgBox "Cannot find Python (pythonw.exe)." & vbCrLf & "Install Python 3.x or keep WorkBuddy's Python.", vbExclamation, "Daily Todo List"
    WScript.Quit 1
End If

If fso.FileExists(base & "server.pid") Then
    pid = fso.OpenTextFile(base & "server.pid").ReadLine
    If IsRunning(pid) Then
        url = ReadUrl(base)
        OpenBrowser url
        WScript.Quit 0
    End If
    fso.DeleteFile base & "server.pid", True
End If

sh.Run """" & pyw & """ """ & base & "app.py""", 0, False

url = ""
For i = 1 To 60
    If fso.FileExists(base & "app.url") Then
        url = fso.OpenTextFile(base & "app.url").ReadLine
        Exit For
    End If
    WScript.Sleep 500
Next

If url = "" Then
    MsgBox "Server did not start. Check app.log for details.", vbExclamation, "Daily Todo List"
    WScript.Quit 1
End If

OpenBrowser url
WScript.Quit 0

Function FindPython()
    Dim lapp, up, c, f, pf, item
    lapp = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    up = sh.ExpandEnvironmentStrings("%USERPROFILE%")
    Dim cand(5)
    cand(0) = lapp & "\Programs\Python\Python313\pythonw.exe"
    cand(1) = lapp & "\Programs\Python\Python312\pythonw.exe"
    cand(2) = lapp & "\Programs\Python\Python311\pythonw.exe"
    cand(3) = up & "\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
    cand(4) = "C:\Python313\pythonw.exe"
    cand(5) = "C:\Python312\pythonw.exe"
    For Each c In cand
        If fso.FileExists(c) Then FindPython = c: Exit Function
    Next
    pf = lapp & "\Programs\Python"
    If fso.FolderExists(pf) Then
        For Each item In fso.GetFolder(pf).SubFolders
            If fso.FileExists(item.Path & "\pythonw.exe") Then FindPython = item.Path & "\pythonw.exe": Exit Function
        Next
    End If
    FindPython = ""
End Function

Function IsRunning(p)
    Dim wmi, q
    On Error Resume Next
    Set wmi = GetObject("winmgmts:\\.\root\cimv2")
    Set q = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE ProcessId = " & p)
    IsRunning = (q.Count > 0)
    On Error GoTo 0
End Function

Function ReadUrl(b)
    Dim f
    Set f = fso.OpenTextFile(b & "app.url")
    ReadUrl = f.ReadLine
    f.Close
End Function

Sub OpenBrowser(u)
    If u = "" Then u = "http://127.0.0.1:8000/"
    sh.Run u, 1, False
End Sub
