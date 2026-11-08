# Attiva o disattiva l'avvio automatico di Eva all'accensione del computer.
# Crea (o toglie) il collegamento "Eva.lnk" nella cartella di Esecuzione automatica di Windows.
# Toglie anche il vecchio collegamento "Vox.lnk" (quando l'assistente si chiamava Vox),
# così non partono mai due copie insieme.
#   .\eva_avvio_automatico.ps1 attiva
#   .\eva_avvio_automatico.ps1 disattiva
#   .\eva_avvio_automatico.ps1 stato
param(
    [ValidateSet('attiva', 'disattiva', 'stato')][string]$Azione = 'stato',
    [string]$Cartella = [Environment]::GetFolderPath('Startup')   # solo per le prove
)

$qui = Split-Path -Parent $MyInvocation.MyCommand.Path
$collegamento = Join-Path $Cartella 'Eva.lnk'
$vecchio = Join-Path $Cartella 'Vox.lnk'

switch ($Azione) {
    'attiva' {
        $exe = Join-Path $qui 'Eva.exe'
        if (Test-Path $exe) {                       # Eva installata: parte Eva.exe, senza Python
            $pyw = $exe
            $argEva = ''
        } else {                                    # da sorgente: pythonw dettatura.py
            $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
            if (-not (Test-Path $pyw)) {
                $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
            }
            $argEva = '"' + (Join-Path $qui 'dettatura.py') + '"'
        }
        if (-not $pyw) { Write-Host 'pythonw non trovato: controlla che Python sia installato.'; exit 1 }
        if (Test-Path $vecchio) {
            Remove-Item $vecchio
            Write-Host 'Tolto il vecchio collegamento Vox.lnk (ora c''e'' Eva.lnk).'
        }
        $ws = New-Object -ComObject WScript.Shell
        $s = $ws.CreateShortcut($collegamento)
        $s.TargetPath = $pyw
        $s.Arguments = $argEva
        $s.WorkingDirectory = $qui
        $s.WindowStyle = 7          # minimizzata: nessuna finestra
        $s.Description = 'Eva - assistente vocale'
        $s.Save()
        Write-Host "Avvio automatico ATTIVATO: $collegamento"
        # Guardia: aspetta rete e audio e riavvia Eva se non e' in esecuzione (scrive guardia_eva.log)
        $g = $ws.CreateShortcut((Join-Path $Cartella 'Eva Guardia.lnk'))
        $g.TargetPath = 'powershell.exe'
        $g.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $qui 'guardia_eva.ps1') + '"'
        $g.WorkingDirectory = $qui
        $g.WindowStyle = 7
        $g.Save()
        Write-Host 'Guardia ATTIVATA: Eva Guardia.lnk'
    }
    'disattiva' {
        $tolto = $false
        foreach ($f in @($collegamento, $vecchio, (Join-Path $Cartella 'Eva Guardia.lnk'))) {
            if (Test-Path $f) { Remove-Item $f; $tolto = $true }
        }
        if ($tolto) { Write-Host 'Avvio automatico DISATTIVATO.' } else { Write-Host 'Era gia disattivato.' }
    }
    'stato' {
        if (Test-Path $collegamento) { Write-Host "Avvio automatico: ATTIVO ($collegamento)" }
        elseif (Test-Path $vecchio) { Write-Host "Avvio automatico: ATTIVO con il vecchio nome Vox ($vecchio): usa 'attiva' per passare a Eva" }
        else { Write-Host 'Avvio automatico: NON attivo' }
    }
}
