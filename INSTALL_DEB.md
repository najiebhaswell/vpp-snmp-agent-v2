# VPP SNMP Agent V2 - Debian Package Installation Guide

## Quick Installation (2 minutes)

### Option 1: Install from Pre-built .deb Package

```bash
# Download or locate the .deb file
wget https://your-repo/vpp-snmp-agent-v2_2.0.0_all.deb

# Install the package
sudo dpkg -i vpp-snmp-agent-v2_2.0.0_all.deb

# Verify installation
sudo systemctl status vpp-snmp-agent

# View logs
sudo journalctl -u vpp-snmp-agent -f
```

### Option 2: Build .deb Package from Source

```bash
# Install build dependencies
sudo apt-get install debhelper dh-python dpkg-dev

# Clone or extract the source
cd ~/vpp-snmp-agent

# Build the package
./build-deb.sh

# Install the built package
sudo dpkg -i ../vpp-snmp-agent-v2_2.0.0_all.deb
```

## What Gets Installed

```
/usr/bin/snmp-agent-v2                    Main executable
/etc/vpp-snmp-agent/config.yaml           Configuration file
/lib/systemd/system/vpp-snmp-agent.service   Systemd service
/var/log/vpp-snmp-agent/                  Log directory
/usr/share/doc/vpp-snmp-agent-v2/         Documentation
```

## Post-Installation Steps

### 1. Verify Installation

```bash
# Check if service is installed
systemctl list-unit-files | grep vpp-snmp-agent

# Check file permissions
ls -la /usr/bin/snmp-agent-v2
```

### 2. Configure (Optional)

Edit the configuration if needed:

```bash
sudo nano /etc/vpp-snmp-agent/config.yaml
```

Or use command-line options:

```bash
# View available options
snmp-agent-v2 -h
```

### 3. Enable and Start Service

```bash
# Start the service
sudo systemctl start vpp-snmp-agent

# Enable auto-start on boot
sudo systemctl enable vpp-snmp-agent

# Verify it's running
sudo systemctl status vpp-snmp-agent
```

### 4. Test SNMP Queries

```bash
# Test GET query
snmpget -v2c -c public localhost:705 1.3.6.1.2.1.2.2.1.2.1000

# Test WALK query
snmpwalk -v2c -c public localhost:705 1.3.6.1.2.1.2.2.1
```

## Configuration

### View Current Configuration

```bash
sudo cat /etc/vpp-snmp-agent/config.yaml
```

### Modify Polling Period

Edit the systemd service to change polling period:

```bash
sudo systemctl edit vpp-snmp-agent
```

Add or modify the ExecStart line:

```ini
[Service]
ExecStart=/usr/bin/snmp-agent-v2 -a localhost:705 -p 2 -t 5
```

Then restart:

```bash
sudo systemctl restart vpp-snmp-agent
```

### Common Configurations

**Smooth Graphs (2 second polling):**
```
ExecStart=/usr/bin/snmp-agent-v2 -a localhost:705 -p 2 -t 5
```

**Balanced (default 5 second polling):**
```
ExecStart=/usr/bin/snmp-agent-v2 -a localhost:705 -p 5 -t 5
```

**Low Resource (10 second polling):**
```
ExecStart=/usr/bin/snmp-agent-v2 -a localhost:705 -p 10 -t 5
```

**High Reliability (longer timeout):**
```
ExecStart=/usr/bin/snmp-agent-v2 -a localhost:705 -p 5 -t 10
```

## Monitoring

### View Status

```bash
# Service status
sudo systemctl status vpp-snmp-agent

# Active logs (real-time)
sudo journalctl -u vpp-snmp-agent -f

# Recent logs
sudo journalctl -u vpp-snmp-agent -n 50

# Logs from last hour
sudo journalctl -u vpp-snmp-agent --since "1 hour ago"
```

### Check Performance

```bash
# CPU and memory usage
ps aux | grep snmp-agent

# Process info
ps -eo pid,user,%cpu,%mem,cmd | grep snmp-agent
```

## Uninstallation

```bash
# Stop the service
sudo systemctl stop vpp-snmp-agent

# Remove the package
sudo dpkg -r vpp-snmp-agent-v2

# Remove configuration (if you want)
sudo rm -rf /etc/vpp-snmp-agent/
```

## Troubleshooting

### Service won't start

Check logs for error:
```bash
sudo journalctl -u vpp-snmp-agent -n 100
```

Common issues:
- VPP not running: `vppctl show version`
- Permission denied: Check `/run/vpp/` permissions
- Port in use: Check if snmpd is running: `systemctl status snmpd`

### Connection refused

```bash
# Check if snmpd is running
sudo systemctl status snmpd

# Check if agent is listening
netstat -tlnp | grep 705

# Check firewall
sudo ufw status
```

### Data not updating

```bash
# Verify VPP sockets exist
ls -la /run/vpp/

# Check VPP status
vppctl show version

# View agent debug logs
sudo systemctl stop vpp-snmp-agent
/usr/bin/snmp-agent-v2 -a localhost:705 -p 5 -t 5 -d
```

### High CPU usage

Increase polling period in systemd service:
```bash
sudo systemctl edit vpp-snmp-agent
# Change -p 5 to -p 10
sudo systemctl restart vpp-snmp-agent
```

## Documentation

All documentation is included in the package:

```bash
# Read quick start
cat /usr/share/doc/vpp-snmp-agent-v2/SOLUTION.md

# Read technical analysis
cat /usr/share/doc/vpp-snmp-agent-v2/IMPROVEMENTS.md

# View deployment guide
cat /usr/share/doc/vpp-snmp-agent-v2/DEPLOYMENT_CHECKLIST.md
```

## Upgrade

To upgrade to a new version:

```bash
# Download new package
wget https://your-repo/vpp-snmp-agent-v2_2.1.0_all.deb

# Install (will upgrade automatically)
sudo dpkg -i vpp-snmp-agent-v2_2.1.0_all.deb

# Restart service
sudo systemctl restart vpp-snmp-agent
```

## Support

For issues or questions:

1. Check logs: `sudo journalctl -u vpp-snmp-agent -f`
2. Enable debug: Stop service and run with `-d` flag
3. Review documentation in `/usr/share/doc/vpp-snmp-agent-v2/`
4. Check VPP status and permissions

## Package Details

```
Package: vpp-snmp-agent-v2
Version: 2.0.0
Architecture: all (architecture-independent)
Size: ~150KB installed
Dependencies: python3 (>=3.6), python3-yaml, vpp (>=20.05), snmpd
Maintainer: VPP Team
License: BSD 2-Clause
```

---

**Installed successfully!** Your VPP SNMP Agent V2 is ready to use. Enjoy smooth monitoring graphs! ✨
