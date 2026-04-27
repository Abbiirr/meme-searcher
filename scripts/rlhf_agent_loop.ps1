param(
    [Parameter(Mandatory=$true)]
    [string]$EvalRunId,

    [string]$ClientSessionPrefix = "rlhf-agent",
    [string]$Operator = "codex-agent",
    [string]$WorkDir = "artifacts/feedback_agent/latest",
    [int]$TopK = 20,
    [int]$Repeats = 5,
    [switch]$ReplacePrefix
)

$ErrorActionPreference = "Stop"

$argsList = @(
    "-m", "vidsearch.feedback.agent_operator", "run",
    "--eval-run-id", $EvalRunId,
    "--work-dir", $WorkDir,
    "--client-session-prefix", $ClientSessionPrefix,
    "--operator", $Operator,
    "--top-k", "$TopK",
    "--repeats", "$Repeats",
    "--p0-g4-passing"
)

if ($ReplacePrefix) {
    $argsList += "--replace-prefix"
}

python @argsList
