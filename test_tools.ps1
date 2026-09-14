# test_tools.ps1
$ErrorActionPreference = "Stop"

$modelResponse = Invoke-RestMethod -Uri "http://localhost:8080/v1/models" -Method Get
$model = $modelResponse.data[0].id
Write-Host "Modelo detectado: $model"

$plainBodyObj = @{
    model = $model
    messages = @(
        @{ role = "system"; content = "You are a concise financial assistant." }
        @{ role = "user"; content = "Reply with exactly: template working" }
    )
    temperature = 0
    max_tokens = 32
}

$plainJson = $plainBodyObj | ConvertTo-Json -Depth 10
$plainUtf8 = [System.Text.Encoding]::UTF8.GetBytes($plainJson)

$plainResponse = Invoke-RestMethod `
    -Uri "http://localhost:8080/v1/chat/completions" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body $plainUtf8

Write-Host "---- RESPUESTA CHAT SIMPLE ----"
$plainResponse | ConvertTo-Json -Depth 20