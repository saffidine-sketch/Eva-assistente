"""Scelta del servizio IA per la chat di Eva: "groq" (predefinito) oppure "deepseek".
Si cambia con una sola impostazione, PROVIDER in dettatura.py. Con "groq" tutto resta com'era prima.

DeepSeek usa il formato OpenAI (pacchetto "openai"). La chiave si legge dalla variabile d'ambiente
DEEPSEEK_API_KEY; in alternativa si può scrivere in dettatura.py (DEEPSEEK_API_KEY), ma NON se pubblichi
quel file su GitHub.
"""
import os

PROVIDER = "groq"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELLO = "deepseek-flash"
_chiave_config = ""


def configura(imp):
    """Legge PROVIDER e DEEPSEEK_API_KEY dalle impostazioni di dettatura.py."""
    global PROVIDER, _chiave_config
    p = str(imp.get("PROVIDER", "groq")).strip().lower()
    PROVIDER = p if p in ("groq", "deepseek") else "groq"
    _chiave_config = str(imp.get("DEEPSEEK_API_KEY", "") or "").strip()


def e_deepseek():
    return PROVIDER == "deepseek"


def nome():
    return "DeepSeek" if e_deepseek() else "Groq"


def nome_variabile():
    return "DEEPSEEK_API_KEY" if e_deepseek() else "GROQ_API_KEY"


def chiave():
    """La chiave del servizio scelto (None se manca)."""
    if e_deepseek():
        return os.environ.get("DEEPSEEK_API_KEY") or _chiave_config or None
    return os.environ.get("GROQ_API_KEY")


def crea_client(timeout, max_retries):
    """Il client del servizio scelto (stessa interfaccia: client.chat.completions.create)."""
    if e_deepseek():
        from openai import OpenAI
        return OpenAI(api_key=chiave(), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=max_retries)
    from groq import Groq
    return Groq(api_key=chiave(), timeout=timeout, max_retries=max_retries)


def modelli(principale, riserva=None):
    """Elenco dei modelli da provare. Groq: quelli di sempre. DeepSeek: solo deepseek-flash."""
    if e_deepseek():
        return (DEEPSEEK_MODELLO,)
    return (principale,) if riserva is None else (principale, riserva)


def limite(n):
    """Il parametro del limite di lunghezza: ha un nome diverso nei due servizi."""
    return {"max_tokens": n} if e_deepseek() else {"max_completion_tokens": n}


def errori_rete():
    """Le eccezioni che significano 'internet assente o timeout'."""
    try:
        if e_deepseek():
            import openai
            return (openai.APIConnectionError,)
        import groq
        return (groq.APIConnectionError,)
    except Exception:
        return ()


def host():
    return "api.deepseek.com" if e_deepseek() else "api.groq.com"
