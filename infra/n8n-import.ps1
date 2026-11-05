# Importa y publica TODOS los workflows de n8n/workflows y reinicia n8n.
#
# Uso (desde la raíz del proyecto, con Docker encendido):
#     .\infra\n8n-import.ps1
#
# Úsalo después de cambiar cualquier JSON de n8n/workflows. Los workflows se publican con
# su ID fijo: importar de nuevo REEMPLAZA la versión anterior (no crea copias).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# CM-99 primero: los demás lo usan como workflow de errores. Los sub-workflows antes que quien los llama.
$order = @(
    @{ file = "cm-99-errores.json";            id = "cm99Errores" },
    @{ file = "cm-05-ejecutar-acciones.json";  id = "cm05EjecutarAcciones" },
    @{ file = "cm-03-archivos-ia.json";        id = "cm03ArchivosIA" },
    @{ file = "cm-02-entrada-telegram.json";   id = "cm02EntradaTelegram" },
    @{ file = "cm-06-notificaciones.json";     id = "cm06Notificaciones" },
    @{ file = "cm-08-recordatorios.json";      id = "cm08Recordatorios" }
)

foreach ($wf in $order) {
    Write-Host "Importando $($wf.file)..." -ForegroundColor Cyan
    docker compose exec -T n8n n8n import:workflow --input=/workflows/$($wf.file)
    if ($LASTEXITCODE -ne 0) { throw "Falló la importación de $($wf.file)" }
    docker compose exec -T n8n n8n publish:workflow --id=$($wf.id)
    if ($LASTEXITCODE -ne 0) { throw "Falló la publicación de $($wf.id)" }
}

Write-Host "Reiniciando n8n para activar los cambios..." -ForegroundColor Cyan
docker compose up -d --force-recreate n8n | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:5678/healthz" -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}
Write-Host "Listo: $($order.Count) workflows publicados." -ForegroundColor Green
