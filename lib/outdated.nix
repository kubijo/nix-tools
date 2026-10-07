{
  lib,
  toolPkgs,
  python,
}:
{
  config ? false,
  treeRootFile,
  entries,
}:
let
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./outdated
      ./terminal_env.py
    ];
  };
  toolRecords = import ./tool-records.nix { inherit lib toolPkgs; };
  nodeReport = import ./outdated-node.nix { inherit lib toolPkgs; };
  inherit (builtins)
    functionArgs
    isAttrs
    isBool
    isInt
    isList
    isPath
    isString
    match
    toJSON
    unsafeDiscardStringContext
    ;
  checked =
    schema: value:
    assert lib.assertMsg (
      isAttrs value && lib.all (key: (functionArgs schema) ? ${key}) (lib.attrNames value)
    ) "outdated: expected an attribute set containing only supported options";
    schema value;
  toggle = schema: value: checked schema (if isBool value then { enable = value; } else value);
  provider =
    {
      enable ? true,
      root ? ".",
      package ? null,
      exe ? null,
      path ? ".github/workflows",
      lockFile ? "flake.lock",
      projects ? null,
    }:
    assert lib.assertMsg (
      isBool enable && isString root && isString path && isString lockFile
    ) "outdated provider paths must be strings and enable must be boolean";
    assert lib.assertMsg (
      !enable
      || (
        (exe == null || isString exe || isPath exe)
        && (exe != null || package == null || lib.isDerivation package)
      )
    ) "outdated provider package must be a derivation and exe must be a string or path";
    {
      inherit
        enable
        root
        package
        exe
        path
        lockFile
        projects
        ;
    };
  options =
    {
      enable ? true,
      nix ? true,
      cargo ? false,
      uv ? false,
      npm ? false,
      pnpm ? false,
      yarn ? false,
      composer ? false,
      githubActions ? false,
      releases ? { },
      skips ? { },
      adapters ? { },
      timeout ? 45,
      concurrency ? 6,
      githubApi ? "https://api.github.com",
    }:
    assert lib.assertMsg (
      isBool enable
      && isInt timeout
      && timeout > 0
      && isInt concurrency
      && concurrency > 0
      && concurrency <= 32
    ) "outdated: positive integer timeout and concurrency (1..32) required";
    assert lib.assertMsg (
      isAttrs releases && isAttrs adapters && isAttrs skips && isString githubApi
    ) "outdated: releases/adapters must be attribute sets and githubApi must be a URL string";
    {
      inherit
        enable
        nix
        cargo
        uv
        npm
        pnpm
        yarn
        composer
        githubActions
        releases
        skips
        adapters
        timeout
        concurrency
        githubApi
        ;
    };
  cfg = toggle options config;
  overrideNames = lib.attrNames cfg.releases ++ lib.attrNames cfg.adapters ++ lib.attrNames cfg.skips;
  specs = {
    nix = {
      package = toolPkgs.nix;
      binary = "nix";
    };
    cargo = {
      package = toolPkgs.cargo-outdated;
      binary = "cargo-outdated";
    };
    uv = {
      package = toolPkgs.uv;
      binary = "uv";
    };
    npm = {
      package = toolPkgs.nodejs;
      binary = "npm";
    };
    pnpm = {
      package = toolPkgs.pnpm;
      binary = "pnpm";
    };
    yarn = {
      package = toolPkgs.yarn-berry;
      binary = "yarn";
    };
    composer = {
      package = toolPkgs.phpPackages.composer;
      binary = "composer";
    };
    githubActions = { };
  };
  project =
    {
      root ? null,
    }:
    assert lib.assertMsg (
      isString root && root != ""
    ) "outdated project root must be a nonempty string";
    {
      inherit root;
    };
  providers = lib.mapAttrs (
    name: _:
    let
      value = cfg.${name};
      fields = [
        "enable"
        "root"
      ]
      ++
        lib.optionals
          (
            !(lib.elem name [
              "nix"
              "githubActions"
            ])
          )
          [
            "package"
            "exe"
            "projects"
          ]
      ++ lib.optional (name == "nix") "lockFile"
      ++ lib.optional (name == "githubActions") "path";
    in
    assert lib.assertMsg (
      isBool value || (isAttrs value && lib.all (key: lib.elem key fields) (lib.attrNames value))
    ) "outdated.${name}: supported fields are ${lib.concatStringsSep ", " fields}";
    assert lib.assertMsg (
      !(isAttrs value && value ? projects && value ? root)
    ) "outdated.${name}: root and projects are mutually exclusive";
    let
      opts = toggle provider value;
    in
    assert lib.assertMsg (
      !opts.enable
      || !(isAttrs value && value ? projects)
      || (isAttrs opts.projects && opts.projects != { })
    ) "outdated.${name}.projects must be a nonempty attribute set";
    opts
    // lib.optionalAttrs (opts.enable && opts.projects != null) {
      projects = lib.mapAttrs (
        projectName: value:
        assert lib.assertMsg (match "[A-Za-z0-9][A-Za-z0-9_-]{0,63}" projectName != null)
          "outdated.${name}: project names must be 1..64 letters, digits, underscores or hyphens, starting with a letter or digit";
        checked project value
      ) opts.projects;
    }
  ) specs;
  active = lib.filterAttrs (_: item: item.enable) providers;
  executable =
    spec: opts:
    if opts.exe != null then
      opts.exe
    else
      lib.getExe' (if opts.package != null then opts.package else spec.package) spec.binary;
  adapter =
    name:
    {
      package ? null,
      exe ? null,
      args ? [ ],
      runtimeInputs ? [ ],
      root ? ".",
      timeout ? cfg.timeout,
    }:
    assert lib.assertMsg (
      exe != null || package != null
    ) "outdated.adapters.${name}: package or exe is required";
    assert lib.assertMsg (
      isList args
      && lib.all isString args
      && isString root
      && isInt timeout
      && timeout > 0
      && (exe == null || isString exe || isPath exe)
      && isList runtimeInputs
    ) "outdated.adapters.${name}: invalid args, root or timeout";
    {
      inherit
        name
        args
        runtimeInputs
        root
        timeout
        ;
      exe = if exe != null then exe else lib.getExe package;
    };
  adapters = lib.mapAttrsToList (name: checked (adapter name)) cfg.adapters;
  # Explicit inline checker/formatter metadata participates in the same
  # replacement policy as named release entries and adapters.
  automatic = lib.filter (
    item:
    !(cfg.releases ? ${item.name})
    && !(cfg.releases ? ${item.source or item.name})
    && !(cfg.adapters ? ${item.name})
    && !(cfg.adapters ? ${item.source or item.name})
    && !(cfg.skips ? ${item.name})
    && !(cfg.skips ? ${item.source or item.name})
  ) entries;
  deduplicated = lib.foldl' (
    acc: item:
    let
      key = unsafeDiscardStringContext (toJSON (removeAttrs item [ "source" ]));
    in
    acc
    // {
      ${key} = item // {
        source = lib.concatStringsSep ", " (
          lib.unique ((lib.optional (acc ? ${key}) acc.${key}.source) ++ [ item.source or item.name ])
        );
      };
    }
  ) { } automatic;
  settings =
    assert lib.assertMsg (
      lib.length overrideNames == lib.length (lib.unique overrideNames)
    ) "outdated: releases, adapters and skips must have distinct names";
    {
      schemaVersion = 2;
      nvchecker = lib.getExe' toolPkgs.nvchecker "nvchecker";
      inherit treeRootFile;
      inherit (cfg) timeout concurrency githubApi;
      providers = lib.mapAttrs (
        name: opts:
        (removeAttrs opts (
          [
            "enable"
            "package"
            "exe"
            "projects"
          ]
          ++ lib.optional (opts.projects != null) "root"
        ))
        // lib.optionalAttrs (opts.projects != null) { inherit (opts) projects; }
        // {
          git = lib.getExe toolPkgs.gitMinimal;
        }
        // lib.optionalAttrs (name == "pnpm") { semver = lib.getExe nodeReport; }
        // lib.optionalAttrs (name == "npm") { reporter = lib.getExe nodeReport; }
        // lib.optionalAttrs (name == "cargo") { cargo = lib.getExe toolPkgs.cargo; }
        // lib.optionalAttrs (specs.${name} ? binary) { exe = executable specs.${name} opts; }
      ) active;
      tools = lib.attrValues deduplicated;
      releases = lib.mapAttrsToList (
        name: value: (toolRecords.metadata name value) // { inherit name; }
      ) cfg.releases;
      skips = lib.mapAttrsToList (name: reason: toolRecords.metadata name { skip = reason; }) cfg.skips;
      adapters = map (item: removeAttrs item [ "runtimeInputs" ]) adapters;
    };
  program = toolPkgs.writeShellApplication {
    name = "repo-outdated";
    runtimeInputs = [
      toolPkgs.gitMinimal
      toolPkgs.cacert
      # UV ignores virtualenv interpreters during ordinary system discovery.
      toolPkgs.python314
    ]
    ++ lib.optionals (active ? cargo) [
      toolPkgs.cargo
      toolPkgs.rustc
    ]
    ++ lib.concatMap (item: item.runtimeInputs) adapters;
    passthru.inventory = settings;
    text = ''
      export SSL_CERT_FILE="''${SSL_CERT_FILE:-${toolPkgs.cacert}/etc/ssl/certs/ca-bundle.crt}"
      exec ${python}/bin/python ${source}/outdated/main.py --config ${toolPkgs.writeText "outdated.json" (toJSON settings)} "$@"
    '';
  };
in
{
  inherit (cfg) enable;
  package = program;
}
