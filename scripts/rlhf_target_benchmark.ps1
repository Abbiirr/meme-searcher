param(
    [string]$Folder = "data/meme_rlhf",
    [string]$Pack = "artifacts/feedback_targets/target_pack.jsonl",
    [string]$Prompts = "artifacts/feedback_targets/target_prompts.jsonl",
    [string]$AgentPrompt = "artifacts/feedback_targets/agent_prompt.md",
    [string]$Output = "artifacts/feedback_targets/results.jsonl",
    [string]$MissesOutput = "artifacts/feedback_targets/target_not_found.jsonl",
    [string]$ClientSessionPrefix = "rlhf-target",
    [string]$Operator = "codex-agent",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$GatewayUrl = $env:LITELLM_URL,
    [string]$GatewayApiKey = $env:LITELLM_MASTER_KEY,
    [string]$GatewayModel = "qwen3.6-vlm-wrapper",
    [string]$GatewayTextModel = "fast",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$VisionModel = "maternion/Qianfan-OCR:4b",
    [int]$TopK = 20,
    [int]$Limit = 0,
    [int]$PromptsPerImage = 5,
    [int]$PromptBatchSize = 1,
    [switch]$BuildPack,
    [switch]$WriteAgentPrompt,
    [switch]$GeneratePrompts,
    [switch]$GeneratePromptsFromMetadata,
    [switch]$GeneratePromptsOllama,
    [switch]$ResumePrompts,
    [switch]$ReplacePrefix,
    [switch]$ValidateOnly,
    [switch]$Train
)

$ErrorActionPreference = "Stop"

if (-not $GatewayUrl) {
    $GatewayUrl = "http://127.0.0.1:4100"
}

if ($BuildPack) {
    $buildArgs = @(
        "-m", "vidsearch.feedback.target_benchmark", "build-target-pack",
        "--folder", $Folder,
        "--output", $Pack
    )
    if ($Limit -gt 0) {
        $buildArgs += @("--limit", "$Limit")
    }
    python @buildArgs
}

if ($WriteAgentPrompt) {
    python -m vidsearch.feedback.target_benchmark write-target-prompt `
        --pack $Pack `
        --output $AgentPrompt `
        --labels-output $Prompts `
        --prompts-per-image $PromptsPerImage
}

if ($GeneratePrompts) {
    $generateArgs = @(
        "-m", "vidsearch.feedback.target_benchmark", "generate-prompts-gateway",
        "--pack", $Pack,
        "--output", $Prompts,
        "--model", $GatewayModel,
        "--gateway-url", $GatewayUrl,
        "--prompts-per-image", "$PromptsPerImage"
    )
    if ($GatewayApiKey) {
        $generateArgs += @("--api-key", $GatewayApiKey)
    }
    if ($Limit -gt 0) {
        $generateArgs += @("--limit", "$Limit")
    }
    if ($ResumePrompts) {
        $generateArgs += "--resume"
    }
    python @generateArgs
}

if ($GeneratePromptsFromMetadata) {
    $generateArgs = @(
        "-m", "vidsearch.feedback.target_benchmark", "generate-prompts-metadata-gateway",
        "--pack", $Pack,
        "--output", $Prompts,
        "--model", $GatewayTextModel,
        "--gateway-url", $GatewayUrl,
        "--prompts-per-image", "$PromptsPerImage",
        "--batch-size", "$PromptBatchSize"
    )
    if ($GatewayApiKey) {
        $generateArgs += @("--api-key", $GatewayApiKey)
    }
    if ($Limit -gt 0) {
        $generateArgs += @("--limit", "$Limit")
    }
    if ($ResumePrompts) {
        $generateArgs += "--resume"
    }
    python @generateArgs
}

if ($GeneratePromptsOllama) {
    $generateArgs = @(
        "-m", "vidsearch.feedback.target_benchmark", "generate-prompts-ollama",
        "--pack", $Pack,
        "--output", $Prompts,
        "--model", $VisionModel,
        "--ollama-url", $OllamaUrl,
        "--prompts-per-image", "$PromptsPerImage"
    )
    if ($Limit -gt 0) {
        $generateArgs += @("--limit", "$Limit")
    }
    if ($ResumePrompts) {
        $generateArgs += "--resume"
    }
    python @generateArgs
}

$argsList = @(
    "-m", "vidsearch.feedback.target_benchmark", "run-target-searches",
    "--pack", $Pack,
    "--prompts", $Prompts,
    "--output", $Output,
    "--misses-output", $MissesOutput,
    "--client-session-prefix", $ClientSessionPrefix,
    "--operator", $Operator,
    "--api-base-url", $ApiBaseUrl,
    "--top-k", "$TopK"
)

if ($ReplacePrefix) {
    $argsList += "--replace-prefix"
}

if ($ValidateOnly) {
    $argsList += "--validate-only"
}

python @argsList

if ($Train -and -not $ValidateOnly) {
    python -m vidsearch.feedback.backfill_pairs
    python -m vidsearch.feedback.train_ranker --output artifacts/feedback_rankers/latest.json --approve-promotion --p0-g4-passing
    python -m vidsearch.feedback.evaluate_ranker --artifact artifacts/feedback_rankers/latest.json --output artifacts/feedback_eval/latest.json --changed-report-prefix artifacts/feedback_eval/latest_changed
}
