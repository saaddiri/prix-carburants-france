# Lancé par le Planificateur de tâches Windows : démarre la base puis le pipeline.
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = "src"
New-Item -ItemType Directory -Force logs | Out-Null
function Log($m) { "$(Get-Date -Format s) $m" | Add-Content "logs\scheduler.log" }

# 1) Docker et la base : jusqu'à 10 essais (Docker Desktop peut démarrer lentement)
$ok = $false
for ($i = 1; $i -le 10 -and -not $ok; $i++) {
    docker compose up -d *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true } else { Log "Docker pas pret (essai $i)"; Start-Sleep 30 }
}
if (-not $ok) { Log "ECHEC : Docker injoignable"; exit 1 }

# 2) attendre que PostgreSQL soit "healthy" (2 minutes maximum)
for ($i = 0; $i -lt 24; $i++) {
    $h = docker inspect --format "{{.State.Health.Status}}" fuel_db 2>$null
    if ($h -eq "healthy") { break }
    Start-Sleep 5
}

# 3) le pipeline ; son code de sortie devient celui de la tâche
& .\.venv\Scripts\python.exe -m fuel.run_daily
$code = $LASTEXITCODE
Log "pipeline termine, code $code"
exit $code