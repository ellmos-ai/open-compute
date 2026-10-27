param(
    [string]$Model,
    [string]$Context = "Was möchtest du besprechen?",
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'

function Get-TreeDigest([string]$Root) {
    $base = (Resolve-Path -LiteralPath $Root).Path
    $rows = foreach ($file in Get-ChildItem -LiteralPath $base -File -Recurse | Sort-Object FullName) {
        if ($file.Name -eq '.open-compute-chat-pin.json') { continue }
        $relative = [IO.Path]::GetRelativePath($base, $file.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$relative=$hash"
    }
    $payload = [Text.UTF8Encoding]::new($false).GetBytes(($rows -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($payload)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-PinnedTree([string]$Root, [string]$ExpectedCommit, [string]$ExpectedTreeDigest) {
    $pinPath = Join-Path $Root '.open-compute-chat-pin.json'
    if (-not (Test-Path -LiteralPath $pinPath -PathType Leaf)) {
        throw "Vorhandener Snapshot hat keinen Pin-Beleg: $Root"
    }
    $pin = Get-Content -LiteralPath $pinPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($pin.commit -ne $ExpectedCommit -or $pin.tree_sha256 -ne $ExpectedTreeDigest) {
        throw "Vorhandener Snapshot passt nicht zum festgeschriebenen Commit: $Root"
    }
    $actualDigest = Get-TreeDigest $Root
    if ($actualDigest -ne $pin.tree_sha256) {
        throw "Snapshot wurde nach der Pin-Erstellung verändert: $Root"
    }
}

function Install-PinnedArchive(
    [string]$Repo,
    [string]$Commit,
    [string[]]$ArchivePaths,
    [string]$Target,
    [string]$Purpose
) {
    if (Test-Path -LiteralPath $Target) {
        Assert-PinnedTree -Root $Target -ExpectedCommit $Commit -ExpectedTreeDigest (
            (Get-Content -LiteralPath (Join-Path $Target '.open-compute-chat-pin.json') -Raw -Encoding utf8 | ConvertFrom-Json).tree_sha256
        )
        return
    }

    $temporary = "$Target.tmp-$([guid]::NewGuid().ToString('N'))"
    $archive = "$temporary.zip"
    New-Item -ItemType Directory -Path $temporary -Force | Out-Null
    try {
        $gitArgs = @('-C', $Repo, 'archive', '--format=zip', '--output', $archive, $Commit) + $ArchivePaths
        & git @gitArgs
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archive -PathType Leaf)) {
            throw "git archive fehlgeschlagen für $Purpose (Commit $Commit)."
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $temporary -Force
        Remove-Item -LiteralPath $archive -Force
        $treeDigest = Get-TreeDigest $temporary
        $pin = [ordered]@{
            purpose = $Purpose
            commit = $Commit
            tree_sha256 = $treeDigest
        } | ConvertTo-Json -Compress
        [IO.File]::WriteAllText(
            (Join-Path $temporary '.open-compute-chat-pin.json'),
            $pin,
            [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $Target) {
            throw "Snapshotziel wurde parallel angelegt; vorhandene Dateien bleiben unverändert: $Target"
        }
        [IO.Directory]::Move($temporary, $Target)
        Assert-PinnedTree -Root $Target -ExpectedCommit $Commit -ExpectedTreeDigest $treeDigest
    } finally {
        if (Test-Path -LiteralPath $archive -PathType Leaf) {
            Remove-Item -LiteralPath $archive -Force
        }
        if (Test-Path -LiteralPath $temporary -PathType Container) {
            $resolvedTemp = (Resolve-Path -LiteralPath $temporary).Path
            $resolvedBase = (Resolve-Path -LiteralPath (Split-Path -Parent $Target)).Path
            if ($resolvedTemp.StartsWith($resolvedBase, [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
            } else {
                throw "Temporäres Snapshotziel liegt außerhalb des freigegebenen Build-Ordners: $resolvedTemp"
            }
        }
    }
}

function ConvertTo-TomlString([string]$Value) {
    $normalized = $Value.Replace('\', '/').Replace('"', '\"')
    return '"' + $normalized + '"'
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$repoRoot = (& git -C $repo rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or $repoRoot -ne $repo) {
    throw "Die Chat-Startdatei muss aus dem Open-Compute-Git-Worktree gestartet werden: $repo"
}
$dirty = & git -C $repo status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or $dirty) {
    throw "Der Quellworktree muss sauber sein. Es wird kein veränderlicher Dev-Baum registriert."
}
if (-not $PrepareOnly -and [string]::IsNullOrWhiteSpace($Model)) {
    throw 'Bitte das verfügbare Hostmodell ausdrücklich mit -Model angeben; es gibt keinen stillen Modellfallback.'
}
if (-not $PrepareOnly -and ([string]::IsNullOrWhiteSpace($Context) -or $Context.Length -gt 2000)) {
    throw 'Context muss zwischen 1 und 2000 Zeichen lang sein.'
}
$openComputeCommit = (& git -C $repo rev-parse HEAD).Trim()
$voiceIoRepo = 'C:\_Local_DEV\repos\ellmos-voice-io'
$voiceIoCommit = '7d1d1ba154bfa735ed0440d5af0617a0ae026c8a'
$voiceIoKnown = & git -C $voiceIoRepo cat-file -e "$voiceIoCommit^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Der lokal geprüfte ellmos-voice-io-Commit $voiceIoCommit ist nicht verfügbar."
}
$voiceHead = (& git -C $voiceIoRepo rev-parse $voiceIoCommit).Trim()
if ($voiceHead -ne $voiceIoCommit) {
    throw "Der ellmos-voice-io-Pin stimmt nicht mit dem Quell-Commit überein."
}

$shortCommit = $openComputeCommit.Substring(0, 12)
$profileName = "open-compute-chat-$shortCommit"
$localRoot = Join-Path $env:LOCALAPPDATA 'open-compute\chat-builds'
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
$openSnapshot = Join-Path $localRoot "open-compute-$openComputeCommit"
$voiceSnapshot = Join-Path $localRoot "ellmos-voice-io-$voiceIoCommit"
Install-PinnedArchive -Repo $repo -Commit $openComputeCommit -ArchivePaths @('open_compute') -Target $openSnapshot -Purpose 'open-compute'
Install-PinnedArchive -Repo $voiceIoRepo -Commit $voiceIoCommit -ArchivePaths @('src') -Target $voiceSnapshot -Purpose 'ellmos-voice-io'

$python = Join-Path $env:USERPROFILE '.venvs\open-compute-mcp\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Das vorhandene isolierte open-compute-MCP-Venv fehlt: $python"
}
$modelPath = Join-Path $env:LOCALAPPDATA 'open-compute\models\vosk-model-small-de-0.15'
if (-not (Test-Path -LiteralPath $modelPath -PathType Container)) {
    $modelPath = ''
}
$pythonPath = "$openSnapshot;$voiceSnapshot/src"

$codexHome = $env:CODEX_HOME
if ([string]::IsNullOrWhiteSpace($codexHome)) {
    $codexHome = Join-Path $env:USERPROFILE '.codex'
}
New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
$profilePath = Join-Path $codexHome "$profileName.config.toml"
$profileText = @"
[mcp_servers."open-compute"]
enabled = false

[mcp_servers."open-compute-chat"]
command = $(ConvertTo-TomlString $python)
args = ["-m", "open_compute.mcp_server"]
cwd = $(ConvertTo-TomlString $openSnapshot)
enabled = true
required = true
enabled_tools = ["chat"]
default_tools_approval_mode = "auto"
startup_timeout_sec = 30
tool_timeout_sec = 3600

[mcp_servers."open-compute-chat".env]
PYTHONPATH = $(ConvertTo-TomlString $pythonPath)
PYTHONIOENCODING = "utf-8"
PYTHONDONTWRITEBYTECODE = "1"
OPEN_COMPUTE_VOSK_MODEL = $(ConvertTo-TomlString $modelPath)
OC_LANGUAGE = "de"
OC_SAFETY_MODE = "confirm"
OC_SIGNAL_AUTO = "off"
"@

if (Test-Path -LiteralPath $profilePath -PathType Leaf) {
    $existingProfile = Get-Content -LiteralPath $profilePath -Raw -Encoding utf8
    if ($existingProfile -cne $profileText) {
        throw "Profil existiert bereits mit anderem Inhalt und wurde nicht überschrieben: $profilePath"
    }
} else {
    $stagedProfile = "$profilePath.tmp-$([guid]::NewGuid().ToString('N'))"
    [IO.File]::WriteAllText($stagedProfile, $profileText, [Text.UTF8Encoding]::new($false))
    try {
        [IO.File]::Move($stagedProfile, $profilePath)
    } catch {
        if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) { throw }
        $existingProfile = Get-Content -LiteralPath $profilePath -Raw -Encoding utf8
        if ($existingProfile -cne $profileText) { throw }
    } finally {
        if (Test-Path -LiteralPath $stagedProfile -PathType Leaf) {
            Remove-Item -LiteralPath $stagedProfile -Force
        }
    }
}

$codex = Get-Command codex -ErrorAction Stop
$probe = & $codex.Source --profile $profileName mcp get open-compute-chat --json 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Codex konnte das isolierte MCP-Profil nicht laden: $($probe -join "`n")"
}
$serverConfig = ($probe -join "`n") | ConvertFrom-Json
if ($serverConfig.enabled -ne $true -or @($serverConfig.enabled_tools) -notcontains 'chat') {
    throw "Codex-Profil-Readback stimmt nicht mit dem chat-only-Serververtrag überein."
}
$disabledProbe = & $codex.Source --profile $profileName mcp get open-compute --json 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Codex konnte den globalen Serverzustand im Profil nicht prüfen: $($disabledProbe -join "`n")"
}
$oldServerConfig = ($disabledProbe -join "`n") | ConvertFrom-Json
if ($oldServerConfig.enabled -ne $false) {
    throw "Das Profil schaltet den alten globalen open-compute-Server nicht isoliert aus."
}

Write-Output "Profil geprüft: $profileName"
Write-Output "MCP-Server: open-compute-chat; einziges Werkzeug: chat"
Write-Output "Gepinnte Quelle: $openComputeCommit"
Write-Output "Gepinnte VoiceIO-Quelle: $voiceIoCommit"
Write-Output "Codex-Basisconfig wurde nicht verändert: $(Join-Path $codexHome 'config.toml')"

if ($PrepareOnly) {
    Write-Output 'Vorbereitung abgeschlossen; kein Codex-Modellturn gestartet.'
    Write-Output "Start mit: .\scripts\open_compute_chat.ps1 -Model <verfügbares-Modell>"
    return
}
$prompt = @"
Öffne jetzt das native open-compute-Chatfenster über das Werkzeug chat des MCP-Servers open-compute-chat. Starte mit channel="tk" und diesem Kontext: $Context
Nutze für den Dialog ausschließlich open-compute-chat.chat. Antworte dem Menschen im nativen Fenster mit echtem Modelltext und gib jede Antwort mit derselben chat_id, einer neuen eindeutigen assistant_turn_id und assistant_message an chat zurück. Setze den Dialog fort, bis der Mensch das Fenster schließt. Erfinde keine Antwort und rufe keine anderen Desktop- oder Computerwerkzeuge auf.
"@
$codexArgs = @(
    '--profile', $profileName,
    '--model', $Model,
    '--sandbox', 'read-only',
    '--ask-for-approval', 'on-request',
    '--cd', $openSnapshot,
    $prompt
)
& $codex.Source @codexArgs
exit $LASTEXITCODE
