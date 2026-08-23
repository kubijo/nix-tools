# nixpkgs' rustfmt, since this fixture has no toolchain of its own to point at.
{ toolPkgs }:
{
  rust.package = toolPkgs.rustfmt;
}
