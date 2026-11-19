<?php

declare(strict_types=1);

namespace Client;

use Acme\Response;

function firstDeviceId(Response $response): int
{
    $devices = $response->devices();

    return $devices[0]['id'];
}
