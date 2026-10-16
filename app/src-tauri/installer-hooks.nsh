; Tauri NSIS hooks (tauri.bundle.json > bundle.windows.nsis.installerHooks).
;
; An engine left running by an older version (before the app tied it to a Job Object, 2026-10-16)
; holds its files open, and the install stopped on "Error opening file for writing". Stop it first;
; nothing is lost, as it is only ever started by the app.

!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM fawa2ly-engine.exe'
  Pop $0
  Sleep 500
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /T /IM fawa2ly-engine.exe'
  Pop $0
  Sleep 500
!macroend
