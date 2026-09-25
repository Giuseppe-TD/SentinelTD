# Sentinel TD

**Monitoraggio e manutenzione self-hosted per siti WordPress e Joomla** — un pannello che
controlla i siti, applica gli aggiornamenti, tiene d'occhio le scadenze e ti racconta cos'è
successo. Sul tuo server, coi tuoi dati, col tuo marchio.

🇬🇧 [Read in English](README.md)

---

## Funzionalità

**Monitoraggio**
- Controlli di raggiungibilità con **conferma prima di gridare al lupo**: un sito risulta
  offline solo dopo più check falliti, così un singolo intoppo non ti sveglia di notte
- Versioni di core, plugin, temi e traduzioni per ogni sito, tramite i connettori inclusi
- **Anteprima visiva di ogni sito**, rigenerata a intervalli, con le miniature nell'elenco
- Cartelle (clienti), tag, filtri rapidi, ricerca ed esportazione CSV

**Aggiornamenti**
- Aggiorni un sito, una selezione o tutto il parco — a mano o con il **ciclo notturno**
  che ritenta da solo quello che è fallito
- **Installazione e rimozione in blocco** su più siti, guidata passo passo: piattaforma,
  pacchetto o estensione, poi i siti di destinazione anche per cartella. La rimozione
  raggiunge solo i siti che hanno davvero quell'estensione
- Storico degli aggiornamenti con, per ogni componente, quante volte è stato aggiornato e
  da quale versione a quale

**Sicurezza e scadenze**
- Vulnerabilità note confrontate con le estensioni realmente installate, con gravità e
  versione che risolve
- **Scadenza dei domini** via RDAP con ripiego su WHOIS, e avvisi alle soglie che decidi tu
- Rinnovi di licenze e abbonamenti (temi, plugin, hosting) con periodicità e il pulsante
  *Rinnovata* che sposta la data avanti di un periodo

**Report e notifiche**
- Email e Telegram per ogni evento, con **testi modificabili**, anteprima dal vivo e invio
  di prova
- **Report mensile in PDF**, globale o uno per cartella, inviato in automatico all'indirizzo
  che imposti
- **Report dettagliati su richiesta** (PDF o CSV) per un sito, una selezione o tutto, su un
  intervallo di mesi a scelta, con la cronologia di ogni singolo aggiornamento
- Statistiche con viste giornaliera, mensile e confronto fra due mesi

**Amministrazione**
- Accesso con password, **TOTP** e **passkey**
- Personalizzazione: il tuo logo e la tua favicon nel pannello, nei PDF e nelle email
- Interfaccia in **italiano, inglese, francese e tedesco**
- Pacchetti dei connettori generati dal pannello stesso, già col tuo indirizzo e la tua chiave

---

## Requisiti

- Un server Linux con **Docker** e il plugin **Docker Compose**
- Un reverse proxy con HTTPS davanti al pannello (Nginx Proxy Manager, Traefik, Caddy, Nginx…)
- Accesso a internet in uscita, per raggiungere i siti monitorati e le fonti di vulnerabilità
- RAM per Chromium e la generazione dei PDF: 2 GB stanno comodi con qualche decina di siti

| Porta | Protocollo | Cosa |
|-------|------------|------|
| `HOST_PORT` (default 8810) | TCP | Pannello web — dietro il reverse proxy |

Tutto il resto (PostgreSQL, Redis, il servizio screenshot) resta sulla rete interna di Docker
e non viene mai esposto.

---

## Installazione

```bash
git clone https://github.com/Giuseppe-TD/sentinel-td.git
cd sentinel-td

# 1. Configurazione
cp .env.example .env
nano .env        # almeno: POSTGRES_PASSWORD, JWT_SECRET, ADMIN_PASSWORD, TZ

# 2. Avvio
docker compose up -d --build
docker compose logs -f api worker
```

Genera i segreti con:

```bash
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_hex(32))"
```

`ADMIN_PASSWORD` deve avere almeno 12 caratteri: serve solo a creare il primo amministratore,
poi la password vive nel database e si cambia dal pannello.

Apri il pannello (`http://localhost:8810` per una prova locale) ed entra come `admin`.

### Con il reverse proxy su un'altra macchina

La porta è legata al loopback per impostazione predefinita. Se il proxy gira altrove nella
tua rete:

```dotenv
BIND_ADDRESS=0.0.0.0    # poi limita la porta 8810 all'indirizzo del proxy con un firewall
TZ=Europe/Rome          # update notturni, report e confini dei mesi seguono questo fuso
DEFAULT_UI_LANGUAGE=it  # lingua di email e PDF generati dal server
```

Per le passkey, `WEBAUTHN_RP_ID` è il solo nome host e `WEBAUTHN_ORIGIN` l'origine HTTPS esatta:

