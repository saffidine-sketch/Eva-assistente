"""Modalità vista di Eva: "Eva, guarda" -> screenshot dello schermo, ridotto, mandato a un modello con immagini.

Questo modulo non parla: riconosce la frase, fa lo screenshot e restituisce il testo della risposta (la voce la
mette vox_core). L'immagine viene mandata a Groq (stessa chiave GROQ_API_KEY) e non viene salvata su disco.
"""
import base64
import io
import logging
import re

MODELLO_VISTA = "qwen/qwen3.8-27b"   # modello Groq che accetta immagini (provato): se Groq lo ritira, cambialo qui
LATO_MAX = 1024               # lato lungo dello screenshot dopo la riduzione (pixel)
QUALITA_JPEG = 70
TIMEOUT = 30
MAX_TOKEN = 300
DOMANDA_STANDARD = "Descrivi in breve cosa vedi sullo schermo."

_log = logging.getLogger("eva")
_client = None

_ISTRUZIONE = ("Sei Eva, un'assistente vocale. Ti arriva lo screenshot dello schermo dell'utente e la sua domanda. "
               "Rispondi in italiano, in una o due frasi brevi, parlate e senza elenchi, markdown o simboli. "
               "Se la domanda non c'entra con lo schermo, rispondi comunque brevemente.")

# "guarda", "guarda lo schermo", "guarda questo, cos'è?", "cosa vedi", "dimmi cosa vedi sullo schermo"
_RE_GUARDA = re.compile(r"^\s*(?:ehi\s+)?guarda(?:mi)?\b[\s,.:!?]*(?P<resto>.*)$", re.I | re.S)
_RE_VEDI = re.compile(r"\b(?:cosa|che cosa|che)\s+vedi\b|\bdescrivi\s+(?:lo\s+)?schermo\b|\bleggi\s+(?:lo\s+)?schermo\b", re.I)
# "guarda un video su YouTube", "guarda la partita": sono comandi, non una domanda sullo schermo
_RE_COMANDO = re.compile(r"^(?:un|una|il|la|lo|i|gli|le|dei|delle)\s|\b(?:su|sul|sulla)\s+(?:youtube|netflix|google)\b", re.I)


def riconosci(frase):
    """Se la frase è per la modalità vista, restituisce la domanda (anche standard); altrimenti None."""
    f = (frase or "").strip()
    m = _RE_GUARDA.match(f)
    if m:
        resto = m.group("resto").strip()
        if not resto:
            return DOMANDA_STANDARD
        resto = re.sub(r"^(?:lo\s+schermo|qui|questo|quest[ao])\b[\s,.:!?]*", "", resto, flags=re.I).strip()
        if resto and _RE_COMANDO.search(resto):
            return None
        return resto or DOMANDA_STANDARD
    if _RE_VEDI.search(f):
        return f
    return None


def screenshot_base64():
    """Cattura lo schermo intero (tutti i monitor), lo riduce e lo restituisce come JPEG in base64."""
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True).convert("RGB")
    img.thumbnail((LATO_MAX, LATO_MAX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=QUALITA_JPEG)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _client_groq():
    global _client
    if _client is None:
        import os
        from groq import Groq
        chiave = os.environ.get("GROQ_API_KEY")
        if not chiave:
            raise RuntimeError("GROQ_API_KEY non trovata")
        _client = Groq(api_key=chiave, timeout=TIMEOUT, max_retries=1)
    return _client


def guarda(domanda):
    """Screenshot + domanda al modello. Restituisce il testo della risposta, o None se qualcosa non va (già loggato)."""
    try:
        immagine = screenshot_base64()
    except Exception:
        _log.exception("Vista: non riesco a fare lo screenshot")
        return None
    try:
        esito = _client_groq().chat.completions.create(
            model=MODELLO_VISTA, max_completion_tokens=MAX_TOKEN,
            messages=[{"role": "system", "content": _ISTRUZIONE},
                      {"role": "user", "content": [
                          {"type": "text", "text": domanda},
                          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + immagine}}]}])
        return (esito.choices[0].message.content or "").strip() or None
    except Exception:
        _log.exception("Vista: chiamata al modello con immagini fallita (rete, chiave, limite o modello ritirato)")
        return None
