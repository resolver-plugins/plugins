import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


CORE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opnsense"
    / "scripts"
    / "dhcp_interface_ha"
    / "core.py"
)
SPEC = importlib.util.spec_from_file_location("dhcp_interface_ha_core", CORE)
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


class MacTests(unittest.TestCase):
    def test_rejects_multicast_and_broadcast(self):
        self.assertFalse(core.validate_shared_mac("01:00:5e:00:00:01")[0])
        self.assertFalse(core.validate_shared_mac("ff:ff:ff:ff:ff:ff")[0])

    def test_generated_mac_is_local_unicast(self):
        with patch.object(core.secrets, "token_bytes", return_value=bytes.fromhex("1122334455")):
            mac = core.generate_private_mac()
        self.assertEqual(mac, "02:11:22:33:44:55")
        self.assertTrue(core.is_locally_administered_unicast(mac))

    def test_flags_virtual_router_range(self):
        self.assertTrue(core.is_virtual_router_mac("00:00:5e:00:01:ed"))
        self.assertTrue(core.is_virtual_router_mac("00:00:5e:00:02:ed"))
        self.assertFalse(core.is_virtual_router_mac("02:00:5e:00:01:ed"))


class CarpRoleTests(unittest.TestCase):
    def test_all_master_is_master(self):
        self.assertEqual(
            core.reduce_carp_role(["MASTER", "MASTER"]),
            core.GlobalRole.MASTER,
        )

    def test_any_backup_is_backup(self):
        self.assertEqual(
            core.reduce_carp_role(["MASTER", "BACKUP"]),
            core.GlobalRole.BACKUP,
        )

    def test_init_mixed_or_empty_is_indeterminate(self):
        self.assertEqual(core.reduce_carp_role(["MASTER", "INIT"]), core.GlobalRole.INDETERMINATE)
        self.assertEqual(core.reduce_carp_role(["INIT"]), core.GlobalRole.INDETERMINATE)
        self.assertEqual(core.reduce_carp_role([]), core.GlobalRole.INDETERMINATE)


class DesiredStateTests(unittest.TestCase):
    def settings(self):
        return core.Settings(True, "ix0", "02:11:22:33:44:55")

    def observed(self, states=("MASTER",), link=True, member=False, dhcpha_up=False):
        return core.ObservedState(
            carp_states=states,
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=link),
            dhcpha=core.InterfaceSnapshot(
                core.DHCPHA_DEVICE,
                exists=True,
                up=dhcpha_up,
                link_up=member and link,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
                lagg_members=("ix0",) if member else (),
            ),
        )

    def test_pre_migration_state_is_passive(self):
        settings = core.Settings(
            enabled=False,
            carrier="ix0",
            shared_mac="02:11:22:33:44:55",
            managed_by_dhcpha=False,
        )
        observed = self.observed()
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.UNMANAGED)
        self.assertEqual(plan.commands, [])

    def test_enabled_but_not_migrated_is_still_passive(self):
        settings = core.Settings(
            enabled=True,
            carrier="ix0",
            shared_mac="02:11:22:33:44:55",
            managed_by_dhcpha=False,
        )
        observed = self.observed()
        plan = core.plan_reconcile(settings, observed)
        self.assertEqual(plan.desired.attachment, core.DesiredAttachment.UNMANAGED)
        self.assertEqual(plan.commands, [])

    def test_master_with_healthy_carrier_attaches(self):
        desired = core.desired_state(self.settings(), self.observed())
        self.assertEqual(desired.attachment, core.DesiredAttachment.ATTACHED)

    def test_carp_disabled_fences_even_with_master_states(self):
        observed = self.observed()
        observed = core.ObservedState(
            carp_states=observed.carp_states,
            carp_allowed=False,
            carrier=observed.carrier,
            dhcpha=observed.dhcpha,
        )
        desired = core.desired_state(self.settings(), observed)
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_persistent_maintenance_fences_even_with_master_states(self):
        observed = self.observed()
        observed = core.ObservedState(
            carp_states=observed.carp_states,
            carp_allowed=True,
            carp_maintenance=True,
            carrier=observed.carrier,
            dhcpha=observed.dhcpha,
        )
        desired = core.desired_state(self.settings(), observed)
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_backup_fences(self):
        desired = core.desired_state(self.settings(), self.observed(states=("BACKUP",)))
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)

    def test_common_upstream_status_is_not_an_input(self):
        # There is deliberately no gateway/DHCP/Internet health input.
        desired = core.desired_state(self.settings(), self.observed())
        self.assertEqual(desired.attachment, core.DesiredAttachment.ATTACHED)

    def test_local_link_failure_fences_even_when_master(self):
        desired = core.desired_state(self.settings(), self.observed(link=False))
        self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)


