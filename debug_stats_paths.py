#!/usr/bin/env python3
"""
Debug script to list all available stats paths in VPP
Helps identify which paths are available in current VPP version
"""

import sys
import logging
from vppstats import VPPStats

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def main():
    print("\n" + "="*80)
    print("VPP Stats Paths Debug - Checking available paths")
    print("="*80 + "\n")
    
    try:
        stats = VPPStats(socketname="/run/vpp/stats.sock", timeout=5)
        print(f"[*] Connecting to VPP stats segment...")
        stats.connect()
        print(f"[✓] Connected successfully")
        print(f"[*] Stats version: {stats.version}")
        print(f"[*] Epoch: {stats.epoch}")
        
        # Get interface names
        try:
            iface_names = list(stats["/if/names"])
            print(f"[✓] Found {len(iface_names)} interfaces: {iface_names}\n")
        except Exception as e:
            print(f"[✗] Error getting /if/names: {e}\n")
            iface_names = []
        
        # List all available paths
        print("[*] All available stats paths:")
        print("-" * 80)
        
        all_paths = sorted(stats.directory.keys())
        
        # Group by prefix
        interface_paths = []
        other_paths = []
        
        for path in all_paths:
            if '/if' in path:
                interface_paths.append(path)
            else:
                other_paths.append(path)
        
        # Print interface stats
        if interface_paths:
            print("\n[INTERFACE STATS PATHS]")
            for path in interface_paths:
                print(f"  {path}")
        
        if other_paths:
            print("\n[OTHER STATS PATHS]")
            for path in other_paths[:30]:  # Limit to first 30
                print(f"  {path}")
            if len(other_paths) > 30:
                print(f"  ... and {len(other_paths) - 30} more paths")
        
        # Try to access each interface stat path
        if iface_names:
            print("\n" + "="*80)
            print("Testing access to common stats paths for interface 0")
            print("="*80 + "\n")
            
            common_stats = [
                "/if/names",
                "/if/rx",
                "/if/tx",
                "/if/rx-error",
                "/if/tx-error",
                "/if/rx-no-buf",
                "/if/drops",
                "/if/punts",
                "/if/rx-multicast",
                "/if/rx-broadcast",
                "/if/tx-multicast",
                "/if/tx-broadcast",
                "/if/rx-miss",
                "/if/ip4",
                "/if/ip6",
            ]
            
            test_results = {
                'available': [],
                'missing': [],
                'error': []
            }
            
            for stat_path in common_stats:
                try:
                    if stat_path == "/if/names":
                        # Special case for names (not indexed)
                        data = stats[stat_path]
                        test_results['available'].append(f"{stat_path} ✓")
                    else:
                        # Test with interface 0
                        if len(iface_names) > 0:
                            data = stats[stat_path][:, 0]
                            test_results['available'].append(f"{stat_path} ✓")
                        else:
                            test_results['missing'].append(f"{stat_path} (no interfaces)")
                except KeyError:
                    test_results['missing'].append(f"{stat_path} ✗ (not found)")
                except Exception as e:
                    test_results['error'].append(f"{stat_path} ✗ (error: {str(e)[:50]})")
            
            print("[✓] AVAILABLE PATHS:")
            for item in test_results['available']:
                print(f"  {item}")
            
            if test_results['missing']:
                print("\n[✗] MISSING PATHS:")
                for item in test_results['missing']:
                    print(f"  {item}")
            
            if test_results['error']:
                print("\n[⚠] ERROR ACCESSING PATHS:")
                for item in test_results['error']:
                    print(f"  {item}")
        
        stats.disconnect()
        print("\n" + "="*80)
        print("[✓] Successfully disconnected from stats segment")
        print("="*80 + "\n")
        
    except Exception as e:
        print(f"\n[✗] ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
