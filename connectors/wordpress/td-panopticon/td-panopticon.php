<?php

/**
 * Plugin Name: Sentinel TD Agent
 * Description: Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.
 * Version: 2.15.0
 * Author: Tastiere Digitali
 *
 * INSTALLAZIONE: carica lo zip da Plugin → Aggiungi nuovo → Carica plugin, poi attiva.
 *
 * NOTA SULLA CARTELLA: il connettore funziona identico sia in "td-panopticon"
 * (parco storico) sia in "sentinel-td" (nuove installazioni). Tutti gli
 * identificatori interni - option del token, namespace REST, costanti - restano
 * gli stessi, quindi il pannello parla con entrambe allo stesso modo.
 */

if (!defined('ABSPATH')) {
    exit;
}

/*
 * Una sola copia attiva per sito. Se il connettore e' gia' caricato (caso tipico:
 * cartella vecchia "td-panopticon" e nuova "sentinel-td" installate entrambe),
 * questa copia esce SUBITO: senza questa guardia PHP andrebbe in fatal error per
 * ridichiarazione di costanti e funzioni, e il sito resterebbe bianco.
 */
if (defined('TDPANOP_LOADED')) {
    add_action('admin_notices', function () {
        echo '<div class="notice notice-warning"><p><strong>Sentinel TD Agent</strong>: '
           . 'un\'altra copia del connettore è già attiva su questo sito. '
           . 'Tieni attiva una sola copia (disattiva e rimuovi quella non usata).</p></div>';
    });
    return;
}
define('TDPANOP_LOADED', true);

const TDPANOP_OPT = 'td_panopticon_token';

/* ---------------------------------------------------------------------------
 * Collegamento a Sentinel TD PRE-CONFIGURATO.
 * Compila la chiave qui sotto UNA volta (dal pannello: Connettori → Registrazione
 * automatica → Copia) e ri-zippa: ogni sito su cui installi questo plugin mostrera'
 * direttamente "scegli cartella → Collega", senza chiedere URL ne' chiave.
 * Se lasci la chiave vuota, la pagina chiede URL+chiave come prima (fallback).
 * ------------------------------------------------------------------------- */
const TDPANOP_HUB_URL = '';   // vuoto: si imposta dal backend del sito, oppure lo compila Sentinel nel pacchetto che genera
const TDPANOP_HUB_KEY = '';   // idem: mai nel repository

/* ---------------------------------------------------------------------------
 * Token: generazione all'attivazione
 * ------------------------------------------------------------------------- */
register_activation_hook(__FILE__, function () {
    if (!get_option(TDPANOP_OPT)) {
        add_option(TDPANOP_OPT, wp_generate_password(48, false, false));
    }
});

function tdpanop_token(): string
{
    $t = get_option(TDPANOP_OPT);
    if (!$t) {
        // Generazione ATOMICA, a prova di object cache (Redis).
        // Il vecchio get+update aveva una race: due richieste quasi simultanee (es. il
        // POST "Collega" e il render della pagina) su worker FPM diversi, con la cache
        // alloptions non coerente, generavano DUE token diversi - uno finiva al pannello,
        // l'altro restava sul sito -> mismatch dalla nascita (caso reale: Russ Traslochi).
        // 1) butta la cache e rileggi il valore VERO dal DB
        wp_cache_delete(TDPANOP_OPT, 'options');
        wp_cache_delete('alloptions', 'options');
        $t = get_option(TDPANOP_OPT);
        if (!$t) {
            $t = wp_generate_password(48, false, false);
            // 2) add_option e' un INSERT: se un'altra richiesta ha gia' creato il token,
            //    fallisce senza sovrascrivere -> vince SEMPRE il primo, mai due token.
            //    autoload 'no': fuori da alloptions, fuori dalle sue race per sempre.
            if (!add_option(TDPANOP_OPT, $t, '', 'no')) {
                wp_cache_delete(TDPANOP_OPT, 'options');
                wp_cache_delete('alloptions', 'options');
                $t = (string) get_option(TDPANOP_OPT);
            }
        }
    }
    return $t;
}

/* ---------------------------------------------------------------------------
 * Pagina backend: Impostazioni → Sentinel TD
 * ------------------------------------------------------------------------- */
add_action('admin_menu', function () {
    add_options_page('Sentinel TD', 'Sentinel TD', 'manage_options', 'td-panopticon', 'tdpanop_admin_page');
});

