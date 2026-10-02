{
  pkgs,
  pyproject-nix,
  uv2nix,
  pyproject-build-systems,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ../.; };
  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python314; }).overrideScope
      (
        pkgs.lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
        ]
      );
in
{
  runtime = pythonSet.mkVirtualEnv "nix-tools-python" workspace.deps.default;
  dev = pythonSet.mkVirtualEnv "nix-tools-python-dev" workspace.deps.all;
}
