# VPP SNMP Agent V2 - Installation & Deployment Guide

## Quick Summary

✅ **Package Created:** `vpp-snmp-agent-v2_2.0.0_all.deb` (44 KB)  
✅ **Status:** Ready for production deployment  
✅ **New Feature:** Bonding interface speed support  
✅ **Tests:** All passed successfully  

---

## Installation

### 1. Install from Debian Package

```bash
# From the source directory
sudo dpkg -i vpp-snmp-agent-v2_2.0.0_all.deb

# Or from parent directory
sudo dpkg -i ~/vpp-snmp-agent-v2_2.0.0_all.deb
```

### 2. Verify Installation

```bash
# Check if package is installed
dpkg -l | grep vpp-snmp-agent-v2

# Check installed files
dpkg -L vpp-snmp-agent-v2 | head -20

# Verify main executable
which snmp-agent-v2
ls -la /usr/bin/snmp-agent-v2
```

---

## Configuration

### Default Configuration Location
```
/etc/vpp-snmp-agent/vpp-snmp-agent-config.yaml
```

### Edit Configuration (Optional)
```bash
sudo nano /etc/vpp-snmp-agent/vpp-snmp-agent-config.yaml
```

### Key Configuration Options
- **poll_interval**: Data collection interval in seconds (default: 5)
- **snmp_timeout**: SNMP response timeout in seconds (default: 5)
- **vpp_socket**: VPP API socket path (default: /run/vpp/api.sock)

---

## Starting the Service

### Using Systemd

```bash
# Start the service
sudo systemctl start vpp-snmp-agent

# Check status
sudo systemctl status vpp-snmp-agent

# Enable auto-start on boot
sudo systemctl enable vpp-snmp-agent

# Stop the service
sudo systemctl stop vpp-snmp-agent

# Restart the service
sudo systemctl restart vpp-snmp-agent

# View logs
sudo journalctl -u vpp-snmp-agent -f
```

### Manual Start (for testing)

```bash
# Run in foreground with debug output
sudo snmp-agent-v2 -d

# Run with specific polling period
sudo snmp-agent-v2 -p 10

# Run with custom SNMP address
sudo snmp-agent-v2 -a localhost:705
```

### Command-line Options

```
Usage: snmp-agent-v2 [OPTIONS]

Options:
  -p, --period INT         Polling period in seconds (default: 5)
  -t, --timeout INT        VPP API timeout in seconds (default: 5)
  -a, --address ADDR       SNMP agent socket address (default: localhost:705)
  -c, --config FILE        Configuration YAML file
  -d, --debug              Enable debug logging
  -dd, --debug-agent       Enable AgentX debug logging
  -h, --help               Show this help message
```

---

## Verification & Testing

### 1. Check Service Status

```bash
sudo systemctl status vpp-snmp-agent
```

Expected output:
```
● vpp-snmp-agent.service - VPP SNMP Agent
     Loaded: loaded (/lib/systemd/system/vpp-snmp-agent.service; enabled)
     Active: active (running) since ...
```

### 2. Test SNMP Connectivity

```bash
# Using snmpwalk (requires snmp package)
snmpwalk -c public -v 2c localhost 1.3.6.1.2.1.2.2.1

# Using snmpget (for specific OID)
snmpget -c public -v 2c localhost 1.3.6.1.2.1.2.2.1.5.1000
```

### 3. Check Logs

```bash
# Real-time logs
sudo journalctl -u vpp-snmp-agent -f

# Last 50 lines
sudo journalctl -u vpp-snmp-agent -n 50

# Logs from last hour
sudo journalctl -u vpp-snmp-agent --since "1 hour ago"
```

### 4. Run Package Test Suite

```bash
# Copy test script
sudo cp /usr/share/doc/vpp-snmp-agent-v2/test_agent.sh.gz /tmp/
gunzip /tmp/test_agent.sh.gz
chmod +x /tmp/test_agent.sh

# Run tests
/tmp/test_agent.sh
```

---

## Important OIDs

### Interface Speed (Bonding Fix)

The following OIDs now properly report speed for bonding interfaces:

- **1.3.6.1.2.1.2.2.1.5.{ifIndex}** - ifSpeed (32-bit, in bits/sec)
- **1.3.6.1.2.1.31.1.1.1.15.{ifIndex}** - ifHighSpeed (in Mbps)

Example:
```bash
# Get speed for all interfaces
snmpwalk -c public -v 2c localhost 1.3.6.1.2.1.2.2.1.5

# Get speed for interface with index 1000 (bonding)
snmpget -c public -v 2c localhost 1.3.6.1.2.1.2.2.1.5.1000
```

### Bonding Interface Detection

Bonding interfaces are identified by name prefix:
- `bond0`, `bond1`, `bond2`, etc.

