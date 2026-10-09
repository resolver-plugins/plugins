from common_imports import *

import pytest


ROOT = Path(__file__).resolve().parents[3]
VERIFIER = ROOT / ".github" / "ci" / "bind/verify-bind-runtime.sh"


FAKE_COMMANDS = r"""
set -eu

log_command() {
    command_name=$1
    shift
    {
        printf '%s' "$command_name"
        for argument in "$@"
        do
            printf '|%s' "$argument"
        done
        printf '\n'
    } >> "$FAKE_STATE/commands.log"
}

mktemp() {
    log_command mktemp "$@"
    mkdir "$FAKE_STATE/runtime"
    printf '%s\n' "$FAKE_STATE/runtime"
}

checkconf_fixture() {
    log_command named-checkconf "$@"
}
alias named-checkconf=checkconf_fixture

sysrc() {
    log_command sysrc "$@"
    if [ "${1-}" != -s ] || [ "${2-}" != named ]
    then
        return 96
    fi
    shift 2
    if [ "${1-}" = -N ] && [ "${2-}" = -A ]
    then
        if [ "$FAIL_INITIAL_QUERY" = yes ]
        then
            return 1
        fi
        if [ "$ORIGINAL_ENABLE_PRESENT" = yes ]
        then
            printf 'named_enable\n'
        fi
        if [ "$ORIGINAL_CONF_PRESENT" = yes ]
        then
            printf 'named_conf\n'
        fi
        return 0
    fi
    if [ "${1-}" = -n ]
    then
        case "${2-}" in
            named_enable)
                present=$ORIGINAL_ENABLE_PRESENT; fail=$FAIL_ENABLE_READ; value=$ORIGINAL_ENABLE_VALUE;;
            named_conf)
                present=$ORIGINAL_CONF_PRESENT; fail=$FAIL_CONF_READ; value=$ORIGINAL_CONF_VALUE;;
            *) return 98;;
        esac
        [ "$present" = yes ] && [ "$fail" = no ] || return 1
        printf '%s\n' "$value"
    elif [ "${1-}" = "named_conf=$ORIGINAL_CONF_VALUE" ] && \
        [ "$FAIL_CONF_RESTORE" = yes ]
    then
        return 1
    fi
}

service() {
    log_command service "$@"
    if [ "${1-}" = named ] && [ "${2-}" = onestart ]
    then
        : > "$FAKE_STATE/runtime/rndc.key"
    fi
    if [ "${1-}" = named ] && [ "${2-}" = onestart ] && \
        [ "$PARTIAL_START_FAILURE" = yes ]
    then
        return 1
    fi
}

drill() {
    log_command drill "$@"
    if [ "$CANARY_SUCCESS" = yes ]
    then
        printf '%s\n' "$CANARY_ANSWER"
    else
        return 1
    fi
}

chown() {
    log_command chown "$@"
}

sleep() {
    log_command sleep "$@"
}

. "$VERIFIER_PATH"
"""


