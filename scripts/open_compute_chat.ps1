param(
    [string]$Model,
    [string]$Workspace,
    [string]$Context = "Was möchtest du besprechen?",
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'

function Get-TreeDigest([string]$Root) {
    $base = (Resolve-Path -LiteralPath $Root).Path
    $rootItem = Get-Item -LiteralPath $base -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Gepinnter Snapshot-Root ist ein Link/Reparsepoint: $base"
    }
    $items = @(Get-ChildItem -LiteralPath $base -Force -Recurse)
    foreach ($item in $items) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Gepinnter Snapshot enthält einen Link/Reparsepoint: $($item.FullName)"
        }
        if (-not $item.PSIsContainer -and $item.Extension -in @('.pyc', '.pyo', '.exe', '.dll', '.pyd', '.com', '.scr', '.bat', '.cmd')) {
            throw "Gepinnter Snapshot enthält ein unerwartetes ausführbares/Bytecode-Artefakt: $($item.FullName)"
        }
    }
    $rows = foreach ($file in $items | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName) {
        $relative = [IO.Path]::GetRelativePath($base, $file.FullName).Replace('\', '/')
        if ($relative -eq '.open-compute-chat-pin.json') { continue }
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
        throw "Vorhandener Snapshot-Pin passt nicht zum unabhängig aus Git-Commit abgeleiteten Inhalt: $Root"
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
        if (Test-Path -LiteralPath $Target) {
            # Never accept a cache's own marker as authority. Re-derive expected
            # bytes from the requested immutable Git commit on every launch.
            Assert-PinnedTree -Root $Target -ExpectedCommit $Commit -ExpectedTreeDigest $treeDigest
            return
        }
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
if ($LASTEXITCODE -ne 0) {
    throw "Git konnte den Chat-Quellworktree nicht bestimmen: $repo"
}
$repoRoot = $repoRoot.Replace('/', '\').TrimEnd('\')
if ($repoRoot -ine $repo.TrimEnd('\')) {
    throw "Die Chat-Startdatei muss aus dem Open-Compute-Git-Worktree gestartet werden: $repo"
}
if (-not $PrepareOnly -and [string]::IsNullOrWhiteSpace($Workspace)) {
    throw 'Bitte den gewünschten Arbeitsordner ausdrücklich mit -Workspace auswählen.'
}
if (-not $PrepareOnly) {
    $workspaceItem = Get-Item -LiteralPath $Workspace -ErrorAction Stop
    if (-not $workspaceItem.PSIsContainer) {
        throw "Der gewählte Host-Arbeitsordner ist kein Ordner: $Workspace"
    }
    $hostWorkspace = $workspaceItem.FullName
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

$codex = Get-Command codex -ErrorAction Stop
$legacyProbe = & $codex.Source mcp get open-compute --json 2>&1
$legacyProbeText = $legacyProbe -join "`n"
$legacyProbeCode = $LASTEXITCODE
$legacyServerExists = $false
if ($legacyProbeCode -eq 0) {
    try { $null = $legacyProbeText | ConvertFrom-Json }
    catch { throw "Codex-Basisprofil lieferte ungültigen open-compute-Readback: $legacyProbeText" }
    $legacyServerExists = $true
} elseif ($legacyProbeText -match "(?m)^Error: No MCP server named 'open-compute' found\.$") {
    $legacyServerExists = $false
} else {
    throw "Codex-Basisprofil konnte nicht fail-closed gelesen werden: $legacyProbeText"
}
$legacyDisableBlock = ''
if ($legacyServerExists) {
    $legacyDisableBlock = '[mcp_servers."open-compute"]' + "`n" + 'enabled = false'
}

$codexHome = $env:CODEX_HOME
if ([string]::IsNullOrWhiteSpace($codexHome)) {
    $codexHome = Join-Path $env:USERPROFILE '.codex'
}
New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
$profilePath = Join-Path $codexHome "$profileName.config.toml"
$profileText = @"
$legacyDisableBlock

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

$probe = & $codex.Source --profile $profileName mcp get open-compute-chat --json 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Codex konnte das isolierte MCP-Profil nicht laden: $($probe -join "`n")"
}
$serverConfig = ($probe -join "`n") | ConvertFrom-Json
$expectedPython = $python.Replace('\', '/')
$expectedOpenSnapshot = $openSnapshot.Replace('\', '/')
$expectedPythonPath = $pythonPath.Replace('\', '/')
$effectiveArgs = @($serverConfig.transport.args)
$expectedArgs = @('-m', 'open_compute.mcp_server')
$effectiveTools = @($serverConfig.enabled_tools)
$profileValid = (
    ($serverConfig.enabled -eq $true) -and
    ($serverConfig.transport.command -eq $expectedPython) -and
    (($effectiveArgs -join '|') -eq ($expectedArgs -join '|')) -and
    ($serverConfig.transport.cwd -eq $expectedOpenSnapshot) -and
    ($serverConfig.transport.env.PYTHONPATH -eq $expectedPythonPath) -and
    ($serverConfig.transport.env.OC_SAFETY_MODE -eq 'confirm') -and
    ($serverConfig.transport.env.OC_SIGNAL_AUTO -eq 'off') -and
    ($effectiveTools.Count -eq 1) -and
    ($effectiveTools[0] -eq 'chat')
)
if (-not $profileValid) {
    throw "Codex-Profil-Readback stimmt nicht mit dem chat-only-Serververtrag überein."
}
if ($legacyServerExists) {
    $disabledProbe = & $codex.Source --profile $profileName mcp get open-compute --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Codex konnte den globalen Serverzustand im Profil nicht prüfen: $($disabledProbe -join "`n")"
    }
    $oldServerConfig = ($disabledProbe -join "`n") | ConvertFrom-Json
    if ($oldServerConfig.enabled -ne $false) {
        throw "Das Profil schaltet den alten globalen open-compute-Server nicht isoliert aus."
    }
} else {
    $absentProbe = & $codex.Source --profile $profileName mcp get open-compute --json 2>&1
    if ($LASTEXITCODE -eq 0) {
        throw "Das isolierte Profil registriert unerwartet einen globalen open-compute-Server."
    }
    $absentText = $absentProbe -join "`n"
    if ($absentText -notmatch "(?m)^Error: No MCP server named 'open-compute' found\.$") {
        throw "Codex konnte die Abwesenheit des alten Servers nicht bestätigen: $absentText"
    }
}

Write-Output "Profil geprüft: $profileName"
Write-Output "MCP-Server: open-compute-chat; einziges Werkzeug: chat"
Write-Output "Gepinnte Quelle: $openComputeCommit"
Write-Output "Gepinnte VoiceIO-Quelle: $voiceIoCommit"
if (-not $PrepareOnly) { Write-Output "Host-Arbeitsordner: $hostWorkspace" }
Write-Output "Codex-Basisconfig wurde nicht verändert: $(Join-Path $codexHome 'config.toml')"

if ($PrepareOnly) {
    Write-Output 'Vorbereitung abgeschlossen; kein Codex-Modellturn gestartet.'
    if ([string]::IsNullOrWhiteSpace($Workspace)) {
        Write-Output 'Start mit: .\scripts\open_compute_chat.ps1 -Model <verfügbares-Modell> -Workspace <gewählter-Arbeitsordner>'
    } else {
        Write-Output "Start mit: .\scripts\open_compute_chat.ps1 -Model <verfügbares-Modell> -Workspace `"$hostWorkspace`""
    }
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
    '--cd', $hostWorkspace,
    $prompt
)
& $codex.Source @codexArgs
exit $LASTEXITCODE