class FailbackTests(unittest.TestCase):
    def test_preferred_node_waits_while_peer_master_is_alive(self):
        decision = core.evaluate_failback(
            now=100.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=True,
            state=core.FailbackState(),
        )
        self.assertFalse(decision.allow_preempt)
        self.assertEqual(decision.state.healthy_since, 100.0)
        self.assertEqual(decision.remaining_seconds, 120.0)

    def test_hold_expires_after_continuous_health(self):
        decision = core.evaluate_failback(
            now=221.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=True,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertTrue(decision.allow_preempt)
        self.assertEqual(decision.remaining_seconds, 0.0)

    def test_native_master_transition_bypasses_hold(self):
        decision = core.evaluate_failback(
            now=110.0,
            delay_seconds=120,
            local_is_master=True,
            local_healthy=True,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertTrue(decision.allow_preempt)
        self.assertIsNone(decision.state.healthy_since)

    def test_health_failure_resets_hold(self):
        decision = core.evaluate_failback(
            now=150.0,
            delay_seconds=120,
            local_is_master=False,
            local_healthy=False,
            state=core.FailbackState(healthy_since=100.0),
        )
        self.assertFalse(decision.allow_preempt)
        self.assertIsNone(decision.state.healthy_since)


    def test_failback_never_enables_admin_disabled_preemption(self):
        decision = core.FailbackDecision(
            state=core.FailbackState(),
            allow_preempt=True,
            remaining_seconds=0.0,
            reason="complete",
        )
        self.assertFalse(
            core.desired_preempt_enabled(
                baseline_preempt_enabled=False,
                failback=decision,
            )
        )

    def test_failback_hold_suppresses_admin_enabled_preemption(self):
        decision = core.FailbackDecision(
            state=core.FailbackState(healthy_since=100.0),
            allow_preempt=False,
            remaining_seconds=60.0,
            reason="hold",
        )
        self.assertFalse(
            core.desired_preempt_enabled(
                baseline_preempt_enabled=True,
                failback=decision,
            )
        )

    def test_completed_hold_restores_admin_enabled_preemption(self):
        decision = core.FailbackDecision(
            state=core.FailbackState(healthy_since=100.0),
            allow_preempt=True,
            remaining_seconds=0.0,
            reason="complete",
        )
        self.assertTrue(
            core.desired_preempt_enabled(
                baseline_preempt_enabled=True,
                failback=decision,
            )
        )


class ParseTests(unittest.TestCase):
    SAMPLE = """ix0: flags=1008943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
        ether 00:e0:ed:73:20:4e
        carp: MASTER vhid 100 advbase 1 advskew 0
        status: active
dhcpha0lagg: flags=1008943<UP,BROADCAST,RUNNING> metric 0 mtu 1500
        ether 02:11:22:33:44:55
        laggproto failover lagghash l2,l3,l4
        laggport: ix0 flags=5<MASTER,ACTIVE>
        status: active
"""

    def test_parse_carp(self):
        self.assertEqual(core.parse_carp_states(self.SAMPLE), ("MASTER",))

    def test_parse_interface(self):
        snap = core.parse_interface_snapshot("dhcpha0lagg", self.SAMPLE)
        self.assertTrue(snap.exists)
        self.assertTrue(snap.up)
        self.assertTrue(snap.link_up)
        self.assertEqual(snap.mac, "02:11:22:33:44:55")
        self.assertEqual(snap.lagg_protocol, "failover")
        self.assertEqual(snap.mtu, 1500)
        self.assertEqual(snap.lagg_members, ("ix0",))


if __name__ == "__main__":
    unittest.main()