def run_verifier(
    tmp_path,
    *,
    original_conf_present,
    canary_success,
    canary_answer="canary.invalid. 60 IN A 192.0.2.53",
    fail_conf_restore=False,
    fail_conf_read=False,
    fail_enable_read=False,
    fail_initial_query=False,
    original_conf_value="/original/named.conf",
    original_enable_present=True,
    original_enable_value="NO",
    partial_start_failure=False,
):
    assert VERIFIER.stat().st_mode & 0o111
    fake_state = tmp_path / "state"
    fake_state.mkdir()

    environment = os.environ.copy()
    environment.update(
        {
            "CANARY_ANSWER": canary_answer,
            "CANARY_SUCCESS": "yes" if canary_success else "no",
            "FAIL_CONF_RESTORE": "yes" if fail_conf_restore else "no",
            "FAIL_CONF_READ": "yes" if fail_conf_read else "no",
            "FAIL_ENABLE_READ": "yes" if fail_enable_read else "no",
            "FAIL_INITIAL_QUERY": "yes" if fail_initial_query else "no",
            "FAKE_STATE": str(fake_state),
            "ORIGINAL_CONF_PRESENT": "yes" if original_conf_present else "no",
            "ORIGINAL_CONF_VALUE": original_conf_value,
            "ORIGINAL_ENABLE_PRESENT": "yes" if original_enable_present else "no",
            "ORIGINAL_ENABLE_VALUE": original_enable_value,
            "PARTIAL_START_FAILURE": "yes" if partial_start_failure else "no",
            "VERIFIER_PATH": str(VERIFIER),
        }
    )
    result = subprocess.run(
        ["/bin/sh", "-c", textwrap.dedent(FAKE_COMMANDS)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    command_log = fake_state / "commands.log"
    assert command_log.is_file(), result.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines()
    return result, commands, fake_state


@pytest.mark.parametrize(("original_conf_present", "canary_success"), [
    pytest.param(True, True, id="conf-present-success"),
    pytest.param(False, False, id="conf-absent-failure"),
])
def test_runtime_verifier_restores_named_conf_on_every_exit(
    tmp_path, original_conf_present, canary_success
):
    result, commands, fake_state = run_verifier(
        tmp_path,
        original_conf_present=original_conf_present,
        canary_success=canary_success,
    )

    assert (result.returncode == 0) is canary_success
    if original_conf_present:
        assert "sysrc|-s|named|named_conf=/original/named.conf" in commands
    else:
        assert "sysrc|-s|named|-x|named_conf" in commands
    assert "sysrc|-s|named|named_enable=NO" in commands
    assert "service|named|onestop" in commands
    enable_index = commands.index("sysrc|-s|named|named_enable=YES")
    conf_index = commands.index(
        f"sysrc|-s|named|named_conf={fake_state / 'runtime' / 'named.conf'}"
    )
    start_index = commands.index("service|named|onestart")
    check_index = commands.index(f"named-checkconf|{fake_state / 'runtime' / 'named.conf'}")
    restart_index = commands.index("service|named|onerestart")
    assert check_index < enable_index < conf_index < start_index < restart_index
    assert "drill|-p|15353|canary.invalid|@127.0.0.1|A" in commands
    assert not (fake_state / "runtime").exists()
    assert all("named_flags" not in command for command in commands)


def test_runtime_verifier_reports_restore_failure_and_finishes_cleanup(tmp_path):
    result, commands, fake_state = run_verifier(
        tmp_path,
        original_conf_present=True,
        canary_success=True,
        fail_conf_restore=True,
    )

    assert result.returncode != 0
    assert "service|named|onestop" in commands
    assert "sysrc|-s|named|named_conf=/original/named.conf" in commands
    assert "sysrc|-s|named|named_enable=NO" in commands
    assert not (fake_state / "runtime").exists()
    assert all("named_flags" not in command for command in commands)


@pytest.mark.parametrize('settings_present', [False, True], ids=['absent', 'explicit-empty'])
def test_runtime_verifier_preserves_absent_and_empty_rc_settings(tmp_path, settings_present):
    result, commands, _ = run_verifier(
        tmp_path,
        original_conf_present=settings_present,
        original_conf_value='',
        original_enable_present=settings_present,
        original_enable_value='',
        canary_success=True,
    )
    assert result.returncode == 0
    if settings_present:
        assert 'sysrc|-s|named|named_conf=' in commands
        assert 'sysrc|-s|named|named_enable=' in commands
    else:
        assert 'sysrc|-s|named|-x|named_conf' in commands
        assert 'sysrc|-s|named|-x|named_enable' in commands


def test_runtime_verifier_stops_after_partial_start_failure(tmp_path):
    result, commands, _ = run_verifier(
        tmp_path,
        original_conf_present=False,
        canary_success=True,
        partial_start_failure=True,
    )

    assert result.returncode != 0
    assert "service|named|onestart" in commands
    assert "service|named|onestop" in commands
    assert "sysrc|-s|named|-x|named_conf" in commands
    assert "sysrc|-s|named|named_enable=NO" in commands
    assert all("named_flags" not in command for command in commands)


def test_runtime_verifier_fails_before_mutation_when_initial_query_fails(tmp_path):
    result, commands, _ = run_verifier(
        tmp_path,
        original_conf_present=True,
        canary_success=True,
        fail_initial_query=True,
    )

    assert result.returncode != 0
    assert commands == ["sysrc|-s|named|-N|-A"]


@pytest.mark.parametrize("failed_read", ["enable", "conf"])
def test_runtime_verifier_fails_before_mutation_when_value_read_fails(
    tmp_path, failed_read
):
    result, commands, _ = run_verifier(
        tmp_path,
        original_conf_present=True,
        canary_success=True,
        fail_enable_read=failed_read == "enable",
        fail_conf_read=failed_read == "conf",
    )

    assert result.returncode != 0
    expected = ["sysrc|-s|named|-N|-A", "sysrc|-s|named|-n|named_enable"]
    if failed_read == "conf":
        expected.append("sysrc|-s|named|-n|named_conf")
    assert commands == expected


@pytest.mark.parametrize("answer", ["", "canary.invalid. 60 IN A 192.0.2.99"])
def test_runtime_verifier_rejects_successful_queries_with_the_wrong_answer(tmp_path, answer):
    result, commands, state = run_verifier(tmp_path, original_conf_present=True,
                                           canary_success=True, canary_answer=answer)
    assert result.returncode != 0
    assert "did not answer the canary query" in result.stderr
    assert "sysrc|-s|named|named_conf=/original/named.conf" in commands
    assert "sysrc|-s|named|named_enable=NO" in commands
    assert "service|named|onestop" in commands
    assert not (state / 'runtime').exists()
