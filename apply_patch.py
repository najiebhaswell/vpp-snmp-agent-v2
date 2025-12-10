#!/usr/bin/env python3
"""
Apply VPP 25.06 Compatibility Patch to vpp-snmp-agent.py (original version)

This script patches the original vpp-snmp-agent.py to make it compatible
with VPP 25.06 while maintaining backward compatibility.

Usage:
    python3 apply_patch.py <target_file>
    python3 apply_patch.py vpp-snmp-agent.py
"""

import sys
import re
import os
from pathlib import Path

def read_file(filepath):
    """Read file content"""
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    """Write file content"""
    with open(filepath, 'w') as f:
        f.write(content)

def apply_patch(target_file):
    """
    Apply VPP 25.06 compatibility patch to target file
    """
    
    # Read target file
    content = read_file(target_file)
    
    # Check if already patched
    if "_safe_get_stat" in content:
        print("⚠️  File already patched (contains _safe_get_stat)")
        return False
    
    # Create backup
    backup_file = f"{target_file}.backup"
    if not os.path.exists(backup_file):
        write_file(backup_file, content)
        print(f"✅ Backup created: {backup_file}")
    
    # Patch 1: Add safe_get_stat method to MyAgent class
    # Find the class definition and add the method after setup()
    
    safe_get_stat_method = '''
    def _safe_get_stat(self, stats_obj, path, index, method='sum', default=0):
        """
        Safely get a stat, return default if path doesn't exist
        VPP 25.06 compatibility helper
        
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
                self.logger.debug(f"Stats path {path} not available, using default {default}")
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
            self.logger.debug(f"Error accessing {path}: {e}")
            return default
'''
    
    # Find insertion point (after "def setup(self):" method)
    insertion_pattern = r'(    def setup\(self\):.*?return\s+(?:True|False))'
    match = re.search(insertion_pattern, content, re.DOTALL)
    
    if not match:
        print("❌ Could not find insertion point in file")
        return False
    
    insertion_point = match.end()
    
    # Insert the new method
    content = (
        content[:insertion_point] + 
        safe_get_stat_method + 
        "\n" +
        content[insertion_point:]
    )
    
    print("✅ Added _safe_get_stat() method")
    
    # Patch 2: Replace hardcoded stats access in update() method
    # Pattern to replace in update() method
    
    replacements = [
        (
            r'self\.vppstat\["/if/rx-error"\]\[:, i\]\.sum\(\)',
            'self._safe_get_stat(self.vppstat, "/if/rx-error", i, method="sum", default=0)'
        ),
        (
            r'self\.vppstat\["/if/tx-error"\]\[:, i\]\.sum\(\)',
            'self._safe_get_stat(self.vppstat, "/if/tx-error", i, method="sum", default=0)'
        ),
        (
            r'self\.vppstat\["/if/rx-no-buf"\]\[:, i\]\.sum\(\)',
            'self._safe_get_stat(self.vppstat, "/if/rx-no-buf", i, method="sum", default=0)'
        ),
    ]
    
    for pattern, replacement in replacements:
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
            print(f"✅ Patched: {pattern[:50]}...")
        else:
            print(f"⚠️  Pattern not found: {pattern[:50]}...")
    
    # Write patched file
    write_file(target_file, content)
    print(f"✅ File patched: {target_file}")
    
    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 apply_patch.py <target_file>")
        print("Example: python3 apply_patch.py vpp-snmp-agent.py")
        sys.exit(1)
    
    target_file = sys.argv[1]
    
    if not os.path.exists(target_file):
        print(f"❌ File not found: {target_file}")
        sys.exit(1)
    
    print(f"Applying VPP 25.06 compatibility patch to {target_file}...")
    
    if apply_patch(target_file):
        print("\n✅ Patch applied successfully!")
        print(f"\nNext steps:")
        print(f"1. Review changes in {target_file}")
        print(f"2. Test: python3 {target_file} -d")
        print(f"3. If issues, restore backup: cp {target_file}.backup {target_file}")
        sys.exit(0)
    else:
        print("\n❌ Patch failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
