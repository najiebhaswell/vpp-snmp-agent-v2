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


def get_interface_speed(ifname, ifaces, logger=None):
    """
    Get interface speed with special handling for bonding interfaces.
    For bonding interfaces that have no direct link_speed, 
    derive speed from member interfaces.
    Returns speed in Kbps (as stored in VPP)
    """
    if ifname.startswith("loop") or ifname.startswith("tap"):
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
    if ifname.startswith("bond"):
        # Get all member interface speeds
        member_speeds = []
        for other_ifname, other_iface in ifaces.items():
            # Check if this interface is a member of the bond
            # VPP bonding members typically have the bond as their parent
            # We look for interfaces where bond is in the name or by checking hierarchy
            try:
                # Try to get member info from VPP data structures
                if hasattr(other_iface, 'bond_interface') and other_iface.bond_interface == iface.sw_if_index:
                    if other_iface.link_speed > 0:
                        member_speeds.append(other_iface.link_speed)
                # Alternative: check if interface name starts with bond prefix
                elif other_ifname.startswith(ifname + ".") or other_ifname.startswith(ifname + "-"):
                    if other_iface.link_speed > 0:
                        member_speeds.append(other_iface.link_speed)
            except:
                pass
        
        # If we found member speeds, use the first one (all members should be same)
        if member_speeds:
            if logger:
                logger.debug(f"Bonding interface {ifname} member speeds: {member_speeds}")
            return member_speeds[0]
        
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
    
    def _collect_data(self):
        """Collect data from VPP"""
        interfaces = self.vpp_api.get_ifaces()
        lcps = self.vpp_api.get_lcp()
        
        # Get stats from shared memory
        iface_stats = {}
        iface_names = list(self.vpp_stats["/if/names"])
        
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
                self.logger.debug(f"Could not get stats for {ifname}: {e}")
        
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
            
            # Build MIB data for each interface
            for i, ifname in enumerate(data['iface_names']):
                idx = 1000 + i  # Interface index in SNMP
                stats = data['iface_stats'].get(ifname, {})
                interfaces = data['interfaces']
                
                # Get interface metadata
                iface = interfaces.get(ifname)
                mtu = iface.mtu[0] if iface else 0
                admin_status = 1 if (iface and int(iface.flags) & 1) else 2
                oper_status = 1 if (iface and int(iface.flags) & 2) else 2
                mac = str(iface.l2_address) if iface else "00:00:00:00:00:00"
                
                # Speed in bps (VPP reports link_speed in Kbps)
                if ifname.startswith("loop") or ifname.startswith("tap"):
                    speed = 1000000000  # Default 1 Gbps for loopback/tap
                elif iface and iface.link_speed > 0:
                    speed = iface.link_speed * 1000  # Convert Kbps to bps
                else:
                    speed = 0
                
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
