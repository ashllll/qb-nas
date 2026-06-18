# enable-agent-skills-hooks.ps1
# 一键启用 agent-skills hooks 配置

$settingsPath = "$env:USERPROFILE\.claude\settings.json"
$hooksDir = "$env:USERPROFILE\.claude\hooks"

if (-not (Test-Path $settingsPath)) {
    Write-Host "错误: 未找到 settings.json ($settingsPath)" -ForegroundColor Red
    exit 1
}

$settings = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json

if (-not $settings.hooks) {
    $settings = $settings | Select-Object *, @{n='hooks';e={@{}}} -ExcludeProperty hooks
}
if (-not $settings.hooks.SessionStart) {
    $settings.hooks | Add-Member -NotePropertyName SessionStart -NotePropertyValue @() -Force
}
if (-not $settings.hooks.PostToolUse) {
    $settings.hooks | Add-Member -NotePropertyName PostToolUse -NotePropertyValue @() -Force
}

$hasAgentSkills = $false
foreach ($h in $settings.hooks.SessionStart) {
    if ($h.hooks -and $h.hooks[0].command -like "*session-start*") { $hasAgentSkills = $true }
}

if (-not $hasAgentSkills) {
    $matchers = @("startup", "resume", "clear", "compact")
    foreach ($m in $matchers) {
        $entry = @{
            matcher = $m
            hooks = @(@{
                type = "command"
                command = "~/.claude/hooks/session-start.sh"
            })
        }
        $settings.hooks.SessionStart += $entry
    }
    Write-Host "OK session-start hooks (startup/resume/clear/compact)" -ForegroundColor Green
} else {
    Write-Host ".. session-start hooks already present" -ForegroundColor Yellow
}

# enabledPlugins
if (-not $settings.enabledPlugins) {
    $settings | Add-Member -NotePropertyName enabledPlugins -NotePropertyValue @{} -Force
}
if (-not $settings.enabledPlugins.'agent-skills@addy-agent-skills') {
    $settings.enabledPlugins | Add-Member -NotePropertyName 'agent-skills@addy-agent-skills' -NotePropertyValue $true -Force
    Write-Host "OK agent-skills registered in enabledPlugins" -ForegroundColor Green
} else {
    Write-Host ".. agent-skills already in enabledPlugins" -ForegroundColor Yellow
}

$settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
Write-Host ""
Write-Host "Done! Restart Claude Code/Reasonix." -ForegroundColor Green
