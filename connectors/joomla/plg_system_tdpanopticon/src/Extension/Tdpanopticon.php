<?php
namespace TastiereDigitali\Plugin\System\Tdpanopticon\Extension;

defined('_JEXEC') or die;

use Joomla\CMS\Plugin\CMSPlugin;
use Joomla\CMS\Factory;
use Joomla\CMS\Updater\Updater;
use Joomla\CMS\Installer\Installer;
use Joomla\CMS\Installer\InstallerHelper;

final class Tdpanopticon extends CMSPlugin
{
    /**
     * Chiamato da: index.php?option=com_ajax&plugin=tdpanopticon&group=system&format=json
     * Plugin "classico": Joomla cattura il valore di ritorno e com_ajax lo mette in {"data":[ ... ]}.
     */
    /**
     * Intercetta le richieste del connettore PRIMA del check offline di Joomla.
     *
     * Il percorso normale (com_ajax) viene bloccato quando il sito e' OFFLINE
     * (offline=1): Joomla serve la pagina offline con HTTP 503 prima che il plugin
     * possa rispondere -> Panopticon vede il sito in errore e salta gli update.
     * Caso d'uso reale: sito messo offline per morosita' del cliente, ma gli
     * aggiornamenti (sicurezza inclusa) devono continuare a passare.
     *
     * onAfterInitialise scatta prima dell'enforcement offline (stessa tecnica dei
     * frontend cron di Akeeba): se la richiesta e' per noi (com_ajax + plugin
     * tdpanopticon) rispondiamo da qui, nello STESSO formato involucro di com_ajax
     * ({"success":true,"data":[...]}), e chiudiamo l'app. Vale sia online che
     * offline: un solo percorso, nessuna differenza per il pannello.
     * Con token assente/errato non tocchiamo nulla di sensibile: 401 secco.
     */
    public function onAfterInitialise(): void
    {
        $app = Factory::getApplication();
        if (!$app->isClient('site')) {
            return;
        }
        $in = $app->getInput();
        if ($in->getCmd('option') !== 'com_ajax' || $in->getCmd('plugin') !== 'tdpanopticon') {
            return;
        }

        header('Content-Type: application/json; charset=utf-8');
        try {
            $result = $this->onAjaxTdpanopticon();
            http_response_code(200);
            echo json_encode(['success' => true, 'data' => [$result], 'message' => null]);
        } catch (\Throwable $e) {
            http_response_code((int) $e->getCode() === 401 ? 401 : 500);
            echo json_encode(['success' => false, 'data' => null, 'message' => $e->getMessage()]);
        }
        $app->close();
    }

