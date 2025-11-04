Ethernet Settings
=================


The Marvell DPU has a RJ45 dataport (we call "primary") and a configurable
secondary port. The secondary port is connected to a QSFP cable. In the EBF we
can configure 1 to 4 ports and MAC addresses.

Ports
-----

The secondary ports will be numbered first, depending on the configured number
they will get names (`enP2p{2-5}s0`). The primary port comes after, for
example, if there are two secondary ports `enP2p{2-3}s0` it will get
``enP2p4s0`.

We connect the primary port back to the host itself. We also usually use it for
PXE boot. Altough, the other ports would work too, if we start the DHCP
services on the right network. The pxeboot tool can handle that, see
`--dpu-dev` option.

Note that the Marvell Vendor Specific Plugin of
[dpu-operator](https://github.com/openshift/dpu-operator/) will add the first
secondary interface (`enP2p2s0`) to the OVS bridge. You will want to use that
for the external network.

MAC Address
-----------

By default, the MAC address is unstable. In the UEFI boot menu we see with the
[Original Setup](original-setup) the MAC addresses

    UEFI PXEv4 (MAC:80AA99887766) << secondary
    ...
    UEFI PXEv4 (MAC:80AA99887767) << primary

but later the MAC addresses are randomize.

That is probably hightly undesirable when we install RHCOS. That is because
RHCOS will want to booth with "ip:$MAC:dhcp" on the kernel command line and
fail to configure networking. A workaround could be to change the command line
to "ip:dhcp". A better solution is to [configure a fixed MAC address](howto_fix_mac_addresses.txt).

Configure Ports
---------------

To enter the EBF menu do:
```
Marvell CN10K SOC
PASS: CRC32 verification
Transferring to thread scheduler
=======================
Marvell CN10k Boot Stub
=======================
Firmware Version: 2025-01-30 22:07:22
EBF Version: 12.25.01, Branch: /home/fae/PANW-LIO3/SDK122501/work/cn10ka-pcie-ep-release-output/build/marvell-external-fw-SDK12.25.01/firmware/ebf, Built: Thu, 30 Jan 2025 22:06:12 +0000

Board Model:    crb106-pcie
Board Revision: r1p1
Board Serial:   WA-CN106-A1-PCIE-2P100-R2-145

Chip:  0xb9 Pass B0
SKU:   MV-CN10624-B0-AAP
LLC:   49152 KB
Boot:  SPI0_CS0,SPI1_CS0, using SPI1_CS0
AVS:   Enabled

Press 'B' within 10 seconds for boot menu

=================================
Boot Options
=================================
1) Boot from Primary Boot Device
2) Boot from Secondary Boot Device
N) Boot Normally
S) Enter Setup
D) Enter DRAM Diagnostics
K) Burn boot flash using Kermit
U) Change baud rate and flow control
R) Reboot

Choice: S


=================================
Setup
=================================
B) Board Manufacturing Data
C) Chip Features
D) DRAM Options
P) PCIe Configuration
W) Power Options
E) Ethernet configuration
F) Restore factory defaults
S) Save Settings and Exit
X) Exit Setup, discarding changes

Choice: E


=================================
Setup - PORTM Selection
=================================
1) PORTM0 GSERM0.L0 - CAUI_4_C2M No FEC
2) PORTM1 GSERM0.L1 - INACTIVE 
3) PORTM2 GSERM0.L2 - INACTIVE 
4) PORTM3 GSERM0.L3 - INACTIVE 
5) PORTM4 GSERM1.L0 - XFI No FEC
6) PORTM5 GSERM2.L0 - DISABLED 
Z) Return to main menu

Choice: 
```

Here `GSERM0.L{0-3}` are the lanes for the secondary ports.
`GSERM1.L0` is the primary port.

If you configure for example `Configure PORTM with 2-Lane Protocols`,
then that will "use up" two of the `GSERM0.L*` lanes, etc.

FEC
---

Note that the FEC mode must agree with the switch. Otherwise, the interface has
no carrier.

Originally we configured "No FEC" mode. The switch can be configured for that
mode, but I noticed that our Junos Switch is very adament to reset that mode
(for example, when deleting and recreating the interface configuration in the
switch). On the Junos Switch check
```
cli -c "show interfaces et-0/0/24:0; quit
```

I was able to reconfigure the FEC mode on Junos with
```
cli -c "configure; edit interfaces et-0/0/24:0; set gigether-options fec none; commit; commit and-quit; quit"
```

I found it simpler to configure the matching FEC mode on the DPU.

The switch's `FEC74` is called `BASE_R` on the DPU and `FEC91` is `RS_FEC`.


Port Setup
--------------

Our original setup was

```
=================================
Setup - PORTM Selection
=================================
1) PORTM0 GSERM0.L0 - CAUI_4_C2M No FEC
2) PORTM1 GSERM0.L1 - INACTIVE 
3) PORTM2 GSERM0.L2 - INACTIVE 
4) PORTM3 GSERM0.L3 - INACTIVE 
5) PORTM4 GSERM1.L0 - XFI No FEC
6) PORTM5 GSERM2.L0 - DISABLED 
Z) Return to main menu

Choice: 1


