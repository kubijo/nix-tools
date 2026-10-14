{
  lib,
  writeShellApplication,
  python,
}:
let
  source = lib.fileset.toSource {
    root = ../lib;
    fileset = lib.fileset.unions [
      ../lib/dirspec.py
      ../lib/terminal_env.py
    ];
  };
in
writeShellApplication {
  name = "dirspec";
  text = ''
    exec ${python}/bin/python ${source}/dirspec.py "$@"
  '';
  meta = {
    description = "Validate directory trees and file metadata against TOML prescriptions";
    license = lib.licenses.unlicense;
    platforms = lib.platforms.unix;
    mainProgram = "dirspec";
  };
}