    public function onAjaxTdpanopticon($event = null)
    {
        if (!$this->checkAuth()) {
            throw new \RuntimeException('Unauthorized', 401);
        }

        $task = Factory::getApplication()->getInput()->getCmd('task', 'status');
        if ($task === 'update') {
            return $this->doUpdate();
        }
        if ($task === 'install') {
            return $this->doInstall();
        }
        if ($task === 'uninstall') {
            return $this->doUninstall();
        }
        if ($task === 'vendorupdate') {
            return $this->doVendorUpdate();
        }
        if ($task === 'refresh') {
            // Refresh ESPLICITO degli update: contatta i server di update e ripopola
            // #__updates. E' un'operazione PESANTE (richieste HTTP in uscita), quindi NON
            // viene piu' eseguita a ogni status: Panopticon la chiama solo quando serve
            // (es. una volta al giorno, o sul check manuale). Dopo il refresh restituisce
            // comunque lo status aggiornato.
            $this->refreshUpdates();
            // prosegue sotto e restituisce lo status fresco
        }

        // CHECK PASSIVO (come Akeeba Panopticon): leggiamo direttamente le tabelle
        // #__updates / #__extensions cosi' come le mantiene il CMS (lo scheduler di Joomla
        // popola #__updates con la sua task "Update Notification"). Nessun refresh forzato:
        // impatto minimo sul sito monitorato, nessuna richiesta HTTP in uscita, risposta
        // veloce. Il prezzo e' che i dati sono freschi "nell'ordine delle ore" e non al
        // millisecondo - identico comportamento ad Akeeba.

        $db = Factory::getContainer()->get('DatabaseDriver');

        // --- estensioni installate: mappa extension_id -> [name, version corrente] ---
        $installed = [];
        $q = $db->getQuery(true)
            ->select($db->quoteName(['extension_id', 'name', 'type', 'element', 'folder', 'manifest_cache']))
            ->from($db->quoteName('#__extensions'));
        $db->setQuery($q);
        foreach ($db->loadObjectList() as $row) {
            $ver = '';
            $author = '';
            if (!empty($row->manifest_cache)) {
                $mc = json_decode($row->manifest_cache, true);
                if (is_array($mc)) {
                    if (!empty($mc['version'])) { $ver = $mc['version']; }
                    if (!empty($mc['author']))  { $author = $mc['author']; }
                }
            }
            $installed[(int) $row->extension_id] = [
                'name'    => $row->name,
                'type'    => $row->type,
                'element' => $row->element,
                'version' => $ver,
                'author'  => $author,
            ];
        }

        // --- update disponibili: tabella #__updates ---
        $core_update  = false;
        $core_current = JVERSION;
        $core_latest  = JVERSION;
        $updates      = [];   // extension_id => versione nuova
        $updatesByEl  = [];   // element => versione nuova (fallback robusto: gli update dei
                              // language pack a volte hanno extension_id=0 ma element valido)

        $q = $db->getQuery(true)
            ->select($db->quoteName(['extension_id', 'name', 'element', 'type', 'version']))
            ->from($db->quoteName('#__updates'));
        $db->setQuery($q);

        foreach ($db->loadObjectList() as $u) {
            $isCore = ($u->type === 'file' && $u->element === 'joomla')
                   || stripos((string) $u->name, 'joomla!') !== false;
            if ($isCore) {
                // segnala l'update SOLO se la versione proposta e' davvero piu' alta di quella
                // installata. In #__updates puo' restare una riga "stantia" del core con la
                // stessa versione (residuo o ricreata da findUpdates/rebuild): NON e' un update.
                $cand = (string) $u->version;
                if (version_compare($cand, (string) $core_current, '>')) {
                    $core_update = true;
                    $core_latest = $cand;
                }
                continue;
            }
            if ((int) $u->extension_id > 0) {
                $updates[(int) $u->extension_id] = $u->version;
            }
            if (!empty($u->element)) {
                $updatesByEl[(string) $u->element] = $u->version;
            }
        }

        // --- download key mancanti: SOLO le estensioni il cui update site ha un
        // PLACEHOLDER di download key esplicito ma VUOTO (es. "&dlid=" o "&key=" senza
        // valore). Un extra_query semplicemente vuoto NON significa key mancante: la
        // stragrande maggioranza delle estensioni gratuite (e i language pack ufficiali
        // di Joomla) ha extra_query vuoto del tutto legittimamente. Marcare quelle come
        // "key mancante" e' un falso positivo che blocca l'auto-update di update validi.
        $dlkeyMissing = [];   // extension_id => true
        try {
            $qk = $db->getQuery(true)
                ->select($db->quoteName(['s.extra_query', 'm.extension_id']))
                ->from($db->quoteName('#__update_sites', 's'))
                ->join('INNER', $db->quoteName('#__update_sites_extensions', 'm')
                    . ' ON ' . $db->quoteName('m.update_site_id') . ' = ' . $db->quoteName('s.update_site_id'))
                ->where($db->quoteName('s.enabled') . ' = 1');
            $db->setQuery($qk);
            foreach ($db->loadObjectList() as $k) {
                $eq = (string) $k->extra_query;
                if ($eq === '') {
                    // extra_query vuoto = nessuna key richiesta (estensione gratuita,
                    // language pack ufficiale, ecc.). NON e' un problema: salta.
                    continue;
                }
                // c'e' un extra_query: contiene un parametro key/dlid SENZA valore?
                // es. "dlid=" oppure "download_id=&foo=bar" -> placeholder vuoto = key mancante.
                // se invece ha un valore (es. "dlid=ABC123") -> key presente, tutto ok.
                if (preg_match('/\b(dlid|download_id|key|licen[sc]e|secret|token)\s*=(?:&|$)/i', $eq) === 1) {
                    $dlkeyMissing[(int) $k->extension_id] = true;
                }
            }
        } catch (\Throwable $e) {
            // tabella/colonna assente su versioni vecchie: ignoro, non e' critico
        }

        // --- inventario: estensioni di terze parti + language pack ---
        // I language pack CORE di Joomla si aggiornano tramite il loro "package" contenitore
        // (type=package, es. pkg_it-IT), NON tramite i singoli record type=language (che sono
        // i figli per client site/admin/api e non hanno un update proprio). Quindi:
        //  - includo i 'package' (li' c'e' l'update vero)
        //  - ESCLUDO i 'language' individuali (rumore: 3 record senza update)
        $coreAuthors = ['joomla! project', 'joomla project', 'joomla!', 'joomla'];
        // 'file' incluso: i language pack di terze parti (es. iCagenda via Transifex) e altri
        // update di tipo "file" sono registrati come type=file. Senza questo restano invisibili
        // a Panopticon pur avendo un update reale in #__updates (il backend di Joomla li vede).
        $wanted     = ['component', 'module', 'plugin', 'template', 'library', 'package', 'file'];
        $extensions = [];
        foreach ($installed as $eid => $ext) {
            if (!in_array($ext['type'], $wanted, true)) {
                continue;
            }
            // il core Joomla e' type=file element=joomla ed e' gia' gestito come "core" sopra: salta
            if ($ext['type'] === 'file' && strtolower((string) $ext['element']) === 'joomla') {
                continue;
            }
            // escludi core Joomla in base all'autore del manifest, TRANNE i package e i file
            // (i language pack, core o di terze parti, possono avere autore "Joomla! Project"
            // ma li vogliamo vedere/aggiornare)
            if ($ext['type'] !== 'package' && $ext['type'] !== 'file') {
                $auth = strtolower(trim((string) $ext['author']));
                if ($auth !== '' && in_array($auth, $coreAuthors, true)) {
                    continue;
                }
            }
            $name = $ext['name'];
            // se il nome e' una chiave di lingua (TUTTO_MAIUSCOLO_UNDERSCORE) uso l'element
            if (preg_match('/^[A-Z0-9_\.]+$/', (string) $name)) {
                $name = $ext['element'];
            }
            $has = isset($updates[$eid]);
            $newVer = $has ? $updates[$eid] : '';
            // fallback: se non trovato per id, prova per element (es. package lingua)
            if (!$has && isset($updatesByEl[$ext['element']])) {
                $has = true;
                $newVer = $updatesByEl[$ext['element']];
            }
            // anti-falso-positivo: considera l'update valido SOLO se la versione proposta
            // e' davvero piu' alta di quella installata (in #__updates puo' restare una riga
            // con versione uguale/inferiore dopo un aggiornamento o un rebuild update sites).
            $curVer = (string) $ext['version'];
            if ($has && $curVer !== '' && $newVer !== '' && version_compare((string) $newVer, $curVer, '<=')) {
                $has = false;
                $newVer = '';
            }
            $extensions[] = [
                'type'    => $ext['type'],
                'name'    => $name,
                'slug'    => $ext['element'],
                'current' => $ext['version'],
                'new'     => $newVer,
                'update'  => $has,
                // segnala se l'update non e' scaricabile per download key mancante
                'dlkey_missing' => ($has && !empty($dlkeyMissing[$eid])),
            ];
        }

        // dedup: alcune estensioni sono registrate per piu' client con lo stesso element.
        // Tengo una sola voce per (type+slug), preferendo quella che ha un update disponibile.
        $dedup = [];
        foreach ($extensions as $e) {
            $k = $e['type'] . '|' . $e['slug'];
            if (!isset($dedup[$k]) || ($e['update'] && !$dedup[$k]['update'])) {
                $dedup[$k] = $e;
            }
        }
        $extensions = array_values($dedup);

        return [
            'cms'  => 'joomla',
            'core' => ['current' => $core_current, 'latest' => $core_latest, 'update' => $core_update],
            'php'  => PHP_VERSION,
            'extensions' => $extensions,
        ];
    }