function tdpanop_admin_page()
{
    if (!current_user_can('manage_options')) {
        return;
    }

    // rigenera
    if (isset($_POST['tdpanop_rotate']) && check_admin_referer('tdpanop_rotate')) {
        update_option(TDPANOP_OPT, wp_generate_password(48, false, false));
        echo '<div class="notice notice-success is-dismissible"><p>Token rigenerato. Aggiornalo anche in Sentinel TD.</p></div>';
    }

    // ---- Collega a Sentinel TD (auto-registrazione) -------------------------------
    // Le costanti hardcodate hanno la precedenza; le option restano come fallback
    // per zip non pre-configurati.
    $hub_hardcoded = (TDPANOP_HUB_URL !== '' && TDPANOP_HUB_KEY !== '');
    $hub_url = $hub_hardcoded ? TDPANOP_HUB_URL : get_option('td_panopticon_hub_url', '');
    $hub_key = $hub_hardcoded ? TDPANOP_HUB_KEY : get_option('td_panopticon_hub_key', '');
    $hub_tags = [];
    $hub_tags_err = '';

    // salva hub url/chiave e carica le cartelle (solo se NON hardcodato)
    if (!$hub_hardcoded && isset($_POST['tdpanop_hub_save']) && check_admin_referer('tdpanop_hub')) {
        $hub_url = esc_url_raw(trim((string) ($_POST['tdpanop_hub_url'] ?? '')));
        $hub_key = sanitize_text_field((string) ($_POST['tdpanop_hub_key'] ?? ''));
        update_option('td_panopticon_hub_url', $hub_url);
        update_option('td_panopticon_hub_key', $hub_key);
    }

    // registra il sito
    if (isset($_POST['tdpanop_hub_register']) && check_admin_referer('tdpanop_hub')) {
        $hub_url = $hub_hardcoded ? TDPANOP_HUB_URL : get_option('td_panopticon_hub_url', '');
        $hub_key = $hub_hardcoded ? TDPANOP_HUB_KEY : get_option('td_panopticon_hub_key', '');
        $tag_sel = sanitize_text_field((string) ($_POST['tdpanop_hub_tag'] ?? ''));
        $tag_new = sanitize_text_field((string) ($_POST['tdpanop_hub_tag_new'] ?? ''));
        $tag     = $tag_new !== '' ? $tag_new : ($tag_sel === '__none' ? '' : $tag_sel);
        $auto    = !empty($_POST['tdpanop_hub_auto']);
        $resp = wp_remote_post(rtrim($hub_url, '/') . '/api/agent/register', [
            'timeout' => 30,
            'headers' => ['Content-Type' => 'application/json'],
            'body'    => wp_json_encode([
                'key'         => $hub_key,
                'name'        => get_bloginfo('name'),
                'url'         => home_url(),
                'token'       => tdpanop_token(),
                'tags'        => $tag,
                'auto_update' => $auto,
                // URL di login REALE (con WPS Hide Login & simili restituisce quello
                // custom, es. /login): il pannello lo usa come "URL admin" del sito.
                'admin_url'   => wp_login_url(),
            ]),
        ]);
        if (is_wp_error($resp)) {
            echo '<div class="notice notice-error"><p>Collegamento fallito: ' . esc_html($resp->get_error_message()) . '</p></div>';
        } else {
            $code = (int) wp_remote_retrieve_response_code($resp);
            $body = json_decode((string) wp_remote_retrieve_body($resp), true);
            if ($code === 200 && !empty($body['ok'])) {
                $msg = !empty($body['existed'])
                    ? 'Questo sito è GIÀ presente in Sentinel TD: nessuna modifica fatta.'
                    : 'Sito collegato a Sentinel TD' . ($tag !== '' ? ' nella cartella "' . esc_html($tag) . '"' : '') . ($auto ? ' con auto-update attivo.' : '.');
                echo '<div class="notice notice-success is-dismissible"><p>' . $msg . '</p></div>';
            } else {
                $detail = is_array($body) ? ($body['detail'] ?? '') : '';
                echo '<div class="notice notice-error"><p>Collegamento rifiutato (HTTP ' . $code . '): ' . esc_html($detail ?: 'verifica URL e chiave') . '</p></div>';
            }
        }
    }

    // carica le cartelle dal pannello (se URL+chiave presenti)
    if ($hub_url !== '' && $hub_key !== '') {
        $r = wp_remote_get(rtrim($hub_url, '/') . '/api/agent/tags?key=' . rawurlencode($hub_key), ['timeout' => 20]);
        if (is_wp_error($r)) {
            $hub_tags_err = $r->get_error_message();
        } elseif ((int) wp_remote_retrieve_response_code($r) === 200) {
            $b = json_decode((string) wp_remote_retrieve_body($r), true);
            $hub_tags = (is_array($b) && !empty($b['tags']) && is_array($b['tags'])) ? $b['tags'] : [];
        } else {
            $hub_tags_err = 'chiave non valida o pannello non raggiungibile (HTTP ' . (int) wp_remote_retrieve_response_code($r) . ')';
        }
    }

    $token = tdpanop_token();
?>
    <div class="wrap">
        <h1>Sentinel TD</h1>
        <p>Copia questo token e incollalo quando aggiungi il sito in <strong>Sentinel TD</strong>.</p>
        <table class="form-table">
            <tr>
                <th scope="row"><label for="tdpanop_tok">Token del sito</label></th>
                <td>
                    <input type="text" id="tdpanop_tok" readonly value="<?php echo esc_attr($token); ?>"
                        style="width:480px;max-width:100%;font-family:monospace" onclick="this.select()">
                    <button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById('tdpanop_tok').value)">Copia</button>
                </td>
            </tr>
            <tr>
                <th scope="row">Endpoint</th>
                <td><code><?php echo esc_html(rest_url('tdpanopticon/v1/status')); ?></code></td>
            </tr>
        </table>
        <form method="post" onsubmit="return confirm('Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD, altrimenti i check falliranno.');">
            <?php wp_nonce_field('tdpanop_rotate'); ?>
            <input type="submit" name="tdpanop_rotate" class="button button-secondary" value="Rigenera token">
        </form>

        <hr style="margin:24px 0">
        <h2>Collega a Sentinel TD</h2>
        <?php if ($hub_hardcoded) : ?>
            <p>Pannello: <code><?php echo esc_html(TDPANOP_HUB_URL); ?></code> (pre-configurato). Scegli la cartella e collega.</p>
        <?php else : ?>
            <p>Registra questo sito nel pannello da solo: niente copia-incolla del token. Se il sito è già presente in Sentinel TD, non viene modificato nulla.</p>

            <form method="post" style="margin-bottom:14px">
                <?php wp_nonce_field('tdpanop_hub'); ?>
                <table class="form-table">
                    <tr>
                        <th scope="row"><label for="tdpanop_hub_url">URL pannello</label></th>
                        <td><input type="url" id="tdpanop_hub_url" name="tdpanop_hub_url" value="<?php echo esc_attr($hub_url); ?>"
                                placeholder="https://updateweb.tuodominio.it" style="width:420px;max-width:100%"></td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="tdpanop_hub_key">Chiave di registrazione</label></th>
                        <td><input type="text" id="tdpanop_hub_key" name="tdpanop_hub_key" value="<?php echo esc_attr($hub_key); ?>"
                                style="width:420px;max-width:100%;font-family:monospace"
                                placeholder="dal pannello: Connettori → Registrazione automatica"></td>
                    </tr>
                </table>
                <input type="submit" name="tdpanop_hub_save" class="button" value="Salva e carica cartelle">
            </form>
        <?php endif; ?>

        <?php if ($hub_url !== '' && $hub_key !== '') : ?>
            <?php if ($hub_tags_err !== '') : ?>
                <div class="notice notice-warning inline">
                    <p>Impossibile leggere le cartelle: <?php echo esc_html($hub_tags_err); ?></p>
                </div>
            <?php else : ?>
                <form method="post">
                    <?php wp_nonce_field('tdpanop_hub'); ?>
                    <table class="form-table">
                        <tr>
                            <th scope="row"><label for="tdpanop_hub_tag">Cartella</label></th>
                            <td>
                                <select id="tdpanop_hub_tag" name="tdpanop_hub_tag">
                                    <option value="__none">— Nessuna cartella —</option>
                                    <?php foreach ($hub_tags as $t) : ?>
                                        <option value="<?php echo esc_attr($t); ?>"><?php echo esc_html($t); ?></option>
                                    <?php endforeach; ?>
                                </select>
                                <span style="margin:0 8px">oppure nuova:</span>
                                <input type="text" name="tdpanop_hub_tag_new" placeholder="es. ClienteX" style="width:180px">
                            </td>
                        </tr>
                        <tr>
                            <th scope="row">Auto-update</th>
                            <td><label><input type="checkbox" name="tdpanop_hub_auto" value="1" checked>
                                    Attiva gli aggiornamenti automatici gestiti da Sentinel TD per questo sito</label></td>
                        </tr>
                    </table>
                    <input type="submit" name="tdpanop_hub_register" class="button button-primary" value="Collega questo sito a Sentinel TD">
                </form>
            <?php endif; ?>
        <?php endif; ?>
    </div>
