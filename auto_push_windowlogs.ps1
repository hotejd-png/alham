$ErrorActionPreference = "SilentlyContinue"

$sourceRoot = "D:\projects\spy_bot_v3\data\multi_wallet"
$repoRoot   = "D:\projects\spy_bot_v3\data\share_github_2026-04-02"
$wallets    = @(
  "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
  "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
)

$dateKyiv = (Get-Date).ToString("yyyy-MM-dd")

foreach ($w in $wallets) {
  $src = Join-Path $sourceRoot "$w\window_logs\$dateKyiv"
  $dst = Join-Path $repoRoot   "$w\window_logs\$dateKyiv"

  if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null

    Get-ChildItem $src -Directory | ForEach-Object {
      $winDst = Join-Path $dst $_.Name
      New-Item -ItemType Directory -Force -Path $winDst | Out-Null
      Copy-Item "$($_.FullName)\summary.txt"      $winDst -Force
      Copy-Item "$($_.FullName)\summary_full.txt" $winDst -Force
      Copy-Item "$($_.FullName)\stats.csv"        $winDst -Force
    }
  }
}

Set-Location $repoRoot
git add .
git commit -m "auto update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" 2>$null
git push 2>$null
