#!/usr/bin/env python3
"""
VPP 25.06 Compatibility Patch for SNMP Agent
This patch adds:
1. Safe stats access with fallback for missing paths
2. Better error handling and logging
3. Support for new stats paths in VPP 25.06
"""

import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("vpp-25.06-patch")

class VPP2506Patch:
    """
    Patch for VPP 25.06 compatibility
    
    Changes:
    1. Add safe stats access helper
    2. Handle missing /if/punts gracefully
    3. Improve error messages
    4. Support new stats counters
    """
    
    # Stats paths that are REQUIRED in all VPP versions
    REQUIRED_STATS = [
        "/if/names",
        "/if/rx",
        "/if/tx",
    ]
    
    # Stats paths that are OPTIONAL (may not exist in all versions)
    OPTIONAL_STATS = [
        "/if/rx-error",
        "/if/tx-error",
        "/if/drops",
        "/if/rx-no-buf",
        "/if/rx-multicast",
        "/if/rx-broadcast",
        "/if/tx-multicast",
        "/if/tx-broadcast",
        "/if/punts",  # New in some versions
    ]
    
    @staticmethod
    def validate_stats(stats_obj):
        """Validate that required stats paths exist"""
        missing_required = []
        for path in VPP2506Patch.REQUIRED_STATS:
            if path not in stats_obj.directory:
                missing_required.append(path)
        
        if missing_required:
            raise Exception(f"Missing required stats: {missing_required}")
        
        # Log which optional stats are available
        available_optional = []
        unavailable_optional = []
        for path in VPP2506Patch.OPTIONAL_STATS:
            if path in stats_obj.directory:
                available_optional.append(path)
            else:
                unavailable_optional.append(path)
        
        logger.info(f"Available optional stats: {available_optional}")
        if unavailable_optional:
            logger.warning(f"Unavailable optional stats: {unavailable_optional}")
    
    @staticmethod
    def safe_get_stat(stats_obj, path, index, method='sum', default=0):
        """
        Safely get a stat, return default if path doesn't exist
        
        Args:
            stats_obj: VPPStats instance
            path: Stats path (e.g., "/if/rx-error")
            index: Interface index
            method: Aggregation method ('sum', 'sum_packets', 'sum_octets')
            default: Default value if stat doesn't exist
        
        Returns:
            Stat value or default
        """
        try:
            if path not in stats_obj.directory:
                logger.debug(f"Stats path {path} not available, using default {default}")
                return default
            
            stat = stats_obj[path][:, index]
            
            if method == 'sum':
                return stat.sum()
            elif method == 'sum_packets':
                return stat.sum_packets()
            elif method == 'sum_octets':
                return stat.sum_octets()
            else:
                return stat.sum()
                
        except Exception as e:
            logger.debug(f"Error accessing {path} for interface {index}: {e}")
            return default
    
    @staticmethod
    def get_interface_stats(stats_obj, iface_name, index):
        """
        Get complete stats for one interface with VPP 25.06 compatibility
        
        Returns dict with all available stats
        """
        stats = {}
        
        try:
            # RX stats
            stats['rx_packets'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx", index, method='sum_packets', default=0
            )
            stats['rx_octets'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx", index, method='sum_octets', default=0
            )
            stats['rx_errors'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx-error", index, method='sum', default=0
            )
            stats['rx_no_buf'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx-no-buf", index, method='sum', default=0
            )
            stats['rx_multicast'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx-multicast", index, method='sum_packets', default=0
            )
            stats['rx_broadcast'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/rx-broadcast", index, method='sum_packets', default=0
            )
            
            # TX stats
            stats['tx_packets'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/tx", index, method='sum_packets', default=0
            )
            stats['tx_octets'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/tx", index, method='sum_octets', default=0
            )
            stats['tx_errors'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/tx-error", index, method='sum', default=0
            )
            stats['tx_multicast'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/tx-multicast", index, method='sum_packets', default=0
            )
            stats['tx_broadcast'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/tx-broadcast", index, method='sum_packets', default=0
            )
            
            # Other stats
            stats['drops'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/drops", index, method='sum', default=0
            )
            stats['punts'] = VPP2506Patch.safe_get_stat(
                stats_obj, "/if/punts", index, method='sum', default=0
            )
            
        except Exception as e:
            logger.error(f"Error getting stats for {iface_name}: {e}")
            raise
        
        return stats

def main():
    """Test the patch"""
    try:
        from vppstats import VPPStats
        
        logger.info("Testing VPP 25.06 compatibility patch...")
        
        # Connect to VPP stats
        stats = VPPStats(socketname="/run/vpp/stats.sock", timeout=5)
        stats.connect()
        
        # Validate stats
        logger.info("Validating stats paths...")
        VPP2506Patch.validate_stats(stats)
        
        # Get interface names
        iface_names = list(stats["/if/names"])
        logger.info(f"Found {len(iface_names)} interfaces: {iface_names}")
        
        # Test getting stats for each interface
        logger.info("Testing stats collection for each interface...")
        for i, ifname in enumerate(iface_names):
            try:
                iface_stats = VPP2506Patch.get_interface_stats(stats, ifname, i)
                logger.info(f"  [{i}] {ifname}: rx_pkts={iface_stats['rx_packets']}, tx_pkts={iface_stats['tx_packets']}")
            except Exception as e:
                logger.error(f"  [{i}] {ifname}: ERROR - {e}")
        
        stats.disconnect()
        logger.info("✅ VPP 25.06 patch validation successful!")
        return 0
        
    except Exception as e:
        logger.error(f"❌ Patch validation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
