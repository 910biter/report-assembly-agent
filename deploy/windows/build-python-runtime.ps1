param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$PythonVersion = '3.12'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
$output = [IO.Path]::GetFullPath($OutputDirectory)
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw 'uv is required on the build machine, not on target computers' }
if ((Test-Path -LiteralPath $output) -and (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1)) {
    throw "Output directory must be empty: $output"
}
$drive = [IO.Path]::GetPathRoot($output)
if ($drive -match '^[A-Za-z]:\\$') {
    $freeBytes = (Get-PSDrive -Name $drive.Substring(0, 1)).Free
    if ($freeBytes -lt 30GB) {
        throw "At least 30 GB of free space is required to build the Windows wheelhouse on $drive"
    }
}

New-Item -ItemType Directory -Path $output -Force | Out-Null
$uvCopy = Join-Path $output 'uv.exe'
Copy-Item -LiteralPath $uv.Source -Destination $uvCopy
$pythonHome = Join-Path $output 'python'
$wheelhouse = Join-Path $output 'wheels'
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null

# The private interpreter is not registered in Windows and never modifies PATH.
& $uv.Source python install $PythonVersion --install-dir $pythonHome --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Private Python download failed' }
$python = Get-ChildItem -LiteralPath $pythonHome -Filter python.exe -Recurse -File |
    Where-Object { $_.DirectoryName -notmatch '[\\/]Scripts$' } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $python) { throw 'Private Python executable not found' }

$lock = Join-Path $output 'requirements-windows.lock'
& $uv.Source pip compile (Join-Path $repo 'requirements.txt') `
    --python $python --python-platform windows `
    --constraints (Join-Path $PSScriptRoot 'constraints.txt') `
    --index https://download.pytorch.org/whl/cpu `
    --default-index https://pypi.org/simple `
    --index-strategy unsafe-best-match `
    --output-file $lock --quiet
if ($LASTEXITCODE -ne 0) { throw 'Windows dependency resolution failed' }

# Build source-only packages such as Whisper here, never on the offline target.
& $python -m pip wheel --no-deps --wheel-dir $wheelhouse `
    --index-url https://pypi.org/simple `
    --extra-index-url https://download.pytorch.org/whl/cpu `
    --requirement $lock
if ($LASTEXITCODE -ne 0) { throw 'Windows wheelhouse build failed' }

$validation = Join-Path $output 'validation'
& $uv.Source venv --seed --python $python $validation
if ($LASTEXITCODE -ne 0) { throw 'Validation environment creation failed' }
$validationPython = Join-Path $validation 'Scripts\python.exe'
& $uvCopy pip install --python $validationPython --no-index --find-links $wheelhouse -r $lock --quiet
if ($LASTEXITCODE -ne 0) { throw 'Offline wheelhouse installation failed' }
& $validationPython -c 'import docling, whisper, torch, onnxruntime, psycopg, qdrant_client; print(torch.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Windows native import smoke test failed' }
& $validationPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Windows dependency integrity check failed' }

Write-Output "Private Python: $python"
Write-Output "Offline wheelhouse: $wheelhouse"
Write-Output "Resolved lock: $lock"
