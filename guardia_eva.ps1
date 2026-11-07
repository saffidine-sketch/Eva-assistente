# Guardia di Eva: da mettere nell'avvio automatico di Windows (lo fa eva_avvio_automatico.ps1 attiva).
# Aspetta rete e audio, controlla se Eva e' in esecuzione, la riavvia se no, e scrive tutto in guardia_eva.log.
$qui = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $qui 'guardia_eva.log'
$porta = 47625   # la stessa di dettatura.py (PORTA_ISTANZA_UNICA)

function Scrivi($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }
function EvaAttiva {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $porta); $c.Close(); return $true } catch { return $false }
}

Scrivi 'Guardia avviata.'
# Chiusura volontaria (pulsante X sulla sfera): non riavvio Eva e cancello il segnale, cosi' al prossimo avvio riparte tutto normale.
$flag = Join-Path $qui 'chiusura_volontaria.flag'
function ChiusaDaUtente {
    if (Test-Path $flag) {
        Remove-Item $flag -Force -ErrorAction SilentlyContinue
        Scrivi 'Trovato chiusura_volontaria.flag: Eva chiusa dall''utente. NON la riavvio. File segnale cancellato.'
        return $true
    }
    return $false
}
if (ChiusaDaUtente) { exit 0 }
$pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
if (-not $pyw) { Scrivi 'ERRORE: pythonw non trovato.'; exit 1 }

# 1) aspetto la rete (max 2 minuti) e il servizio audio (max 2 minuti)
$rete = $false
for ($i = 0; $i -lt 24; $i++) {
    try { [void][Net.Dns]::GetHostAddresses('api.groq.com'); $rete = $true; break } catch { Start-Sleep 5 }
}
Scrivi ("Rete: " + $(if ($rete) { 'pronta' } else { 'NON pronta dopo 2 minuti (Eva parte lo stesso)' }))
$audio = $false
for ($i = 0; $i -lt 24; $i++) {
    $s = Get-Service Audiosrv -ErrorAction SilentlyContinue
    if ($s -and $s.Status -eq 'Running') { $audio = $true; break }
    Start-Sleep 5
}
Scrivi ("Audio: " + $(if ($audio) { 'pronto' } else { 'NON pronto dopo 2 minuti' }))

# 2) controllo Eva e, se serve, la riavvio (fino a 3 tentativi)
for ($t = 1; $t -le 3; $t++) {
    # lascio a Eva il tempo di partire da sola dal collegamento: controllo ogni 2 secondi (max 30) ed esco appena risponde
    $attiva = $false
    for ($s = 0; $s -lt 15; $s++) {
        if (EvaAttiva) { $attiva = $true; break }
        Start-Sleep 2
    }
    if ($attiva) { Scrivi "Eva in esecuzione (controllo $t, dopo $($s * 2) s): OK."; break }
    if (ChiusaDaUtente) { exit 0 }
    Scrivi "Eva NON in esecuzione: la riavvio (tentativo $t)."
    Start-Process -FilePath $pyw -ArgumentList ('"' + (Join-Path $qui 'dettatura.py') + '"') -WorkingDirectory $qui
}
for ($s = 0; $s -lt 15 -and -not (EvaAttiva); $s++) { Start-Sleep 2 }
Scrivi ("Esito finale: Eva " + $(if (EvaAttiva) { 'ATTIVA' } else { 'NON ATTIVA: guarda eva.log' }))
