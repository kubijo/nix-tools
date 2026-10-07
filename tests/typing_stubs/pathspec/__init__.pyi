from collections.abc import Iterable
from typing import Self

class GitIgnoreSpec:
    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> Self: ...
    def match_file(self, file: str) -> bool: ...
