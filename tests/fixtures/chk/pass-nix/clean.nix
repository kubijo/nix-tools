{ lib }:
let
  double = n: n * 2;
in
{
  inherit (lib) mapAttrs;
  values = map double [
    1
    2
  ];
}
