{ lib, toolPkgsFor }:
let
  options = import ./options.nix { inherit lib; };
  inherit (options)
    toggle
    formatterOptions
    formatterSpecOptions
    defaultExcludes
    toTreefmtExcludes
    ;
in
{
  system,
  toolPkgs ? toolPkgsFor system,
  # No default: a node tool takes the runtime this repo already has, or none at all.
  nodejs ? null,

  name ? "repofmt",
  # On treefmt 2.5, `error` logs and still exits 0, even under `--fail-on-change`.
  onUnmatched ? "fatal",
  walk ? "auto",
  treeRootFile ? "flake.nix",
  exclude ? [ ],
  unexclude ? [ ],
  excludeDefaults ? true,
  # PATH is nulled, so whatever a custom `command` shells out to belongs here.
  extraRuntimeInputs ? [ ],
  # Keyed by name: colliding with a built-in overrides it rather than adding a second.
  extraFormatters ? { },

  nix ? true,
  shell ? true,
  markdown ? true,
  toml ? true,
  yaml ? true,
  json ? true,
  justfile ? true,

  # Off by default: `onUnmatched = "fatal"` then reports the first unclaimed `.rs`,
  # instead of formatting it with a toolchain the repo never chose.
  rust ? false,
  python ? false,
  javascript ? false,
  typescript ? false,
  css ? false,
  html ? false,
  graphql ? false,
  scss ? false,
  protobuf ? false,
  sql ? false,
  po ? false,
  svg ? false,
  png ? false,
  caddyfile ? false,
}:
let
  args = lib.mapAttrs (_: toggle formatterOptions) {
    inherit
      nix
      shell
      markdown
      toml
      yaml
      json
      justfile
      rust
      python
      javascript
      typescript
      css
      html
      graphql
      scss
      protobuf
      sql
      po
      svg
      png
      caddyfile
      ;
  };

  formatters =
    import ./formatters.nix {
      inherit
        lib
        system
        toolPkgs
        nodejs
        args
        ;
    }
    // lib.mapAttrs (_: formatterSpecOptions) extraFormatters;

  # Subtracts before expansion, so "Cargo.toml" need only be named once.
  excludes = toTreefmtExcludes (
    lib.optionals excludeDefaults (lib.subtractLists unexclude defaultExcludes) ++ exclude
  );

  treefmtConfig = toolPkgs.treefmt.buildConfig {
    on-unmatched = onUnmatched;
    inherit excludes;
    formatter = formatters;
  };
in
assert lib.assertMsg (lib.elem onUnmatched [
  "fatal"
  "error"
  "warn"
  "info"
  "debug"
]) "onUnmatched: expected one of fatal|error|warn|info|debug, got ${onUnmatched}";
lib.warnIf (onUnmatched == "error")
  "onUnmatched = \"error\" logs and still exits 0 on treefmt 2.5 — use \"fatal\" to gate"
  (
    toolPkgs.writeShellApplication {
      inherit name;
      runtimeInputs = [
        toolPkgs.gitMinimal
        # `buf format` shells out to `diff`, which PATH being nulled would otherwise hide.
        toolPkgs.diffutils
      ]
      ++ extraRuntimeInputs;
      runtimeEnv.PATH = null;
      excludeShellChecks = [ "SC2123" ];
      # Carried forward so a dev shell or validate step reuses it instead of adding a second.
      passthru = { inherit nodejs; };
      # Searches upward for the marker, so a subdirectory and a sandbox copy both work.
      # Without it, `--walk auto` outside a git repo roots at the config file: /nix/store.
      text = ''
        exec ${lib.getExe toolPkgs.treefmt} \
          --config-file ${treefmtConfig} \
          --tree-root-file ${treeRootFile} \
          --walk ${walk} \
          "$@"
      '';
    }
  )
