# Guarda TU clave personal de Altum en Windows, cifrada para tu usuario de este computador (DPAPI,
# lo mismo que usa Windows para las credenciales guardadas). No queda en texto plano, no hay que
# definir variables de entorno ni tocar el perfil de PowerShell: el plugin la lee solo.
#
#   powershell -ExecutionPolicy Bypass -File scripts\sn\sn-clave-altum.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\sn\sn-clave-altum.ps1 <empresa>
#
# La clave la generas en Altum: Mi perfil -> Mis datos -> "Tu clave personal de API" -> Regenerar.
# Es UNA sola para todos tus proyectos y esto se corre UNA vez (o cuando la regeneres).
param([string]$Empresa = '')

$ErrorActionPreference = 'Stop'
$sufijo = ''
if ($Empresa -ne '') {
  $limpia = ($Empresa.ToUpperInvariant() -replace '[^A-Z0-9]', '_')
  $sufijo = "_$limpia"
}
$var = "SN_ALTUM_KEY$sufijo"
$carpeta = Join-Path $env:LOCALAPPDATA 'Softnexus'
$archivo = Join-Path $carpeta "$var.dpapi"

$de = ''
if ($Empresa -ne '') { $de = " para $Empresa" }
Write-Host "Pega tu clave personal de Altum$de y presiona Enter (no se vera en pantalla):"
$secreta = Read-Host -AsSecureString
if ($secreta.Length -eq 0) {
  Write-Host 'No escribiste nada: no se guardo la clave.'
  exit 1
}

New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
ConvertFrom-SecureString -SecureString $secreta | Set-Content -Path $archivo -Encoding ascii
# Solo tu usuario puede leer el archivo (y, aun copiado a otro computador, no se puede descifrar).
$acl = Get-Acl $archivo
$acl.SetAccessRuleProtection($true, $false)
$acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
  [Security.Principal.WindowsIdentity]::GetCurrent().Name, 'FullControl', 'Allow')))
Set-Acl -Path $archivo -AclObject $acl

Write-Host "Listo: $var quedo guardada y cifrada para tu usuario ($archivo)."
Write-Host 'No tienes que reiniciar nada ni definir variables: el plugin la lee solo.'
Write-Host 'Comprueba con: "quien soy en Altum?"'