```dotenv
WEBAUTHN_RP_ID=sentinel.esempio.it
WEBAUTHN_ORIGIN=https://sentinel.esempio.it
```

---

## Collegare i siti

Ogni sito parla col pannello tramite un piccolo connettore. **Non devi crearlo tu**: i
pacchetti sono inclusi nell'applicazione.

1. *Impostazioni → Connettori* → imposta **Indirizzo pubblico di questo pannello** (il bottone
   **Usa questo** compila l'indirizzo da cui stai navigando)
2. Premi **Scarica** accanto a WordPress o Joomla. Il pacchetto WordPress viene generato con
   il tuo indirizzo e la tua chiave di registrazione dentro
3. Installalo sul sito e attivalo. Su WordPress vai in *Impostazioni → Sentinel TD*, scegli la
   cartella e premi **Collega**: il sito compare nel pannello, già accoppiato

Un sito si può aggiungere anche a mano: installi il pacchetto, copi il token che il connettore
mostra e lo incolli quando aggiungi il sito nel pannello. I sorgenti stanno in `connectors/` e
sono neutri — nessun indirizzo, nessuna chiave — così chiunque può costruirsi i propri;
vedi `connectors/README.md`.

---

## Dove si imposta cosa

| Cosa | Dove |
|------|------|
| Logo, favicon, nome del pannello | Impostazioni → Branding |
| Soglie di scadenza, frequenza scansioni, anteprime, conservazione cronologia | Impostazioni |
| Indirizzo del pannello e chiave di registrazione per i connettori | Impostazioni → Connettori |
| Testi, HTML e canali di ogni notifica | Notifiche |
| Report mensile: giorno, destinatario, contenuto, impaginazione | Report mensile |
| Credenziali database, SMTP, Telegram, segreti, porte | `.env` |

---

## Aggiornamenti

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Le migrazioni del database partono da sole all'avvio. Se sostituisci i file a mano invece di
usare Git, ricostruisci entrambi i servizi che condividono il contesto di build:

```bash
docker compose up -d --build api worker
```

---

## Backup

Salva insieme:

- i dati di PostgreSQL (`pg_data`)
- i volumi `branding`, `connectors` e `screenshots`
- il tuo `.env` privato

```bash
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > sentinel-$(date +%F).sql
```

Un normale `docker compose down` conserva i volumi. **`docker compose down -v` li cancella**, e
con loro storico e impostazioni.

---

## Se qualcosa non va

- **Un sito risulta offline ma funziona** — apri il suo dettaglio: può essere stato rigenerato
  il token del connettore sul sito. Ricopialo in *Modifica*.
- **Gli aggiornamenti falliscono su più siti insieme** — quasi sempre è DNS o filesystem, non il
  pannello. `docker compose logs worker | grep "UPDATE FALLITO"` mostra il motivo vero
  restituito da ogni sito.
- **Nessuna anteprima, o un rettangolo bianco dove c'è un video** — il servizio screenshot ha
  bisogno di Google Chrome per i video di sfondo in H.264:
  `docker compose logs shooter | grep pronto` deve nominare Chrome. Se è ripiegato su Chromium,
  ricostruisci con `docker compose build shooter`.
- **Email o PDF nella lingua sbagliata** — seguono `DEFAULT_UI_LANGUAGE`, non la lingua scelta
  nel browser.
- **Le cose programmate scattano all'ora sbagliata** — imposta `TZ` nel `.env`; senza, i
  container girano in UTC.

---

## Documentazione

- `CHANGELOG.md` — note di rilascio
- `SECURITY.md` — come segnalare una vulnerabilità
- `THIRD-PARTY.md` — componenti di terze parti e relative licenze
- `connectors/README.md` — i connettori WordPress e Joomla
- `docs/LANGUAGES.md` — manutenzione delle traduzioni
- `docs/PUBLISHING.md` — pubblicare questo progetto su GitHub

---

## Licenza

Sentinel TD è software libero rilasciato sotto
**GNU Affero General Public License v3.0 o successiva** — vedi [LICENSE](LICENSE).

In breve: puoi usarlo, modificarlo e ridistribuirlo, anche commercialmente. Se offri una
versione **modificata** come servizio di rete ad altre persone, devi mettere a disposizione
di quegli utenti il codice sorgente.

I componenti di terze parti e le loro licenze sono elencati in [THIRD-PARTY.md](THIRD-PARTY.md).

Copyright © Giuseppe Sciarra — [Tastiere Digitali](https://tastieredigitali.it)

---

## Sostieni il progetto

Se Sentinel TD ti è utile, puoi sostenerne lo sviluppo con una donazione:

**[paypal.me/raxiel87](https://paypal.me/raxiel87)**

Segnalazioni di bug e pull request sono benvenute.
