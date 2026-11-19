{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins) deepSeq tryEval;
  php = toolPkgs.php.buildEnv { extraConfig = "precision = 9"; };
  source = toolPkgs.runCommandLocal "phpstan-fixture" { nativeBuildInputs = [ php ]; } ''
    cp -r ${./fixtures/phpstan} "$out"
    chmod -R u+w "$out"
    chmod +x "$out/alpha/tools/phpstan"
    patchShebangs --build "$out/alpha/tools/phpstan"
  '';
  dependency = ./fixtures/phpstan-dependency;
  project =
    args:
    api.configure (
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = source;
        treeRootFile = ".fixture-root";
        check.prepare = ''
          touch .fixture-root
          cp -r ${dependency} alpha/vendor
          chmod -R u+w alpha/vendor
        '';
        exclude = [ "**/vendor/**" ];
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          onUnmatched = "info";
          extraFormatters.identity = {
            command = "${toolPkgs.coreutils}/bin/true";
            includes = [ "*" ];
            cacheInputs = [ ];
          };
        };
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
          phpstan.projects = {
            alpha = {
              inherit php;
              root = "alpha";
              configFile = "config/phpstan.neon";
            };
            beta = {
              inherit php;
              root = "beta";
              configFile = "phpstan.neon";
            };
          };
        };
      } args
    );
  configured = project { };
  lint = args: (project { lint.phpstan.projects.alpha = args; }).apps.lint.program;
  recorder = toolPkgs.writeShellScriptBin "phpstan" ''
    exec ${toolPkgs.python3}/bin/python -I ${./phpstan_record.py} "$@"
  '';
  failure = toolPkgs.writeShellScriptBin "phpstan" ''
    echo deliberate-phpstan-failure >&2
    exit 23
  '';
  programs = toolPkgs.writeText "phpstan-tests.json" (
    builtins.toJSON {
      inherit source dependency;
      php = "${php}/bin/php";
      lint = configured.apps.lint.program;
      validate = configured.apps.validate.program;
      missingRoot = lint { root = "missing"; };
      fileRoot = lint { root = "alpha/bootstrap.php"; };
      escapingRoot = lint { root = ".."; };
      missingConfig = lint { configFile = "missing.neon"; };
      directoryConfig = lint { configFile = "config"; };
      escapingConfig = lint { configFile = "../beta/phpstan.neon"; };
      # Keep the CLI buildable so the runner can reject these runtimes.
      missingPhp = lint {
        php = toolPkgs.emptyDirectory;
        exe = lib.getExe failure;
      };
      invalidPhp = lint {
        php = toolPkgs.writeTextDir "bin/php" "not executable";
        exe = lib.getExe failure;
      };
      failure = lint { package = failure; };
      recorded = lint {
        package = recorder;
        extraOptions = [ "--memory-limit=1G" ];
      };
      exe = lint {
        exe = lib.getExe recorder;
        package = throw "exe must win";
      };
      relativeExe = lint { exe = "tools/phpstan"; };
      badOption = lint { extraOptions = [ "--generate-baseline=baseline.neon" ]; };
      excluded = (project { exclude = [ "**/*.php" ]; }).apps.lint.program;
      coverage = configured.apps.coverage.program;
      declaredCoverage =
        (project {
          lint.phpstan.projects.alpha.exe = lib.getExe failure;
          coverage.projectChecks."lint:phpstan-alpha" = {
            includes = [ "alpha/src/**/*.php" ];
            kind = "semantic";
            description = "Consumer-defined PHP type checks";
          };
        }).apps.coverage.program;
    }
  );
  checker =
    args:
    (import ../lib/checker.nix {
      inherit lib;
      toolPkgsFor = _: toolPkgs;
    })
      ({ inherit system toolPkgs; } // args);
in
{
  phpstan-runner = toolPkgs.runCommandLocal "phpstan-runner" { } ''
    ${toolPkgs.python3}/bin/python ${./phpstan_runner_test.py} ${../lib}
    touch "$out"
  '';

  phpstan-integration =
    toolPkgs.runCommandLocal "phpstan-integration" { nativeBuildInputs = [ toolPkgs.gitMinimal ]; }
      ''
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        ${toolPkgs.python3}/bin/python ${./phpstan_test.py} ${programs}
        touch "$out"
      '';

  phpstan-sandbox-lint = configured.checks.linting;

  phpstan-schema =
    let
      valid = {
        inherit php;
        configFile = "phpstan.neon";
      };
      rejects = value: !(tryEval (deepSeq (checker { phpstan = value; }).selection true)).success;
      lazy = checker {
        toolPkgs = toolPkgs // {
          phpstan = throw "disabled PHPStan was evaluated";
        };
      };
      replaced = checker {
        phpstan.projects.app = throw "replaced PHPStan project was evaluated";
        extraProjectCheckers.phpstan-app = {
          command = "true";
          outdated.skip = "Replacement";
        };
      };
      metadata =
        (checker {
          phpstan.projects.app = valid // {
            outdated = {
              package = toolPkgs.phpstan;
              repo = "phpstan/phpstan";
            };
          };
        }).outdatedEntries;
    in
    assert rejects true;
    assert rejects { projects = [ ]; };
    assert rejects { projects.app = { inherit php; }; };
    assert rejects { projects.app.configFile = "phpstan.neon"; };
    assert rejects {
      projects.app = valid // {
        root = "/absolute";
      };
    };
    assert rejects {
      projects.app = valid // {
        configFile = "";
      };
    };
    assert rejects {
      projects.app = valid // {
        configFile = ./fixtures/phpstan/beta/phpstan.neon;
      };
    };
    assert rejects {
      projects.app = valid // {
        extraOptions = [ 42 ];
      };
    };
    assert (lib.head metadata).source == "lint.phpstan-app";
    assert (lib.head metadata).version == toolPkgs.phpstan.version;
    assert (lib.head replaced.outdatedEntries).skip == "Replacement";
    assert (lib.head replaced.selection.projectCheckers).command == "true";
    toolPkgs.runCommandLocal "phpstan-schema" { } ''
      test -x ${lib.getExe lazy}
      touch "$out"
    '';
}
