# test_chat_tools_forced.ps1
$ErrorActionPreference = "Stop"

$modelResponse = Invoke-RestMethod -Uri "http://localhost:8080/v1/models" -Method Get
$model = $modelResponse.data[0].id
Write-Host "Modelo detectado: $model"

$toolsBodyObj = @{
    model = $model
    messages = @(
        @{ role = "system"; content = "You are a concise financial assistant." }
        @{ role = "user"; content = "What is the current price of AAPL?" }
    )
    tools = @(
        @{
            type = "function"
            function = @{
                name = "get_stock_price"
                description = "Obtiene el precio actual de una accion"
                parameters = @{
                    type = "object"
                    properties = @{
                        ticker = @{
                            type = "string"
                            description = "Simbolo bursatil"
                        }
                    }
                    required = @("ticker")
                }
            }
        }
    )
    tool_choice = "auto"
    temperature = 0
    max_tokens = 128
}

$toolsJson = $toolsBodyObj | ConvertTo-Json -Depth 10
$toolsUtf8 = [System.Text.Encoding]::UTF8.GetBytes($toolsJson)

$toolsResponse = Invoke-RestMethod `
    -Uri "http://localhost:8080/v1/chat/completions" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body $toolsUtf8

Write-Host "---- RESPUESTA CON TOOLS ----"
$toolsResponse | ConvertTo-Json -Depth 20