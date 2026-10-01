# A command receives one file path and exits non-zero to fail it.
{
  lib,
  toolPkgs,
  args,
}:
let
  inherit (toolPkgs) writeShellScript;
  inherit (import ./options.nix { inherit lib; }) xmlIncludes;

  configFlagged = import ./config-path.nix { inherit toolPkgs; };

  biomeLint = {
    package = toolPkgs.biome;
    binary = "biome";
    options = [
      "lint"
      "--error-on-warnings"
    ];
    configFile = ../conf/biome.json;
    configFlag = configFlagged "--config-path";
    batch = true;
  };

  defaults = {
    salt = {
      package = toolPkgs.salt-lint;
      binary = "salt-lint";
      options = [ "--nocolor" ];
      configFile = ../conf/salt-lint.yaml;
      configFlag = configFlagged "-c";
      includes = [
        "*.sls"
        "*.j2"
        "*.jinja"
      ];
      batch = true;
    };
    whitespace = {
      package = toolPkgs.editorconfig-checker;
      binary = "editorconfig-checker";
      mkCommand =
        exe:
        writeShellScript "whitespace-check" ''
          exec ${lib.getExe (import ./whitespace.nix { inherit toolPkgs; })} ${exe} "$@"
        '';
      options = [ "check" ];
      configFile = ../conf/editorconfig;
      configFlag = import ./whitespace-policy.nix { inherit lib; };
      includes = [
        "*.sls"
        "*.j2"
        "*.jinja"
      ];
      batch = true;
    };

    php = {
      package = toolPkgs.mago;
      binary = "mago";
      options = [ "lint" ];
      configFile = ../conf/mago.toml;
      configFlag = configFlagged "--config";
      configFirst = true;
      includes = [
        "*.php"
        "*.inc"
      ];
      batch = true;
    };

    nix = {
      includes = [ "*.nix" ];
      # Short-circuiting would hide every deadnix finding behind an unrelated statix one.
      mkCommand =
        _:
        writeShellScript "nix-check" ''
          status=0
          ${lib.getExe toolPkgs.statix} check "$1" || status=1
          ${lib.getExe toolPkgs.deadnix} --fail "$1" || status=1
          exit "$status"
        '';
    };

    shell = {
      package = toolPkgs.shellcheck;
      binary = "shellcheck";
      # `-x` follows `source`d files rather than warning that it cannot.
      options = [ "-x" ];
      includes = [
        "*.sh"
        "*.bash"
        ".envrc"
      ];
    };

    yaml = {
      package = toolPkgs.yamllint;
      binary = "yamllint";
      options = [ "--strict" ];
      configFile = ../conf/yamllint.yml;
      configFlag = configFlagged "--config-file";
      includes = [
        "*.yaml"
        "*.yml"
      ];
    };

    python = {
      package = toolPkgs.ruff;
      binary = "ruff";
      options = [
        "check"
        "--no-cache"
      ];
      configFile = ../conf/ruff.toml;
      configFlag = configFlagged "--config";
      includes = [
        "*.py"
        "*.pyi"
      ];
      batch = true;
    };

    # `--error-on-warnings`: biome otherwise exits 0 on anything below an error, which would
    # surface in an editor and never fail here.
    javascript = biomeLint // {
      includes = [
        "*.js"
        "*.mjs"
        "*.cjs"
        "*.jsx"
      ];
    };

    typescript = biomeLint // {
      includes = [
        "*.ts"
        "*.mts"
        "*.cts"
        "*.tsx"
      ];
    };

    links = {
      package = toolPkgs.lychee;
      binary = "lychee";
      # `--offline` resolves on-disk targets only, so an unreachable host cannot fail this.
      options = [
        "--offline"
        "--no-progress"
      ];
      # lychee builds its HTTP client at startup even offline, and that wants a CA bundle.
      mkCommand =
        exe:
        writeShellScript "link-check" ''
          export SSL_CERT_FILE=${toolPkgs.cacert}/etc/ssl/certs/ca-bundle.crt
          exec ${exe} "$@"
        '';
      mkOptions = opts: lib.concatMap (p: [ "--exclude" ] ++ [ p ]) opts.ignoreLinks;
      includes = [
        "*.md"
        "*.markdown"
      ];
      batch = true;
    };

    workflows = {
      package = toolPkgs.actionlint;
      binary = "actionlint";
      configFlag = configFlagged "-config-file";
      # fd globs match the file name, so a directory has to be a search root instead.
      searchPaths = [
        ".github/workflows"
        ".forgejo/workflows"
      ];
      includes = [
        "*.yml"
        "*.yaml"
      ];
    };

    protobuf = {
      package = toolPkgs.buf;
      binary = "buf";
      options = [ "lint" ];
      configFlag = configFlagged "--config";
      includes = [ "*.proto" ];
      batch = true;
    };

    sql = {
      package = toolPkgs.sqlfluff;
      binary = "sqlfluff";
      options = [ "lint" ];
      configFlag = configFlagged "--config";
      includes = [ "*.sql" ];
      batch = true;
      requiresConfig = true;
    };

    po = {
      package = toolPkgs.gettext;
      binary = "msgfmt";
      options = [ "--output-file=/dev/null" ];
      includes = [ "*.po" ];
    };

    xml = {
      package = toolPkgs.libxml2;
      binary = "xmllint";
      options = [
        "--nonet"
        "--strict-namespace"
        "--noout"
      ];
      includes = xmlIncludes;
      batch = true;
    };
  };

  resolve =
    name: opts:
    let
      def = defaults.${name};

      exe =
        if opts.exe != null then
          opts.exe
        else if opts.package != null then
          lib.getExe' opts.package def.binary
        else if def ? package then
          lib.getExe' def.package def.binary
        else
          null;

      configFile = if opts.configFile != null then opts.configFile else def.configFile or null;

      argv =
        (
          let
            configArgs = lib.optionals (configFile != null) (def.configFlag configFile);
          in
          if def.configFirst or false then
            configArgs ++ (def.options or [ ])
          else
            (def.options or [ ]) ++ configArgs
        )
        ++ (def.mkOptions or (_: [ ])) opts;
    in
    assert lib.assertMsg (
      opts.configFile == null || def ? configFlag
    ) "${name}: this checker takes no config path — pass the flag through `extraOptions`";
    assert lib.assertMsg (
      !(def.requiresConfig or false) || configFile != null
    ) "${name}: this checker requires `configFile`";
    {
      inherit name;
      inherit (opts) stdin;
      command = if def ? mkCommand then def.mkCommand exe else exe;
      options = argv ++ opts.extraOptions ++ lib.optional (name == "whitespace") "--";
      includes = if opts.includes != null then opts.includes else def.includes;
      excludes = opts.exclude;
      searchPaths = def.searchPaths or [ "." ];
      batch = if opts.batch != null then opts.batch else def.batch or false;
    };
in
# Keyed by name, so a spliced checker of the same name overrides rather than joining it.
lib.mapAttrs resolve (lib.filterAttrs (_: opts: opts.enable) args)
