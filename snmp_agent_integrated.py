#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPP SNMP Agent - Integrated version with VPPDataCollector
Uses improved async polling with AgentX protocol
"""

import argparse
import logging
import threading
import time
import yaml
import signal
import sys
from datetime import datetime

try:
    from vppstats import VPPStats
    from vppapi import VPPApi
    import agentx
except ImportError as e:
    print(f"ERROR: Import failed: {e}")
    sys.exit(1)


def get_interface_speed(ifname, ifaces, bond_members_map=None, logger=None):
    """
    Get interface speed with special handling for bonding interfaces.
    For bonding interfaces, speed = SUM of active member speeds.
    Returns speed in Kbps (as stored in VPP)
    
    Args:
        ifname: Interface name
        ifaces: Dictionary of interfaces from VPP API
        bond_members_map: Dict mapping bond sw_if_index to list of member sw_if_indices
        logger: Logger instance
    """
    if ifname.startswith("loop") or ifname.startswith("tap"):
        return 1000000  # 1 Gbps in Kbps for loopback/tap
    
    if ifname not in ifaces:
        if logger:
            logger.warning("Could not get link speed for interface %s", ifname)
        return 0
    
    iface = ifaces[ifname]
    
    # If interface has direct link_speed, use it (for regular interfaces)
    if iface.link_speed > 0:
        return iface.link_speed
    
    # For bonding interfaces (BondEthernet0, BondEthernet1, etc.) with zero speed,
    # derive speed as SUM of active member interfaces
    if iface.interface_dev_type == "bond" and iface.sw_if_index == iface.sup_sw_if_index:
        total_speed = 0
        
        # Use pre-computed bond members map if available
        if bond_members_map and iface.sw_if_index in bond_members_map:
            member_speeds = []
            for member_sw_if_index in bond_members_map[iface.sw_if_index]:
                # Find interface by sw_if_index
                for other_ifname, other_iface in ifaces.items():
                    if other_iface.sw_if_index == member_sw_if_index:
                        if other_iface.link_speed > 0:
                            member_speeds.append(other_iface.link_speed)
                        break
            
            if member_speeds:
                total_speed = sum(member_speeds)
                if logger:
                    logger.debug(f"Bond {ifname}: {len(member_speeds)} active members, "
                                f"total speed={total_speed/1000000:.0f} Gbps "
                                f"(members: {[f'{s/1000000:.0f}Gbps' for s in member_speeds]})")
                return total_speed
        
        # Fallback: try to calculate from all members if bond_members_map not available
        # This is less efficient but ensures it works
        try:
            member_speeds = []
            for other_ifname, other_iface in ifaces.items():
                if other_iface.interface_dev_type != "bond" and other_iface.sup_sw_if_index == iface.sw_if_index:
                    if other_iface.link_speed > 0:
                        member_speeds.append(other_iface.link_speed)
            
            if member_speeds:
                total_speed = sum(member_speeds)
                if logger:
                    logger.debug(f"Bond {ifname}: calculated from members, "
                                f"total speed={total_speed/1000000:.0f} Gbps")
                return total_speed
        except Exception as e:
            if logger:
                logger.debug(f"Could not calculate bond speed from members: {e}")
        
        # If still no speed found, check flags for admin/link status
        # If up, default to 1 Gbps per active member
        try:
            if iface.flags & 1:  # Admin up
                if logger:
                    logger.warning(f"Bond {ifname} is admin up but has no member speed info, defaulting to 1 Gbps")
                return 1000000  # Default 1 Gbps in Kbps
        except:
            pass
        
        if logger:
            logger.warning(f"Bond {ifname} has no member speeds, defaulting to 0 Kbps")
        return 0
    
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
            'iface_names': [],
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
        self._disconnect_vpp()
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
                    time.sleep(2)
                else:
                    time.sleep(0.5)
                continue
            
            # Sleep until next poll
            time.sleep(self.poll_interval)
    
    def _connect_vpp(self):
        """Connect to VPP API"""
        self.logger.debug("Connecting to VPP API...")
        self.vpp_api = VPPApi(clientname="snmp-agent-v2-integrated")
        if not self.vpp_api.connect():
            raise Exception("Failed to connect to VPP API")
        self.logger.info("Connected to VPP API")
    
    def _connect_stats(self):
        """Connect to VPP Stats segment"""
        self.logger.debug("Connecting to VPP Stats...")
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
    
    def _safe_get_stat(self, path, index, method='sum', default=0):
        """
        Safely get a stat, return default if path doesn't exist
        VPP 25.06 compatibility helper
        
        Args:
            path: Stats path (e.g., "/if/rx-error")
            index: Interface index
            method: Aggregation method ('sum', 'sum_packets', 'sum_octets')
            default: Default value if stat doesn't exist
        
        Returns:
            Stat value or default
        """
        try:
            if path not in self.vpp_stats.directory:
                self.logger.debug(f"Stats path {path} not available, using default {default}")
                return default
            
            stat = self.vpp_stats[path][:, index]
            
            if method == 'sum':
                return stat.sum()
            elif method == 'sum_packets':
                return stat.sum_packets()
            elif method == 'sum_octets':
                return stat.sum_octets()
            else:
                return stat.sum()
                
        except Exception as e:
            self.logger.debug(f"Error accessing {path} for interface {index}: {e}")
            return default
    
    def _get_bond_members_map(self, interfaces):
        """
        Build a map of bond interface sw_if_index to list of member sw_if_indices.
        Uses VPP sw_interface_bond_dump and sw_interface_slave_dump APIs.
        
        Returns:
            Dict mapping bond sw_if_index to list of member sw_if_indices
        """
        bond_members = {}
        try:
            # Try to get bond info via API
            if not hasattr(self.vpp_api.vpp, 'api'):
                return bond_members
            
            result = self.vpp_api.vpp.api.sw_interface_bond_dump()
            if result:
                for bond_info in result:
                    bond_sw_if_index = bond_info.sw_if_index
                    members = []
                    
                    try:
                        # Get slave members for this bond
                        slaves = self.vpp_api.vpp.api.sw_interface_slave_dump(sw_if_index=bond_sw_if_index)
                        if slaves:
                            for slave_info in slaves:
                                members.append(slave_info.sw_if_index)
                    except Exception as e:
                        self.logger.debug(f"Could not get slaves for bond {bond_sw_if_index}: {e}")
                    
                    bond_members[bond_sw_if_index] = members
                    self.logger.debug(f"Bond sw_if_index={bond_sw_if_index} has {len(members)} members")
        except Exception as e:
            self.logger.debug(f"Could not get bond members: {e}")
        
        return bond_members
    
    def _collect_data(self):
        """
        Collect data from VPP
        VPP 25.06 compatible - handles missing optional stats paths
        """
        interfaces = self.vpp_api.get_ifaces()
        lcps = self.vpp_api.get_lcp()
        
        # Get bond members map for speed calculation
        bond_members_map = self._get_bond_members_map(interfaces)
        
        # Get stats from shared memory
        iface_stats = {}
        iface_names = list(self.vpp_stats["/if/names"])
        
        for i, ifname in enumerate(iface_names):
            try:
                stats = {
                    'rx_packets': self._safe_get_stat("/if/rx", i, method='sum_packets', default=0),
                    'rx_octets': self._safe_get_stat("/if/rx", i, method='sum_octets', default=0),
                    'rx_errors': self._safe_get_stat("/if/rx-error", i, method='sum', default=0),
                    'rx_no_buf': self._safe_get_stat("/if/rx-no-buf", i, method='sum', default=0),
                    'rx_multicast': self._safe_get_stat("/if/rx-multicast", i, method='sum_packets', default=0),
                    'rx_broadcast': self._safe_get_stat("/if/rx-broadcast", i, method='sum_packets', default=0),
                    
                    'tx_packets': self._safe_get_stat("/if/tx", i, method='sum_packets', default=0),
                    'tx_octets': self._safe_get_stat("/if/tx", i, method='sum_octets', default=0),
                    'tx_errors': self._safe_get_stat("/if/tx-error", i, method='sum', default=0),
                    'tx_multicast': self._safe_get_stat("/if/tx-multicast", i, method='sum_packets', default=0),
                    'tx_broadcast': self._safe_get_stat("/if/tx-broadcast", i, method='sum_packets', default=0),
                    'drops': self._safe_get_stat("/if/drops", i, method='sum', default=0),
                    'punts': self._safe_get_stat("/if/punts", i, method='sum', default=0),
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
            self._data['iface_names'] = iface_names
            self._data['last_update'] = time.time()
            self._data['update_count'] += 1
    
    def get_data(self):
        """Get current data snapshot (thread-safe)"""
        with self._lock:
            return {
                'interfaces': dict(self._data['interfaces']),
                'iface_stats': dict(self._data['iface_stats']),
                'lcps': dict(self._data['lcps']),
                'iface_names': list(self._data['iface_names']),
                'last_update': self._data['last_update'],
                'error_count': self._data['error_count'],
                'update_count': self._data['update_count'],
            }


class SNMPAgentIntegrated(agentx.Agent):
    """SNMP Agent integrated with VPPDataCollector"""
    
    def setup(self):
        """Setup phase - called at initialization"""
        self.logger.info("Setting up SNMP Agent")
        
        # Load config
        self.config = None
        if self._args.config:
            try:
                with open(self._args.config, "r") as f:
                    self.logger.info(f"Loading config from {self._args.config}")
                    self.config = yaml.load(f, Loader=yaml.FullLoader)
            except Exception as e:
                self.logger.error(f"Could not load config: {e}")
        
        # Create data collector with config from args
        poll_period = getattr(self._args, 'period', 5)
        timeout = getattr(self._args, 'timeout', 5)
        self.collector = VPPDataCollector(poll_interval=poll_period, timeout=timeout)
        self.collector.start()
        
        # Wait for first data
        for i in range(30):
            data = self.collector.get_data()
            if data['update_count'] > 0:
                self.logger.info(f"Data collector ready with {len(data['iface_names'])} interfaces")
                break
            time.sleep(0.5)
        else:
            self.logger.error("Could not get data from VPP")
            return False
        
        # Register OID subtrees
        self.register("1.3.6.1.2.1.2.2.1")  # ifEntry
        self.register("1.3.6.1.2.1.31.1.1.1")  # ifXEntry
        
        self.logger.info("SNMP Agent setup complete")
        return True
    
    def update(self):
        """Update phase - called periodically to update MIB data"""
        try:
            data = self.collector.get_data()
            ds = agentx.DataSet()
            
            if not data['iface_stats']:
                self.logger.warning("No interface data available")
                return ds
            
            # Build bond members map for speed calculation
            interfaces = data['interfaces']
            bond_members_map = self.collector._get_bond_members_map(interfaces)
            
            # Build MIB data for each interface
            for i, ifname in enumerate(data['iface_names']):
                idx = 1000 + i  # Interface index in SNMP
                stats = data['iface_stats'].get(ifname, {})
                
                # Get interface metadata
                iface = interfaces.get(ifname)
                mtu = iface.mtu[0] if iface else 0
                admin_status = 1 if (iface and int(iface.flags) & 1) else 2
                oper_status = 1 if (iface and int(iface.flags) & 2) else 2
                mac = str(iface.l2_address) if iface else "00:00:00:00:00:00"
                
                # Speed in bps (VPP reports link_speed in Kbps)
                # Use improved get_interface_speed for bonding support
                speed_kbps = get_interface_speed(ifname, interfaces, bond_members_map, self.logger)
                speed = speed_kbps * 1000  # Convert Kbps to bps
                
                # For OID 1.3.6.1.2.1.2.2.1.5 (32-bit ifSpeed), cap at 4.29 Gbps
                # For speeds > 4.29 Gbps, use 0 to indicate use HC counter
                speed_32 = 0 if speed >= 2 ** 32 else speed
                
                # Interface Name (mib-2.ifTable.ifEntry.ifName)
                ds.set(f"1.3.6.1.2.1.2.2.1.1.{idx}", "int", idx)
                ds.set(f"1.3.6.1.2.1.2.2.1.2.{idx}", "str", ifname)
                
                # Interface type
                if ifname.startswith("loop"):
                    ds.set(f"1.3.6.1.2.1.2.2.1.3.{idx}", "int", 24)  # softwareLoopback
                else:
                    ds.set(f"1.3.6.1.2.1.2.2.1.3.{idx}", "int", 6)  # ethernet-csmacd
                
                ds.set(f"1.3.6.1.2.1.2.2.1.4.{idx}", "int", mtu)
                ds.set(f"1.3.6.1.2.1.2.2.1.5.{idx}", "gauge32", speed_32)
                ds.set(f"1.3.6.1.2.1.2.2.1.6.{idx}", "str", mac)
                ds.set(f"1.3.6.1.2.1.2.2.1.7.{idx}", "int", admin_status)
                ds.set(f"1.3.6.1.2.1.2.2.1.8.{idx}", "int", oper_status)
                ds.set(f"1.3.6.1.2.1.2.2.1.9.{idx}", "ticks", 0)
                
                # RX stats (32-bit)
                ds.set(f"1.3.6.1.2.1.2.2.1.10.{idx}", "u32", stats.get('rx_octets', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.11.{idx}", "u32", stats.get('rx_packets', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.12.{idx}", "u32", stats.get('rx_multicast', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.13.{idx}", "u32", stats.get('rx_no_buf', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.14.{idx}", "u32", stats.get('rx_errors', 0) % 2**32)
                
                # TX stats (32-bit)
                ds.set(f"1.3.6.1.2.1.2.2.1.16.{idx}", "u32", stats.get('tx_octets', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.17.{idx}", "u32", stats.get('tx_packets', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.18.{idx}", "u32", stats.get('tx_multicast', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.19.{idx}", "u32", stats.get('drops', 0) % 2**32)
                ds.set(f"1.3.6.1.2.1.2.2.1.20.{idx}", "u32", stats.get('tx_errors', 0) % 2**32)
                
                # ifX table (64-bit counters)
                ds.set(f"1.3.6.1.2.1.31.1.1.1.1.{idx}", "str", ifname)
                ds.set(f"1.3.6.1.2.1.31.1.1.1.6.{idx}", "u64", stats.get('rx_octets', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.7.{idx}", "u64", stats.get('rx_packets', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.8.{idx}", "u64", stats.get('rx_multicast', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.9.{idx}", "u64", stats.get('rx_broadcast', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.10.{idx}", "u64", stats.get('tx_octets', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.11.{idx}", "u64", stats.get('tx_packets', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.12.{idx}", "u64", stats.get('tx_multicast', 0))
                ds.set(f"1.3.6.1.2.1.31.1.1.1.13.{idx}", "u64", stats.get('tx_broadcast', 0))
                # HC Speed counter (OID 1.3.6.1.2.1.31.1.1.1.15) - ifHighSpeed in Mbps as 64-bit
                ds.set(f"1.3.6.1.2.1.31.1.1.1.15.{idx}", "u64", int(speed / 1000000))
            
            return ds
        
        except Exception as e:
            self.logger.error(f"Error in update: {e}")
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
        description="VPP SNMP Agent - Integrated with Real-time Data Collector",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-a", "--address",
        type=str,
        default="localhost:705",
        help="SNMP AgentX address (default: localhost:705)"
    )
    parser.add_argument(
        "-p", "--period",
        type=int,
        default=5,
        help="Data polling period in seconds (default: 5)"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=5,
        help="VPP API timeout in seconds (default: 5)"
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
    
    args = parser.parse_args()
    setup_logging(args.debug)
    
    logger = logging.getLogger("main")
    
    # Handle signals
    def signal_handler(sig, frame):
        logger.info("Received signal, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logger.info(f"Starting SNMP Agent on {args.address}")
        agent = SNMPAgentIntegrated(
            server_address=args.address,
            period=args.period,
            args=args
        )
        agent.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