    /**
     * Aggiorna il core Joomla invocando via CLI `joomla.php core:update`.
     * Questo e' l'unico modo affidabile: com_joomlaupdate via API richiede sessione
     * admin + restore.php via HTTP e non funziona da com_ajax. La CLI ufficiale
     * di Joomla fa tutto (download, extract, DB migration, cleanup) come un solo
     * comando atomico e ha error handling corretto.
     * Richiede: exec() disponibile (non in disable_functions) e permessi FS per l'utente
     * PHP-FPM (di norma 'www' su aaPanel, che e' anche l'owner dei file del sito).
     */
    private function doCoreUpdate(): array
    {
        // funzione exec disponibile?
        $disabled = array_map('trim', explode(',', (string) ini_get('disable_functions')));
        if (!function_exists('exec') || in_array('exec', $disabled, true)) {
            return ['ok' => false, 'error' => 'exec() disabilitata: impossibile lanciare CLI core:update', 'new' => JVERSION];
        }

        // il binario PHP CLI. Attenzione: sotto PHP-FPM, PHP_BINARY punta a `php-fpm`
        // NON al `php` CLI (che ci serve per lanciare joomla.php). Quindi cerchiamo
        // il vero binario CLI: prima nella stessa cartella di PHP_BINARY (aaPanel:
        // /www/server/php/XX/bin/php-fpm -> /www/server/php/XX/bin/php), poi $PATH,
        // poi qualche path noto.
        $phpBin = null;
        if (defined('PHP_BINARY') && PHP_BINARY !== '') {
            $candidate = dirname(PHP_BINARY) . '/php';
            if (is_file($candidate) && is_executable($candidate)) {
                $phpBin = $candidate;
            }
        }
        if ($phpBin === null) {
            // fallback: cerca `php` in PATH
            $which = @exec('command -v php 2>/dev/null');
            if (is_string($which) && $which !== '' && is_file($which) && is_executable($which)) {
                $phpBin = $which;
            }
        }
        if ($phpBin === null) {
            // ultimo fallback: path aaPanel comuni
            foreach (['/www/server/php/84/bin/php', '/www/server/php/83/bin/php', '/www/server/php/82/bin/php', '/usr/bin/php', '/usr/local/bin/php'] as $p) {
                if (is_file($p) && is_executable($p)) { $phpBin = $p; break; }
            }
        }
        if ($phpBin === null) {
            return ['ok' => false, 'error' => 'Binario PHP CLI non trovato', 'new' => JVERSION];
        }
        // path assoluto del CLI di Joomla: JPATH_ROOT/cli/joomla.php
        $cliScript = JPATH_ROOT . '/cli/joomla.php';
        if (!is_file($cliScript)) {
            return ['ok' => false, 'error' => 'CLI Joomla non trovato: ' . $cliScript, 'new' => JVERSION];
        }

        // costruisci comando con env pulita
        $cmd = escapeshellcmd($phpBin) . ' ' . escapeshellarg($cliScript) . ' core:update 2>&1';

        // esegui: se il web server ha un timeout stretto (PHP-FPM request_terminate_timeout
        // o NPMplus proxy_read_timeout), un download lento puo' troncare la risposta.
        // In quel caso alzare max_execution_time e request_terminate_timeout a 300s.
        $output = [];
        $rc = 0;
        exec($cmd, $output, $rc);
        $joined = implode("\n", $output);

        // il core:update finisce con "[OK] Joomla core updated successfully!" al successo
        if ($rc === 0 && (strpos($joined, '[OK]') !== false || strpos($joined, 'updated successfully') !== false)) {
            // rileggo la versione POST-update: JVERSION e' quella caricata all'inizio del
            // request, non riflette il nuovo file di versione. Leggo il file direttamente.
            $newVer = $this->readVersionFile();
            return ['ok' => true, 'error' => '', 'new' => $newVer ?: 'aggiornato'];
        }

        // fallito: rispedisce le ultime righe utili dell'output CLI per debug
        $tail = implode("\n", array_slice($output, -8));
        return [
            'ok' => false,
            'error' => 'core:update fallito (rc=' . $rc . '): ' . ($tail !== '' ? $tail : 'nessun output'),
            'new' => JVERSION,
        ];
    }

    /**
     * Legge la versione dal file Version.php di Joomla dopo un update, senza fidarsi
     * della costante JVERSION gia' caricata in memoria.
     */
    private function readVersionFile(): string
    {
        $vfile = JPATH_ROOT . '/libraries/src/Version.php';
        if (!is_file($vfile)) {
            return '';
        }
        $src = @file_get_contents($vfile);
        if ($src === false) {
            return '';
        }
        // Joomla espone MAJOR_VERSION, MINOR_VERSION, PATCH_VERSION come costanti public
        $major = $minor = $patch = null;
        if (preg_match('/MAJOR_VERSION\s*=\s*(\d+)/', $src, $m)) $major = $m[1];
        if (preg_match('/MINOR_VERSION\s*=\s*(\d+)/', $src, $m)) $minor = $m[1];
        if (preg_match('/PATCH_VERSION\s*=\s*(\d+)/', $src, $m)) $patch = $m[1];
        if ($major !== null && $minor !== null && $patch !== null) {
            return $major . '.' . $minor . '.' . $patch;
        }
        return '';
    }

