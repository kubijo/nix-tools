<?php

namespace Acme;

final class Response
{
    /** @return non-empty-list<array{id: int, name: string}> */
    public function devices(): array
    {
        return [['id' => 1, 'name' => 'router']];
    }
}
