<section class="page-content-main">
    <div class="content-box" style="padding: 20px;">
        <h2>{{ lang._('HA DHCP Interface') }}</h2>
        <p>{{ lang._('Let a DHCP connection follow the active firewall in your high availability pair. This is useful for an internet connection that receives its address automatically.') }}</p>

        <h3>{{ lang._('How it works') }}</h3>
        <ol>
            <li>{{ lang._('OPNsense chooses the active firewall through its existing CARP high availability system.') }}</li>
            <li>{{ lang._('The plugin connects the selected interface on the active firewall and keeps the standby side disconnected from that connection.') }}</li>
            <li>{{ lang._('When the firewalls change roles, the former active side disconnects and the new active side connects and requests an address through DHCP.') }}</li>
        </ol>
        <p>{{ lang._('Both firewalls use the same shared MAC address, so the upstream network sees the same connection identity. A handoff may briefly interrupt connectivity; the DHCP server decides which address is assigned.') }}</p>

        <h3>{{ lang._('Getting started') }}</h3>
        <p>{{ lang._('Start with an OPNsense HA pair and connect both selected interfaces to the same upstream network. In Settings on each firewall, choose its local DHCP interface and use the same shared MAC address on both. Select Enable and click Save & Apply; the plugin prepares the interface automatically.') }}</p>

        <h3>{{ lang._('What to expect') }}</h3>
        <p>{{ lang._('Settings shows whether this firewall is active, waiting for DHCP, or on standby. Standby is normal: it is ready to take over when CARP changes roles. Use Diagnostics for connection details and the Log tab for events and problems.') }}</p>
        <p>{{ lang._('The plugin follows your existing HA setup. OPNsense continues to handle DHCP, routing, firewall rules and connection-state synchronization.') }}</p>
        <p><a class="btn btn-primary" href="/ui/dhcpinterfaceha">{{ lang._('Open Settings') }}</a></p>
    </div>
</section>
