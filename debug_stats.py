#!/usr/bin/env python3
"""
Debug script to identify available stats paths in VPP 25.06
Run this to see what stats are available and help diagnose the issue
"""

from vppstats import VPPStats
import sys

def debug_stats():
    try:
        print("🔍 Connecting to VPP Stats Segment...")
        stats = VPPStats(socketname="/run/vpp/stats.sock", timeout=5)
        stats.connect()
        print("✅ Connected successfully\n")
        
        # Get interface names
        print("=" * 80)
        print("INTERFACE NAMES")
        print("=" * 80)
        try:
            iface_names = list(stats["/if/names"])
            print(f"Found {len(iface_names)} interfaces:")
            for i, ifname in enumerate(iface_names):
                print(f"  [{i}] {ifname}")
        except Exception as e:
            print(f"❌ Error reading /if/names: {e}")
        
        # Get all paths with /if/ in them
        print("\n" + "=" * 80)
        print("AVAILABLE STATS PATHS (interface-related)")
        print("=" * 80)
        
        if_paths = {}
        for path in sorted(stats.directory.keys()):
            if '/if' in path or '/rx' in path or '/tx' in path or '/drop' in path:
                if_paths[path] = True
        
        print(f"Found {len(if_paths)} interface-related stat paths:\n")
        for path in sorted(if_paths.keys()):
            print(f"  ✓ {path}")
        
        # Try to access each path for the first interface
        if len(iface_names) > 0:
            print("\n" + "=" * 80)
            print(f"TESTING STATS ACCESS FOR FIRST INTERFACE (index 0)")
            print("=" * 80)
            print(f"Interface: {iface_names[0]}\n")
            
            test_paths = [
                "/if/names",
                "/if/rx",
                "/if/tx",
                "/if/rx-error",
                "/if/tx-error",
                "/if/drops",
                "/if/rx-no-buf",
                "/if/rx-multicast",
                "/if/rx-broadcast",
                "/if/tx-multicast",
                "/if/tx-broadcast",
                "/if/punts",
            ]
            
            for path in test_paths:
                try:
                    if path == "/if/names":
                        val = stats[path]
                        print(f"  ✓ {path}: {val}")
                    else:
                        val = stats[path][:, 0]
                        # Try different aggregation methods
                        methods = []
                        try:
                            methods.append(f"sum()={val.sum()}")
                        except:
                            pass
                        try:
                            methods.append(f"sum_packets()={val.sum_packets()}")
                        except:
                            pass
                        try:
                            methods.append(f"sum_octets()={val.sum_octets()}")
                        except:
                            pass
                        
                        method_str = ", ".join(methods) if methods else "N/A"
                        print(f"  ✓ {path}: [{method_str}]")
                except KeyError:
                    print(f"  ✗ {path}: NOT FOUND (optional)")
                except Exception as e:
                    print(f"  ✗ {path}: ERROR - {e}")
        
        # Print all available paths (for reference)
        print("\n" + "=" * 80)
        print("ALL AVAILABLE STATS PATHS IN VPP")
        print("=" * 80)
        print(f"Total paths: {len(stats.directory)}\n")
        
        for path in sorted(stats.directory.keys()):
            print(f"  {path}")
        
        stats.disconnect()
        print("\n✅ Debug complete")
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(debug_stats())
