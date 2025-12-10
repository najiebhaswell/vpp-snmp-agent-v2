#!/bin/bash
# VPP SNMP Agent Testing Script
# Version 2.2.0
# Tests SNMP agent installation and functionality

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Defaults
SNMP_HOST="${1:-localhost}"
SNMP_PORT="${2:-705}"
SNMP_COMMUNITY="public"
AGENT_PID=""

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     VPP SNMP Agent V2 - Testing Script                        ║${NC}"
echo -e "${BLUE}║     Version 2.2.0 (VPP 25.06)                                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Test functions
test_prerequisites() {
    echo -e "${BLUE}[1/6] Checking prerequisites...${NC}"
    
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}✗ Python3 not found${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ Python3 found${NC}"
    
    if ! command -v snmpget &> /dev/null; then
        echo -e "${RED}✗ SNMP tools not found. Install with: sudo apt install snmp${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ SNMP tools found${NC}"
    
    if ! command -v vppctl &> /dev/null; then
        echo -e "${YELLOW}⚠ VPP not found or not in PATH${NC}"
        echo "  Note: VPP may be running via Docker or in separate location"
    else
        echo -e "${GREEN}✓ VPP found${NC}"
    fi
    
    return 0
}

test_vpp_connectivity() {
    echo -e "${BLUE}[2/6] Testing VPP connectivity...${NC}"
    
    if ! python3 << 'EOF' 2>/dev/null
import sys
from vppstats import VPPStats
try:
    stats = VPPStats()
    stats.connect()
    print(f"VPP version: {stats.version}")
    print(f"Available interfaces: {len(stats['/if/names'])}")
    stats.disconnect()
    sys.exit(0)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
EOF
    then
        echo -e "${RED}✗ Could not connect to VPP stats segment${NC}"
        echo "  Make sure VPP is running and /run/vpp/stats.sock exists"
        return 1
    fi
    echo -e "${GREEN}✓ VPP connectivity verified${NC}"
    
    return 0
}

test_snmpd_config() {
    echo -e "${BLUE}[3/6] Checking SNMP daemon configuration...${NC}"
    
    if [ ! -f /etc/snmp/snmpd.conf ]; then
        echo -e "${RED}✗ /etc/snmp/snmpd.conf not found${NC}"
        return 1
    fi
    
    if ! grep -q "agentXSocket" /etc/snmp/snmpd.conf; then
        echo -e "${RED}✗ AgentX not configured in snmpd.conf${NC}"
        echo "  Add 'agentXSocket tcp:localhost:705' to /etc/snmp/snmpd.conf"
        return 1
    fi
    echo -e "${GREEN}✓ SNMP AgentX configured${NC}"
    
    if ! systemctl is-active --quiet snmpd; then
        echo -e "${YELLOW}⚠ SNMP daemon not running. Starting...${NC}"
        sudo systemctl start snmpd
    fi
    echo -e "${GREEN}✓ SNMP daemon running${NC}"
    
    return 0
}

test_agent_startup() {
    echo -e "${BLUE}[4/6] Starting VPP SNMP Agent...${NC}"
    
    # Start agent in background
    python3 snmp_agent_integrated.py -p 5 -d > /tmp/vpp-snmp-agent-test.log 2>&1 &
    AGENT_PID=$!
    
    echo "  Agent PID: $AGENT_PID"
    
    # Wait for agent to initialize
    sleep 3
    
    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo -e "${RED}✗ Agent failed to start${NC}"
        echo "  Check logs: tail -50 /tmp/vpp-snmp-agent-test.log"
        return 1
    fi
    echo -e "${GREEN}✓ Agent started successfully (PID $AGENT_PID)${NC}"
    
    return 0
}

test_snmp_queries() {
    echo -e "${BLUE}[5/6] Testing SNMP queries...${NC}"
    
    # Test 1: Get interface name
    if ! snmpget -v2c -c $SNMP_COMMUNITY $SNMP_HOST:$SNMP_PORT 1.3.6.1.2.1.2.2.1.2.1000 &>/dev/null; then
        echo -e "${RED}✗ SNMP query failed${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ Basic SNMP query works${NC}"
    
    # Test 2: Check number of interfaces
    IFACE_COUNT=$(snmpwalk -v2c -c $SNMP_COMMUNITY $SNMP_HOST:$SNMP_PORT 1.3.6.1.2.1.2.2.1.2 2>/dev/null | wc -l)
    echo -e "${GREEN}✓ Found $IFACE_COUNT interfaces in SNMP${NC}"
    
    # Test 3: Check bonding speed (if bond exists)
    BOND_SPEED=$(snmpget -v2c -c $SNMP_COMMUNITY $SNMP_HOST:$SNMP_PORT 1.3.6.1.2.1.2.2.1.5.1003 2>/dev/null | tail -1)
    if [ ! -z "$BOND_SPEED" ]; then
        echo -e "${GREEN}✓ Bonding interface speed query: $BOND_SPEED${NC}"
    fi
    
    return 0
}

test_data_stability() {
    echo -e "${BLUE}[6/6] Testing data stability...${NC}"
    
    # Get initial stats
    STAT1=$(snmpget -v2c -c $SNMP_COMMUNITY $SNMP_HOST:$SNMP_PORT 1.3.6.1.2.1.2.2.1.10.1001 2>/dev/null | tail -1)
    
    # Wait
    sleep 3
    
    # Get second reading
    STAT2=$(snmpget -v2c -c $SNMP_COMMUNITY $SNMP_HOST:$SNMP_PORT 1.3.6.1.2.1.2.2.1.10.1001 2>/dev/null | tail -1)
    
    if [ "$STAT1" != "$STAT2" ]; then
        echo -e "${GREEN}✓ Data is updating regularly${NC}"
    else
        echo -e "${YELLOW}⚠ Data may be stale (need more traffic or longer wait)${NC}"
    fi
    
    return 0
}

cleanup() {
    echo -e "${BLUE}Cleaning up...${NC}"
    if [ ! -z "$AGENT_PID" ]; then
        kill $AGENT_PID 2>/dev/null || true
    fi
}

# Run tests
trap cleanup EXIT

test_prerequisites || exit 1
test_vpp_connectivity || exit 1
test_snmpd_config || exit 1
test_agent_startup || exit 1
test_snmp_queries || exit 1
test_data_stability || exit 1

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          ✓ All tests passed successfully!                      ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps:"
echo "  1. Install agent: sudo dpkg -i vpp-snmp-agent-v2_2.2.0_all.deb"
echo "  2. Start service: sudo systemctl start vpp-snmp-agent"
echo "  3. Enable on boot: sudo systemctl enable vpp-snmp-agent"
echo "  4. Integrate with monitoring (Grafana, Prometheus, etc.)"
echo ""
