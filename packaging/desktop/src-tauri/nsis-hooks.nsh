; Removes what the app wrote outside its install folder when the user switched it on.
!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey HKCU "Software\Classes\Directory\shell\KohakuTerrarium"
  DeleteRegKey HKCU "Software\Classes\Directory\Background\shell\KohakuTerrarium"
!macroend
