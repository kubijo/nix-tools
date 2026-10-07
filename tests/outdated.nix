{
  api,
  lib,
  system,
  toolPkgs,
  python,
  runtimePython,
}:
let
  source = lib.fileset.toSource {
    root = ../lib;
    fileset = lib.fileset.unions [
      ../lib/outdated
      ../lib/terminal_env.py
    ];
  };
  inherit (builtins) deepSeq tryEval toJSON;
  project =
    outdated:
    api.configure {
      inherit system toolPkgs outdated;
      src = ../.;
    };
  configured = project {
    nix = false;
  };
  rejects = config: !(tryEval (deepSeq (project config).apps.outdated.program true)).success;
  clients = toolPkgs.writeText "outdated-clients.json" (toJSON {
    multiProject =
      (project {
        nix = false;
        concurrency = 2;
        uv.projects = {
          tooling.root = ".";
          api.root = "src/api";
        };
        pnpm.projects = {
          probe.root = "tools/web-probe";
          app.root = "src/web/app";
        };
      }).apps.outdated.program;
    releaseExamples = [
      {
        name = "biome";
        version = "2.5.14";
        provider = "npm";
        project = "@biomejs/biome";
        reporter = lib.getExe (import ../lib/outdated-node.nix { inherit lib toolPkgs; });
      }
      {
        name = "lychee";
        version = "0.24.2";
        provider = "crates";
        project = "lychee";
        reporter = lib.getExe (import ../lib/outdated-node.nix { inherit lib toolPkgs; });
      }
    ];
    nix = lib.getExe toolPkgs.nix;
    nvchecker = lib.getExe (
      toolPkgs.writeShellScriptBin "nvchecker-fixture" ''
        exec ${
          toolPkgs.python314.withPackages (p: [
            p.nvchecker
            p.packaging
          ])
        }/bin/python ${./nvchecker_fixture.py} "$@"
      ''
    );
    uv = lib.getExe toolPkgs.uv;
    npm = lib.getExe' toolPkgs.nodejs "npm";
    npmReporter = lib.getExe (import ../lib/outdated-node.nix { inherit lib toolPkgs; });
    semver = lib.getExe (import ../lib/outdated-node.nix { inherit lib toolPkgs; });
    cargoMetadata = lib.getExe toolPkgs.cargo;
    pnpm = lib.getExe toolPkgs.pnpm;
    yarn = lib.getExe toolPkgs.yarn-berry;
    composer = lib.getExe toolPkgs.phpPackages.composer;
    cargo = lib.getExe toolPkgs.cargo-outdated;
  });
  inventory =
    args:
    (lib.findFirst (package: package ? inventory) null
      (api.configure (
        {
          inherit system toolPkgs;
          src = ../.;
          outdated = true;
        }
        // args
      )).packages
    ).inventory;
  automatic = inventory {
    format.python = true;
    lint.python = true;
  };
  debputyPackage = (api.packagesFor toolPkgs).debputy;
  withDebputy = inventory {
    outdated = {
      nix = false;
      releases.debputy = {
        package = debputyPackage;
        provider = "git";
        url = "https://salsa.debian.org/debian/debputy.git";
      };
    };
  };
  withMetadata = inventory {
    lint.extraCheckers.css = {
      command = "/not-executed";
      includes = [ "*.css" ];
      outdated = {
        package = toolPkgs.biome;
        provider = "npm";
        project = "@biomejs/biome";
      };
    };
    lint.extraProjectCheckers.local = {
      command = "/not-executed";
      outdated.skip = "Versioned with this repository";
    };
    format.extraFormatters.css = {
      command = lib.getExe toolPkgs.biome;
      includes = [ "*.css" ];
      cacheInputs = [ ];
      outdated = {
        package = toolPkgs.biome;
        provider = "npm";
        project = "@biomejs/biome";
      };
    };
    outdated = {
      skips."lint.yaml" = "Checked by the repository's release process";
      releases.host = {
        repo = "NixOS/nix";
        versionCommand = [
          "nix"
          "--version"
        ];
        versionPattern = "nix .* (?P<version>[^ ]+)";
      };
    };
  };
