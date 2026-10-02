"""Package-manager providers keyed by their public configuration names."""

from . import cargo, composer, npm, pnpm, uv, yarn
from .contract import Provider

PROVIDERS: dict[str, Provider] = {
    'cargo': cargo.report,
    'composer': composer.report,
    'npm': npm.report,
    'pnpm': pnpm.report,
    'uv': uv.report,
    'yarn': yarn.report,
}
