<?php
namespace TastiereDigitali\Plugin\System\Tdpanopticon\Field;

defined('_JEXEC') or die;

use Joomla\CMS\Factory;
use Joomla\CMS\Form\Field\TextField;

/**
 * Campo che genera il token alla prima apertura e lo persiste nei params del plugin.
 * Mostra il valore readonly con bottoni Copia / Rigenera.
 */
class TdtokenField extends TextField
{
    protected $type = 'Tdtoken';

    protected function getInput()
    {
        $app = Factory::getApplication();

        // rigenera se richiesto (solo backend, l'utente e' gia' nell'editor del plugin)
        $regen = $app->isClient('administrator') && $app->getInput()->getInt('tdpanop_regen', 0) === 1;

        $token = (string) $this->value;
        if ($token === '' || $regen) {
            $token = bin2hex(random_bytes(24));
            $this->persist($token);
            $this->value = $token;
        }

        $id  = htmlspecialchars($this->id, ENT_QUOTES);
        $val = htmlspecialchars($token, ENT_QUOTES);

        // URL della stessa pagina + flag rigenera
        $uri = clone \Joomla\CMS\Uri\Uri::getInstance();
        $uri->setVar('tdpanop_regen', '1');
        $regenUrl = htmlspecialchars($uri->toString(), ENT_QUOTES);

        return <<<HTML
<input type="text" id="{$id}" name="{$this->name}" value="{$val}" readonly
       style="width:480px;max-width:100%;font-family:monospace" onclick="this.select()">
<button type="button" class="btn btn-secondary"
        onclick="navigator.clipboard.writeText(document.getElementById('{$id}').value)">Copia</button>
<a class="btn btn-outline-danger" href="{$regenUrl}"
   onclick="return confirm('Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD.');">Rigenera</a>
<div class="form-text">Copia questo token e incollalo quando aggiungi il sito in Sentinel TD.</div>
HTML;
    }

    private function persist(string $token): void
    {
        $db = Factory::getContainer()->get('DatabaseDriver');
        $q = $db->getQuery(true)
            ->select($db->quoteName('params'))
            ->from($db->quoteName('#__extensions'))
            ->where($db->quoteName('type') . ' = ' . $db->quote('plugin'))
            ->where($db->quoteName('folder') . ' = ' . $db->quote('system'))
            ->where($db->quoteName('element') . ' = ' . $db->quote('tdpanopticon'));
        $db->setQuery($q);
        $raw = $db->loadResult();

        $params = $raw ? json_decode($raw, true) : [];
        if (!is_array($params)) { $params = []; }
        $params['token'] = $token;

        $upd = $db->getQuery(true)
            ->update($db->quoteName('#__extensions'))
            ->set($db->quoteName('params') . ' = ' . $db->quote(json_encode($params)))
            ->where($db->quoteName('type') . ' = ' . $db->quote('plugin'))
            ->where($db->quoteName('folder') . ' = ' . $db->quote('system'))
            ->where($db->quoteName('element') . ' = ' . $db->quote('tdpanopticon'));
        $db->setQuery($upd);
        $db->execute();
    }
}
