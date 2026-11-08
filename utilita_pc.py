"""Parte "sistema" delle utilità di Eva: batteria, musica, luminosità, Wi-Fi e Bluetooth.

Niente librerie in più: usa psutil (già in requirements.txt), le API di Windows via PowerShell (WinRT: lettore
multimediale e radio) e dxva2.dll via ctypes (luminosità dei monitor esterni, DDC/CI).

Ogni funzione restituisce un dizionario semplice e NON parla: a dire l'esito pensa utilita.py. Se il PC non ha
quella funzione (un desktop non ha la batteria, un monitor può non accettare la luminosità) lo dice nel dizionario.
"""
import base64
import ctypes
import json
import subprocess
import time
from ctypes import wintypes

TIMEOUT_POWERSHELL = 25

# Pezzo comune dei comandi WinRT di PowerShell: aspetta un'operazione asincrona di Windows.
_PREAMBOLO = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $tipo) { $t = $asTaskGeneric.MakeGenericMethod($tipo).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
"""


def _powershell(script, timeout=TIMEOUT_POWERSHELL):
    """Esegue uno script PowerShell (nascosto) e restituisce il JSON che stampa. Mai un'eccezione: {"errore": ...}."""
    codificato = base64.b64encode((_PREAMBOLO + script).encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", codificato],
                           capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"errore": "timeout"}
    except OSError as e:
        return {"errore": f"powershell: {e}"}
    uscita = r.stdout.decode("utf-8", "replace").strip()
    for riga in reversed(uscita.splitlines()):
        riga = riga.strip()
        if riga.startswith("{"):
            try:
                return json.loads(riga)
            except ValueError:
                break
    return {"errore": (r.stderr.decode("utf-8", "replace").strip() or uscita or "nessuna risposta")[:200]}


# ======================================================================
# BATTERIA
# ======================================================================
def batteria():
    """{"presente": False} | {"presente": True, "percentuale": 76, "in_carica": False, "secondi": 7800 | None}"""
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception as e:                                   # noqa: BLE001
        return {"presente": False, "errore": str(e)}
    if b is None:
        return {"presente": False}
    secondi = b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None
    return {"presente": True, "percentuale": round(b.percent), "in_carica": bool(b.power_plugged), "secondi": secondi}


# ======================================================================
# MUSICA (il lettore che Windows considera "in primo piano": Spotify, YouTube nel browser, Media Player...)
# ======================================================================
_SCRIPT_MUSICA = r"""
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
$m = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$s = $m.GetCurrentSession()
function Titolo($sess) { try { $p = Await ($sess.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]); return [string]$p.Title } catch { return '' } }
if ($null -eq $s) { '{"esito":"nessun_lettore"}'; exit }
$stato = [string]$s.GetPlaybackInfo().PlaybackStatus
$azione = '__AZIONE__'
$ok = $true
if ($azione -eq 'pausa') {
    if ($stato -eq 'Playing') { $ok = Await ($s.TryPauseAsync()) ([bool]) } else { @{esito='non_in_riproduzione'; stato=$stato; titolo=(Titolo $s)} | ConvertTo-Json -Compress; exit }
} elseif ($azione -eq 'riprendi') {
    if ($stato -eq 'Playing') { @{esito='gia_in_riproduzione'; stato=$stato; titolo=(Titolo $s)} | ConvertTo-Json -Compress; exit }
    $ok = Await ($s.TryPlayAsync()) ([bool])
} elseif ($azione -eq 'avanti') { $ok = Await ($s.TrySkipNextAsync()) ([bool]); Start-Sleep -Milliseconds 900 }
elseif ($azione -eq 'indietro') { $ok = Await ($s.TrySkipPreviousAsync()) ([bool]); Start-Sleep -Milliseconds 900 }
elseif ($azione -eq 'stato') { }
@{esito=$(if ($ok) { 'ok' } else { 'rifiutato' }); stato=$stato; titolo=(Titolo $s); app=[string]$s.SourceAppUserModelId} | ConvertTo-Json -Compress
"""


def musica(azione):
    """azione: "pausa" | "riprendi" | "avanti" | "indietro" | "stato".
    {"esito": "nessun_lettore" | "ok" | "rifiutato" | "non_in_riproduzione" | "gia_in_riproduzione", "titolo": ...}"""
    if azione not in ("pausa", "riprendi", "avanti", "indietro", "stato"):
        return {"errore": "azione sconosciuta"}
    return _powershell(_SCRIPT_MUSICA.replace("__AZIONE__", azione))


# ======================================================================
# LUMINOSITÀ: schermo del portatile (WMI) oppure monitor esterno che accetta DDC/CI (dxva2)
# ======================================================================
_SCRIPT_LUM_LEGGI = r"""
try { $b = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1
      @{ok=$true; valore=[int]$b.CurrentBrightness} | ConvertTo-Json -Compress } catch { '{"ok":false}' }
"""
_SCRIPT_LUM_IMPOSTA = r"""
try { Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=__VALORE__} | Out-Null
      '{"ok":true}' } catch { '{"ok":false}' }
"""


class _MonitorFisico(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("handle", wintypes.HANDLE), ("descrizione", wintypes.WCHAR * 128)]


def _monitor_ddc(operazione, valore=None):
    """Legge o imposta la luminosità del primo monitor esterno con DDC/CI. -> (ok, valore_attuale, minimo, massimo)"""
    try:
        user32, dxva2 = ctypes.windll.user32, ctypes.windll.dxva2
        trovati = []
        callback_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def cb(hmon, hdc, rect, lparam):
            trovati.append(hmon)
            return True
        user32.EnumDisplayMonitors(None, None, callback_t(cb), 0)
        for hmon in trovati:
            n = wintypes.DWORD(0)
            if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or n.value == 0:
                continue
            fisici = (_MonitorFisico * n.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, fisici):
                continue
            try:
                for m in fisici:
                    mi, cur, ma = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                    if not dxva2.GetMonitorBrightness(m.handle, ctypes.byref(mi), ctypes.byref(cur), ctypes.byref(ma)):
                        continue
                    if operazione == "imposta":
                        nuovo = int(mi.value + (ma.value - mi.value) * valore / 100)
                        if not dxva2.SetMonitorBrightness(m.handle, nuovo):
                            return False, cur.value, mi.value, ma.value
                        cur = wintypes.DWORD(nuovo)
                    return True, cur.value, mi.value, ma.value
            finally:
                dxva2.DestroyPhysicalMonitors(n.value, fisici)
    except Exception:                                        # noqa: BLE001
        pass
    return False, None, None, None


def luminosita_leggi():
    """{"supportata": True, "percentuale": 60, "via": "wmi"|"ddc"} | {"supportata": False}"""
    r = _powershell(_SCRIPT_LUM_LEGGI, timeout=15)
    if r.get("ok"):
        return {"supportata": True, "percentuale": int(r["valore"]), "via": "wmi"}
    for tentativo in range(3):                               # il DDC/CI dei monitor fallisce a volte: riprovo
        ok, cur, mi, ma = _monitor_ddc("leggi")
        if ok and ma is not None and ma > mi:
            return {"supportata": True, "percentuale": round((cur - mi) * 100 / (ma - mi)), "via": "ddc"}
        time.sleep(0.3)
    return {"supportata": False}


def luminosita_imposta(percentuale):
    """Imposta la luminosità (0-100). {"ok": True, "percentuale": n} | {"ok": False, "supportata": False}"""
    percentuale = max(0, min(100, int(percentuale)))
    attuale = luminosita_leggi()
    if not attuale.get("supportata"):
        return {"ok": False, "supportata": False}
    if attuale["via"] == "wmi":
        r = _powershell(_SCRIPT_LUM_IMPOSTA.replace("__VALORE__", str(percentuale)), timeout=15)
        return {"ok": bool(r.get("ok")), "supportata": True, "percentuale": percentuale}
    ok = False
    for tentativo in range(3):
        ok, cur, mi, ma = _monitor_ddc("imposta", percentuale)
        if ok:
            break
        time.sleep(0.3)
    return {"ok": ok, "supportata": True, "percentuale": percentuale}


# ======================================================================
# WI-FI e BLUETOOTH (le "radio" di Windows: non servono i diritti di amministratore)
# ======================================================================
_SCRIPT_RADIO = r"""
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r = $radios | Where-Object { [string]$_.Kind -eq '__TIPO__' } | Select-Object -First 1
if ($null -eq $r) { '{"presente":false}'; exit }
$azione = '__AZIONE__'
if ($azione -eq 'stato') { @{presente=$true; stato=[string]$r.State; nome=[string]$r.Name} | ConvertTo-Json -Compress; exit }
$acc = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ([string]$acc -ne 'Allowed') { @{presente=$true; esito='accesso_negato'; stato=[string]$r.State} | ConvertTo-Json -Compress; exit }
$nuovo = if ($azione -eq 'accendi') { [Windows.Devices.Radios.RadioState]::On } else { [Windows.Devices.Radios.RadioState]::Off }
$res = Await ($r.SetStateAsync($nuovo)) ([Windows.Devices.Radios.RadioAccessStatus])
Start-Sleep -Milliseconds 700
$radios2 = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$r2 = $radios2 | Where-Object { [string]$_.Kind -eq '__TIPO__' } | Select-Object -First 1
@{presente=$true; esito=[string]$res; stato=[string]$r2.State} | ConvertTo-Json -Compress
"""
_TIPI_RADIO = {"wifi": "WiFi", "bluetooth": "Bluetooth"}


def radio(quale, azione):
    """quale: "wifi" | "bluetooth"; azione: "stato" | "accendi" | "spegni".
    {"presente": bool, "stato": "On"|"Off"|"Disabled", "esito": "Allowed"|"accesso_negato"|...}"""
    if quale not in _TIPI_RADIO or azione not in ("stato", "accendi", "spegni"):
        return {"errore": "richiesta sconosciuta"}
    return _powershell(_SCRIPT_RADIO.replace("__TIPO__", _TIPI_RADIO[quale]).replace("__AZIONE__", azione))
