# Rename this checkout folder (default: to "bucephalus") and move its per-checkout state directory
# (<data dir>\checkouts\<folder name>-<first 8 hex of sha256 of its full path>) to match the new path.
# Run it from OUTSIDE the folder: Windows refuses to rename a folder that is a current directory.
#   powershell -ExecutionPolicy Bypass -File scripts\rename_checkout.ps1 [-NewName bucephalus] [-WhatIf]
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$NewName = "bucephalus",
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"

function Tag([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($path))
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "").Substring(0, 8)
}

$Root = (Get-Item -LiteralPath $Root).FullName.TrimEnd("\")
if (-not ((Test-Path -LiteralPath (Join-Path $Root "pyproject.toml")) -and (Test-Path -LiteralPath (Join-Path $Root "src\agent")))) {
    Write-Error "$Root is not a checkout of this project (no pyproject.toml and src\agent)"
    exit 2
}
$parent = Split-Path -Parent $Root
$oldName = Split-Path -Leaf $Root
$target = Join-Path $parent $NewName
if (Test-Path -LiteralPath $target) {
    Write-Error "$target already exists; nothing was changed"
    exit 2
}

$data = if ($env:BUCEPHALUS_DATA_DIR) { $env:BUCEPHALUS_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "bucephalus\bucephalus" }
$checkouts = Join-Path $data "checkouts"
$oldState = Join-Path $checkouts ("{0}-{1}" -f $oldName, (Tag $Root))
$newState = Join-Path $checkouts ("{0}-{1}" -f $NewName, (Tag $target))
if (-not (Test-Path -LiteralPath $oldState) -and (Test-Path -LiteralPath $checkouts)) {
    $cands = @(Get-ChildItem -LiteralPath $checkouts -Directory -Filter "$oldName-*")
    if ($cands.Count -eq 1) {
        Write-Output "note: no state directory with this path's exact tag; using the only one for $oldName ($($cands[0].Name))"
        $oldState = $cands[0].FullName
    }
}
$moveState = Test-Path -LiteralPath $oldState
if ($moveState -and (Test-Path -LiteralPath $newState)) {
    Write-Error "$newState already exists; nothing was changed"
    exit 2
}

$busy = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.ProcessId -ne $PID })
if ($busy.Count -gt 0) {
    Write-Error ("processes are using the checkout; stop them first: " + (($busy | ForEach-Object { "$($_.ProcessId) $($_.Name)" }) -join ", "))
    exit 2
}

if ($WhatIf) {
    Write-Output "would rename: $Root -> $target"
    if ($moveState) { Write-Output "would move state: $oldState -> $newState" } else { Write-Output "no state directory to move" }
    exit 0
}

try {
    Rename-Item -LiteralPath $Root -NewName $NewName
} catch {
    Write-Output "Windows refused to rename the folder: $($_.Exception.Message)"
    Write-Output "Close anything using it (a terminal or editor whose current folder is inside it), then run from outside:"
    Write-Output "  powershell -ExecutionPolicy Bypass -File `"$target\..\$oldName\scripts\rename_checkout.ps1`" -NewName $NewName"
    exit 3
}
Write-Output "renamed: $Root -> $target"
if ($moveState) {
    Move-Item -LiteralPath $oldState -Destination $newState
    Write-Output "moved state: $oldState -> $newState"
} else {
    Write-Output "no state directory to move"
}
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git -and (Test-Path -LiteralPath (Join-Path $target ".git"))) { & git -C $target worktree prune 2>$null }
exit 0