<?php
}

/* ---------------------------------------------------------------------------
 * REST API
 * ------------------------------------------------------------------------- */
add_action('rest_api_init', function () {
    register_rest_route('tdpanopticon/v1', '/status', [
        'methods'  => 'GET',
        'callback' => 'tdpanop_status',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/autologin', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_autologin',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/update', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_update',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/install', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_install',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/uninstall', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_uninstall',
        'permission_callback' => 'tdpanop_auth',
    ]);
});

function tdpanop_auth(WP_REST_Request $req)
{
    $hdr = $req->get_header('authorization');
    if (!$hdr || stripos($hdr, 'Bearer ') !== 0) {
        return false;
    }
    $bearer = trim(substr($hdr, 7));
    if (hash_equals(tdpanop_token(), $bearer)) {
        return true;
    }
    // AUTH SELF-HEALING contro l'object cache avvelenata (Redis).
    // Caso reale: alla riattivazione del plugin, con la cache alloptions stantia,
    // add_option puo' scrivere in CACHE un token nuovo mentre nel DB resta quello
    // vero -> il connettore confronta col token fantasma -> 401 su un token giusto,
    // stabile finche' nessuno flusha Redis. Qui, SOLO in caso di mismatch, buttiamo
    // la cache e riconfrontiamo col valore VERO dal DB: costo zero nel caso normale,
    // autoguarigione in quello avvelenato.
    wp_cache_delete(TDPANOP_OPT, 'options');
    wp_cache_delete('alloptions', 'options');
    $real = get_option(TDPANOP_OPT);
    return is_string($real) && $real !== '' && hash_equals($real, $bearer);
}

