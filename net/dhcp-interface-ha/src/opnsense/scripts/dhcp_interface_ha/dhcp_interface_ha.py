#!/usr/local/bin/python3

"""Experimental DHCP Interface HA controller and read-only planning CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import signal
import syslog
import threading

from runtime import Controller, emit_event, run

from core import (
    InterfaceSnapshot,
    ObservedState,
    Settings,
    DHCPHA_DEVICE,
    generate_private_mac,
    parse_carp_states,
    parse_interface_snapshot,
    plan_reconcile,
    validate_shared_mac,
)


def read_ifconfig() -> str:
    return subprocess.run(
        ["/sbin/ifconfig", "-a"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout


def read_carp_admin() -> tuple[bool, bool]:
    result = subprocess.run(
        ["/usr/local/sbin/configctl", "interface", "show", "carp"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    payload = json.loads(result.stdout or "{}")
    return bool(int(payload.get("allow", 0))), bool(payload.get("maintenancemode", False))


def cmd_generate_mac(_args: argparse.Namespace) -> int:
    print(generate_private_mac())
    return 0


def cmd_validate_mac(args: argparse.Namespace) -> int:
    ok, message = validate_shared_mac(args.mac)
    print(json.dumps({"valid": ok, "message": message}))
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    if args.from_config:
        print(json.dumps(Controller().status(), indent=2))
        return 0
    else:
        if not args.carrier or not args.shared_mac:
            raise SystemExit("--carrier and --shared-mac are required unless --from-config is used")
        settings = Settings(
            enabled=args.enabled,
            carrier=args.carrier,
            shared_mac=args.shared_mac,
            managed_by_dhcpha=args.managed_by_dhcpha,
            managed_mtu=args.mtu,
        )
        managed_name = None
        managed_config = {}
        config_source = "arguments"

    data = read_ifconfig()
    carp_states = parse_carp_states(data)
    carrier = parse_interface_snapshot(settings.carrier, data)
    dhcpha = parse_interface_snapshot(DHCPHA_DEVICE, data)

    carp_allowed, carp_maintenance = read_carp_admin()
    observed = ObservedState(
        carp_states=carp_states,
        carp_allowed=carp_allowed,
        carp_maintenance=carp_maintenance,
        dhcpha_owned=Controller().owned(),
        carrier=carrier,
        dhcpha=dhcpha,
    )
    plan = plan_reconcile(settings, observed)

    payload = {
        "config_source": config_source,
        "managed_interface": managed_name,
        "managed_config": managed_config,
        "settings": {
            "enabled": settings.enabled,
            "carrier": settings.carrier,
            "shared_mac": settings.shared_mac,
            "managed_by_dhcpha": settings.managed_by_dhcpha,
            "managed_mtu": settings.managed_mtu,
        },
        "carp_states": list(carp_states),
        "carp_allowed": carp_allowed,
        "carp_maintenance": carp_maintenance,
        "global_role": plan.desired.role.value,
        "desired_attachment": plan.desired.attachment.value,
        "reason": plan.desired.reason,
        "carrier": carrier.__dict__,
        "dhcpha": dhcpha.__dict__,
        "commands": [
            {"argv": list(command.argv), "reason": command.reason}
            for command in plan.commands
        ],
        "warnings": plan.warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


def serve(controller, event_sink=None):
    wake = threading.Event()
    stopping = False
    previous_state = None
    previous_addresses = object()

    def event(code, severity=syslog.LOG_INFO, **fields):
        emit_event(code, severity, event_sink, **fields)

    def terminate(_signum, _frame):
        nonlocal stopping
        stopping = True
        wake.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGUSR1, lambda *_: wake.set())
    event("service_started", operation="serve", outcome="started")
    while not stopping:
        wake.clear()
        try:
            status = controller.reconcile(daemon=True)
            interface = status.get("managed_interface") or "unknown"
            carrier = status.get("carrier", {}).get("name") or "unknown"
            state = (
                status.get("state", "UNKNOWN"),
                status.get("global_role", "INDETERMINATE"),
                status.get("actual_attachment", "UNVERIFIED"),
                status.get("reason_code", "unknown"),
                interface,
                carrier,
            )
            if previous_state is None:
                event(
                    "state_observed", interface=interface, carrier=carrier,
                    state=state[0], role=state[1], attachment=state[2],
                    reason=state[3], outcome="observed",
                )
            elif state != previous_state:
                event(
                    "state_changed", interface=interface, carrier=carrier,
                    old_state=previous_state[0], state=state[0],
                    old_role=previous_state[1], role=state[1],
                    old_interface=previous_state[4], old_carrier=previous_state[5],
                    attachment=state[2], reason=state[3],
                    safety="verified" if state[2] != "UNVERIFIED" else "unverified",
                    outcome="observed",
                )
            previous_state = state

            addresses = status.get("ipv4_addresses")
            if addresses is not None:
                addresses = tuple(addresses)
                if previous_addresses != addresses:
                    old_text = (
                        "unobserved" if not isinstance(previous_addresses, tuple)
                        else ",".join(previous_addresses) or "none"
                    )
                    new_text = ",".join(addresses) or "none"
                    if not isinstance(previous_addresses, tuple):
                        outcome = "observed"
                    elif previous_addresses and not addresses:
                        outcome = "lost"
                    elif not previous_addresses and addresses:
                        outcome = "acquired"
                    else:
                        outcome = "changed"
                    severity = (
                        syslog.LOG_WARNING if outcome == "lost" and state[0] == "ACTIVE"
                        else syslog.LOG_INFO
                    )
                    event(
                        "ipv4_changed", severity, interface=interface, carrier=carrier,
                        old=old_text, address=new_text, state=state[0], role=state[1],
                        outcome=outcome,
                    )
                previous_addresses = addresses
        except Exception:
            # Controller owns operation failure events; do not log the same exception here.
            pass
        # Native service health takes its own lock and calls our health hook.
        # Never invoke it while holding the interface transition lock.
        try:
            run(["/usr/local/sbin/carp_service_status"])
        except Exception as exc:
            controller.report_daemon_failure("carp_status_refresh", exc)
        else:
            controller.report_daemon_recovery("carp_status_refresh")
        wake.wait(5)
    try:
        controller.fence(stop=True)
    except Exception:
        event(
            "service_stopped", operation="serve", safety="unverified", outcome="stopped",
        )
        raise
    event("service_stopped", operation="serve", safety="fenced", outcome="stopped")


def cmd_runtime(args):
    if os.geteuid() != 0:
        raise PermissionError("runtime operations require root")
    controller = Controller()
    if args.command == "serve":
        serve(controller)
    elif args.command == "health":
        try:
            return 0 if controller.health() else 100
        except Exception:
            # Unverified fencing must not deliberately demote an attached node.
            return 0
    elif args.command == "suspend":
        controller.fence(stop=True)
    elif args.command == "apply":
        service = "/usr/local/etc/rc.d/dhcp_interface_ha"
        try:
            run([service, "onestatus"])
        except subprocess.CalledProcessError:
            try:
                run([service, "onestart"])
            except Exception as exc:
                emit_event(
                    "operation_failed", syslog.LOG_ERR, operation="apply_start",
                    reason=exc, safety="unchanged", outcome="failed",
                )
                raise
        print(json.dumps(controller.reconcile()))
    else:
        result = getattr(controller, args.command)()
        if result is not None:
            print(json.dumps(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental DHCP Interface HA controller")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-mac", help="generate a locally administered unicast MAC")
    gen.set_defaults(func=cmd_generate_mac)

    validate = sub.add_parser("validate-mac", help="validate a candidate shared MAC")
    validate.add_argument("mac")
    validate.set_defaults(func=cmd_validate_mac)

    status = sub.add_parser("status", help="show observed state and dry-run reconcile plan")
    status.add_argument(
        "--from-config",
        action="store_true",
        help="read current native configuration and interface inventory",
    )
    status.add_argument("--carrier")
    status.add_argument("--shared-mac")
    status.add_argument("--enabled", action="store_true")
    status.add_argument(
        "--managed-by-dhcpha",
        action="store_true",
        help="assert that the logical managed DHCP interface is already assigned to dhcpha0lagg",
    )
    status.add_argument(
        "--mtu",
        type=int,
        default=None,
        help="native managed DHCP interface MTU to inherit for dry-run planning",
    )
    status.set_defaults(func=cmd_status)

    for command in ("prepare", "prepare_setup", "reconcile", "fence", "suspend", "resume", "remove", "health", "serve", "apply"):
        action = sub.add_parser(command, help="root controller operation")
        action.set_defaults(func=cmd_runtime)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
