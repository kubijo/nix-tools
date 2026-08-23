{ lib,pkgs,... }:
let
    values = [ 1   2 3 ];
  helper = x: x+1;
in {
  inherit values ;
    mapped=map helper values;
  nested = { deep = { deeper = lib.strings.toUpper "value"; }; };
    package=pkgs.hello;
}