function tdpanop_status($req = null)
{
    if (!function_exists('get_plugins')) {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
    }
    if (!function_exists('get_core_updates')) {
        require_once ABSPATH . 'wp-admin/includes/update.php';
    }

    // CHECK PASSIVO (come Akeeba Panopticon): di default leggiamo i transient di update
    // cosi' come li mantiene WordPress (il wp-cron li aggiorna periodicamente). Nessun
    // refresh forzato a ogni status: impatto minimo sul sito, nessuna richiesta in uscita
    // a wordpress.org, risposta veloce.
    // Il refresh forzato (pesante: contatta wordpress.org) avviene SOLO se richiesto
    // esplicitamente con ?refresh=1, cosi' Sentinel TD puo' rinfrescare quando serve (es.
    // una volta al giorno o sul check manuale) senza gravare su ogni controllo.
    $forceRefresh = false;
    if ($req instanceof WP_REST_Request) {
        $forceRefresh = (string) $req->get_param('refresh') === '1';
    }
    if ($forceRefresh) {
        // Con un object cache persistente il transient di update e' instabile (sfrattato
        // dalla LRU o vuoto) e wp_update_plugins()/wp_update_themes() sono throttlati a
        // 12h: se last_checked e' recente NON ricontattano wordpress.org e restituiscono
        // uno stato vecchio. Cancellando prima i transient il throttle non scatta e il
        // refresh e' reale (identico al pulsante "Controlla di nuovo" del backend).
        delete_site_transient('update_plugins');
        delete_site_transient('update_themes');
        delete_site_transient('update_core');
        wp_version_check();
        wp_update_plugins();
        wp_update_themes();
    }

    $core_cur = get_bloginfo('version');
    $core_latest = $core_cur;
    $core_update = false;
    $uc = get_site_transient('update_core');
    if ($uc && !empty($uc->updates)) {
        foreach ($uc->updates as $u) {
            if (
                isset($u->response) && $u->response === 'upgrade' && !empty($u->current)
                && version_compare((string) $u->current, (string) $core_cur, '>')
            ) {
                $core_latest = $u->current;
                $core_update = true;
                break;
            }
        }
    }

    $extensions = [];

    // plugin/temi di default inclusi da WP ma non installati da te -> esclusi
    $skip_plugins = ['akismet', 'hello'];   // cartelle: akismet/, hello.php
    // i temi di default WP iniziano con "twenty" (twentytwentyfour, ...)

    // PLUGIN: solo quelli di terze parti (tuoi), con flag update
    $all_plugins = get_plugins();
    $up = get_site_transient('update_plugins');
    $up_resp = ($up && !empty($up->response)) ? $up->response : [];
    foreach ($all_plugins as $file => $p) {
        $dir = dirname($file);                       // es. "advanced-custom-fields" oppure "." per file in root
        $base = strtolower($dir !== '.' ? $dir : basename($file, '.php'));
        if (in_array($base, $skip_plugins, true)) {
            continue;
        }
        $has = isset($up_resp[$file]);
        // guardia anti-falso-positivo (come il connettore Joomla): l'update vale SOLO se la
        // versione proposta e' davvero piu' alta di quella installata. Dopo un update il
        // transient puo' restare stantio con una voce a pari versione: senza questa guardia
        // lo ri-segnaleremmo come "da aggiornare" -> update riproposto ad ogni ciclo.
        if ($has) {
            $curV = isset($p['Version']) ? (string) $p['Version'] : '';
            $newV = isset($up_resp[$file]->new_version) ? (string) $up_resp[$file]->new_version : '';
            if ($curV !== '' && $newV !== '' && version_compare($newV, $curV, '<=')) {
                $has = false;
            }
        }
        $extensions[] = [
            'type'    => 'plugin',
            'name'    => isset($p['Name']) ? $p['Name'] : $file,
            'slug'    => $dir,
            'current' => isset($p['Version']) ? $p['Version'] : '',
            'new'     => $has && isset($up_resp[$file]->new_version) ? $up_resp[$file]->new_version : '',
            'update'  => $has,
        ];
    }

    // TEMI: TUTTI quelli installati, default "twenty*" COMPRESI. Prima erano esclusi
    // ("tanto uso YOOtheme"), ma un tema installato e non aggiornato e' superficie
    // d'attacco anche da disattivo: se sta sul sito, va tenuto aggiornato come il resto.
    $themes = wp_get_themes();
    $ut = get_site_transient('update_themes');
    $ut_resp = ($ut && !empty($ut->response)) ? $ut->response : [];
    foreach ($themes as $slug => $t) {
        $has = isset($ut_resp[$slug]);
        // stessa guardia versione dei plugin
        if ($has) {
            $curV = (string) $t->get('Version');
            $newV = isset($ut_resp[$slug]['new_version']) ? (string) $ut_resp[$slug]['new_version'] : '';
            if ($curV !== '' && $newV !== '' && version_compare($newV, $curV, '<=')) {
                $has = false;
            }
        }
        $extensions[] = [
            'type'    => 'theme',
            'name'    => $t->get('Name'),
            'slug'    => $slug,
            'current' => $t->get('Version'),
            'new'     => $has && isset($ut_resp[$slug]['new_version']) ? $ut_resp[$slug]['new_version'] : '',
            'update'  => $has,
        ];
    }

    // TRADUZIONI (language pack): su WP gli update delle lingue arrivano come
    // "translations" dentro i transient update_core / update_plugins / update_themes.
    // Li aggrego in UNA voce sintetica (WP li aggiorna tutti insieme), cosi' compaiono
    // nel monitoraggio e sono aggiornabili. Tipo 'language' per coerenza con Joomla.
    // Solo le traduzioni REALMENTE piu' recenti di quelle gia' installate.
    // WP a volte continua a elencare la stessa traduzione anche DOPO averla aggiornata
    // (le revisioni PO non vengono riconciliate): senza questo confronto la voce
    // "Traduzioni" resterebbe update=true per sempre -> riproposta ad ogni ciclo con
    // relativa email/Telegram, anche se non c'e' nulla di nuovo da fare.
    $installedTrans = [];
    if (function_exists('wp_get_installed_translations')) {
        foreach (['plugin' => 'plugins', 'theme' => 'themes', 'core' => 'default'] as $trType => $dom) {
            foreach ((array) wp_get_installed_translations($dom) as $islug => $langs) {
                foreach ((array) $langs as $lang => $meta) {
                    $rev = isset($meta['PO-Revision-Date']) ? strtotime((string) $meta['PO-Revision-Date']) : 0;
                    $installedTrans[$trType . '|' . $islug . '|' . $lang] = $rev ?: 0;
                }
            }
        }
    }
    $trans = [];
    foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
        $tr = get_site_transient($tname);
        if ($tr && !empty($tr->translations) && is_array($tr->translations)) {
            foreach ($tr->translations as $t) {
                $type = isset($t['type']) ? (string) $t['type'] : '';
                $slug = isset($t['slug']) ? (string) $t['slug'] : '';
                $lang = isset($t['language']) ? (string) $t['language'] : '';
                // 'default' e' lo slug del core in wp_get_installed_translations
                $islug   = ($type === 'core') ? 'default' : $slug;
                $availTs = isset($t['updated']) ? strtotime((string) $t['updated']) : 0;
                $instTs  = $installedTrans[$type . '|' . $islug . '|' . $lang] ?? 0;
                // tieni SOLO se la disponibile e' davvero piu' nuova (o se non risulta
                // installata): le voci "fantasma" a pari data (o piu' vecchie) spariscono.
                if ($availTs > 0 && $instTs > 0 && $availTs <= $instTs) {
                    continue;
                }
                $k = $type . '|' . $slug . '|' . $lang;
                $trans[$k] = $t;
            }
        }
    }
    if (!empty($trans)) {
        $n = count($trans);
        $extensions[] = [
            'type'    => 'language',
            'name'    => 'Traduzioni (' . $n . ')',
            'slug'    => 'wp-translations',
            'current' => '',
            'new'     => (string) $n,
            'update'  => true,
        ];
    }

    return new WP_REST_Response([
        'cms'  => 'wp',
        'core' => ['current' => $core_cur, 'latest' => $core_latest, 'update' => $core_update],
        'php'  => PHP_VERSION,
        'extensions' => $extensions,
    ], 200);
}