    /**
     * Aggiorna UNA estensione. Parametri: extype (component|module|plugin|template|library), slug (element).
     * Se extype='core', delega a doCoreUpdate() che invoca la CLI ufficiale di Joomla.
     */
    private function doUpdate(): array
    {
        $app  = Factory::getApplication();
        $type = $app->getInput()->getCmd('extype', '');
        $slug = $app->getInput()->getString('slug', '');

        if ($type === 'core') {
            return $this->doCoreUpdate();
        }

        $db = Factory::getContainer()->get('DatabaseDriver');

        // Un'estensione (specie i language pack) puo' avere PIU' record in #__extensions
        // con lo stesso element ma extension_id diversi (client site/administrator/api).
        // L'update in #__updates e' legato a UNO specifico extension_id, quindi raccolgo
        // TUTTI gli id che matchano e cerco l'update su tutti loro.
        $q = $db->getQuery(true)
            ->select($db->quoteName('extension_id'))
            ->from($db->quoteName('#__extensions'))
            ->where($db->quoteName('element') . ' = ' . $db->quote($slug));
        if ($type !== '') {
            $q->where($db->quoteName('type') . ' = ' . $db->quote($type));
        }
        $db->setQuery($q);
        $eids = array_map('intval', (array) $db->loadColumn());
        if (empty($eids)) {
            return ['ok' => false, 'error' => 'Estensione non trovata: ' . $slug];
        }

        // cerca l'update tra TUTTI gli extension_id candidati: prendi il primo che ne ha uno
        $eid      = 0;
        $updateId = 0;
        $expected = '';
        $q2 = $db->getQuery(true)
            ->select($db->quoteName(['extension_id', 'update_id', 'version']))
            ->from($db->quoteName('#__updates'))
            ->where($db->quoteName('extension_id') . ' IN (' . implode(',', $eids) . ')');
        $db->setQuery($q2);
        $row = $db->loadObject();
        if ($row && (int) $row->update_id) {
            $eid      = (int) $row->extension_id;
            $updateId = (int) $row->update_id;
            $expected = (string) $row->version;
        }

        // fallback: alcuni update (es. package lingua) hanno extension_id=0 in #__updates;
        // cercali per element. Uso comunque un eid reale installato per leggere la versione.
        if (!$updateId) {
            $q3 = $db->getQuery(true)
                ->select($db->quoteName(['update_id', 'version']))
                ->from($db->quoteName('#__updates'))
                ->where($db->quoteName('element') . ' = ' . $db->quote($slug));
            $db->setQuery($q3);
            $row3 = $db->loadObject();
            if ($row3 && (int) $row3->update_id) {
                $updateId = (int) $row3->update_id;
                $expected = (string) $row3->version;
                $eid      = $eids[0];   // usa il primo eid installato per leggere la versione
            }
        }

        if (!$updateId) {
            // Nessun update sul CANALE Joomla. Per i prodotti Balbooa esiste il percorso
            // alternativo dal file API pubblico (usato quando il loro canale e' in ritardo):
            // instrada li'. Quando invece il canale HA l'update (come sopra), si usa il
            // percorso NORMALE: installa il pacchetto vero, sistema il manifest e rimuove
            // la riga da #__updates -> niente loop di re-rilevamenti.
            $vendorAliases = ['pkg_baforms', 'com_baforms', 'baforms', 'pkg_gallery', 'com_gallery', 'gallery'];
            if (in_array(strtolower($slug), $vendorAliases, true)) {
                return $this->doVendorUpdateBySlug(strtolower($slug));
            }
            return ['ok' => true, 'error' => '', 'new' => 'nessun update'];
        }

        // l'eid su cui agire e' quello che ha l'update (importante per leggere la versione giusta)

        // versione + stato di pubblicazione PRIMA dell'update
        $verBefore = $this->readInstalledVersion($db, $eid);
        $wasEnabled = $this->isExtensionEnabled($db, $eid);

        try {
            $factory = $app->bootComponent('com_installer')->getMVCFactory();
            $model   = $factory->createModel('Update', 'Administrator', ['ignore_request' => true]);

            // svuota eventuali messaggi precedenti per leggere quelli di questo giro
            $app->getMessageQueue(true);

            $model->update([$updateId]);

            // versione installata DOPO l'update
            $verAfter = $this->readInstalledVersion($db, $eid);

            // RIATTIVAZIONE: se l'estensione era abilitata prima ma l'update l'ha
            // lasciata disabilitata, ripristina lo stato enabled=1.
            // Non tocco le estensioni che erano gia' disabilitate.
            if ($wasEnabled && !$this->isExtensionEnabled($db, $eid)) {
                $this->setExtensionEnabled($db, $eid, true);
            }

            // successo SOLO se la versione è effettivamente cambiata (ed è salita a quella attesa, se nota)
            $changed = ($verAfter !== '' && $verAfter !== $verBefore);
            $reached = ($expected === '' || $verAfter === $expected);

            if ($changed && $reached) {
                return ['ok' => true, 'error' => '', 'new' => $verAfter];
            }

            // SEGNALE DB AFFIDABILE (indipendente dalla lingua): quando un update va a buon
            // fine, Joomla RIMUOVE la riga da #__updates. I package (es. iCagenda lingua) al
            // riaggiornarsi possono re-registrarsi con un element/record DIVERSO: la rilettura
            // della versione sul vecchio extension_id resta ferma -> $changed=false anche a
            // install riuscito, e il vecchio verdetto rispondeva "fallito" allegando come
            // motivo i messaggi di Joomla... che dicevano successo. Riga sparita = successo.
            $qGone = $db->getQuery(true)
                ->select('COUNT(*)')
                ->from($db->quoteName('#__updates'))
                ->where($db->quoteName('update_id') . ' = ' . (int) $updateId);
            $db->setQuery($qGone);
            $rowGone = ((int) $db->loadResult() === 0);

            if ($rowGone) {
                // versione da riportare: quella riletta se cambiata, altrimenti la piu' alta
                // tra i record con lo stesso element (il nuovo record del package), altrimenti
                // quella attesa dal canale.
                $best = ($changed && $verAfter !== '') ? $verAfter : '';
                if ($best === '') {
                    foreach ($eids as $cand) {
                        $v = $this->readInstalledVersion($db, (int) $cand);
                        if ($v !== '' && version_compare($v, $best ?: '0', '>')) {
                            $best = $v;
                        }
                    }
                }
                if ($best === '') {
                    $best = $expected !== '' ? $expected : $verAfter;
                }
                return ['ok' => true, 'error' => '', 'new' => $best];
            }

            // fallito DAVVERO: come motivo usa SOLO i messaggi di errore/avviso di Joomla
            // (mai i messaggi di successo: erano loro a produrre "fallito: aggiornato
            // correttamente"). Se non ci sono, spiega il mancato cambio versione.
            $reason = '';
            foreach ((array) $app->getMessageQueue() as $m) {
                $mtype = strtolower((string) ($m['type'] ?? ''));
                if (!in_array($mtype, ['error', 'warning', 'notice'], true)) {
                    continue;
                }
                if (!empty($m['message'])) {
                    $reason .= ($reason ? ' | ' : '') . strip_tags((string) $m['message']);
                }
            }
            if ($reason === '') {
                $reason = 'versione non cambiata (' . ($verBefore ?: '?') . ' → attesa ' . ($expected ?: '?') . '): update non applicato';
            }

            return ['ok' => false, 'error' => $reason, 'new' => $expected, 'current' => $verBefore];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }

    /**
     * Installa un pacchetto (.zip) caricato via multipart (campo "package") e, se richiesto,
     * lo attiva. Parametri:
     *  - file "package": lo zip dell'estensione (plugin/module/template/component/package)
     *  - activate (int, default 1): se 1 e l'estensione installata e' un plugin/module, la abilita
     *
     * Usa l'Installer nativo di Joomla, quindi e' equivalente a "Estensioni > Installa > Carica".
     * Il core Joomla NON e' installabile da qui (e' un pacchetto speciale, va fatto a mano).
     */
    private function doInstall(): array
    {
        $app      = Factory::getApplication();
        $activate = (int) $app->getInput()->getInt('activate', 1);

        // --- recupera il file caricato (campo "package") ---
        $file = $app->getInput()->files->get('package', null, 'raw');
        if (empty($file) || !is_array($file) || empty($file['tmp_name'])) {
            return ['ok' => false, 'error' => 'Nessun file ricevuto (campo "package")', 'new' => ''];
        }
        if (!empty($file['error'])) {
            return ['ok' => false, 'error' => 'Upload fallito (codice PHP ' . (int) $file['error'] . ')', 'new' => ''];
        }
        $origName = (string) ($file['name'] ?? 'package.zip');
        if (strtolower(pathinfo($origName, PATHINFO_EXTENSION)) !== 'zip') {
            return ['ok' => false, 'error' => 'Il pacchetto deve essere un file .zip', 'new' => ''];
        }

        // --- sposta lo zip nella tmp di Joomla (move_uploaded_file = compatibile J4/J5) ---
        $tmpDir   = rtrim((string) $app->get('tmp_path'), '/') ?: sys_get_temp_dir();
        $safeName = preg_replace('/[^A-Za-z0-9._-]/', '_', $origName);
        $target   = $tmpDir . '/tdpkg_' . uniqid('', true) . '_' . $safeName;

        if (!@move_uploaded_file($file['tmp_name'], $target) && !@copy($file['tmp_name'], $target)) {
            return ['ok' => false, 'error' => 'Impossibile scrivere il pacchetto nella tmp (' . $tmpDir . ')', 'new' => ''];
        }

        $package = null;
        try {
            // --- scompatta lo zip ---
            $package = InstallerHelper::unpack($target, true);
            if (!is_array($package) || empty($package['dir'])) {
                return ['ok' => false, 'error' => 'Pacchetto non valido o unzip fallito', 'new' => ''];
            }

            // --- installa con l'Installer nativo ---
            // In contesto com_ajax l'Installer non eredita il DB dal container:
            // glielo passo esplicitamente, altrimenti "Database not set in Installer".
            $installer = new Installer();
            $dbDriver = Factory::getContainer()->get('DatabaseDriver');
            if (method_exists($installer, 'setDatabase')) {
                $installer->setDatabase($dbDriver);
            }
            $app->getMessageQueue(true);   // pulisci la coda messaggi per leggere quelli di questo giro

            $ok = $installer->install($package['dir']);

            if (!$ok) {
                $reason = $this->collectMessages($app) ?: 'Installazione fallita';
                return ['ok' => false, 'error' => $reason, 'new' => ''];
            }

            // --- ricava info estensione installata dall'oggetto Installer ---
            $ext = $installer->extension ?? null;
            $instType    = $ext ? (string) $ext->type : '';
            $instElement = $ext ? (string) $ext->element : '';
            $instName    = $ext ? (string) $ext->name : '';
            $eid         = $ext ? (int) $ext->extension_id : 0;

            $db      = Factory::getContainer()->get('DatabaseDriver');
            $version = $eid ? $this->readInstalledVersion($db, $eid) : '';

            // --- attivazione (solo plugin/module, solo se richiesto) ---
            $activated = false;
            if ($activate && $eid && in_array($instType, ['plugin', 'module'], true)) {
                if (!$this->isExtensionEnabled($db, $eid)) {
                    $this->setExtensionEnabled($db, $eid, true);
                }
                $activated = $this->isExtensionEnabled($db, $eid);
            }

            return [
                'ok'        => true,
                'error'     => '',
                'new'       => $version,
                'type'      => $instType,
                'name'      => $instName,
                'slug'      => $instElement,
                'activated' => $activated,
            ];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage(), 'new' => ''];
        } finally {
            // --- cleanup: rimuovi zip e cartella estratta ---
            try {
                if (is_array($package)) {
                    InstallerHelper::cleanupInstall($package['packagefile'] ?? $target, $package['extractdir'] ?? null);
                }
            } catch (\Throwable $e) {
                // best effort
            }
            if (is_file($target)) {
                @unlink($target);
            }
        }
    }

