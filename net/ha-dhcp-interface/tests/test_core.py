from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
import core


class MacTests(unittest.TestCase):
    def test_rejects_multicast_and_broadcast(self):
        self.assertFalse(core.validate_shared_mac("01:00:5e:00:00:01")[0])
        self.assertFalse(core.validate_shared_mac("ff:ff:ff:ff:ff:ff")[0])

    def test_generated_mac_is_local_unicast(self):
        with patch.object(core.secrets, "token_bytes", return_value=bytes.fromhex("1122334455")):
            mac = core.generate_private_mac()
        self.assertEqual(mac, "02:11:22:33:44:55")
        self.assertTrue(core.is_locally_administered_unicast(mac))


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

    def observed(self):
        return core.ObservedState(
            carp_states=("MASTER",),
            carrier=core.InterfaceSnapshot("ix0", exists=True, up=True, link_up=True),
            dhcpha=core.InterfaceSnapshot(
                core.DHCPHA_DEVICE,
                exists=True,
                mac="02:11:22:33:44:55",
                lagg_protocol="failover",
                mtu=1500,
            ),
        )

    def test_unmigrated_interface_stays_passive_regardless_of_enablement(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                settings = replace(self.settings(), enabled=enabled, managed_by_dhcpha=False)
                plan = core.plan_reconcile(settings, self.observed())
                self.assertEqual(plan.desired.attachment, core.DesiredAttachment.UNMANAGED)
                self.assertEqual(plan.commands, [])

    def test_carp_disabled_or_maintenance_fences_even_with_master_states(self):
        for flags in ({'carp_allowed': False}, {'carp_maintenance': True}):
            with self.subTest(flags=flags):
                observed = replace(self.observed(), **flags)
                desired = core.desired_state(self.settings(), observed)
                self.assertEqual(desired.attachment, core.DesiredAttachment.FENCED)


class ParseTests(unittest.TestCase):
    SAMPLE = """ix0: flags=1008943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
        ether 02:11:22:33:44:01
        carp: MASTER vhid 100 advbase 1 advskew 0
        status: active
dhcpha0lagg: flags=1008943<UP,BROADCAST,RUNNING> metric 0 mtu 1500
        ether 02:11:22:33:44:55
        laggproto failover lagghash l2,l3,l4
        laggport: ix0 flags=5<MASTER,ACTIVE>
        status: active
"""

    def test_parse_interface_and_carp(self):
        self.assertEqual(core.parse_carp_states(self.SAMPLE), ("MASTER",))
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
