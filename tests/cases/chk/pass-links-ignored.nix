# The same broken target as `fail-links`, rescued by a pattern matched against the link
# rather than the file holding it.
{ links.ignoreLinks = [ "missing" ]; }
