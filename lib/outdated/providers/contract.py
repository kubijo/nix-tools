"""Shared callable contract for stateless package-manager providers."""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from common import Result

type ProviderConfig = Mapping[str, str]
type Provider = Callable[[ProviderConfig, Path, float], Iterable[Result]]
