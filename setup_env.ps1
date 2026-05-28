# chat.z.ai Proxy - Environment Setup Script
# Run this to extract values from the HAR file and create .env

param(
    [Parameter(Mandatory)]
    [string]$HarPath
)

if (-not (Test-Path $HarPath)) {
    Write-Error "HAR file not found: $HarPath"
    exit 1
}

Write-Host "Parsing HAR file: $HarPath" -ForegroundColor Cyan

$har = Get-Content $HarPath -Raw | ConvertFrom-Json
$entries = $har.log.entries

# Find chat completion entry
$chatEntry = $null
foreach ($entry in $entries) {
    $url = $entry.request.url
    if ($url -match '/api/v2/chat/completions') {
        $chatEntry = $entry
        break
    }
}

if (-not $chatEntry) {
    Write-Error "Could not find chat completion entry in HAR"
    exit 1
}

Write-Host "Found chat completion entry" -ForegroundColor Green

# Extract query string
$url = $chatEntry.request.url
$queryString = ($url -split '\?')[1]
Write-Host "QUERY_STRING=$queryString" -ForegroundColor Yellow

# Extract headers
$headers = @{}
foreach ($h in $chatEntry.request.headers) {
    $name = $h.name
    $val = $h.value
    if ($name -ne 'Content-Length' -and $name -ne 'Host' -and $name -ne 'Connection') {
        $headers[$name] = $val
    }
}
$headersJson = $headers | ConvertTo-Json -Compress
Write-Host "STATIC_HEADERS=$headersJson" -ForegroundColor Yellow

# Extract x-signature
$xSignature = ""
foreach ($h in $chatEntry.request.headers) {
    if ($h.name -eq 'x-signature') {
        $xSignature = $h.value
        break
    }
}
Write-Host "X_SIGNATURE=$xSignature" -ForegroundColor Yellow

# Extract captcha_verify_param from POST body
$bodyText = $chatEntry.request.postData.text
$body = $bodyText | ConvertFrom-Json
$captchaParam = $body.captcha_verify_param
Write-Host "CAPTCHA_VERIFY_PARAM=$captchaParam" -ForegroundColor Yellow

# Extract user_id and token from query params
$user_id = $chatEntry.request.queryString | Where-Object { $_.name -eq 'user_id' } | Select-Object -ExpandProperty value
$chat_id = $body.chat_id
Write-Host "CHAT_ID=$chat_id" -ForegroundColor Yellow

# Generate .env file
$envContent = @"
# Target
TARGET_URL=https://chat.z.ai
MODEL_NAME=GLM-5.1
HOST=0.0.0.0
PORT=8000
API_KEY=sk-web2api-placeholder
DSML_ENABLED=true

# From HAR (extracted on $(Get-Date))
CAPTCHA_VERIFY_PARAM=$captchaParam
X_SIGNATURE=$xSignature
QUERY_STRING=$queryString
STATIC_HEADERS=$headersJson
USER_NAME=
USER_LANGUAGE=zh-CN
CHAT_ID=$chat_id
"@

$envPath = Join-Path (Split-Path $HarPath -Parent) "chat-z-ai-proxy\.env"
$envContent | Set-Content $envPath -Encoding UTF8
Write-Host ".env file written to: $envPath" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. cd chat-z-ai-proxy" -ForegroundColor White
Write-Host "  2. pip install -r requirements.txt" -ForegroundColor White
Write-Host "  3. python server.py" -ForegroundColor White
