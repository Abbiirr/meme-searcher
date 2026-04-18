param(
    [string]$ModelRoot = "K:\models\video_searcher"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Invoke-GitCloneOrPull {
    param(
        [string]$RepoUrl,
        [string]$TargetPath
    )

    if (Test-Path -LiteralPath (Join-Path $TargetPath ".git")) {
        git -C $TargetPath pull --ff-only
    } else {
        git clone $RepoUrl $TargetPath
    }
}

function Invoke-QwenCloneOrPull {
    param(
        [string]$RepoUrl,
        [string]$TargetPath
    )

    if (Test-Path -LiteralPath (Join-Path $TargetPath ".git")) {
        git -C $TargetPath fetch origin
        git -C $TargetPath reset --hard origin/main
    } else {
        git -c filter.lfs.smudge= -c filter.lfs.required=false clone $RepoUrl $TargetPath
    }

    git -C $TargetPath lfs pull --include="Qwen3VL-8B-Instruct-Q4_K_M.gguf,mmproj-Qwen3VL-8B-Instruct-F16.gguf"
}

git lfs install | Out-Null

$dirs = @(
    $ModelRoot,
    (Join-Path $ModelRoot "asr"),
    (Join-Path $ModelRoot "embeddings"),
    (Join-Path $ModelRoot "ocr"),
    (Join-Path $ModelRoot "vlm")
)

foreach ($dir in $dirs) {
    Ensure-Directory -Path $dir
}

$repos = @(
    @{
        Url = "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3"
        Path = (Join-Path $ModelRoot "asr\parakeet-tdt-0.6b-v3")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/Systran/faster-whisper-large-v3"
        Path = (Join-Path $ModelRoot "asr\faster-whisper-large-v3")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/BAAI/bge-m3"
        Path = (Join-Path $ModelRoot "embeddings\bge-m3")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/google/siglip2-so400m-patch16-384"
        Path = (Join-Path $ModelRoot "embeddings\siglip2-so400m-patch16-384")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual"
        Path = (Join-Path $ModelRoot "embeddings\jina-reranker-v2-base-multilingual")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det"
        Path = (Join-Path $ModelRoot "ocr\PP-OCRv5_server_det")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/PaddlePaddle/PP-OCRv5_server_rec"
        Path = (Join-Path $ModelRoot "ocr\PP-OCRv5_server_rec")
        Type = "full"
    },
    @{
        Url = "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF"
        Path = (Join-Path $ModelRoot "vlm\Qwen3-VL-8B-Instruct-GGUF")
        Type = "qwen_partial"
    }
)

foreach ($repo in $repos) {
    Write-Host "Processing $($repo.Url) -> $($repo.Path)"
    if ($repo.Type -eq "qwen_partial") {
        Invoke-QwenCloneOrPull -RepoUrl $repo.Url -TargetPath $repo.Path
    } else {
        Invoke-GitCloneOrPull -RepoUrl $repo.Url -TargetPath $repo.Path
    }
}

Write-Host ""
Write-Host "Download roots:"
Get-ChildItem -LiteralPath $ModelRoot -Directory | Select-Object Name, FullName | Format-Table -AutoSize
