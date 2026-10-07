{
  description = "Code formatters and linters as a configurable Nix flake library";

  inputs = {
    # Not meant to be `follows`-ed, since identical tool versions across consuming repos
    # is the whole point — a `follows` resolves the tools against their nixpkgs instead.
    # Override per call site with `toolPkgs`.
    nixpkgs-pinned.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs-pinned";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        nixpkgs.follows = "nixpkgs-pinned";
      };
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
        nixpkgs.follows = "nixpkgs-pinned";
      };
    };

    nix-gritql = {
      url = "github:kubijo/nix-gritql/v0.6.0";
      inputs.nixpkgs-pinned.follows = "nixpkgs-pinned";
    };
  };

  outputs =
    {
      self,
      nix-gritql,
      nixpkgs-pinned,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    let
      inherit (nixpkgs-pinned) lib;

      # x86_64-darwin is absent because nixpkgs 26.11 dropped it.
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      eachSystem = lib.genAttrs supportedSystems;

      pythonEnvsFor =
        pkgs:
        import ./nix/python.nix {
          inherit
            pkgs
            pyproject-nix
            uv2nix
            pyproject-build-systems
            ;
        };

      toolPkgsFor =
        system:
        if lib.elem system supportedSystems then
          nixpkgs-pinned.legacyPackages.${system}
        else
          throw "unsupported system `${system}`; nix-tools' pinned tool set supports: ${lib.concatStringsSep ", " supportedSystems}";

      api = import ./lib {
        inherit
          lib
          nix-gritql
          supportedSystems
          toolPkgsFor
          pythonEnvsFor
          ;
      };

      # Consumed exactly as a consumer would, so the published entrypoint is the tested one.
      project = eachSystem (
        system:
        api.configure {
          inherit system;
          src = self;
          inherit (import ./nix/self.nix) exclude;
          format = {
            python.configFile = ./pyproject.toml;
            javascript = true;
            whitespace.includes = [
              "conf/editorconfig"
              "tests/conf/editorconfig"
              "tests/conf/editorconfig-sections"
            ];
          };
          lint = {
            python.configFile = ./pyproject.toml;
            deptry.projects.nix-tools.sourceRoots = [ "lib/outdated" ];
            javascript = true;
          };
          coverage.projectChecks."lint:deptry-nix-tools" = {
            includes = [ "lib/outdated/**/*.py" ];
            kind = "semantic";
            description = "Python imports and declared dependencies for outdated reporting";
          };
          outdated = {
            enable = true;
            githubActions = true;
            uv = true;
          };
        }
      );

      tests = eachSystem (
        system:
        import ./tests {
          inherit
            api
            lib
            nix-gritql
            system
            pythonEnvsFor
            ;
          toolPkgs = toolPkgsFor system;
        }
      );
    in
    {
      lib = api;

      checks = eachSystem (
        system:
        tests.${system}.checks
        // lib.mapAttrs' (kind: lib.nameValuePair "self-${kind}") project.${system}.checks
      );

      formatter = eachSystem (system: project.${system}.formatter);

      apps = eachSystem (system: project.${system}.apps);

      devShells = eachSystem (system: {
        default = (toolPkgsFor system).mkShellNoCC {
          packages = project.${system}.packages ++ [
            (toolPkgsFor system).just
            (toolPkgsFor system).uv
            (toolPkgsFor system).basedpyright
            (pythonEnvsFor (toolPkgsFor system)).dev
          ];
          env = {
            UV_NO_SYNC = "1";
            UV_PYTHON = "${(pythonEnvsFor (toolPkgsFor system)).dev}/bin/python";
            UV_PYTHON_DOWNLOADS = "never";
          };
        };
      });
    };
}
