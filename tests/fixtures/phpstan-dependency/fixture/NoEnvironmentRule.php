<?php

namespace Fixture;

use PhpParser\Node;
use PhpParser\Node\Expr\FuncCall;
use PhpParser\Node\Name;
use PHPStan\Analyser\Scope;
use PHPStan\Rules\Rule;
use PHPStan\Rules\RuleErrorBuilder;

/** @implements Rule<FuncCall> */
final class NoEnvironmentRule implements Rule
{
    public function getNodeType(): string
    {
        return FuncCall::class;
    }

    public function processNode(Node $node, Scope $scope): array
    {
        if ($node->name instanceof Name && $node->name->toString() === 'getenv') {
            return [RuleErrorBuilder::message('Fixture extension rejects getenv')->identifier('fixture.environment')->build()];
        }

        return [];
    }
}
