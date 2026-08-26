"""Theurian: Git-native engineering knowledge for AI agents.

The public surface for external consumers is the ``theurian`` CLI, the MCP
server, and the JSON Schemas under ``schemas/``. Python modules inside this
package are internal and carry no stability guarantee -- notably, the Claude Code
plugin must never import them (ADR-0001, CP-2).
"""

from theurian.domain.compatibility import CURRENT_PROTOCOL_VERSION

__version__ = "0.1.0.dev12"
__protocol_version__ = CURRENT_PROTOCOL_VERSION

__all__ = ["__protocol_version__", "__version__"]