For these interfaces, the agent:
1. First checks if direct `link_speed` is available
2. If zero, searches for member interface speeds
3. Returns the speed of the first member interface found
4. Defaults to 1 Gbps if no member speed is available

---

## Troubleshooting

### Service Won't Start

**Symptoms:**
```
Failed to start vpp-snmp-agent.service
```

**Solutions:**
1. Check VPP is running: `sudo systemctl status vpp`
2. Check SNMPD is running: `sudo systemctl status snmpd`
3. Check logs: `sudo journalctl -u vpp-snmp-agent -n 20`

### No SNMP Data

**Symptoms:**
```
snmpwalk returns empty
```

**Solutions:**
1. Verify agent is running: `sudo systemctl status vpp-snmp-agent`
2. Check VPP API connection: `sudo vppctl show version`
3. Check SNMPD configuration: `cat /etc/snmp/snmpd.conf | grep agentx`

### Bonding Interface Shows Zero Speed

**Symptoms:**
```
ifSpeed is 0 for bond interface
```

**Troubleshooting:**
1. Verify bonding interface exists: `ip link | grep bond`
2. Check member interfaces have speed: `ethtool eth0`
3. Review logs: `sudo journalctl -u vpp-snmp-agent | grep bond`

---

## Advanced Configuration

### Custom Polling Interval

Edit `/etc/vpp-snmp-agent/vpp-snmp-agent-config.yaml`:

```yaml
polling:
  interval: 10  # seconds
  timeout: 5    # seconds
```

Then restart:
```bash
sudo systemctl restart vpp-snmp-agent
```

### Integration with Monitoring Systems

#### Prometheus

Add to `/etc/prometheus/prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'vpp-snmp'
    static_configs:
      - targets: ['localhost:9161']  # SNMP exporter
```

#### Grafana

1. Add SNMP data source
2. Use OID queries for bonding interfaces
3. Example dashboard queries:
   ```
   1.3.6.1.2.1.2.2.1.5.1000  # bond0 speed
   1.3.6.1.2.1.2.2.1.10.1000 # bond0 RX octets
   1.3.6.1.2.1.2.2.1.16.1000 # bond0 TX octets
   ```

---

## Performance Tuning

### CPU Usage

If CPU usage is high:
1. Increase polling interval: `-p 30` (30 seconds)
2. Reduce interface count in VPP
3. Check system load: `top -u vpp`

### Memory Usage

Typical memory usage:
- Base: ~20-30 MB
- Per 100 interfaces: +10 MB

Monitor with:
```bash
ps aux | grep snmp-agent-v2
```

---

## Uninstallation

```bash
# Stop the service
sudo systemctl stop vpp-snmp-agent

# Disable auto-start
sudo systemctl disable vpp-snmp-agent

# Remove the package
sudo dpkg -r vpp-snmp-agent-v2

# Remove configuration (optional)
sudo rm -rf /etc/vpp-snmp-agent/
```

---

## Support & Documentation

### Files Included

- **Main Script:** `/usr/bin/snmp-agent-v2`
- **Python Modules:** `/usr/share/vpp-snmp-agent/`
- **Config:** `/etc/vpp-snmp-agent/vpp-snmp-agent-config.yaml`
- **Service:** `/lib/systemd/system/vpp-snmp-agent.service`
- **Documentation:** `/usr/share/doc/vpp-snmp-agent-v2/`

### Documentation Files

- `00_START_HERE.txt` - Quick start guide
- `SOLUTION.md` - Architecture & solution overview
- `IMPROVEMENTS.md` - Feature improvements
- `DEPLOYMENT_CHECKLIST.md` - Pre-deployment checks
- `MANIFEST.txt` - File manifest
- `test_agent.sh` - Test script

---

## Version Information

- **Package Name:** vpp-snmp-agent-v2
- **Version:** 2.0.0
- **Release Date:** 2024-12-09
- **License:** Check /usr/share/doc/vpp-snmp-agent-v2/copyright
- **Maintainer:** VPP Team <vpp@example.com>

---

## Key Features

✅ **Real-time Polling** - 5-second default polling interval  
✅ **Bonding Support** - Proper speed reporting for bonded interfaces  
✅ **AgentX Protocol** - Standards-based SNMP integration  
✅ **High Performance** - Async data collection  
✅ **Systemd Integration** - Full service management  
✅ **Comprehensive Logging** - Debug and operational logs  

---

## Next Steps

1. ✅ Install package
2. ✅ Verify installation
3. ✅ Configure (if needed)
4. ✅ Start service
5. ✅ Test with snmpwalk
6. ✅ Monitor logs
7. ✅ Integrate with monitoring system

---

**Last Updated:** 2024-12-09  
**Status:** Ready for Production Deployment
