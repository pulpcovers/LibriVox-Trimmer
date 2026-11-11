Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_VERSION
  !error "APP_VERSION is required"
!endif
!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR is required"
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE is required"
!endif

Name "LibriVox Trimmer"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\LibriVox Trimmer"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "LibriVox Trimmer"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "LibriVox Trimmer Setup"

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\LibriVoxTrimmer"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\LibriVox Trimmer.exe"
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Create a desktop shortcut"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateDesktopShortcut
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${__FILEDIR__}\..\LICENSE"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  SetShellVarContext current
  StrCmp $INSTDIR "$LOCALAPPDATA\Programs\LibriVox Trimmer" location_ok
  MessageBox MB_ICONSTOP "LibriVox Trimmer must be installed in its per-user application folder."
  Abort
location_ok:
  IfFileExists "$INSTDIR\*" 0 ready
  IfFileExists "$INSTDIR\.librivox-trimmer-install" ready
  MessageBox MB_ICONSTOP "The destination already contains files not managed by this installer. Move those files before installing."
  Abort
ready:
FunctionEnd

Function CreateDesktopShortcut
  CreateShortcut "$DESKTOP\LibriVox Trimmer.lnk" "$INSTDIR\LibriVox Trimmer.exe"
FunctionEnd

Section "LibriVox Trimmer"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*"
  File /oname=LICENSE.txt "${__FILEDIR__}\..\LICENSE"
  FileOpen $0 "$INSTDIR\.librivox-trimmer-install" w
  FileWrite $0 "LibriVox Trimmer ${APP_VERSION}$\r$\n"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\LibriVox Trimmer"
  CreateShortcut "$SMPROGRAMS\LibriVox Trimmer\LibriVox Trimmer.lnk" "$INSTDIR\LibriVox Trimmer.exe"
  CreateShortcut "$SMPROGRAMS\LibriVox Trimmer\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "LibriVox Trimmer"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Pulp Covers"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\LibriVox Trimmer.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Function un.onInit
  SetShellVarContext current
  StrCmp $INSTDIR "$LOCALAPPDATA\Programs\LibriVox Trimmer" 0 refuse
  IfFileExists "$INSTDIR\.librivox-trimmer-install" ready
refuse:
  MessageBox MB_ICONSTOP "This folder is not a managed LibriVox Trimmer installation. Nothing was removed."
  Abort
ready:
FunctionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\LibriVox Trimmer\LibriVox Trimmer.lnk"
  Delete "$SMPROGRAMS\LibriVox Trimmer\Uninstall.lnk"
  RMDir "$SMPROGRAMS\LibriVox Trimmer"
  Delete "$DESKTOP\LibriVox Trimmer.lnk"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\LibriVox Trimmer.exe"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\.librivox-trimmer-install"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd