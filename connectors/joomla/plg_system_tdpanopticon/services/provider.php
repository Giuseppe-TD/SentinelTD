<?php
defined('_JEXEC') or die;

use Joomla\CMS\Extension\PluginInterface;
use Joomla\CMS\Plugin\PluginHelper;
use Joomla\DI\Container;
use Joomla\DI\ServiceProviderInterface;
use Joomla\Event\DispatcherInterface;
use TastiereDigitali\Plugin\System\Tdpanopticon\Extension\Tdpanopticon;

return new class () implements ServiceProviderInterface {
    public function register(Container $container): void
    {
        $container->set(
            PluginInterface::class,
            function (Container $container) {
                $config  = (array) PluginHelper::getPlugin('system', 'tdpanopticon');
                $subject = $container->get(DispatcherInterface::class);
                $plugin  = new Tdpanopticon($subject, $config);
                $plugin->setApplication(\Joomla\CMS\Factory::getApplication());
                return $plugin;
            }
        );
    }
};