    /**
     * Disinstalla UNA estensione. Parametri: extype (plugin|module|template|component|library|package), slug (element).
     * Sicurezze: non rimuove se stessa, ne' estensioni protette (protected=1) o core.
     */
    private function doUninstall(): array
    {
        $app  = Factory::getApplication();
        $type = $app->getInput()->getCmd('extype', '');
        $slug = $app->getInput()->getString('slug', '');

        if ($slug === '') {
            return ['ok' => false, 'error' => 'slug (element) mancante'];
        }
        if ($slug === 'tdpanopticon') {
            return ['ok' => false, 'error' => 'Non posso rimuovere il connettore Sentinel TD stesso'];
        }
        // Il blocco era sul TIPO 'file' intero perche' il core di Joomla e' registrato
        // come estensione 'file' (element 'joomla'). Ma cosi' si impediva di rimuovere
        // anche language pack e add-on di terze parti dello stesso tipo. Ora il core si
        // riconosce con precisione: element 'joomla', oppure i flag protected/locked
        // che Joomla stesso mette sulle proprie estensioni.
        if ($type === 'core') {
            return ['ok' => false, 'error' => 'Il core di Joomla non si rimuove da qui'];
        }
        if (strtolower($slug) === 'joomla') {
            return ['ok' => false, 'error' => 'Il core di Joomla non si rimuove da qui'];
        }

        $db = Factory::getContainer()->get('DatabaseDriver');

        // Joomla 4+ ha la colonna 'locked' (estensioni di sistema non disinstallabili)
        $cols = ['extension_id', 'type', 'name', 'protected'];
        try {
            if (array_key_exists('locked', $db->getTableColumns('#__extensions'))) {
                $cols[] = 'locked';
            }
        } catch (\Throwable $e) {
            // colonna non leggibile: resta il controllo su 'protected'
        }

        // trova l'extension_id per element (+type se passato); escludi protette/core
        $q = $db->getQuery(true)
            ->select($db->quoteName($cols))
            ->from($db->quoteName('#__extensions'))
            ->where($db->quoteName('element') . ' = ' . $db->quote($slug));
        if ($type !== '') {
            $q->where($db->quoteName('type') . ' = ' . $db->quote($type));
        }
        $db->setQuery($q);
        $row = $db->loadObject();

        if (!$row) {
            return ['ok' => false, 'error' => 'Estensione non trovata: ' . $slug];
        }
        if ((int) $row->protected === 1 || (isset($row->locked) && (int) $row->locked === 1)) {
            return ['ok' => false, 'error' => 'Estensione di sistema protetta: non rimovibile'];
        }

        $eid     = (int) $row->extension_id;
        $instTyp = (string) $row->type;
        $name    = (string) $row->name;

        try {
            // l'Installer in com_ajax non eredita il DB dal container: passaglielo.
            $installer = new Installer();
            if (method_exists($installer, 'setDatabase')) {
                $installer->setDatabase($db);
            }
            $app->getMessageQueue(true);   // pulisci la coda per leggere i messaggi di questo giro
            $ok = $installer->uninstall($instTyp, $eid);
            if (!$ok) {
                $reason = $this->collectMessages($app) ?: 'Disinstallazione fallita';
                return ['ok' => false, 'error' => $reason];
            }
            return ['ok' => true, 'error' => '', 'type' => $instTyp, 'name' => $name, 'slug' => $slug, 'removed' => true];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        }
    }

