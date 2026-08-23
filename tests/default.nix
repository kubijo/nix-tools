{
  lib,
  system,
  toolPkgs,
}:
let
  # Straight from the modules, not through `lib`: these are the internals the public
  # entrypoint is built from, and the suite exists to hold each one on its own.
  toolPkgsFor = _: toolPkgs;
  harness = import ./harness.nix {
    inherit lib system toolPkgs;
    mkFormatter = import ../lib/formatter.nix { inherit lib toolPkgsFor; };
    mkChecker = import ../lib/checker.nix { inherit lib toolPkgsFor; };
    mkValidate = import ../lib/validate.nix { inherit lib toolPkgsFor; };
    configure = import ../lib/configure.nix { inherit lib toolPkgsFor; };
  };

  fmtFixtures = ./fixtures/fmt;
  chkFixtures = ./fixtures/chk;

  # Discovered, never listed: a hand-maintained roster is how a fixture ends up orphaned.
  namesIn =
    dir: lib.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir dir));

  # Optional per-fixture arguments; most fixtures need none.
  # A case needing a runtime takes the function form,
  # since that is the only way to reach a package.
  argsFor =
    kind: name:
    let
      case = ./cases + "/${kind}/${name}.nix";
      value = import case;
    in
    if !builtins.pathExists case then
      { }
    else if lib.isFunction value then
      value { inherit toolPkgs; }
    else
      value;

  fmtNames = namesIn fmtFixtures;
  chkNames = namesIn chkFixtures;

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
rec {
  checks =
    lib.listToAttrs (lib.concatMap fmtCheck fmtNames)
    // lib.listToAttrs (map chkCheck chkNames)
    // {
      inherit (harness)
        cacheKey
        laziness
        validateSteps
        projectLayers
        biomeConfig
        ;
      agree = harness.mkAgreeCheck golden;
    };

  golden = toolPkgs.runCommandLocal "golden" { } (
    "mkdir -p \"$out\"\n"
    + lib.concatMapStringsSep "\n" (
      name:
      ''cp -r ${harness.mkGolden name "${fmtFixtures}/${name}" (argsFor "fmt" name)} "$out/${name}"''
    ) fmtNames
  );
}
