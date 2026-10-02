{
  lib,
  nix-gritql,
  system,
  toolPkgs,
  api,
}:
let
  inherit (builtins)
    fromJSON
    pathExists
    readDir
    readFile
    ;
  # Straight from the modules, not through `lib`: these are the internals the public
  # entrypoint is built from, and the suite exists to hold each one on its own.
  toolPkgsFor = _: toolPkgs;
  harness = import ./harness.nix {
    inherit lib system toolPkgs;
    mkFormatter = import ../lib/formatter.nix {
      inherit lib nix-gritql toolPkgsFor;
    };
    mkChecker = import ../lib/checker.nix { inherit lib toolPkgsFor; };
    mkValidate = import ../lib/validate.nix { inherit lib toolPkgsFor; };
    configure = import ../lib/configure.nix {
      inherit lib nix-gritql toolPkgsFor;
    };
    publicLib = api;
  };

  fmtFixtures = ./fixtures/fmt;
  chkFixtures = ./fixtures/chk;

  # Discovered, never listed: a hand-maintained roster is how a fixture ends up orphaned.
  namesIn = dir: lib.attrNames (lib.filterAttrs (_: type: type == "directory") (readDir dir));

  # Optional per-fixture arguments; most fixtures need none.
  # A case needing a runtime takes the function form,
  # since that is the only way to reach a package.
  argsFor =
    kind: name:
    let
      case = ./cases + "/${kind}/${name}.nix";
      value = import case;
    in
    if !pathExists case then
      { }
    else if lib.isFunction value then
      value { inherit toolPkgs; }
    else
      value;

  fmtNames = namesIn fmtFixtures;
  chkNames = namesIn chkFixtures;
  lock = fromJSON (readFile ../flake.lock);
  hasNestedNixTools = lib.any (
    node:
    let
      locked = node.locked or { };
    in
    (locked.owner or null) == "kubijo" && (locked.repo or null) == "nix-tools"
  ) (lib.attrValues lock.nodes);

  gritConsumer = import ./grit.nix {
    inherit
      api
      lib
      system
      toolPkgs
      ;
  };
  astGrepConsumer = import ./ast-grep.nix {
    inherit
      api
      lib
      system
      toolPkgs
      ;
  };

  phpDebian = import ./php-debian.nix {
    inherit
      api
      lib
      system
      toolPkgs
      ;
  };

  fmtCheck =
    name:
    let
      fixture = "${fmtFixtures}/${name}";
      args = argsFor "fmt" name;
    in
    [
      (lib.nameValuePair "fmt-${name}" (harness.mkGoldenCheck name fixture args))
      (lib.nameValuePair "fmt-stable-${name}" (harness.mkStableCheck name fixture args))
    ];

  chkCheck =
    name:
    lib.nameValuePair "chk-${name}" (
      harness.mkChkCase name "${chkFixtures}/${name}" (argsFor "chk" name)
    );
in
assert lib.assertMsg (!hasNestedNixTools) "flake.lock must not contain a nested nix-tools input";
rec {
  checks =
    lib.listToAttrs (lib.concatMap fmtCheck fmtNames)
    // lib.listToAttrs (map chkCheck chkNames)
    // phpDebian
    // import ./djlint.nix {
      inherit
        api
        lib
        system
        toolPkgs
        ;
    }
    // import ./runner.nix {
      inherit
        api
        lib
        system
        toolPkgs
        ;
    }
    // import ./templates.nix {
      inherit
        api
        lib
        system
        toolPkgs
        ;
    }
    // import ./coverage.nix {
      inherit
        api
        lib
        system
        toolPkgs
        ;
    }
    // {
      inherit (harness)
        cacheKey
        laziness
        validateSteps
        projectLayers
        biomeConfig
        wrappedArgv
        organizeImportsArgv
        checkerArgv
        checkPreparation
        pinnedPackageAccess
        projectCheckerReporting
        rootedChecker
        customFormatterCache
        strictSchemas
        unsupportedSystem
        ;
      agree = harness.mkAgreeCheck golden;
      fmt-fail-grit = harness.gritFailureSafety;
      fmt-fail-xml = harness.xmlFailureSafety;
      ast-grep-consumer = astGrepConsumer;
      grit-consumer = gritConsumer;
    };

  golden = toolPkgs.runCommandLocal "golden" { } (
    "mkdir -p \"$out\"\n"
    + lib.concatMapStringsSep "\n" (
      name:
      ''cp -r ${harness.mkGolden name "${fmtFixtures}/${name}" (argsFor "fmt" name)} "$out/${name}"''
    ) fmtNames
  );
}
