"""The pre-spawn transport-override check, and the residual it leaves (ADR-0030).

Two halves, and they are graded differently on purpose.

**The control.** ADR-0030 measured, three ways (runs D, E and F), that a
``http_unix_socket`` setting in the ``gh`` configuration file sends the request to
that socket with ``--hostname github.com`` pinned and the child environment
reduced to the enumerated constant. This check reads the file ``gh`` would read
and refuses before anything is spawned. It reduces the accidental,
pre-existing, single-well-formed-file case -- a typo'd or inherited setting,
which is the case an operator actually meets.

**The residual.** It is not a control against an adversary, and the reason is one
fact: *this check's read cannot be gh's read*. Every way the two diverge is a
member of the class, and
:func:`test_a_key_written_twice_is_seen_by_pyyaml_and_gh_differently` drives
member (c) rather than describing it -- so the recorded residual has a test that
demonstrates it exists, not a paragraph asserting it.

The precedence tests are the other thing worth driving: ADR-0030's runs D-F each
moved **one** variable and never measured the order between them, so a check that
read all three, or read them in the wrong order, would be checking a file ``gh``
will not open.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github import transport_guard

pytestmark = pytest.mark.unit


def _config_dir(root: pathlib.Path, name: str, body: str | None) -> pathlib.Path:
    """A ``gh`` configuration directory, with ``config.yml`` written when given."""
    directory = root / name
    directory.mkdir(parents=True)
    if body is not None:
        (directory / transport_guard.GH_CONFIG_FILE).write_text(body, encoding="utf-8")
    return directory


def test_gh_config_dir_wins_over_both_others(tmp_path: pathlib.Path) -> None:
    """Precedence, not union: ``gh`` opens exactly one directory."""
    located = transport_guard.resolved_config_directory(
        {
            "GH_CONFIG_DIR": str(tmp_path / "explicit"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            "HOME": str(tmp_path / "home"),
        }
    )

    assert located == (tmp_path / "explicit", "GH_CONFIG_DIR")


def test_an_empty_gh_config_dir_falls_through_exactly_as_gh_does(
    tmp_path: pathlib.Path,
) -> None:
    """Measured behaviour, not a convention: an empty variable is absent to ``gh``.

    Treating it as *set* would point this check at the current directory while
    ``gh`` opened ``$XDG_CONFIG_HOME/gh`` -- the two reads answering about
    different files, which is exactly member (d) of the divergence class this
    check is meant to stay out of.
    """
    located = transport_guard.resolved_config_directory(
        {
            "GH_CONFIG_DIR": "",
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            "HOME": str(tmp_path / "home"),
        }
    )

    assert located == (tmp_path / "xdg" / "gh", "XDG_CONFIG_HOME")


def test_an_empty_xdg_falls_through_to_home(tmp_path: pathlib.Path) -> None:
    """The second step of the same chain, so both fall-throughs are driven."""
    located = transport_guard.resolved_config_directory(
        {"GH_CONFIG_DIR": "", "XDG_CONFIG_HOME": "", "HOME": str(tmp_path / "home")}
    )

    assert located == (tmp_path / "home" / ".config" / "gh", "HOME")


def test_an_environment_naming_no_directory_resolves_to_nothing() -> None:
    """With none of the three set, ``gh`` has no configuration this check could read."""
    assert transport_guard.resolved_config_directory({"GH_CONFIG_DIR": "", "HOME": ""}) is None
    assert transport_guard.resolved_config_directory({}) is None


def test_a_planted_transport_override_is_refused_before_anything_is_spawned(
    tmp_path: pathlib.Path,
) -> None:
    """ADR-0030 run D's fixture, as a control rather than a quotation.

    The refusal names the **variable** that selected the directory and the key
    that was set, and not the absolute path: an operator's home directory is not
    something a published message carries, and the variable locates the file just
    as well.
    """
    directory = _config_dir(tmp_path, "gh", "http_unix_socket: /tmp/planted.sock\n")

    with pytest.raises(ReviewIngestRefusedError) as raised:
        transport_guard.refuse_transport_overrides({"GH_CONFIG_DIR": str(directory)})

    assert raised.value.grade is RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED
    assert "http_unix_socket" in str(raised.value)
    assert "GH_CONFIG_DIR" in str(raised.value)
    assert str(directory) not in str(raised.value)
    assert raised.value.remedy


@pytest.mark.parametrize(
    "locator", ("GH_CONFIG_DIR", "XDG_CONFIG_HOME", "HOME"), ids=("run D", "run F", "run E")
)
def test_the_override_is_seen_through_every_locator_gh_resolves(
    tmp_path: pathlib.Path, locator: str
) -> None:
    """Runs D, E and F: the same file reached three ways, refused three ways.

    Each run moved one variable and observed the socket dialled, so a check that
    saw the file through only one of them would leave the other two open. The
    directory layout differs per locator because ``gh``'s own does.
    """
    body = "http_unix_socket: /tmp/planted.sock\n"
    if locator == "GH_CONFIG_DIR":
        parent = {locator: str(_config_dir(tmp_path, "explicit", body))}
    elif locator == "XDG_CONFIG_HOME":
        _config_dir(tmp_path, "xdg/gh", body)
        parent = {locator: str(tmp_path / "xdg")}
    else:
        _config_dir(tmp_path, "home/.config/gh", body)
        parent = {locator: str(tmp_path / "home")}

    with pytest.raises(ReviewIngestRefusedError) as raised:
        transport_guard.refuse_transport_overrides(parent)

    assert raised.value.grade is RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED


@pytest.mark.parametrize(
    ("label", "body"),
    (
        ("no config file at all", None),
        ("an empty file", ""),
        ("the key set to an empty value", 'http_unix_socket: ""\n'),
        ("an unrelated key", "git_protocol: https\n"),
        ("a document that is not a mapping", "- http_unix_socket\n"),
        ("a document that will not parse", "http_unix_socket: [unclosed\n"),
    ),
)
def test_a_configuration_that_moves_nothing_refuses_nothing(
    tmp_path: pathlib.Path, label: str, body: str | None
) -> None:
    """Six ways of not carrying an override, including the two that fail open.

    The last two are the **recorded decision**, not an oversight: refusing to
    spawn on any configuration this check cannot parse would deny the ingest to
    precisely the operator it exists to help -- somebody whose file has a typo --
    and would make a YAML reader's strictness a gate on an unrelated capability.
    The exposure that accepts is member (c) of the divergence class.
    """
    directory = _config_dir(tmp_path, "gh", body)

    transport_guard.refuse_transport_overrides({"GH_CONFIG_DIR": str(directory)})


def test_a_configuration_larger_than_the_recorded_bound_is_not_read(
    tmp_path: pathlib.Path,
) -> None:
    """An oversized file joins the fail-open arm rather than being parsed.

    A caller does not get to make this check spend unbounded work on a file, and
    a configuration past the bound is treated exactly like one that will not
    parse -- same decision, same recorded exposure.
    """
    padding = "# " + "x" * transport_guard.MAX_GH_CONFIG_BYTES + "\n"
    directory = _config_dir(tmp_path, "gh", padding + "http_unix_socket: /tmp/planted.sock\n")

    transport_guard.refuse_transport_overrides({"GH_CONFIG_DIR": str(directory)})


def test_a_key_written_twice_is_seen_by_pyyaml_and_gh_differently(
    tmp_path: pathlib.Path,
) -> None:
    """Member (c) of the divergence class, driven rather than described.

    ADR-0030 measured it on ``gh`` 2.86.0: with ``http_unix_socket`` present
    **twice**, PyYAML's ``safe_load`` takes the **last** occurrence while ``gh``
    dials the **first**. So a file whose second occurrence is empty reads as "no
    override" here and still sends the request to the first occurrence's socket.

    This test drives *this* side of the divergence -- what the check sees -- and
    is honest about the half it cannot run: no assertion here observes ``gh``,
    because doing so needs the binary and a socket, which is
    ``tests/integration/test_gh_transport_residual.py``'s job. What it does prove
    is that the recorded residual is real rather than theoretical: the last-wins
    parse is demonstrated on the exact fixture the ADR describes.
    """
    body = 'http_unix_socket: /tmp/planted.sock\nhttp_unix_socket: ""\n'
    directory = _config_dir(tmp_path, "gh", body)

    parsed = yaml.safe_load(body)

    assert parsed["http_unix_socket"] == "", (
        "PyYAML no longer takes the last occurrence of a duplicated key, which is "
        "the measurement member (c) of ADR-0030's divergence class rests on. "
        "Re-take the measurement before changing the residual's wording."
    )
    # The check therefore refuses nothing here, which is the residual itself.
    transport_guard.refuse_transport_overrides({"GH_CONFIG_DIR": str(directory)})


def test_the_known_key_set_is_what_the_check_looks_for() -> None:
    """The positive control on the population: an empty set would refuse nothing, silently.

    A check whose key set were emptied would pass every test above that asserts a
    *non*-refusal and would simply stop refusing. Naming the member here is what
    makes that visible, and it is also where the standing obligation is recorded:
    the set is bounded by what ``gh`` 2.86.0 understands, so it is re-taken
    whenever the version floor moves (member (b)).
    """
    assert "http_unix_socket" in transport_guard.TRANSPORT_OVERRIDE_KEYS
