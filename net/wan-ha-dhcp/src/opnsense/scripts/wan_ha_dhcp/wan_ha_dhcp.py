#!/usr/local/bin/python3

"""Experimental WAN HA DHCP controller and read-only planning CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import signal
import syslog
import threading

from runtime import Controller, run

from core import (
    InterfaceSnapshot,
    ObservedState,
    Settings,
    WANHA_DEVICE,
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
            managed_by_wanha=args.managed_by_wanha,
            managed_mtu=args.mtu,
        )
        managed_name = None
        managed_config = {}
        config_source = "arguments"

    data = read_ifconfig()
    carp_states = parse_carp_states(data)
    carrier = parse_interface_snapshot(settings.carrier, data)
    wanha = parse_interface_snapshot(WANHA_DEVICE, data)

    carp_allowed, carp_maintenance = read_carp_admin()
    observed = ObservedState(
        carp_states=carp_states,
        carp_allowed=carp_allowed,
        carp_maintenance=carp_maintenance,
        wanha_owned=Controller().owned(),
        carrier=carrier,
        wanha=wanha,
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
            "managed_by_wanha": settings.managed_by_wanha,
            "managed_mtu": settings.managed_mtu,
        },
        "carp_states": list(carp_states),
        "carp_allowed": carp_allowed,
        "carp_maintenance": carp_maintenance,
        "global_role": plan.desired.role.value,
        "desired_attachment": plan.desired.attachment.value,
        "reason": plan.desired.reason,
        "carrier": carrier.__dict__,
        "wanha": wanha.__dict__,
        "commands": [
            {"argv": list(command.argv), "reason": command.reason}
            for command in plan.commands
        ],
        "warnings": plan.warnings,
    }
    print(json.dumps(payload, indent=2))
    return 0


def serve(controller):
    wake = threading.Event()
    stopping = False

    def terminate(_signum, _frame):
        nonlocal stopping
        stopping = True
        wake.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGUSR1, lambda *_: wake.set())
    while not stopping:
        wake.clear()
        try:
            controller.reconcile()
        except Exception as exc:
            syslog.syslog(syslog.LOG_ERR, str(exc))
        # Native service health takes its own lock and calls our health hook.
        # Never invoke it while holding the WAN transition lock.
        try:
            run(["/usr/local/sbin/carp_service_status"])
        except Exception as exc:
            syslog.syslog(syslog.LOG_ERR, "CARP service health refresh failed: " + str(exc))
        wake.wait(5)
    controller.fence(stop=True)


def cmd_runtime(args):
    if os.geteuid() != 0:
        raise PermissionError("runtime operations require root")
    controller = Controller()
    if args.command == "serve":
        serve(controller)
    elif args.command == "health":
        try:
            return 0 if controller.health() else 100
        except Exception as exc:
            # Unverified fencing must not deliberately demote an attached node.
            syslog.syslog(syslog.LOG_ERR, "WAN health unknown; demotion withheld: " + str(exc))
            return 0
    elif args.command == "suspend":
        controller.fence(stop=True)
    elif args.command == "apply":
        service = "/usr/local/etc/rc.d/wan_ha_dhcp"
        try:
            run([service, "onestatus"])
        except subprocess.CalledProcessError:
            run([service, "onestart"])
        print(json.dumps(controller.reconcile()))
    else:
        result = getattr(controller, args.command)()
        if result is not None:
            print(json.dumps(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Experimental WAN HA DHCP controller")
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
        "--managed-by-wanha",
        action="store_true",
        help="assert that the logical managed WAN is already assigned to wanha0lagg",
    )
    status.add_argument(
        "--mtu",
        type=int,
        default=None,
        help="native managed WAN MTU to inherit for dry-run planning",
    )
    status.set_defaults(func=cmd_status)

    for command in ("prepare", "reconcile", "fence", "suspend", "resume", "remove", "health", "serve", "apply"):
        action = sub.add_parser(command, help="root controller operation")
        action.set_defaults(func=cmd_runtime)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        syslog.syslog(syslog.LOG_ERR, str(exc))
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
