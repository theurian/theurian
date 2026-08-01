"""Security primitives shared across layers.

Path containment (SEC-7) and input limits (SEC-8) live here rather than in an
adapter because every layer that touches a filesystem path needs them, and a
control that is easy to bypass is not a control.
"""

from theurian.security.paths import (
    MAX_PATH_DEPTH,
    MAX_SOURCE_FILE_BYTES,
    assert_no_symlink_escape,
    ensure_private_mode,
    is_world_accessible,
    read_source_file,
    resolve_within_root,
)
from theurian.security.yaml_loading import MAX_YAML_BYTES, load_yaml, load_yaml_mapping

__all__ = [
    "MAX_PATH_DEPTH",
    "MAX_SOURCE_FILE_BYTES",
    "MAX_YAML_BYTES",
    "assert_no_symlink_escape",
    "ensure_private_mode",
    "is_world_accessible",
    "load_yaml",
    "load_yaml_mapping",
    "read_source_file",
    "resolve_within_root",
]
