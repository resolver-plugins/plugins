# Maintainer documentation

These guides describe the Resolver Plugins `os-bind-rp` fork for maintainers
and contributors.

Read the guide matching the work you are about to do:

- [Fork model](fork-model.md) explains package identity, compatibility, and
  branch responsibilities.
- [Building](building.md) describes release metadata and local package builds.
- [Upstream synchronization](upstream-sync.md) describes the scheduled CI
  workflow, review PRs, and temporary artifacts.
- [Package repository](package-repository.md) describes the signed GitHub
  Release channels, publication workflow, and key rotation responsibilities.
- [HA DHCP Interface design](dhcp-interface-ha.md) specifies the experimental `os-dhcp-interface-ha` architecture, safety invariants, required UI/API and migration behavior, qualification gates, and product roadmap.
- [HA DHCP Interface UI repair plan](dhcp-interface-ha-ui-plan.md) records the implemented source changes, ordered acceptance checks, local verification, and outstanding native-framework/appliance qualification.
- [HA DHCP Interface UI streamlining specification](dhcp-interface-ha-ui-streamlining-spec.md) defines the 0.2_9 increment: automated setup ownership, guarded recovery, a quieter interface, and native logging for users and agents.
- [HA DHCP Interface UI streamlining plan](dhcp-interface-ha-ui-streamlining-plan.md) tracks implementation, behavior tests and native acceptance gates separately from appliance deployment.
- [HA DHCP Interface standby Internet specification](dhcp-interface-ha-standby-internet-spec.md) specifies optional IPv4 standby access through the active firewall over a selected internal interface, with role-dependent routing and preserved WAN fencing.
- [HA DHCP Interface standby Internet plan](dhcp-interface-ha-standby-internet-plan.md) records implementation, native routing evidence and remaining two-node qualification.

- [HA DHCP Interface releases](dhcp-interface-ha-releases.md) describes its manual package workflow, signed current/rollback feeds, provenance checks and installation.
