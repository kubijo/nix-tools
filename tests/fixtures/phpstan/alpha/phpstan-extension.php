<?php

require_once __DIR__ . '/vendor/autoload.php';

return [
    'services' => [
        'fixture.rule' => [
            'class' => Fixture\NoEnvironmentRule::class,
            'tags' => ['phpstan.rules.rule'],
        ],
    ],
];
