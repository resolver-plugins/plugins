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
                present=$ORIGINAL_ENABLE_PRESENT; value=$ORIGINAL_ENABLE_VALUE;;
            named_conf)
                present=$ORIGINAL_CONF_PRESENT; value=$ORIGINAL_CONF_VALUE;;
            *) return 98;;
        esac
        [ "$present" = yes ] || return 1
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
    original_enable_present=True,
    partial_start_failure=False,
):
    fake_state = tmp_path / "state"
    fake_state.mkdir()

    environment = os.environ.copy()
    environment.update(
        {
            "CANARY_ANSWER": canary_answer,
            "CANARY_SUCCESS": "yes" if canary_success else "no",
            "FAIL_CONF_RESTORE": "yes" if fail_conf_restore else "no",
            "FAKE_STATE": str(fake_state),
            "ORIGINAL_CONF_PRESENT": "yes" if original_conf_present else "no",
            "ORIGINAL_CONF_VALUE": "/original/named.conf",
            "ORIGINAL_ENABLE_PRESENT": "yes" if original_enable_present else "no",
            "ORIGINAL_ENABLE_VALUE": "NO",
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


def test_runtime_verifier_preserves_absent_rc_settings(tmp_path):
    result, commands, _ = run_verifier(
        tmp_path,
        original_conf_present=False,
        original_enable_present=False,
        canary_success=True,
    )
    assert result.returncode == 0
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


def test_runtime_verifier_rejects_successful_queries_with_the_wrong_answer(tmp_path):
    result, commands, state = run_verifier(tmp_path, original_conf_present=True,
                                           canary_success=True,
                                           canary_answer="canary.invalid. 60 IN A 192.0.2.99")
    assert result.returncode != 0
    assert "sysrc|-s|named|named_conf=/original/named.conf" in commands
    assert "sysrc|-s|named|named_enable=NO" in commands
    assert "service|named|onestop" in commands
    assert not (state / 'runtime').exists()