    /**
     * Update di estensioni "vendor" che NON pubblicano le nuove versioni sul canale update
     * pubblico di Joomla (che resta indietro), ma le espongono in un file API pubblico usato
     * dal pannello "About" del componente. Gestiti: Balbooa Forms e Balbooa Gallery.
     * Parametro: slug.
     */
    private function doVendorUpdate(): array
    {
        return $this->doVendorUpdateBySlug(strtolower(Factory::getApplication()->getInput()->getString('slug', '')));
    }

    private function doVendorUpdateBySlug(string $slug): array
    {

        // Per ogni prodotto: package element, tabella token (gate licenza), URL del file API
        // pubblico, nome della variabile JS in quel file, nome del file zip temporaneo.
        $map = [
            'baforms' => [
                'pkg'    => 'pkg_BaForms',
                'com'    => 'com_baforms',
                'api'    => '#__baforms_api',
                'jsurl'  => 'https://www.balbooa.com/updates/baforms/formsApi/formsApi.js',
                'jsvar'  => 'formsApi',
                'tmpzip' => 'pkg_BaForms.zip',
            ],
            'gallery' => [
                'pkg'    => 'pkg_Gallery',
                'com'    => 'com_gallery',
                'api'    => '#__gallery_api',
                'jsurl'  => 'https://www.balbooa.com/updates/gallery/galleryApi/galleryApi.js',
                'jsvar'  => 'galleryApi',
                'tmpzip' => 'pkg_Gallery.zip',
            ],
        ];
        $aliases = [
            'pkg_baforms' => 'baforms', 'com_baforms' => 'baforms', 'baforms' => 'baforms',
            'pkg_gallery' => 'gallery', 'com_gallery' => 'gallery', 'gallery' => 'gallery',
        ];
        $key = $aliases[$slug] ?? '';
        if ($key === '' || !isset($map[$key])) {
            return ['ok' => false, 'error' => 'vendor non gestito: ' . $slug];
        }
        return $this->updateBalbooaProduct($map[$key]);
    }

