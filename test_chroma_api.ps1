$baseUrl = "http://127.0.0.1:8010"
$headers = @{
    "X-Chroma-Admin-Token" = "copilot-test-token"
    "Content-Type" = "application/json"
}

function Invoke-Chroma {
    param($Uri, $Method, $Body = $null)
    try {
        if ($Body) {
            $response = Invoke-WebRequest -Uri $Uri -Method $Method -Headers $headers -Body $Body -ErrorAction Stop
        } else {
            $response = Invoke-WebRequest -Uri $Uri -Method $Method -Headers $headers -ErrorAction Stop
        }
        return @{ StatusCode = $response.StatusCode; Content = ($response.Content | ConvertFrom-Json) }
    } catch {
        return @{ StatusCode = $_.Exception.Response.StatusCode.value__; Error = $_.Exception.Message; Content = ($_.ErrorDetails.Message) }
    }
}

Write-Host "--- 1) GET /admin/chroma/diagnostics ---"
$res1 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/diagnostics" -Method Get
$res1 | ConvertTo-Json -Depth 5

Write-Host "`n--- 2) GET /admin/chroma/diagnostics?collection_names=code_symbols ---"
$res2 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/diagnostics?collection_names=code_symbols" -Method Get
$res2 | ConvertTo-Json -Depth 5

Write-Host "`n--- 3) POST /admin/chroma/query (list_collections) ---"
$body3 = @{ operation = "list_collections" } | ConvertTo-Json
$res3 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/query" -Method Post -Body $body3
$res3 | ConvertTo-Json -Depth 5

Write-Host "`n--- 4) POST /admin/chroma/query (collection_count: code_symbols) ---"
$body4 = @{ operation = "collection_count"; collection_name = "code_symbols" } | ConvertTo-Json
$res4 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/query" -Method Post -Body $body4
$res4 | ConvertTo-Json -Depth 5

Write-Host "`n--- 5) POST /admin/chroma/query (collection_count: where repo_id='mall') ---"
$body5 = @{ operation = "collection_count"; collection_name = "code_symbols"; "where" = @{ repo_id = "mall" } } | ConvertTo-Json
$res5 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/query" -Method Post -Body $body5
$res5 | ConvertTo-Json -Depth 5

Write-Host "`n--- 6) POST /admin/chroma/query (get: code_symbols limit 3) ---"
$body6 = @{ 
    operation = "get"
    collection_name = "code_symbols"
    limit = 3
    include = @("metadatas", "documents")
} | ConvertTo-Json
$res6 = Invoke-Chroma -Uri "$baseUrl/admin/chroma/query" -Method Post -Body $body6
if ($res6.Content -and $res6.Content.documents) {
    for ($i=0; $i -lt $res6.Content.documents.Count; $i++) {
        if ($res6.Content.documents[$i].Length -gt 100) {
            $res6.Content.documents[$i] = $res6.Content.documents[$i].Substring(0, 100) + "..."
        }
    }
}
$res6 | ConvertTo-Json -Depth 5
