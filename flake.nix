{
  description = "Code formatters and linters as a configurable Nix flake library";

  inputs = {
    # Not meant to be `follows`-ed, since identical tool versions across consuming repos
    # is the whole point — a `follows` resolves the tools against their nixpkgs instead.
    # Override per call site with `toolPkgs`.
    nixpkgs-pinned.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    nix-gritql = {
      url = "github:kubijo/nix-gritql/v0.4.1";
      inputs.nixpkgs-pinned.follows = "nixpkgs-pinned";
    };
  };

  outputs =
    {
      self,
      nix-gritql,
      nixpkgs-pinned,
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
          ;
      };

      # Consumed exactly as a consumer would, so the published entrypoint is the tested one.
      project = eachSystem (
        system:
        api.configure {
          inherit system;
          src = self;
          inherit (import ./nix/self.nix) exclude;
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
          packages = project.${system}.packages ++ [ (toolPkgsFor system).just ];
        };
      });
    };
}