    /**
     * Aggiorna un prodotto Balbooa (Forms/Gallery) leggendo il file API pubblico, che contiene
     * l'ultima versione e il pacchetto completo in base64. E' la stessa fonte del pannello
     * "About" del componente: il canale update di Joomla non annuncia queste versioni.
     * Installa SOLO se piu' recente dell'installata (idempotente).
     */
    private function updateBalbooaProduct(array $c): array
    {
        $app = Factory::getApplication();
        $db  = Factory::getContainer()->get('DatabaseDriver');

        // 1) versione installata: la VERA e' quella del COMPONENT, non del package.
        // Balbooa rilascia manifest incoerenti (pkg_BaForms "2.4.3" con dentro com_baforms
        // "2.4.3.2"): confrontando col solo package si reinstallerebbe/notificherebbe a vuoto
        // un prodotto gia' aggiornato. Uso la piu' alta tra le due.
        $verPkg = $this->readInstalledVersionByElement($db, $c['pkg'], 'package');
        $verCom = isset($c['com']) ? $this->readInstalledVersionByElement($db, $c['com'], 'component') : '';
        if ($verPkg === '' && $verCom === '') {
            return ['ok' => false, 'error' => $c['pkg'] . ' non installato su questo sito'];
        }
        $verBefore = ($verCom !== '' && version_compare($verCom, $verPkg ?: '0', '>')) ? $verCom : ($verPkg ?: $verCom);

        // 2) gate licenza: aggiorno solo se la licenza Balbooa e' attivata su questo sito
        //    (token in <api>, service='balbooa'). Rispetta il vincolo di licenza di Balbooa.
        $licensed = false;
        try {
            $q = $db->getQuery(true)
                ->select($db->quoteName('key'))
                ->from($db->quoteName($c['api']))
                ->where($db->quoteName('service') . ' = ' . $db->quote('balbooa'));
            $db->setQuery($q);
            $state = json_decode((string) $db->loadResult());
            $licensed = is_object($state) && !empty($state->data);
        } catch (\Throwable $e) {
            // tabella assente: trattala come non attivata
        }
        if (!$licensed) {
            return ['ok' => false, 'error' => 'Licenza Balbooa non attivata su questo sito (attivala una volta dal componente > About).'];
        }

        // 3) scarica il file API pubblico (serve user-agent da browser, altrimenti 403)
        $body = '';
        try {
            $resp = \Joomla\CMS\Http\HttpFactory::getHttp()->get(
                $c['jsurl'],
                ['User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)']
            );
            if ((int) $resp->code === 200) {
                $body = (string) $resp->body;
            } else {
                return ['ok' => false, 'error' => 'Server Balbooa: HTTP ' . (int) $resp->code];
            }
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => 'Impossibile contattare il server Balbooa: ' . $e->getMessage()];
        }
        if ($body === '') {
            return ['ok' => false, 'error' => 'Risposta vuota dal server Balbooa'];
        }

        // 4) versione dall'API
        $verNew = '';
        if (preg_match('/' . preg_quote($c['jsvar'], '/') . '\.version\s*=\s*[\'"]([0-9][0-9.]*)[\'"]/', $body, $m)) {
            $verNew = $m[1];
        }
        if ($verNew === '') {
            return ['ok' => false, 'error' => 'Versione non trovata nel file API Balbooa'];
        }

        // 5) niente da fare se non e' piu' recente
        if (version_compare($verNew, $verBefore, '<=')) {
            return ['ok' => true, 'error' => '', 'new' => $verBefore];
        }

        // 6) c'e' update: estrai il pacchetto base64 (proprieta' <jsvar>.package)
        if (!preg_match('/' . preg_quote($c['jsvar'], '/') . '\.package\s*=\s*[\'"]([A-Za-z0-9+\/=]+)[\'"]/', $body, $mp)) {
            return ['ok' => false, 'error' => 'Pacchetto ' . $verNew . ' non presente nel file API Balbooa'];
        }
        $bin = base64_decode($mp[1], true);
        if ($bin === false || strlen($bin) < 1000) {
            return ['ok' => false, 'error' => 'Pacchetto Balbooa corrotto (base64 non valido)'];
        }

