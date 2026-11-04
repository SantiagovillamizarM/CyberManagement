# Abre el túnel HTTPS de desarrollo y conecta el bot de Telegram a n8n.
#
# Uso (desde la raíz del proyecto, con Docker encendido):
#     .\infra\dev-tunnel.ps1
#
# Qué hace:
#   1. Arranca cloudflared (túnel gratuito, URL https://xxxx.trycloudflare.com).
#   2. Escribe esa URL en N8N_WEBHOOK_URL del .env.
#   3. Recrea n8n para que use la nueva URL.
#   4. Registra el webhook en Telegram (con secret_token).
#
# La URL cambia cada vez que el túnel se reinicia: vuelve a ejecutar este script.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "1/4 Iniciando el túnel..." -ForegroundColor Cyan
docker compose --profile tunnel up -d --force-recreate tunnel | Out-Null

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    $logs = docker compose --profile tunnel logs tunnel 2>&1 | Out-String
    $match = [regex]::Match($logs, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($match.Success) { $url = $match.Value }
}
if (-not $url) { throw "No se obtuvo la URL del túnel. Revisa: docker compose --profile tunnel logs tunnel" }
Write-Host "    URL pública: $url" -ForegroundColor Green

Write-Host "2/4 Actualizando N8N_WEBHOOK_URL en .env..." -ForegroundColor Cyan
$envPath = Join-Path $root ".env"
$content = [IO.File]::ReadAllText($envPath)
$content = [regex]::Replace($content, "(?m)^N8N_WEBHOOK_URL=.*$", "N8N_WEBHOOK_URL=$url/")
# UTF-8 SIN BOM: con BOM, Docker y Python leerían mal la primera línea.
[IO.File]::WriteAllText($envPath, $content, (New-Object Text.UTF8Encoding($false)))

Write-Host "3/4 Recreando n8n con la nueva URL..." -ForegroundColor Cyan
docker compose up -d n8n | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:5678/healthz" -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}

Write-Host "4/4 Registrando el webhook en Telegram..." -ForegroundColor Cyan
Push-Location (Join-Path $root "backend")
try { & .\.venv\Scripts\python.exe -m scripts.telegram_webhook set } finally { Pop-Location }
