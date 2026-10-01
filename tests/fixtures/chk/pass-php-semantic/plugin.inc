<?php
namespace Engined\Module;

class Plugin extends \OMV\Engine\Module\ServiceAbstract
{
    public function getStatus()
    {
        return \OMV\Config\Database::getInstance()->get("conf.service.example");
    }
}