        // 7) scrivi lo zip, scompatta e installa in UPDATE (come fa il componente Balbooa)
        $tmpPath = rtrim((string) $app->get('tmp_path'), '/') ?: sys_get_temp_dir();
        $zipPath = $tmpPath . '/' . $c['tmpzip'];
        if (@file_put_contents($zipPath, $bin) === false) {
            return ['ok' => false, 'error' => 'Impossibile scrivere il pacchetto in tmp'];
        }
        $package = null;
        try {
            $package = InstallerHelper::unpack($zipPath, true);
            if (!is_array($package) || empty($package['dir'])) {
                return ['ok' => false, 'error' => 'Unzip del pacchetto Balbooa fallito'];
            }
            $installer = new Installer();
            if (method_exists($installer, 'setDatabase')) {
                $installer->setDatabase($db);
            }
            $app->getMessageQueue(true);
            $ok = $installer->update($package['dir']);
            if (!$ok) {
                $reason = $this->collectMessages($app) ?: 'Installazione Balbooa fallita';
                return ['ok' => false, 'error' => $reason, 'new' => $verNew, 'current' => $verBefore];
            }
            $verAfterPkg = $this->readInstalledVersionByElement($db, $c['pkg'], 'package');
            $verAfterCom = isset($c['com']) ? $this->readInstalledVersionByElement($db, $c['com'], 'component') : '';
            $verAfter = ($verAfterCom !== '' && version_compare($verAfterCom, $verAfterPkg ?: '0', '>')) ? $verAfterCom : ($verAfterPkg ?: $verAfterCom);
            return ['ok' => true, 'error' => '', 'new' => $verAfter ?: $verNew];
        } catch (\Throwable $e) {
            return ['ok' => false, 'error' => $e->getMessage()];
        } finally {
            try {
                if (is_array($package)) {
                    InstallerHelper::cleanupInstall($package['packagefile'] ?? $zipPath, $package['extractdir'] ?? null);
                }
            } catch (\Throwable $e) {
                // best effort
            }
            if (is_file($zipPath)) {
                @unlink($zipPath);
            }
        }
    }

    /**
     * Versione installata (manifest_cache) per element + type.
     */
    private function readInstalledVersionByElement($db, string $element, string $type): string
    {
        try {
            $q = $db->getQuery(true)
                ->select($db->quoteName('manifest_cache'))
                ->from($db->quoteName('#__extensions'))
                ->where($db->quoteName('element') . ' = ' . $db->quote($element))
                ->where($db->quoteName('type') . ' = ' . $db->quote($type));
            $db->setQuery($q);
            $mc = json_decode((string) $db->loadResult(), true);
            return (is_array($mc) && !empty($mc['version'])) ? (string) $mc['version'] : '';
        } catch (\Throwable $e) {
            return '';
        }
    }

    /**
     * Concatena i messaggi correnti della coda applicativa (utile per il motivo di un fallimento).
     */
    private function collectMessages($app): string
    {
        $reason = '';
        foreach ((array) $app->getMessageQueue() as $m) {
            if (!empty($m['message'])) {
                $reason .= ($reason ? ' | ' : '') . strip_tags((string) $m['message']);
            }
        }
        return $reason;
    }

    /**
     * Forza Joomla a ricontrollare gli update e a ripopolare #__updates,
     * esattamente come fa il backend quando apri Estensioni > Aggiorna.
     * Senza questo, leggiamo una tabella potenzialmente stale.
     */
    private function refreshUpdates(): void
    {
        $db = Factory::getContainer()->get('DatabaseDriver');

        // IMPORTANTE: il "Rebuild Update Sites" di Joomla AZZERA la colonna extra_query
        // (i download key delle estensioni commerciali: Balbooa, YOOtheme, RSForm, Akeeba...)
        // perche' ricrea i canali dai manifest, che il key non lo contengono. Senza key nel
        // canale, il server non offre piu' l'update -> l'estensione sparisce da #__updates.
        // Bug storico di Joomla mai risolto: quindi salvo le extra_query PRIMA del rebuild,
        // indicizzate per URL (location) che sopravvive al rebuild, e le RIPRISTINO dopo.
        $savedEq = [];
        try {
            $qeq = $db->getQuery(true)
                ->select($db->quoteName(['location', 'extra_query']))
                ->from($db->quoteName('#__update_sites'))
                ->where($db->quoteName('extra_query') . " IS NOT NULL")
                ->where($db->quoteName('extra_query') . " <> " . $db->quote(''));
            $db->setQuery($qeq);
            foreach ($db->loadObjectList() as $r) {
                $loc = trim((string) $r->location);
                if ($loc !== '') {
                    $savedEq[$loc] = (string) $r->extra_query;
                }
            }
        } catch (\Throwable $e) {
            // se non riesco a leggere, il rebuild sotto e' comunque protetto dal ripristino vuoto
        }

        // 0) ricostruisci gli update site mancanti/rotti (come il pulsante "Rebuild" di
        // Joomla in Sistema > Aggiorna > Siti di aggiornamento). Se un update site e' stato
        // cancellato per errore, senza questo Joomla non vedrebbe MAI l'update di quella
        // estensione. Lo facciamo leggendo i manifest delle estensioni installate.
        try {
            $model = Factory::getApplication()
                ->bootComponent('com_installer')
                ->getMVCFactory()
                ->createModel('Updatesites', 'Administrator', ['ignore_request' => true]);
            if (is_object($model) && method_exists($model, 'rebuild')) {
                $model->rebuild();
            }
        } catch (\Throwable $e) {
            // non fatale
        }

        // 0-bis) RIPRISTINA le extra_query (download key) che il rebuild ha azzerato,
        // matchando per location. Tocco solo le righe rimaste vuote, per non sovrascrivere
        // key eventualmente gia' corretti.
        if (!empty($savedEq)) {
            foreach ($savedEq as $loc => $eq) {
                try {
                    $qup = $db->getQuery(true)
                        ->update($db->quoteName('#__update_sites'))
                        ->set($db->quoteName('extra_query') . ' = ' . $db->quote($eq))
                        ->where($db->quoteName('location') . ' = ' . $db->quote($loc))
                        ->where('(' . $db->quoteName('extra_query') . ' IS NULL OR '
                                    . $db->quoteName('extra_query') . ' = ' . $db->quote('') . ')');
                    $db->setQuery($qup);
                    $db->execute();
                } catch (\Throwable $e) {
                    // best effort, continuo con gli altri
                }
            }
        }

        try {
            // 1) ripopola #__updates per tutti gli update site abilitati
            $updater = Updater::getInstance();
            // caching = 0 -> non usare la cache, controlla davvero
            $updater->findUpdates(0, 0);
        } catch (\Throwable $e) {
            // se il refresh fallisce non e' fatale: leggeremo quel che c'e' in tabella
        }

        // 2) anche il core: chiede a com_joomlaupdate di aggiornare la sua info
        try {
            $model = Factory::getApplication()
                ->bootComponent('com_joomlaupdate')
                ->getMVCFactory()
                ->createModel('Update', 'Administrator', ['ignore_request' => true]);
            if (is_object($model) && method_exists($model, 'refreshUpdates')) {
                $model->refreshUpdates(true);
            }
        } catch (\Throwable $e) {
            // non fatale
        }
    }

    private function readInstalledVersion($db, int $eid): string
    {
        $q = $db->getQuery(true)
            ->select($db->quoteName('manifest_cache'))
            ->from($db->quoteName('#__extensions'))
            ->where($db->quoteName('extension_id') . ' = ' . $eid);
        $db->setQuery($q);
        $mc = json_decode((string) $db->loadResult(), true);
        return (is_array($mc) && !empty($mc['version'])) ? (string) $mc['version'] : '';
    }

    private function isExtensionEnabled($db, int $eid): bool
    {
        $q = $db->getQuery(true)
            ->select($db->quoteName('enabled'))
            ->from($db->quoteName('#__extensions'))
            ->where($db->quoteName('extension_id') . ' = ' . $eid);
        $db->setQuery($q);
        return (int) $db->loadResult() === 1;
    }

    private function setExtensionEnabled($db, int $eid, bool $enabled): void
    {
        $q = $db->getQuery(true)
            ->update($db->quoteName('#__extensions'))
            ->set($db->quoteName('enabled') . ' = ' . ((int) $enabled))
            ->where($db->quoteName('extension_id') . ' = ' . $eid);
        $db->setQuery($q);
        $db->execute();
    }

    private function checkAuth(): bool
    {
        $token = (string) $this->params->get('token', '');
        if ($token === '') {
            return false;
        }
        $hdr = '';
        if (function_exists('getallheaders')) {
            foreach (getallheaders() as $k => $v) {
                if (strtolower($k) === 'authorization') {
                    $hdr = $v;
                    break;
                }
            }
        }
        if ($hdr === '' && !empty($_SERVER['HTTP_AUTHORIZATION'])) {
            $hdr = $_SERVER['HTTP_AUTHORIZATION'];
        }
        if ($hdr === '' && !empty($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) {
            $hdr = $_SERVER['REDIRECT_HTTP_AUTHORIZATION'];
        }
        if (stripos($hdr, 'Bearer ') !== 0) {
            return false;
        }
        return hash_equals($token, trim(substr($hdr, 7)));
    }
}
