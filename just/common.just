# This file's own location, so it holds when a submodule recipe runs elsewhere.
flake_dir := parent_directory(source_directory())

# Empty inside the dev shell. A PATH test would silently prefer a host toolchain.
nix_dev := if env("IN_NIX_SHELL", "") == "" { "nix develop " + flake_dir + " --command" } else { "" }

# nix spells the system differently from just.
system := arch() + "-" + if os() == "macos" { "darwin" } else { os() }
