' install_desktop_icon.vbs
' One-time installer: creates a "Daily Task List" shortcut on the user's
' desktop, pointing to start.vbs with the custom beige app icon.
'
' This file is 100% pure ASCII on purpose. It never hard-codes the project
' path (which lives under a Chinese-named user folder and would break under
' a GBK code page). Instead, it discovers the project folder at runtime
' from WScript.ScriptFullName, which is always returned as a Unicode
' string regardless of the script file's code page interpretation.
'
' Double-click this file ONCE. A confirmation message shows the path of
' the created shortcut.

Option Explicit

Dim fso, base, assets, target, iconloc, lnk, name, ws, sc

Set fso = CreateObject("Scripting.FileSystemObject")
base    = fso.GetParentFolderName(WScript.ScriptFullName)
assets  = fso.BuildPath(base, "assets")
target  = fso.BuildPath(base, "start.vbs")
iconloc = fso.BuildPath(assets, "icon.ico")

' Build the Chinese display name from Unicode code points (codepage-independent):
'   6BCF 65E5 4EFB 52A1 6E05 5355 5F55
name = ChrW(&H6BCF) & ChrW(&H65E5) & ChrW(&H4EFB) & ChrW(&H52A1) & ChrW(&H6E05) & ChrW(&H5355) & ChrW(&H5F55)

Set ws = CreateObject("WScript.Shell")
lnk = ws.SpecialFolders("Desktop") & "\" & name & ".lnk"

Set sc = ws.CreateShortcut(lnk)
sc.TargetPath       = target
sc.WorkingDirectory = base
sc.IconLocation     = iconloc
sc.Description      = name
sc.Save

WScript.Echo "Desktop shortcut created:" & vbCrLf & vbCrLf & lnk