/* ---------------------------------------------------------------------------
 * Update on-demand: aggiorna UNA estensione (o il core)
 * body JSON: {"type":"plugin|theme|core", "slug":"..."}
 * ------------------------------------------------------------------------- */
function tdpanop_plugin_file_by_slug(string $slug): string
{
    if (!function_exists('get_plugins')) {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
    }
    foreach (array_keys(get_plugins()) as $file) {
        if (dirname($file) === $slug || $file === $slug) {
            return $file;
        }
    }
    return '';
}

function tdpanop_update(WP_REST_Request $req)
{
    $type = (string) $req->get_param('type');
    $slug = (string) $req->get_param('slug');

    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/update.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: update bloccati'], 200);
    }

    // refresh transient prima di agire. Con un object cache persistente il transient puo'
    // essere stantio/vuoto e wp_update_plugins() e' throttlato a 12h: l'upgrader vedrebbe
    // "up_to_date" (response mancante) e NON applicherebbe nulla. Cancellandolo prima, il
    // throttle non scatta, il controllo e' reale e l'upgrade parte davvero.
    delete_site_transient('update_plugins');
    delete_site_transient('update_themes');
    delete_site_transient('update_core');
    wp_update_plugins();
    wp_update_themes();
    wp_version_check();

    $skin = new Automatic_Upgrader_Skin();
    $res  = null;
    $verBefore = '';
    $verAfter  = '';
    $expected  = '';   // versione attesa dall'update, se nota
    $reactivated = null;   // null = non applicabile; true/false = esito riattivazione plugin

    try {
        if ($type === 'plugin') {
            $file = tdpanop_plugin_file_by_slug($slug);
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin non trovato: ' . $slug], 200);
            }
            $plugins   = get_plugins();
            $verBefore = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';

            // stato di attivazione PRIMA dell'update (per riattivarlo dopo se serve)
            $wasActive        = is_plugin_active($file);
            $wasNetworkActive = is_multisite() && is_plugin_active_for_network($file);

            // versione attesa dal transient di update
            $upd = get_site_transient('update_plugins');
            if ($upd && !empty($upd->response[$file]->new_version)) {
                $expected = (string) $upd->response[$file]->new_version;
            }

            $up  = new Plugin_Upgrader($skin);

            // IMPORTANTE: Plugin_Upgrader disattiva il plugin PRIMA dell'upgrade quando NON
            // siamo in cron (deactivate_plugin_before_upgrade), perche' assume che un browser
            // lo riattivera'. Noi giriamo via REST: nessun browser -> il plugin resterebbe spento.
            // Soluzione (stessa logica del core WP per i background update): segnaliamo a WP di
            // essere in contesto cron SOLO per la durata dell'upgrade, cosi' il plugin NON viene
            // disattivato. Usiamo un filtro mirato invece della costante DOING_CRON (piu' pulito).
            $force_cron = function () {
                return true;
            };
            add_filter('wp_doing_cron', $force_cron, 999);
            try {
                $res = $up->upgrade($file);
            } finally {
                remove_filter('wp_doing_cron', $force_cron, 999);
            }

            // svuota le cache delle opzioni: dopo l'upgrade lo stato 'active_plugins'
            // in memoria puo' essere stale, e get_plugins() ha la sua cache.
            wp_cache_delete('alloptions', 'options');
            wp_clean_plugins_cache(false);

            $plugins  = get_plugins();
            $verAfter = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';

            // RETE DI SICUREZZA (cintura + bretelle): anche se il contesto cron dovrebbe aver
            // evitato la disattivazione, se per qualunque motivo il plugin risulta spento e prima
            // era attivo, lo riattiviamo. activate_plugin e' idempotente.
            if (($wasActive || $wasNetworkActive) && !is_wp_error($res) && file_exists(WP_PLUGIN_DIR . '/' . $file)) {
                $network = $wasNetworkActive;
                if ($network ? !is_plugin_active_for_network($file) : !is_plugin_active($file)) {
                    $act = activate_plugin($file, '', $network, true);   // silent
                    if (is_wp_error($act)) {
                        activate_plugin($file, '', $network, false);     // ritenta con hook
                    }
                }
                // verifica finale: ora risulta attivo?
                wp_cache_delete('alloptions', 'options');
                $reactivated = $network ? is_plugin_active_for_network($file) : is_plugin_active($file);
            }
        } elseif ($type === 'theme') {
            $t = wp_get_theme($slug);
            $verBefore = $t->exists() ? (string) $t->get('Version') : '';

            $upd = get_site_transient('update_themes');
            if ($upd && !empty($upd->response[$slug]['new_version'])) {
                $expected = (string) $upd->response[$slug]['new_version'];
            }

            $up  = new Theme_Upgrader($skin);
            $res = $up->upgrade($slug);

            wp_clean_themes_cache(false);
            $t = wp_get_theme($slug);
            $verAfter = $t->exists() ? (string) $t->get('Version') : '';
        } elseif ($type === 'language') {
            // aggiorna TUTTI i language pack disponibili (core + plugin + temi).
            // WP li gestisce insieme con Language_Pack_Upgrader.
            require_once ABSPATH . 'wp-admin/includes/class-language-pack-upgrader.php';

            // conta le traduzioni disponibili PRIMA
            $countBefore = 0;
            foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
                $tr = get_site_transient($tname);
                if ($tr && !empty($tr->translations)) {
                    $countBefore += count($tr->translations);
                }
            }
            if ($countBefore === 0) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'nessuna traduzione da aggiornare'], 200);
            }

            $up  = new Language_Pack_Upgrader($skin);
            // upgrade() senza argomenti aggiorna tutte le lingue disponibili dai transient
            $res = $up->bulk_upgrade();

            // ricalcola dopo: rinfresca i transient e riconta
            wp_clean_update_cache();
            wp_update_plugins();
            wp_update_themes();
            wp_version_check();
            $countAfter = 0;
            foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
                $tr = get_site_transient($tname);
                if ($tr && !empty($tr->translations)) {
                    $countAfter += count($tr->translations);
                }
            }

            // successo se sono state applicate (countAfter < countBefore) o nessun errore
            $okLang = !is_wp_error($res);
            if (is_array($res)) {
                // bulk_upgrade torna array di risultati; ok se almeno uno non e' WP_Error/false
                $okLang = false;
                foreach ($res as $r) {
                    if ($r && !is_wp_error($r)) {
                        $okLang = true;
                        break;
                    }
                }
            }
            if ($okLang) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'traduzioni aggiornate (' . max(0, $countBefore - $countAfter) . '/' . $countBefore . ')'], 200);
            }
            $emsg = is_wp_error($res) ? $res->get_error_message() : 'aggiornamento traduzioni non riuscito';
            return new WP_REST_Response(['ok' => false, 'error' => $emsg, 'new' => ''], 200);
        } elseif ($type === 'core') {
            require_once ABSPATH . 'wp-admin/includes/class-core-upgrader.php';
            $updates = get_core_updates();
            if (empty($updates) || !isset($updates[0]) || $updates[0]->response !== 'upgrade') {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => get_bloginfo('version')], 200);
            }
            $verBefore = get_bloginfo('version');
            $expected  = !empty($updates[0]->current) ? (string) $updates[0]->current : '';

            $up  = new Core_Upgrader($skin);
            $res = $up->upgrade($updates[0]);

            $verAfter = $expected;   // per il core ci fidiamo dell'esito dell'upgrader (verificato sotto via is_wp_error)
        } else {
            return new WP_REST_Response(['ok' => false, 'error' => 'type non valido'], 200);
        }
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    }

    // errore esplicito dall'upgrader
    if (is_wp_error($res)) {
        return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message(), 'current' => $verBefore], 200);
    }

    // SUCCESSO solo se la versione è davvero cambiata (ed è salita a quella attesa, se nota)
    $changed = ($verAfter !== '' && $verAfter !== $verBefore);
    $reached = ($expected === '' || $verAfter === $expected);

    if ($changed && $reached) {
        $out = ['ok' => true, 'error' => '', 'new' => $verAfter];
        if ($reactivated !== null) {
            $out['reactivated'] = (bool) $reactivated;
        }
        return new WP_REST_Response($out, 200);
    }

    // fallito: prova a recuperare il motivo dai messaggi dello skin (es. credenziali FS, pacchetto non scaricabile)
    $reason = '';
    if (!empty($skin->result) && is_wp_error($skin->result)) {
        $reason = $skin->result->get_error_message();
    }
    if ($reason === '' && method_exists($skin, 'get_upgrade_messages')) {
        $msgs = $skin->get_upgrade_messages();
        if (!empty($msgs)) {
            $reason = trim(strip_tags(implode(' | ', (array) $msgs)));
        }
    }
    if ($reason === '') {
        $reason = 'versione non cambiata (' . ($verBefore ?: '?') . ' → attesa ' . ($expected ?: '?') . '): update non applicato (licenza/credenziali?)';
    }

    return new WP_REST_Response(['ok' => false, 'error' => $reason, 'new' => $expected, 'current' => $verBefore], 200);
}