in
{
  python-project =
    toolPkgs.runCommandLocal "python-project" { nativeBuildInputs = [ toolPkgs.uv ]; }
      ''
        export HOME="$TMPDIR/home"
        export UV_CACHE_DIR="$TMPDIR/uv-cache"
        export UV_PYTHON_DOWNLOADS=never
        mkdir -p "$HOME" project
        cp ${../pyproject.toml} project/pyproject.toml
        cp ${../uv.lock} project/uv.lock
        cd project
        uv lock --quiet --check --offline --python ${python}/bin/python
        cmp uv.lock ${../uv.lock}
        ${python}/bin/python ${./python_project_test.py} ${../uv.lock} ${runtimePython}/bin/python
        touch "$out"
      '';
  outdated-inventory =
    assert automatic.tools == [ ];
    assert (lib.head withDebputy.releases).version == debputyPackage.version;
    assert lib.length withMetadata.tools == 3;
    assert lib.any (row: row.name == "lint.css" && row.provider == "npm") withMetadata.tools;
    assert lib.any (row: row.name == "format.css" && row.provider == "npm") withMetadata.tools;
    assert lib.any (row: row.name == "lint.local" && row ? skip) withMetadata.tools;
    assert !(lib.any (row: row.source == "lint.yaml") withMetadata.tools);
    assert (lib.head withMetadata.skips).skip == "Checked by the repository's release process";
    assert
      let
        projects =
          (inventory {
            outdated = {
              nix = false;
              uv = {
                exe = "/explicit/uv";
                package = throw "exe takes precedence";
                projects = {
                  tooling.root = ".";
                  api.root = "src/api";
                };
              };
            };
          }).providers.uv;
      in
      projects.exe == "/explicit/uv"
      && !(projects ? root)
      &&
        projects.projects == {
          tooling.root = ".";
          api.root = "src/api";
        };
    assert
      (lib.head withMetadata.releases).versionCommand == [
        "nix"
        "--version"
      ];
    assert (project true).checks.formatting.drvPath == (project false).checks.formatting.drvPath;
    assert (project true).checks.linting.drvPath == (project false).checks.linting.drvPath;
    assert
      (inventory {
        outdated = {
          nix = false;
          uv = {
            enable = false;
            package = throw "unused UV";
          };
        };
      }).providers == { };
    toolPkgs.runCommandLocal "outdated-inventory" { } ''
      cp ${toolPkgs.writeText "explicit-release-entries.json" (toJSON withMetadata.tools)} "$out"
    '';
  outdated-native =
    toolPkgs.runCommandLocal "outdated-native"
      {
        nativeBuildInputs = [
          python
          toolPkgs.nodejs
          toolPkgs.gitMinimal
          toolPkgs.cargo
          toolPkgs.rustc
        ];
      }
      ''
        export HOME="$TMPDIR/home"
        export XDG_CACHE_HOME="$TMPDIR/cache"
        export CARGO_HOME="$TMPDIR/cargo"
        export PYTHONDONTWRITEBYTECODE=1
        mkdir -p "$HOME"
        export PATH="${toolPkgs.python314}/bin:$PATH"
        ${python}/bin/python ${./outdated_native_test.py} ${clients} ${./fixtures/outdated} ${source}/outdated -v
        touch "$out"
      '';
  outdated-behavior =
    toolPkgs.runCommandLocal "outdated-behavior" { nativeBuildInputs = [ python ]; }
      ''
        mkdir "$out"
        export PYTHONDONTWRITEBYTECODE=1
        export COVERAGE_FILE="$TMPDIR/.coverage"
        python -m coverage run --branch --source=${source}/outdated ${./outdated_test.py} ${source}/outdated -v
        python -m coverage report --show-missing > "$out/coverage.txt"
        cat "$out/coverage.txt"
        python -m coverage json -o "$out/coverage.json"
      '';
  outdated-schema =
    assert !((project false).apps ? outdated);
    assert
      !(
        (project {
          enable = false;
          cargo.package = throw "disabled";
        }).apps
          ? outdated
      );
    assert (project true).apps ? outdated;
    assert !((project true).checks ? outdated);
    assert lib.all rejects [
      { unknown = true; }
      { concurrency = 0; }
      { timeout = "1"; }
      { tools.scope = "wrong"; }
      { pythonEnv = true; }
      {
        releases.bad = {
          package = toolPkgs.biome;
        };
      }
      { yarn.unknown = true; }
      {
        uv = {
          root = ".";
          projects.api.root = "src/api";
        };
      }
      { uv.projects = { }; }
      { pnpm.projects = null; }
      { cargo.projects = [ ]; }
      { composer.projects.app = { }; }
      { npm.projects.app.root = ""; }
      { yarn.projects.app.root = 1; }
      { uv.projects."bad/name".root = "."; }
      {
        uv.projects.app = {
          root = ".";
          exe = "uv";
        };
      }
      { nix.projects.app.root = "."; }
      { githubActions.projects.app.root = "."; }
      { releases.bad = { }; }
      { adapters.bad = { }; }
      { skips.bad = " "; }
      {
        skips.bad = "local";
        releases.bad = {
          version = "1";
          repo = "example/repo";
        };
      }
      {
        releases.bad = {
          skip = " ";
        };
      }
      {
        releases.bad = {
          skip = "local";
          repo = "example/repo";
        };
      }
      {
        releases.bad = {
          versionCommand = [ ];
          repo = "example/repo";
        };
      }
      {
        releases.bad = {
          versionCommand = [ "tool" ];
          version = "1";
          repo = "example/repo";
        };
      }
      {
        releases.bad = {
          version = "1";
          versionPattern = "x";
          repo = "example/repo";
        };
      }
      {
        releases.bad = {
          provider = "npm";
          version = "1";
        };
      }
      {
        releases.bad = {
          provider = "crates";
          version = "1";
        };
      }
    ];
    toolPkgs.runCommandLocal "outdated-schema" { } ''
      mkdir work
      cd work
      touch flake.nix
      status=0
      ${configured.apps.outdated.program} --json > report.json || status=$?
      test "$status" -eq 2
      ${python}/bin/python -c 'import json; d=json.load(open("report.json")); assert d["state"] == "ERROR"'
      touch "$out"
    '';
}
