{ lib }:
phase: schema: value:
let
  inherit (builtins) length;
  opts = (import ./options.nix { inherit lib; }).toggle schema value;
in
assert lib.assertMsg
  (
    !opts.enable
    || (
      lib.isList opts.includes
      && opts.includes != [ ]
      && lib.all (pattern: lib.isString pattern && pattern != "") opts.includes
    )
  )
  "${phase}: djLint HTML template entries require explicit nonempty `includes`, for example { includes = [ \"*.html.njk\" ]; }; select only HTML templates";
assert lib.assertMsg (!opts.enable || (opts.options or null) == null)
  "${phase}: djLint controls its invocation; use `extraOptions` for native style/rule options instead of replacing `options`";
opts
// {
  # Frame native options independently of their contents.
  # A user-supplied "--" must be validated as an option,
  # never mistaken for the runner's file boundary.
  extraOptions = [ (toString (length opts.extraOptions)) ] ++ opts.extraOptions;
}
