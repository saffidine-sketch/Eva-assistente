# Eva 2.1

Eva è un'assistente vocale per Windows: la senti parlare, ti risponde a voce, apre programmi e cartelle,
cerca sul web, gestisce il volume, prepara bozze di mail, dice il meteo e fa due chiacchiere.
Ha una sfera animata sullo schermo e parla in italiano, francese, spagnolo e inglese.

Eva è un regalo gratuito e usa **solo Groq**, che ha un piano gratuito (vedi sotto).

## Novità della 2.1: la modalità compagna

Prima Eva stava a riposo finché non dicevi "Eva" o tenevi premuto F9. Con la modalità compagna:

- **ascolta in modo continuo**, senza F9 e senza ripetere "Eva" ogni volta;
- **aspetta che tu abbia finito di parlare** e solo dopo risponde, in modo naturale, come una persona
  e non come un assistente a comandi;
- ricorda quello che hai appena detto (per esempio "aprila" dopo una ricerca su YouTube);
- se in mezzo al discorso le dai un ordine ("apri Chrome"), lo esegue; se non è sicura di aver capito,
  te lo chiede con naturalezza invece di rispondere "comando non riconosciuto";
- mentre Eva parla, o il PC riproduce audio (video, musica), non ascolta, così non si confonde;
- scarta le frasi sentite male (televisione, rumore) invece di risponderci a caso.

"Chiudi", "basta" o "ciao" la fanno solo tacere un momento. Per fermarla del tutto di' **"spegni Eva"**.

Si attiva o disattiva con una sola riga in `dettatura.py`:

```python
COMPAGNA = True    # False = come prima: a riposo, si sveglia con "Eva" o con F9
```

## Cosa serve

- Windows 10 o 11
- Python 3.12
- un microfono
- una chiave gratuita di Groq (qui sotto)

## Installazione

1. Scarica o clona questa cartella.
2. Apri PowerShell nella cartella ed esegui:

   ```powershell
   pip install -r requirements.txt
   ```

3. Crea la tua chiave gratuita di Groq (vedi sotto).
4. Avvia Eva facendo doppio clic su `avvia_eva.bat`.
5. Al primo avvio compare la **schermata iniziale**: scrivi il tuo nome (Eva ti chiamerà così) e incolla la chiave
   di Groq. Premi **Avvia Eva**.

Il primo avvio scarica i modelli di riconoscimento vocale e può richiedere qualche minuto.
Eva scrive i suoi errori in `eva.log`, nella stessa cartella.

Per cambiare nome o chiave in seguito: chiudi Eva (icona accanto all'orologio, **Esci**), apri
`cambia_nome_e_chiave.bat` e poi riavvia Eva con `avvia_eva.bat`.

## Come creare la chiave gratuita di Groq

La chiave serve a Eva per capire gli ordini e chiacchierare. Il piano gratuito ha dei limiti di utilizzo:
se li superi, Eva te lo dice a voce e puoi riprovare più tardi.

1. Vai su **https://console.groq.com** e crea un account (o accedi).
2. Nel menu apri **API Keys** e premi **Create API Key**.
3. Dai un nome alla chiave (per esempio "Eva") e copiala subito: Groq la mostra **una sola volta**.
4. Incollala nella schermata iniziale di Eva, nel campo "chiave".

**Dove finisce la chiave:** Eva non la scrive in nessun file. La salva come variabile d'ambiente del tuo utente
Windows (`GROQ_API_KEY`), come farebbe il comando `setx`, e non compare nemmeno nei registri. Se preferisci, puoi
impostarla tu a mano con `setx GROQ_API_KEY "gsk_..."` in PowerShell: Eva la trova lo stesso.
Non pubblicare mai la tua chiave: chiunque la veda può usare il tuo account Groq.

## Primi comandi

- "Eva, che tempo fa oggi?"
- "Apri Chrome" / "chiudi Chrome"
- "Cerca ricette di pasta" / "cerca su YouTube ..."
- "Alza il volume" / "volume al 30"
- "Scrivi una mail a Marco oggetto ciao" (prepara solo la bozza, non la invia mai)
- "Eva, cosa sai fare?" per l'elenco completo

Per la documentazione completa leggi `LEGGIMI.txt` e `aiuto_eva.txt`.

## Avvio automatico con il PC

`attiva_avvio_automatico.bat` fa partire Eva (e la sua guardia, che la riavvia se si blocca) all'accensione
del PC. `disattiva_avvio_automatico.bat` lo toglie.

## Licenza

Eva è software libero con licenza MIT: vedi il file `LICENSE`.

## Privacy

Il riconoscimento vocale (Whisper) gira sul tuo PC. A Groq vanno solo le frasi trascritte, non l'audio.
Eva non cancella mai file e le mail restano bozze.
