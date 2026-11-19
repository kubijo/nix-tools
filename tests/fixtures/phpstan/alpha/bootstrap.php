<?php

if (ini_get('precision') !== '9') {
    throw new RuntimeException('PHPStan did not use the selected PHP configuration');
}