/* ---------------------------------------------------------------------------
 * Install on-demand: installa (e opzionalmente attiva) un plugin/tema da zip.
 * multipart/form-data:
 *   - package : file .zip
 *   - kind    : 'plugin' | 'theme'
 *   - activate: '1' | '0' (default 1)
 * Usa gli Upgrader nativi di WP (= Plugin/Tema → Aggiungi nuovo → Carica).
 * Con overwrite_package=true reinstalla anche se gia' presente (utile per
 * ridistribuire una nuova build di un plugin custom su piu' siti).
 * ------------------------------------------------------------------------- */
function tdpanop_install(WP_REST_Request $req)
{
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/theme.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: install bloccati'], 200);
    }

    $kind     = strtolower((string) $req->get_param('kind'));
    $activate = ((string) $req->get_param('activate')) !== '0';   // default: attiva
    if (!in_array($kind, ['plugin', 'theme'], true)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'kind non valido (plugin|theme)'], 200);
    }

    // --- file caricato ---
    $files = $req->get_file_params();
    $f = isset($files['package']) ? $files['package'] : null;
    if (!$f || empty($f['tmp_name'])) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Nessun file ricevuto (campo "package")'], 200);
    }
    if (!empty($f['error'])) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Upload fallito (codice PHP ' . (int) $f['error'] . ')'], 200);
    }
    $origName = (string) (isset($f['name']) ? $f['name'] : 'package.zip');
    if (strtolower(pathinfo($origName, PATHINFO_EXTENSION)) !== 'zip') {
        return new WP_REST_Response(['ok' => false, 'error' => 'Il pacchetto deve essere un file .zip'], 200);
    }

    // --- sposta lo zip in un percorso temporaneo scrivibile ---
    $up_dir = wp_upload_dir();
    $base   = (!empty($up_dir['basedir']) && wp_is_writable($up_dir['basedir']))
        ? $up_dir['basedir']
        : get_temp_dir();
    $tmp = trailingslashit($base) . 'tdpanop-' . wp_generate_password(10, false, false) . '.zip';
    if (!@move_uploaded_file($f['tmp_name'], $tmp) && !@copy($f['tmp_name'], $tmp)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Impossibile scrivere il pacchetto in ' . $base], 200);
    }

    $skin = new Automatic_Upgrader_Skin();

    try {
        if ($kind === 'plugin') {
            $up  = new Plugin_Upgrader($skin);
            $res = $up->install($tmp, ['overwrite_package' => true]);

            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            if ($res === false) {
                $reason = tdpanop_skin_reason($skin) ?: 'Installazione plugin fallita';
                return new WP_REST_Response(['ok' => false, 'error' => $reason], 200);
            }

            wp_clean_plugins_cache(false);
            $file = method_exists($up, 'plugin_info') ? (string) $up->plugin_info() : '';
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin installato ma file principale non rilevato'], 200);
            }
            $data    = get_plugin_data(WP_PLUGIN_DIR . '/' . $file, false, false);
            $name    = !empty($data['Name']) ? $data['Name'] : $file;
            $version = !empty($data['Version']) ? $data['Version'] : '';
            $slug    = dirname($file) !== '.' ? dirname($file) : $file;

            $activated = false;
            if ($activate) {
                $act = activate_plugin($file, '', false, false);
                if (is_wp_error($act)) {
                    return new WP_REST_Response([
                        'ok' => true,
                        'error' => 'Installato ma attivazione fallita: ' . $act->get_error_message(),
                        'new' => $version,
                        'type' => 'plugin',
                        'name' => $name,
                        'slug' => $slug,
                        'activated' => false,
                    ], 200);
                }
                wp_cache_delete('alloptions', 'options');
                $activated = is_plugin_active($file);
            }

            return new WP_REST_Response([
                'ok' => true,
                'error' => '',
                'new' => $version,
                'type' => 'plugin',
                'name' => $name,
                'slug' => $slug,
                'activated' => $activated,
            ], 200);
        }

        // --- theme ---
        $up  = new Theme_Upgrader($skin);
        $res = $up->install($tmp, ['overwrite_package' => true]);

        if (is_wp_error($res)) {
            return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
        }
        if ($res === false) {
            $reason = tdpanop_skin_reason($skin) ?: 'Installazione tema fallita';
            return new WP_REST_Response(['ok' => false, 'error' => $reason], 200);
        }

        wp_clean_themes_cache(false);
        $stylesheet = method_exists($up, 'theme_info') && $up->theme_info() ? (string) $up->theme_info()->get_stylesheet() : '';
        if ($stylesheet === '') {
            // fallback: lo skin espone il risultato con destination_name
            $stylesheet = isset($up->result['destination_name']) ? (string) $up->result['destination_name'] : '';
        }
        if ($stylesheet === '') {
            return new WP_REST_Response(['ok' => false, 'error' => 'Tema installato ma stylesheet non rilevato'], 200);
        }
        $theme   = wp_get_theme($stylesheet);
        $name    = $theme->exists() ? (string) $theme->get('Name') : $stylesheet;
        $version = $theme->exists() ? (string) $theme->get('Version') : '';

        $activated = false;
        if ($activate && $theme->exists()) {
            switch_theme($stylesheet);
            $activated = (get_stylesheet() === $stylesheet);
        }

        return new WP_REST_Response([
            'ok' => true,
            'error' => '',
            'new' => $version,
            'type' => 'theme',
            'name' => $name,
            'slug' => $stylesheet,
            'activated' => $activated,
        ], 200);
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    } finally {
        if (is_file($tmp)) {
            @unlink($tmp);
        }
    }
}

