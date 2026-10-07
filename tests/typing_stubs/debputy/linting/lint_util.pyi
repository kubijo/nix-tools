from enum import IntEnum

from lsprotocol.types import Diagnostic

class LintDiagnosticResultState(IntEnum):
    REPORTED = 1

class LintReport:
    def report_diagnostic(
        self,
        diagnostic: Diagnostic,
        *,
        result_state: LintDiagnosticResultState = ...,
        in_file: str | None = ...,
    ) -> None: ...