=================================
Setup - PORTM0 Config Options
=================================
1) Configure PORTM with 1-Lane Protocols
2) Configure PORTM with 2-Lane Protocols
3) Configure PORTM with 4-Lane Protocols
A) Configure 802.3AP settings
R) Configure RVU Num VFs settings
I) Configure Inter Packet Gap setting
Z) Return to main menu

Choice: 3


=================================
Setup - PORTM0 Configuration
=================================
1) XLAUI(C2C), Serdes Speed: 10.3125G, Data Speed: 4*10
2) XLAUI_C2M, Serdes Speed: 10.3125G, Data Speed: 4*10G
3) 40GBASE-CR4, Serdes Speed: 10.3125G, Data Speed: 4*10G
4) 40GBASE-KR4, Serdes Speed: 10.3125G, Data Speed: 4*10G
5) CAUI-4_C2C, Serdes Speed: 25.78125G, Data Speed: 4*25G
6) CAUI-4_C2M, Serdes Speed: 25.78125G, Data Speed: 4*25G
7) 100GBASE-CR4, Serdes Speed: 25.78125G, Data Speed: 4*25G
8) 100GBASE-KR4, Serdes Speed: 25.78125G, Data Speed: 4*25G
Z) Return to main menu

Choice: 6

Program FEC
(INS)FEC TYPE (NONE = 0, BASE_R = 1, RS_FEC = 2): 0
```

Note that the FEC mode here is `NONE`. See [FEC](#fec) above whether
you want to change that.

Here we only have one secondary interface (`enP2p2s0`) and the primary (`enP2p3s0`).
With the two-cluster setup of the DPU Operator, where we run Microshift on the DPU, that
suffices. We use the primary (connected back to the DPU's host) for PXE boot. Afterwards
it is mostly unused, but useful for accessing the DPU from the host. The secondary is switched
to an external network.

When we use a one-cluster setup with the DPU being an OCP node, we need also a connection
to the OCP network. Optimally, we use another secondary port for that.

At this moment, our tools are not ready to handle one-cluster setup and expect the "original setup".
But in the future, we want to reconfigure the DPUs to use all secondary ports. Thus we will
configure:

```
=================================
Setup - PORTM Selection
=================================
1) PORTM0 GSERM0.L0 - 25GAUI_C2M BASER FEC
2) PORTM1 GSERM0.L1 - 25GAUI_C2M BASER FEC
3) PORTM2 GSERM0.L2 - 25GAUI_C2M BASER FEC
4) PORTM3 GSERM0.L3 - 25GAUI_C2M BASER FEC
5) PORTM4 GSERM1.L0 - XFI No FEC
6) PORTM5 GSERM2.L0 - DISABLED 
Z) Return to main menu

Choice: 1


=================================
Setup - PORTM0 Config Options
=================================
1) Configure PORTM with 1-Lane Protocols
2) Configure PORTM with 2-Lane Protocols
3) Configure PORTM with 4-Lane Protocols
A) Configure 802.3AP settings
R) Configure RVU Num VFs settings
I) Configure Inter Packet Gap setting
Z) Return to main menu

Choice: 1


=================================
Setup - PORTM0 Configuration
=================================
1) SGMII, Serdes Speed: 1.25G, Data Speed: 1*1 MAC (10M..1G)
2) 1000BASE-X, Serdes Speed: 1.25G, Data Speed: 1*1G
3) 2500BASE-X, Serdes Speed: 3.125G, Data Speed: 1*2.5G
4) SFI_1G, Serdes Speed: 1.25G, Data Speed: 1*1G
5) XFI, Serdes Speed: 10.3125G, Data Speed: 1*10G
6) SFI, Serdes Speed: 10.3125G, Data Speed: 1*10G
7) 10GBASE-KR, Serdes Speed: 10.3125G, Data Speed: 1*10G
8) 25GAUI_C2C, Serdes Speed: 25.78125G, Data Speed: 1*25G
9) 25GAUI_C2M, Serdes Speed: 25.78125G, Data Speed: 1*25G
A) 25GBASE-CR, Serdes Speed: 25.78125G, Data Speed: 1*25G
B) 25GBASE-KR, Serdes Speed: 25.78125G, Data Speed: 1*25G
C) 25GBASE-CR Cons, Serdes Speed: 25.78125G, Data Speed: 1*25G
D) 25GBASE-KR Cons, Serdes Speed: 25.78125G, Data Speed: 1*25G
E) 50GAUI-1_C2C, Serdes Speed: 26.5625G, Data Speed: 1*51.51G
F) 50GAUI-1_C2M, Serdes Speed: 26.5625G, Data Speed: 1*51.51G
G) 50GBASE-CR, Serdes Speed: 26.5625G, Data Speed: 1*51.51G
H) 50GBASE-KR, Serdes Speed: 26.5625G, Data Speed: 1*51.51G
I) 10G-SXGMII, Serdes speed: 10.3125G, Data Speed: 1*1 MAC (10M..1
J) Disabled
Z) Return to main menu

Choice: 9

Program FEC
(INS)FEC TYPE (NONE = 0, BASE_R = 1, RS_FEC = 2): 1
```

To configure the Junos switch with a 4x25G breakout (fanout) connection, set the following:
```
configure
set chassis fpc 0 pic 0 port 27 channel-speed 25g
commit
```
