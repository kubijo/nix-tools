{
  lib,
  python3Packages,
  fetchurl,
  dpkg,
  fd,
}:
python3Packages.buildPythonApplication rec {
  pname = "debputy";
  version = "0.1.95";
  pyproject = true;
  src = fetchurl {
    url = "https://deb.debian.org/debian/pool/main/d/debputy/debputy_${version}.tar.xz";
    sha256 = "2d2e276f6290cf18fd8b02764f61ad6ecff9540a67fbe8ee50d71a7067f58f15";
  };
  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [
    python-debian
    ruyaml
    colored
    colorlog
    lsprotocol
    junit-xml
    pathspec
  ];
  postPatch = ''
    substituteInPlace src/debputy/packages.py \
      src/debputy/lsp/lsp_debian_control_reference_data.py \
      src/debputy/commands/debputy_cmd/context.py \
      --replace-fail 'DpkgArchTable.load_arch_table()' 'DpkgArchTable.load_arch_table("${dpkg}/share/dpkg")'
    substituteInPlace src/debputy/version.py \
      --replace-fail '_VERSION = ""' '_VERSION = "${version}"' \
      --replace-fail 'pathlib.Path(__file__).parent.parent.parent' 'pathlib.Path(__file__).parent.parent'
    cp ${../lib/debian-tool.py} src/debputy/nix_tools.py
    substituteInPlace src/debputy/nix_tools.py --replace-fail '@fd@' '${lib.getExe fd}'
    substituteInPlace pyproject.toml \
      --replace-fail 'build-backend = ["hatchling.build"]' 'build-backend = "hatchling.build"' \
      --replace-fail 'debputy = "debputy.commands.debputy_cmd.__main__:main"' \
        'debputy = "debputy.commands.debputy_cmd.__main__:main"
    debputy-nix-tools = "debputy.nix_tools:main"'
  '';
  makeWrapperArgs = [
    "--prefix PATH : ${lib.makeBinPath [ dpkg ]}"
    "--set DPKG_DATADIR ${dpkg}/share/dpkg"
  ];
  # Debian's full tests exercise its package builder and require a Debian host.
  # The integration suite exercises the installed lint/format commands instead.
  doCheck = false;
  pythonImportsCheck = [ "debputy.nix_tools" ];
  meta = {
    description = "Debian packaging linter and formatter with a scoped nix-tools adapter";
    homepage = "https://salsa.debian.org/debian/debputy";
    license = [
      lib.licenses.asl20
      lib.licenses.gpl2Plus
    ];
    platforms = lib.platforms.unix;
    mainProgram = "debputy";
  };
}
