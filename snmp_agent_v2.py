#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPP SNMP Agent V2 - Improved polling-based SNMP agent for VPP
Features:
- Real-time async data polling from VPP API
- Better timeout handling
- Graceful error recovery
- Responsive SNMP queries
- Proper bonding interface speed handling
"""

import argparse
import logging
import threading
import time
import queue
import yaml
import signal
import sys
from collections import defaultdict
from datetime import datetime

try:
    from vppapi import VPPApi
    from vppstats import VPPStats
except ImportError:
    print("ERROR: Could not import vppapi or vppstats")
    sys.exit(1)

try:
    import agentx
except ImportError:
    print("ERROR: Could not import agentx")
    sys.exit(1)


def get_interface_speed(ifname, ifaces, logger=None):
    """
    Get interface speed with special handling for bonding interfaces.
    For bonding interfaces that have no direct link_speed, 
    derive speed from member interfaces.
    Returns speed in Kbps (as stored in VPP)
    """
    # Normalize to lowercase for comparison
    ifname_lower = ifname.lower()
    
    if ifname_lower.startswith("loop") or ifname_lower.startswith("tap"):
        return 1000000  # 1 Gbps in Kbps for loopback/tap
    
    if ifname not in ifaces:
        if logger:
            logger.warning("Could not get link speed for interface %s", ifname)
        return 0
    
    iface = ifaces[ifname]
    
    # If interface has direct link_speed, use it
    if iface.link_speed > 0:
        return iface.link_speed
    
    # For bonding interfaces (bond0, bond1, etc.) with zero speed,
    # try to derive speed from member interfaces
    if ifname_lower.startswith("bond"):
        # Get all member interface speeds
        # Strategy: Find any physical interface with speed in the system
        member_speeds = []
        if logger:
            logger.debug(f"[BOND DEBUG] Processing {ifname}, found {len(ifaces)} interfaces total")
        
        for other_ifname, other_iface in ifaces.items():
            # Skip the bond interface itself
            if other_ifname == ifname:
                continue
            
            # Check if this interface is a member of the bond
            try:
                speed_found = False
                other_speed = getattr(other_iface, 'link_speed', 0)
                if logger:
                    logger.debug(f"[BOND DEBUG]   Checking {other_ifname}: link_speed={other_speed}, type={type(other_iface).__name__}")
                
                # Try to get member info from VPP data structures
                if hasattr(other_iface, 'bond_interface') and other_iface.bond_interface == iface.sw_if_index:
                    if other_iface.link_speed > 0:
                        member_speeds.append(other_iface.link_speed)
                        speed_found = True
                        if logger:
                            logger.debug(f"[BOND DEBUG]     → Found via bond_interface attribute")
                
                # If not found yet, check if interface name follows bond naming conventions
                if not speed_found and (other_ifname.startswith(ifname + ".") or other_ifname.startswith(ifname + "-")):
                    if other_iface.link_speed > 0:
                        member_speeds.append(other_iface.link_speed)
                        speed_found = True
                        if logger:
                            logger.debug(f"[BOND DEBUG]     → Found via naming convention")
                
                # If still not found, fallback to physical interfaces with speed
                # This handles cases where VPP doesn't explicitly track membership
                if not speed_found and hasattr(other_iface, 'link_speed') and other_iface.link_speed > 0:
                    # Check if it looks like a physical interface
                    if any(x in other_ifname for x in ['Hundred', 'Ten', 'Gigabit', 'Ethernet', 'eth']):
                        # Exclude special interfaces
                        if not (other_ifname.lower().startswith('loop') or other_ifname.lower().startswith('tap')):
                            member_speeds.append(other_iface.link_speed)
                            speed_found = True
                            if logger:
                                logger.debug(f"[BOND DEBUG]     → Found via fallback (physical interface)")
            except Exception as e:
                if logger:
                    logger.debug(f"[BOND DEBUG]     → Exception: {e}")
                pass
        
        # If we found member speeds, SUM all for aggregate bonding speed
        # (not just first member - all active members contribute to total bandwidth)
        if member_speeds:
            if logger:
                logger.debug(f"[BOND DEBUG] {ifname} member speeds: {member_speeds}")
            total_speed = sum(member_speeds)
            if logger:
                logger.info(f"Bonding interface {ifname} aggregate speed: {total_speed} Kbps ({total_speed/1000000:.0f} Gbps)")
            return total_speed
        
        # If still no speed found, default to 1 Gbps
        if logger:
            logger.warning(f"Bonding interface {ifname} has no member with speed, defaulting to 1 Gbps")
        return 1000000  # Default 1 Gbps in Kbps
    
    return 0


class VPPDataCollector:
    """
    Collects data from VPP in a separate thread
    Provides thread-safe access to latest data
    """
    
    def __init__(self, poll_interval=5, timeout=5):
        """
        Args:
            poll_interval: Seconds between polls (default 5 seconds)
            timeout: VPP API timeout in seconds (default 5 seconds)
        """
        self.logger = logging.getLogger("VPPDataCollector")
        self.poll_interval = poll_interval
        self.timeout = timeout
        
        # Thread control
        self._running = False
        self._thread = None
        self._lock = threading.RLock()
        
        # Data storage
        self._data = {
            'interfaces': {},
            'iface_stats': {},
            'lcps': {},
            'last_update': 0,
            'error_count': 0,
            'update_count': 0,
        }
        
        # VPP connections
        self.vpp_api = None
        self.vpp_stats = None
        
    def start(self):
        """Start the data collection thread"""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self.logger.info(f"Data collector started (poll interval: {self.poll_interval}s)")
        
    def stop(self):
        """Stop the data collection thread"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("Data collector stopped")
        
    def _poll_loop(self):
        """Main polling loop running in separate thread"""
        consecutive_errors = 0
        max_consecutive_errors = 3
        
        while self._running:
            try:
                # Connect to VPP if not connected
                if not self.vpp_api or not self.vpp_api.connected:
                    self._connect_vpp()
                
                if not self.vpp_stats:
                    self._connect_stats()
                
                # Collect data
                self._collect_data()
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                with self._lock:
                    self._data['error_count'] += 1
                    
                self.logger.error(f"Poll error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.error("Too many consecutive errors, reconnecting...")
                    self._disconnect_vpp()
                    consecutive_errors = 0
                    time.sleep(2)  # Backoff before retry
                else:
                    time.sleep(0.5)  # Short sleep before next attempt
                continue
            
            # Sleep until next poll
            time.sleep(self.poll_interval)
    
    def _connect_vpp(self):
        """Connect to VPP API"""
        self.logger.info("Connecting to VPP API...")
        self.vpp_api = VPPApi(clientname="snmp-agent-v2")
        if not self.vpp_api.connect():
            raise Exception("Failed to connect to VPP API")
        self.logger.info("Connected to VPP API")
    
    def _connect_stats(self):
        """Connect to VPP Stats segment"""
        self.logger.info("Connecting to VPP Stats...")
        self.vpp_stats = VPPStats(socketname="/run/vpp/stats.sock", timeout=self.timeout)
        self.vpp_stats.connect()
        self.logger.info("Connected to VPP Stats")
    
    def _disconnect_vpp(self):
        """Disconnect from VPP"""
        if self.vpp_api:
            try:
                self.vpp_api.disconnect()
            except:
                pass
            self.vpp_api = None
            
        if self.vpp_stats:
            try:
                self.vpp_stats.disconnect()
            except:
                pass
            self.vpp_stats = None
    
    def _collect_data(self):
        """Collect data from VPP"""
        interfaces = self.vpp_api.get_ifaces()
        lcps = self.vpp_api.get_lcp()
        
        # Get stats from shared memory
        iface_stats = {}
        iface_names = self.vpp_stats["/if/names"]
        
        for i, ifname in enumerate(iface_names):
            try:
                stats = {
                    'rx_packets': self.vpp_stats["/if/rx"][:, i].sum_packets(),
                    'rx_octets': self.vpp_stats["/if/rx"][:, i].sum_octets(),
                    'rx_errors': self.vpp_stats["/if/rx-error"][:, i].sum(),
                    'rx_no_buf': self.vpp_stats["/if/rx-no-buf"][:, i].sum(),
                    'rx_multicast': self.vpp_stats["/if/rx-multicast"][:, i].sum_packets(),
                    'rx_broadcast': self.vpp_stats["/if/rx-broadcast"][:, i].sum_packets(),
                    
                    'tx_packets': self.vpp_stats["/if/tx"][:, i].sum_packets(),
                    'tx_octets': self.vpp_stats["/if/tx"][:, i].sum_octets(),
                    'tx_errors': self.vpp_stats["/if/tx-error"][:, i].sum(),
                    'tx_multicast': self.vpp_stats["/if/tx-multicast"][:, i].sum_packets(),
                    'tx_broadcast': self.vpp_stats["/if/tx-broadcast"][:, i].sum_packets(),
                    'drops': self.vpp_stats["/if/drops"][:, i].sum(),
                    'timestamp': time.time(),
                }
                iface_stats[ifname] = stats
            except Exception as e:
                self.logger.warning(f"Could not get stats for {ifname}: {e}")
        
        # Update data atomically
        with self._lock:
            self._data['interfaces'] = interfaces
            self._data['iface_stats'] = iface_stats
            self._data['lcps'] = lcps
            self._data['last_update'] = time.time()
            self._data['update_count'] += 1
    
    def get_data(self):
        """Get current data snapshot (thread-safe)"""
        with self._lock:
            return {
                'interfaces': dict(self._data['interfaces']),
                'iface_stats': dict(self._data['iface_stats']),
                'lcps': dict(self._data['lcps']),
                'last_update': self._data['last_update'],
                'error_count': self._data['error_count'],
                'update_count': self._data['update_count'],
            }
    
    def get_interface_stat(self, ifname, stat_name):
        """Get a specific interface statistic (thread-safe)"""
        with self._lock:
            if ifname in self._data['iface_stats']:
                return self._data['iface_stats'][ifname].get(stat_name, 0)
            return 0


class SNMPAgent(agentx.Agent):
    """
    SNMP Agent that responds to SNMP queries
    Uses pyagentx for AgentX protocol
    """
    
    def __init__(self, collector, config=None, *args, **kwargs):
        self.logger = logging.getLogger("SNMPAgent")
        self.collector = collector
        self.config = config or {}
        super().__init__(*args, **kwargs)
    
    def setup(self):
        """Setup SNMP OID registrations"""
        self.logger.info("Setting up SNMP OID registrations")
        # Register MIB-2 interface tree
        self.register("1.3.6.1.2.1.2.2.1")  # ifTable
        self.register("1.3.6.1.2.1.31.1.1.1")  # ifXTable
        return True
    
    def update(self):
        """Update SNMP data from VPP collector"""
        ds = agentx.DataSet()
        
        try:
            data = self.collector.get_data()
            interfaces = data['interfaces']
            iface_stats = data['iface_stats']
            iface_names = list(iface_stats.keys())
            
            self.logger.debug(f"Updating SNMP data for {len(iface_names)} interfaces")
            
            # Build MIB data for each interface
            for idx, ifname in enumerate(iface_names, start=1000):
                stats = iface_stats.get(ifname, {})
                iface = interfaces.get(ifname)
                
                # Get interface properties
                mtu = iface.mtu[0] if iface else 0
                admin_status = 1 if (iface and int(iface.flags) & 1) else 2
                oper_status = 1 if (iface and int(iface.flags) & 2) else 2
                mac = str(iface.l2_address) if iface else "00:00:00:00:00:00"
                
                # Get speed with bonding interface support
                speed_kbps = get_interface_speed(ifname, interfaces, self.logger)
                speed_bps = speed_kbps * 1000
                if speed_bps >= 2 ** 32:
                    speed_32 = 2 ** 32 - 1
                else:
                    speed_32 = speed_bps
                
                # Set interface table OIDs (ifTable - 1.3.6.1.2.1.2.2.1)
                ds.set(f"1.3.6.1.2.1.2.2.1.1.{idx}", "int", idx)
                ds.set(f"1.3.6.1.2.1.2.2.1.2.{idx}", "str", ifname)
                
                # Interface type
                if ifname.startswith("loop"):
                    ds.set(f"1.3.6.1.2.1.2.2.1.3.{idx}", "int", 24)  # softwareLoopback
                else:
                    ds.set(f"1.3.6.1.2.1.2.2.1.3.{idx}", "int", 6)  # ethernet-csmacd
                
                ds.set(f"1.3.6.1.2.1.2.2.1.4.{idx}", "int", mtu)
                ds.set(f"1.3.6.1.2.1.2.2.1.5.{idx}", "gauge32", int(speed_32))
                ds.set(f"1.3.6.1.2.1.2.2.1.6.{idx}", "str", mac)
                ds.set(f"1.3.6.1.2.1.2.2.1.7.{idx}", "int", admin_status)
                ds.set(f"1.3.6.1.2.1.2.2.1.8.{idx}", "int", oper_status)
                ds.set(f"1.3.6.1.2.1.2.2.1.9.{idx}", "ticks", 0)
                
                # RX stats (32-bit)
                ds.set(f"1.3.6.1.2.1.2.2.1.10.{idx}", "u32", int(stats.get('rx_octets', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.11.{idx}", "u32", int(stats.get('rx_packets', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.12.{idx}", "u32", int(stats.get('rx_multicast', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.13.{idx}", "u32", int(stats.get('rx_no_buf', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.14.{idx}", "u32", int(stats.get('rx_errors', 0) % 2**32))
                
                # TX stats (32-bit)
                ds.set(f"1.3.6.1.2.1.2.2.1.16.{idx}", "u32", int(stats.get('tx_octets', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.17.{idx}", "u32", int(stats.get('tx_packets', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.18.{idx}", "u32", int(stats.get('tx_multicast', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.19.{idx}", "u32", int(stats.get('drops', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.2.2.1.20.{idx}", "u32", int(stats.get('tx_errors', 0) % 2**32))
                
                # Set ifXTable OIDs (1.3.6.1.2.1.31.1.1.1)
                ds.set(f"1.3.6.1.2.1.31.1.1.1.1.{idx}", "str", ifname)
                ds.set(f"1.3.6.1.2.1.31.1.1.1.2.{idx}", "u32", int(stats.get('rx_multicast', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.3.{idx}", "u32", int(stats.get('rx_broadcast', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.4.{idx}", "u32", int(stats.get('tx_multicast', 0) % 2**32))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.5.{idx}", "u32", int(stats.get('tx_broadcast', 0) % 2**32))
                
                # 64-bit counters
                ds.set(f"1.3.6.1.2.1.31.1.1.1.6.{idx}", "u64", int(stats.get('rx_octets', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.7.{idx}", "u64", int(stats.get('rx_packets', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.8.{idx}", "u64", int(stats.get('rx_multicast', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.9.{idx}", "u64", int(stats.get('rx_broadcast', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.10.{idx}", "u64", int(stats.get('tx_octets', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.11.{idx}", "u64", int(stats.get('tx_packets', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.12.{idx}", "u64", int(stats.get('tx_multicast', 0)))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.13.{idx}", "u64", int(stats.get('tx_broadcast', 0)))
                
                # Speed in Mbps for ifXTable
                speed_mbps = int(speed_kbps / 1000)
                ds.set(f"1.3.6.1.2.1.31.1.1.1.15.{idx}", "gauge32", speed_mbps)
                
                # Other ifXTable OIDs
                ds.set(f"1.3.6.1.2.1.31.1.1.1.16.{idx}", "int", 2)  # promiscuousMode: false
                ds.set(f"1.3.6.1.2.1.31.1.1.1.17.{idx}", "int", 1)  # connectionless: true
                ds.set(f"1.3.6.1.2.1.31.1.1.1.18.{idx}", "str", ifname)  # ifAlias
                ds.set(f"1.3.6.1.2.1.31.1.1.1.19.{idx}", "ticks", 0)  # ifCounterDiscontinuityTime
                
                self.logger.debug(
                    f"Interface {ifname}: speed={speed_kbps}Kbps, "
                    f"rx_pkts={stats.get('rx_packets', 0)}, "
                    f"tx_pkts={stats.get('tx_packets', 0)}"
                )
            
            return ds
            
        except Exception as e:
            self.logger.error(f"Error updating SNMP data: {e}", exc_info=True)
            return agentx.DataSet()


def setup_logging(debug=False):
    """Configure logging"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    parser = argparse.ArgumentParser(
        description="VPP SNMP Agent V2",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-p", "--period",
        type=int,
        default=5,
        help="Polling period in seconds (default: 5)"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=5,
        help="VPP API timeout in seconds (default: 5)"
    )
    parser.add_argument(
        "-a", "--address",
        type=str,
        default="localhost:705",
        help="SNMP agent socket address (default: localhost:705)"
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        help="Configuration YAML file"
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "-dd", "--debug-agent",
        action="store_true",
        help="Enable AgentX debug logging"
    )
    
    args = parser.parse_args()
    setup_logging(args.debug)
    
    logger = logging.getLogger("main")
    
    # Setup AgentX logging if debug enabled
    if args.debug_agent:
        agentx.setup_logging(debug=True)
    
    # Load config
    config = {}
    if args.config:
        try:
            with open(args.config, 'r') as f:
                config = yaml.load(f, Loader=yaml.FullLoader)
                logger.info(f"Loaded config from {args.config}")
        except Exception as e:
            logger.error(f"Could not load config: {e}")
    
    # Create collector
    collector = VPPDataCollector(poll_interval=args.period, timeout=args.timeout)
    
    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received signal, shutting down...")
        collector.stop()
        agent.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start collector
    collector.start()
    
    # Wait for data
    logger.info("Waiting for first data update...")
    for i in range(30):
        data = collector.get_data()
        if data['update_count'] > 0:
            logger.info(f"Successfully collected data ({data['update_count']} updates so far)")
            break
        time.sleep(1)
    else:
        logger.error("Could not get data from VPP, exiting")
        collector.stop()
        sys.exit(1)
    
    # Create and start SNMP agent
    logger.info(f"Starting SNMP agent on {args.address}")
    try:
        agent = SNMPAgent(
            collector=collector,
            config=config,
            server_address=args.address,
            period=args.period
        )
        agent.run()
    except Exception as e:
        logger.error(f"SNMP agent error: {e}", exc_info=True)
        collector.stop()
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()
