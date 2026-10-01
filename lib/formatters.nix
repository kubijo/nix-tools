# The raw treefmt `formatter` attrset, separate so it can be asserted over directly.
{
  lib,
  system,
  toolPkgs,
  nodejs,
  nix-gritql,
  args,
}:
let
  inherit (builtins) readFile;
  # Both node tools are rebuilt against the one runtime, so enabling both lands one closure.
  onNode =
    name: drv:
    if nodejs != null then
      drv.override { inherit nodejs; }
    else
      throw "the `${name}` formatter runs on node, and there is deliberately no default. Add `nodejs = pkgs.nodejs_24;` to your `nix-tools.lib.configure { ... }` call, beside `format.${name} = true;`, so it uses the node this repo already pins.";

  inherit (toolPkgs) writeShellScript;
  inherit (import ./options.nix { inherit lib; }) toTreefmtExcludes xmlIncludes;

  rewriteOnDiff = import ./rewrite-on-diff.nix { toolPkgsFor = _: toolPkgs; };

  xmlFormat = toolPkgs.writeShellApplication {
    name = "xml-format";
    runtimeInputs = [
      toolPkgs.coreutils
      toolPkgs.diffutils
    ];
    text = readFile ./xml-format.sh;
  };

  gritFormat = toolPkgs.writeShellApplication {
    name = "grit-format";
    runtimeInputs = [
      toolPkgs.coreutils
      toolPkgs.diffutils
    ];
    text = readFile ./grit-format.sh;
  };

  # The wrapper reads the tool and its argv at run time, so both stay in `options`.
  sidecar =
    name: mkRender:
    lib.getExe (rewriteOnDiff {
      inherit system name;
      render = mkRender "\"\$tool\"" "\"\${args[@]}\"";
    });

  biome = {
    package = toolPkgs.biome;
    binary = "biome";
    options = [
      "format"
      "--write"
    ];
    configFile = ../conf/biome.json;
    configFlag = configFlagged "--config-path";
  };

  # treefmt hashes a formatter's name, joined options, priority and its exe's size and mtime
  # (2.5, format/formatter.go:74) — so a config reached any other way cannot bust that cache.
  configFlagged = import ./config-path.nix { inherit toolPkgs; };

  # `mkCommand` replaces the exe with a wrapper, for a tool taking one file at a time.
  defaults = {
    whitespace = {
      package = toolPkgs.editorconfig-checker;
      binary = "editorconfig-checker";
      options = [ "fix" ];
      configFile = ../conf/editorconfig;
      configFlag = import ./whitespace-policy.nix { inherit lib; };
      includes = [
        "*.sls"
        "*.j2"
        "*.jinja"
      ];
      # The adapter closure must participate in treefmt's argv cache key too.
      wrapperOptions = [ (lib.getExe (import ./whitespace.nix { inherit toolPkgs; })) ];
      mkCommand = writeShellScript "whitespace-format" ''
        tool=$1
        adapter=$2
        shift 2
        exec "$adapter" "$tool" "$@"
      '';
    };

    php = {
      package = toolPkgs.mago;
      binary = "mago";
      options = [ "format" ];
      configFile = ../conf/mago.toml;
      configFlag = configFlagged "--config";
      configFirst = true;
      includes = [
        "*.php"
        "*.inc"
      ];
    };

    debian = {
      package = toolPkgs.callPackage ../nix/debputy.nix { };
      binary = "debputy-nix-tools";
      options = [
        "reformat"
        "--style=black"
        "--auto-fix"
        "--no-linter-exit-code"
      ];
      configFile = ../conf/debputy.yaml;
      configFlag = configFlagged "--config";
      includes = [
        "debian/control"
        "debian/copyright"
        "debian/tests/control"
        "debian/watch"
      ];
      mkCommand = writeShellScript "debian-format" ''
        tool=$1
        shift
        exec "$tool" "$@"
      '';
    };

    nix = {
      package = toolPkgs.nixfmt;
      binary = "nixfmt";
      includes = [ "*.nix" ];
      fence.tags = [ "nix" ];
    };

    shell = {
      package = toolPkgs.shfmt;
      binary = "shfmt";
      options = [
        "--write"
        "--simplify"
        "--binary-next-line"
        "--indent"
        "4"
      ];
      includes = [
        "*.sh"
        "*.bash"
        "*.envrc"
        "*.envrc.*"
      ];
      fence = {
        tags = [
          "bash"
          "sh"
          "shell"
        ];
        drop = [ "--write" ];
      };
    };

    toml = {
      package = toolPkgs.taplo;
      binary = "taplo";
      options = [ "format" ];
      configFile = ../conf/taplo.toml;
      configFlag = configFlagged "--config";
      includes = [ "*.toml" ];
      fence = {
        tags = [ "toml" ];
        extra = _: [ "-" ];
      };
    };

    yaml = {
      package = toolPkgs.yamlfmt;
      binary = "yamlfmt";
      configFile = ../conf/yamlfmt.yml;
      configFlag = configFlagged "-conf";
      includes = [
        "*.yaml"
        "*.yml"
      ];
    };

    json = biome // {
      includes = [
        "*.json"
        "*.jsonc"
      ];
      # The stdin filename is what tells biome whether comments are legal.
      fence = {
        tags = [
          "json"
          "jsonc"
        ];
        drop = [ "--write" ];
        extra = tag: [
          "--stdin-file-path"
          "fence.${tag}"
        ];
      };
    };

    javascript = biome // {
      includes = [
        "*.js"
        "*.mjs"
        "*.cjs"
        "*.jsx"
      ];
    };

    typescript = biome // {
      includes = [
        "*.ts"
        "*.mts"
        "*.cts"
        "*.tsx"
      ];
    };

    css = biome // {
      includes = [ "*.css" ];
    };

    html = biome // {
      includes = [
        "*.html"
        "*.htm"
      ];
    };

    graphql = biome // {
      includes = [
        "*.graphql"
        "*.gql"
      ];
    };

    # biome has no SCSS parser.
    scss = {
      package = onNode "scss" toolPkgs.prettier;
      binary = "prettier";
      options = [ "--write" ];
      configFile = ../conf/prettier.json;
      configFlag = configFlagged "--config";
      includes = [
        "*.scss"
        "*.sass"
      ];
    };

    rust = {
      # nixpkgs' rustfmt drags in a whole toolchain, 1.6 GiB beside the one a repo pins.
      package = throw "the `rust` formatter needs a toolchain, and there is deliberately no default. Add `format.rust.exe = lib.getExe' <your toolchain> \"rustfmt\";` to your `nix-tools.lib.configure { ... }` call, so it uses the toolchain this repo already pins.";
      binary = "rustfmt";
      options = [
        "--edition"
        "2024"
      ];
      configFlag = configFlagged "--config-path";
      includes = [ "*.rs" ];
      fence = {
        tags = [ "rust" ];
        extra = _: [
          "--emit"
          "stdout"
          "--quiet"
        ];
      };
    };

    python = {
      package = toolPkgs.ruff;
      binary = "ruff";
      options = [
        "format"
        "--no-cache"
      ];
      configFile = ../conf/ruff.toml;
      configFlag = configFlagged "--config";
      includes = [
        "*.py"
        "*.pyi"
      ];
      fence = {
        tags = [
          "python"
          "py"
        ];
        extra = _: [
          "--stdin-filename"
          "fence.py"
          "-"
        ];
      };
    };

    # buf's output is canonical, so a buf.yaml changes nothing here.
    protobuf = {
      package = toolPkgs.buf;
      binary = "buf";
      options = [
        "format"
        "--write"
      ];
      configFlag = configFlagged "--config";
      includes = [ "*.proto" ];
    };

    # sqlfluff cannot guess a dialect, so this needs a `configFile` naming one.
    sql = {
      package = toolPkgs.sqlfluff;
      binary = "sqlfluff";
      options = [ "format" ];
      configFlag = configFlagged "--config";
      includes = [ "*.sql" ];
    };

    po = {
      mkCommand = sidecar "po-format" (
        tool: opts: src: out:
        "${tool} --no-wrap ${opts} --output-file=${out} ${src}"
      );
      package = toolPkgs.gettext;
      binary = "msgcat";
      includes = [
        "*.po"
        "*.pot"
      ];
    };

    grit = {
      mkCommand = lib.getExe gritFormat;
      package = nix-gritql.lib.mkGrit { inherit toolPkgs; };
      binary = "grit";
      options = [
        "format"
        "--write"
        "--output"
        "none"
      ];
      includes = [ "*.grit" ];
    };

    xml = {
      mkCommand = lib.getExe xmlFormat;
      package = toolPkgs.libxml2;
      binary = "xmllint";
      options = [
        "--nonet"
        "--strict-namespace"
        "--format"
      ];
      includes = xmlIncludes;
    };

    svg = {
      mkCommand = sidecar "svg-format" (
        tool: opts: src: out:
        "${tool} --quiet ${opts} --input ${src} --output ${out}"
      );
      package = onNode "svg" toolPkgs.svgo;
      binary = "svgo";
      configFlag = configFlagged "--config";
      includes = [ "*.svg" ];
    };

    # `--preserve` keeps mtime, so an already-optimised file does not read as changed.
    png = {
      package = toolPkgs.oxipng;
      binary = "oxipng";
      options = [
        "--quiet"
        "--preserve"
        "--strip"
        "safe"
        "--opt"
        "max"
      ];
      includes = [ "*.png" ];
    };

    caddyfile = {
      # `caddy fmt` exits 1 on unformatted input; the sidecar's size guard still catches
      # a genuine failure.
      mkCommand = sidecar "caddy-format" (
        tool: opts: src: out:
        "${tool} fmt ${opts} ${src} > ${out} || true"
      );
      package = toolPkgs.caddy;
      binary = "caddy";
      includes = [
        "Caddyfile"
        "**/Caddyfile"
        "*.caddyfile"
      ];
    };

    markdown = {
      package = toolPkgs.mdformat.withPlugins (
        p:
        [
          p.mdformat-gfm
          p.mdformat-frontmatter
          p.mdformat-simple-breaks
        ]
        ++ lib.optional (fenceTags != [ ]) fencePlugin
      );
      binary = "mdformat";
      # Naming each tag makes mdformat require it, so a plugin that stops loading
      # is an error rather than fences quietly going unformatted.
      options = [
        "--number"
        "--wrap=120"
      ]
      ++ (
        if fenceTags == [ ] then
          [ "--no-codeformatters" ]
        else
          lib.concatMap (tag: [
            "--codeformatters"
            tag
          ]) fenceTags
      );
      includes = [
        "*.md"
        "*.markdown"
      ];
    };

    justfile = {
      package = toolPkgs.just;
      binary = "just";
      # One file at a time, hence the loop. `--fmt` is unstable upstream, hence `--unstable`.
      mkCommand = writeShellScript "just-format" ''
        opts=()
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "--" ]; then
            shift
            break
          fi
          opts+=("$1")
          shift
        done

        for file in "$@"; do
          "''${opts[0]}" "''${opts[@]:1}" --unstable --fmt --justfile "$file"
        done
      '';
      includes = [
        "justfile"
        "**/justfile"
        "Justfile"
        "**/Justfile"
        "*.just"
        "*.justfile"
      ];
    };
  };

  # Built from the resolved argv, so an injected rustfmt reaches fenced rust too,
  # and a language switched off takes its tags with it.
  fenceCommands = lib.foldl' (
    acc: name:
    acc
    // lib.optionalAttrs (args.${name}.enable && defaults.${name} ? fence) (
      let
        spec = defaults.${name}.fence;
        resolved = resolve name args.${name};
      in
      lib.genAttrs spec.tags (
        tag:
        [ resolved.command ]
        ++ lib.subtractLists (spec.drop or [ ]) resolved.options
        ++ (spec.extra or (_: [ ])) tag
      )
    )
  ) { } (lib.attrNames args);

  fenceTags = lib.attrNames fenceCommands;

  fencePlugin = import ./fences.nix {
    inherit lib toolPkgs;
    commands = fenceCommands;
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
        else
          lib.getExe' def.package def.binary;

      configFile = if opts.configFile != null then opts.configFile else def.configFile or null;

      argv =
        if opts.options != null then
          opts.options
        else
          let
            configArgs = lib.optionals (configFile != null) (def.configFlag configFile);
          in
          if def.configFirst or false then
            configArgs ++ (def.options or [ ])
          else
            (def.options or [ ]) ++ configArgs;
    in
    assert lib.assertMsg (opts.configFile == null || def ? configFlag)
      "${name}: this formatter takes no config path — pass the flag through `extraOptions`, or replace the argv with `options`";
    let
      options = argv ++ opts.extraOptions;
      wrapped = def ? mkCommand;
    in
    {
      command = if wrapped then def.mkCommand else exe;
      # The tool leads, then its argv, then `--` before the file list.
      options =
        if wrapped then [ exe ] ++ (def.wrapperOptions or [ ]) ++ options ++ [ "--" ] else options;
      includes = if opts.includes != null then opts.includes else def.includes;
      excludes = toTreefmtExcludes opts.exclude;
      priority = if opts.priority != null then opts.priority else def.priority or 0;
    };

  enabled = lib.filterAttrs (_: opts: opts.enable) args;

  # A separate lower-priority treefmt phase: the normal Biome formatter tidies the
  # imports after the assist moves them. Both phases resolve the same package and config.
  organizeImports =
    name: opts:
    let
      def = defaults.${name};
      normal = resolve name opts;
      exe =
        if opts.exe != null then
          opts.exe
        else if opts.package != null then
          lib.getExe' opts.package def.binary
        else
          lib.getExe' def.package def.binary;
      configFile = if opts.configFile != null then opts.configFile else def.configFile or null;
    in
    lib.nameValuePair "${name}-organize-imports" {
      command = exe;
      options = [
        "check"
        "--write"
        "--only=assist/source/organizeImports"
      ]
      ++ lib.optionals (configFile != null) (def.configFlag configFile);
      inherit (normal) includes excludes;
      priority = normal.priority - 1;
    };

  importOrganizers = lib.mapAttrs' organizeImports (
    lib.filterAttrs (_: opts: opts.enable && (opts.organizeImports or false)) args
  );
in
lib.mapAttrs resolve enabled // importOrganizers
