# The adapter only uses registry keys.
CLI_DIAGNOSTIC_HANDLERS: dict[str, object]
CLI_FORMAT_FILE_HANDLERS: dict[str, object]

def ensure_cli_lsp_features_are_loaded() -> None: ...
