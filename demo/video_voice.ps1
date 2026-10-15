param([Parameter(Mandatory=$true)][string]$Storyboard,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$voiceType=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$streamType=[Windows.Media.SpeechSynthesis.SpeechSynthesisStream,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$voice=$voiceType::AllVoices | Where-Object { $_.Language -eq 'zh-CN' } | Select-Object -First 1
if (-not $voice) { throw 'Missing installed zh-CN voice; install Windows Chinese speech or supply recorded narration.' }
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$synth=[Activator]::CreateInstance($voiceType)
$synth.Voice=$voice
$plan=Get-Content -Raw -Encoding UTF8 -LiteralPath $Storyboard | ConvertFrom-Json
if (Test-Path -LiteralPath $Output) { throw 'Output already exists; preserve previous narration.' }
New-Item -ItemType Directory -Path $Output | Out-Null
try {
  for ($i=0;$i -lt $plan.scenes.Count;$i++) {
    for ($j=0;$j -lt $plan.scenes[$i].speech.Count;$j++) {
      $op=$synth.SynthesizeTextToStreamAsync([string]$plan.scenes[$i].speech[$j])
      $task=$asTask.MakeGenericMethod($streamType).Invoke($null,@($op))
      $task.GetAwaiter().GetResult() | Out-Null
      $speech=$task.Result
      $inputStream=[System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($speech)
      $filename=Join-Path $Output ('{0:D2}-{1:D2}.wav' -f $i,$j)
      $file=[IO.File]::Create($filename)
      try { $inputStream.CopyTo($file) } finally { $file.Dispose(); $inputStream.Dispose(); $speech.Dispose() }
    }
  }
  [IO.File]::WriteAllText((Join-Path $Output 'voice.json'),(@{voice=$voice.DisplayName;language=$voice.Language;synthetic=$true}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
} finally { $synth.Dispose() }
