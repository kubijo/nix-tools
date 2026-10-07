{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins)
    attrNames
    deepSeq
    functionArgs
    toJSON
    tryEval
    ;
  source = ./fixtures/deptry;
  projects = {
    alpha = {
      root = "alpha";
      sourceRoots = [
        "."
        "src"
        "lib/internal"
        "src/."
      ];
    };
    beta = {
      root = "beta";
      sourceRoots = [ "src" ];
    };
  };
  project =
    args:
    api.configure (
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = source;
        treeRootFile = ".fixture-root";
        check.prepare = "touch .fixture-root";
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          onUnmatched = "info";
        };
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
          deptry = { inherit projects; };
        };
      } args
    );
  configured = project { };
  lint = args: (project { lint.deptry.projects.alpha = args; }).apps.lint.program;
  recorder = toolPkgs.writeShellScriptBin "deptry" ''
    exec ${toolPkgs.python3}/bin/python -I ${./deptry_record.py} "$@"
  '';
  fail = toolPkgs.writeShellScriptBin "deptry" ''
    echo deliberate-tool-failure >&2
    exit 23
  '';
  coverage = args: (project args).apps.coverage.program;
  declaration = {
    includes = [ "alpha/**/*.py" ];
    kind = "semantic";
    description = "Dependency declarations and imports in alpha";
  };
  programs = toolPkgs.writeText "deptry-tests.json" (toJSON {
    inherit source;
    lint = configured.apps.lint.program;
    validate = configured.apps.validate.program;
    nested =
      (project {
        lint.deptry.projects = {
          alpha.sourceRoots = [
            "src"
            "lib/internal"
          ];
          beta.root = "alpha/nested/beta";
        };
      }).apps.lint.program;
    namespaceOff = lint { configFile = "namespace-off.toml"; };
    relativeConfig = lint { configFile = "config/pyproject.toml"; };
    storeConfig = lint { configFile = ./fixtures/deptry/alpha/pyproject.toml; };
    missingRoot = lint { root = "missing"; };
    missingSource = lint {
      sourceRoots = [
        "src"
        "missing"
      ];
    };
    fileSource = lint { sourceRoots = [ "pyproject.toml" ]; };
    escapingSource = lint { sourceRoots = [ "../beta/src" ]; };
    missingConfig = lint { configFile = "missing.toml"; };
    failure = lint { package = fail; };
    badOption = lint { extraOptions = [ "--does-not-exist" ]; };
    configOption = lint { extraOptions = [ "--config=other.toml" ]; };
    recorded = lint {
      package = recorder;
      extraOptions = [ "--verbose" ];
    };
    exe = lint {
      exe = lib.getExe recorder;
      package = throw "exe must win";
    };
    # Generic file excludes must not hide generated-code dependency imports.
    excluded =
      (project {
        exclude = [
          "**/*_pb2.py"
          "alpha/src/generated/**"
        ];
      }).apps.lint.program;
    coverage = coverage { };
    declaredCoverage = coverage { coverage.projectChecks."lint:deptry-alpha" = declaration; };
  });
  checker =
    args:
    (import ../lib/checker.nix {
      inherit lib;
      toolPkgsFor = _: toolPkgs;
    })
      ({ inherit system toolPkgs; } // args);
in
{
  deptry-integration =
    toolPkgs.runCommandLocal "deptry-integration"
      {
        nativeBuildInputs = [
          toolPkgs.python3
          toolPkgs.gitMinimal
        ];
      }
      ''
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        python ${./deptry_test.py} ${programs}
        touch "$out"
      '';
  deptry-sandbox-lint = configured.checks.linting;
  deptry-schema =
    let
      schemas = import ../lib/options.nix { inherit lib; };
      rejects = value: !(tryEval (deepSeq (checker { deptry = value; }).selection true)).success;
      metadata =
        (checker {
          deptry.projects.app.outdated = {
            package = toolPkgs.deptry;
            provider = "pypi";
            project = "deptry";
          };
        }).outdatedEntries;
      inherit
        (lib.findFirst (package: package ? inventory) null
          (project {
            outdated = true;
            lint.deptry.projects.alpha.outdated = {
              package = toolPkgs.deptry;
              provider = "pypi";
              project = "deptry";
            };
          }).packages
        )
        inventory
        ;
      lazy = checker {
        toolPkgs = toolPkgs // {
          deptry = throw "disabled deptry was evaluated";
        };
      };
      replaced = checker {
        deptry.projects.app.package = throw "replaced deptry was evaluated";
        extraProjectCheckers.deptry-app = {
          command = "true";
          outdated.skip = "Replacement";
        };
      };
    in
    assert
      attrNames (functionArgs schemas.deptryProjectOptions) == [
        "configFile"
        "exe"
        "extraOptions"
        "outdated"
        "package"
        "root"
        "sourceRoots"
      ];
    assert rejects true;
    assert
      attrNames (functionArgs schemas.deptryOptions) == [
        "enable"
        "projects"
      ];
    assert rejects { projects.app.sourceRoots = [ ]; };
    assert rejects { projects.app.root = "/absolute"; };
    assert rejects { projects.app.sourceRoots = [ "/absolute" ]; };
    assert (lib.head metadata).source == "lint.deptry-app";
    assert (lib.head metadata).version == toolPkgs.deptry.version;
    assert (lib.head inventory.tools).source == "lint.deptry-alpha";
    assert (lib.head inventory.tools).provider == "pypi";
    assert (lib.head replaced.outdatedEntries).skip == "Replacement";
    assert (lib.head replaced.selection.projectCheckers).command == "true";
    toolPkgs.runCommandLocal "deptry-schema" { } ''
      test -x ${lib.getExe lazy}
      touch "$out"
    '';
}