/* Estrae un motivo leggibile dai messaggi dello skin dell'upgrader. */
function tdpanop_skin_reason($skin): string
{
    if (!empty($skin->result) && is_wp_error($skin->result)) {
        return $skin->result->get_error_message();
    }
    if (method_exists($skin, 'get_upgrade_messages')) {
        $msgs = $skin->get_upgrade_messages();
        if (!empty($msgs)) {
            return trim(strip_tags(implode(' | ', (array) $msgs)));
        }
    }
    return '';
}

/* ---------------------------------------------------------------------------
 * Uninstall on-demand: rimuove un plugin o un tema.
 * body JSON: {"type":"plugin|theme", "slug":"..."}
 * Sicurezze: non rimuove se stesso, non rimuove il tema attivo.
 * ------------------------------------------------------------------------- */
function tdpanop_uninstall(WP_REST_Request $req)
{
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/theme.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: rimozioni bloccate'], 200);
    }

    $type = strtolower((string) $req->get_param('type'));
    $slug = (string) $req->get_param('slug');
    if ($slug === '') {
        return new WP_REST_Response(['ok' => false, 'error' => 'slug mancante'], 200);
    }

    try {
        if ($type === 'plugin') {
            $file = tdpanop_plugin_file_by_slug($slug);
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin non trovato: ' . $slug], 200);
            }
            // non rimuovere il connettore stesso
            if (dirname($file) === 'td-panopticon' || strpos($file, 'td-panopticon') === 0) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Non posso rimuovere il connettore Sentinel TD stesso'], 200);
            }
            $name = '';
            $pdata = get_plugin_data(WP_PLUGIN_DIR . '/' . $file, false, false);
            $name = !empty($pdata['Name']) ? $pdata['Name'] : $file;

            if (is_plugin_active($file) || (is_multisite() && is_plugin_active_for_network($file))) {
                deactivate_plugins([$file], true);
            }
            $res = delete_plugins([$file]);
            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            wp_clean_plugins_cache(false);
            return new WP_REST_Response(['ok' => true, 'error' => '', 'type' => 'plugin', 'name' => $name, 'slug' => $slug, 'removed' => true], 200);
        } elseif ($type === 'theme') {
            $t = wp_get_theme($slug);
            if (!$t->exists()) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Tema non trovato: ' . $slug], 200);
            }
            // non rimuovere il tema attivo (ne' il suo parent se attivo come child)
            if (get_stylesheet() === $slug || get_template() === $slug) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Tema attivo: non rimuovibile (attivane un altro prima)'], 200);
            }
            $name = (string) $t->get('Name');
            $res = delete_theme($slug);
            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            wp_clean_themes_cache(false);
            return new WP_REST_Response(['ok' => true, 'error' => '', 'type' => 'theme', 'name' => $name, 'slug' => $slug, 'removed' => true], 200);
        }

        return new WP_REST_Response(['ok' => false, 'error' => 'type non valido (plugin|theme)'], 200);
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    }
}

/* Autologin one-time (60s). Link verso la home per non urtare login custom. */
function tdpanop_autologin()
{
    $admins = get_users(['role' => 'administrator', 'number' => 1, 'orderby' => 'ID', 'order' => 'ASC']);
    if (empty($admins)) {
        return new WP_Error('no_admin', 'Nessun amministratore', ['status' => 404]);
    }
    $uid = $admins[0]->ID;
    $key = wp_generate_password(40, false);
    set_transient('tdpanop_al_' . $key, $uid, 60);
    return new WP_REST_Response(['url' => add_query_arg(['tdpanop_al' => $key], home_url('/'))], 200);
}

add_action('init', function () {
    if (empty($_GET['tdpanop_al'])) {
        return;
    }
    $key = sanitize_text_field($_GET['tdpanop_al']);
    $uid = get_transient('tdpanop_al_' . $key);
    if (!$uid) {
        return;
    }
    delete_transient('tdpanop_al_' . $key);
    wp_set_current_user($uid);
    wp_set_auth_cookie($uid, false);
    wp_safe_redirect(admin_url());
    exit;
});
