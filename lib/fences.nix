# An mdformat plugin registering one codeformatter per fence tag,
# so a fenced block is formatted by the very tool that formats
# a file of that language.
{
  lib,
  toolPkgs,
  # tag -> argv, each reading the block on stdin and writing it to stdout.
  commands,
}:
let
  inherit (builtins) toJSON;
  tags = lib.attrNames commands;

  pyproject = toolPkgs.writeText "pyproject.toml" ''
    [build-system]
    requires = ["setuptools"]
    build-backend = "setuptools.build_meta"

    [project]
    name = "mdformat-fences"
    version = "0.1.0"

    [project.entry-points."mdformat.codeformatter"]
    ${lib.concatMapStringsSep "\n" (tag: ''"${tag}" = "mdformat_fences:format_${tag}"'') tags}

    [tool.setuptools]
    py-modules = ["mdformat_fences"]
  '';

  module = toolPkgs.writeText "mdformat_fences.py" ''
    """Format fenced code with the tools that format whole files."""

    import subprocess

    COMMANDS = ${toJSON commands}


    def _entry(tag):
        def format_fence(unformatted: str, _info_str: str) -> str:
            done = subprocess.run(
                COMMANDS[tag],
                input=unformatted,
                capture_output=True,
                text=True,
                check=False,
            )
            # mdformat downgrades this to a warning naming the file and line, and leaves
            # the block alone, so a fence that is not valid code cannot fail the run.
            if done.returncode:
                raise ValueError(done.stderr.strip() or "the formatter rejected this block")
            return done.stdout

        return format_fence


    ${lib.concatMapStringsSep "\n" (tag: ''format_${tag} = _entry("${tag}")'') tags}
  '';
in
toolPkgs.python3Packages.buildPythonPackage {
  pname = "mdformat-fences";
  version = "0.1.0";
  pyproject = true;
  build-system = [ toolPkgs.python3Packages.setuptools ];
  doCheck = false;
  src = toolPkgs.runCommandLocal "mdformat-fences-src" { } ''
    mkdir -p "$out"
    cp ${pyproject} "$out/pyproject.toml"
    cp ${module} "$out/mdformat_fences.py"
  '';
}
