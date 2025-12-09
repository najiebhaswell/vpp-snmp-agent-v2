#!/bin/bash
# VPP SNMP Agent - Deployment Checklist

cat << 'EOF'
╔═══════════════════════════════════════════════════════════════════════════╗
║          VPP SNMP Agent - Deployment & Verification Checklist             ║
╚═══════════════════════════════════════════════════════════════════════════╝

PREPARATION PHASE
═════════════════════════════════════════════════════════════════════════════

Pre-Deployment:
  ☐ Read SOLUTION.md for quick overview
  ☐ Read IMPROVEMENTS.md for technical details  
  ☐ Verify VPP is running: vppctl show version
  ☐ Verify snmpd is installed: sudo systemctl status snmpd
  ☐ Check VPP sockets exist:
      ls -la /run/vpp/api.sock
      ls -la /run/vpp/stats.sock
  ☐ Verify snmp tools: which snmpget snmpwalk


TESTING PHASE (LOCAL)
═════════════════════════════════════════════════════════════════════════════

Option A: Quick Manual Test
  ☐ Start agent with debug:
      cd ~/vpp-snmp-agent
      python3 snmp_agent_integrated.py -p 5 -d
  
  ☐ In another terminal, test SNMP query:
      snmpget -v2c -c public localhost:705 1.3.6.1.2.1.2.2.1.2.1000
  
  ☐ Verify output shows interface name (success = STRING response)
  
  ☐ Walk through interfaces:
      snmpwalk -v2c -c public localhost:705 1.3.6.1.2.1.2.2.1
  
  ☐ Check grafik smoothness:
      - Set monitoring to collect every 5 seconds
      - Verify no gaps in graph
      - Verify smooth progression (not jagged)

Option B: Automated Test (Recommended)
  ☐ Run test suite:
      cd ~/vpp-snmp-agent
      ./test_agent.sh localhost:705 5
  
  ☐ Verify all tests pass:
      ✓ Prerequisites
      ✓ Agent Startup
      ✓ SNMP Queries
      ✓ Data Consistency
      ✓ Responsiveness
  
  ☐ Note response times (should be <100ms average)


OPTIONAL: FINE-TUNING
═════════════════════════════════════════════════════════════════════════════

Graph Smoothness:
  ☐ If graph still jagged, try:
      python3 snmp_agent_integrated.py -p 2 -d
      (Decreases poll period from 5s to 2s, 2.5x more responsive)
  
  ☐ If high CPU, try:
      python3 snmp_agent_integrated.py -p 10 -d
      (Increases poll period from 5s to 10s, less responsive but lower CPU)

Stability:
  ☐ If timeouts occur, increase timeout:
      python3 snmp_agent_integrated.py -p 5 -t 10 -d
      (Increases from 5s to 10s timeout)

Production Configuration:
  ☐ Create config file:
      cp vpp-snmp-agent-config.yaml vpp-snmp-agent-custom.yaml
      # Edit as needed
  
  ☐ Test with config:
      python3 snmp_agent_integrated.py -c vpp-snmp-agent-custom.yaml -p 5 -d


SYSTEMD DEPLOYMENT
═════════════════════════════════════════════════════════════════════════════

Installation:
  ☐ Copy agent to system location:
      sudo cp snmp_agent_integrated.py /usr/local/bin/
      sudo chmod +x /usr/local/bin/snmp_agent_integrated.py
  
  ☐ Create systemd service file:
      sudo tee /etc/systemd/system/vpp-snmp-agent.service > /dev/null << 'SERVICE'
[Unit]
Description=VPP SNMP Agent V2 - Real-time Monitoring
After=network.target vpp.service
Wants=snmpd.service

[Service]
Type=simple
User=vpp
Group=vpp
ExecStart=/usr/bin/python3 /usr/local/bin/snmp_agent_integrated.py \
    -a localhost:705 \
    -p 5 \
    -t 5
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE
  
  ☐ Reload systemd:
      sudo systemctl daemon-reload
  
  ☐ Enable at boot:
      sudo systemctl enable vpp-snmp-agent
  
  ☐ Start service:
      sudo systemctl start vpp-snmp-agent
  
  ☐ Check status:
      sudo systemctl status vpp-snmp-agent
  
  ☐ Verify logs:
      sudo journalctl -u vpp-snmp-agent -f
  
  ☐ Test SNMP query (should work now):
      snmpget -v2c -c public localhost:705 1.3.6.1.2.1.2.2.1.2.1000


GRAFANA INTEGRATION
═════════════════════════════════════════════════════════════════════════════

If using Prometheus:
  ☐ Update prometheus.yml to scrape agent:
      - job_name: 'vpp-snmp'
        static_configs:
          - targets: ['localhost:705']
        scrape_interval: 5s
  
  ☐ Restart Prometheus:
      sudo systemctl restart prometheus

If using direct SNMP polling (e.g., Grafana SNMP plugin):
  ☐ Add SNMP data source in Grafana:
      - Host: localhost
      - Port: 705
      - Version: v2c
      - Community: public
  
  ☐ Create dashboard with queries:
      - ifInOctets (RX bytes)
      - ifOutOctets (TX bytes)
      - ifInUcastPkts (RX packets)
      - ifOutUcastPkts (TX packets)
  
  ☐ Set dashboard refresh to 5 seconds minimum
  
  ☐ Verify graphs appear smooth (no jagged lines)


VERIFICATION & MONITORING
═════════════════════════════════════════════════════════════════════════════

Daily Checks:
  ☐ Service running:
      sudo systemctl status vpp-snmp-agent
  
  ☐ No errors in logs:
      sudo journalctl -u vpp-snmp-agent -p err
  
  ☐ CPU usage reasonable:
      ps aux | grep snmp_agent
      # Should be <5% CPU with default settings
  
  ☐ Memory stable:
      ps aux | grep snmp_agent
      # Should be <100MB

Monitoring Dashboard:
  ☐ Create monitoring dashboard tracking:
      - Agent uptime
      - Data collection rate
      - Last successful update timestamp
      - SNMP query response times
  
  ☐ Set alerts for:
      - Service down for >5 minutes
      - High error count (>10 errors per minute)
      - CPU >50% (might indicate problem)

Scheduled Health Checks:
  ☐ Weekly: Run test_agent.sh to verify functionality
  ☐ Monthly: Review logs for patterns or errors
  ☐ Quarterly: Performance tuning review


TROUBLESHOOTING
═════════════════════════════════════════════════════════════════════════════

Problem: Graphs Still Jagged
  ☐ Check polling period: systemctl status vpp-snmp-agent | grep ExecStart
  ☐ Try: sudo systemctl stop vpp-snmp-agent
  ☐ Edit service: sudo systemctl edit vpp-snmp-agent
  ☐ Change -p 5 to -p 2
  ☐ Restart: sudo systemctl restart vpp-snmp-agent

Problem: SNMP Connection Refused
  ☐ Check snmpd: sudo systemctl status snmpd
  ☐ Check port: sudo netstat -tlnp | grep 705
  ☐ Check firewall: sudo ufw status

Problem: Data Not Updating
  ☐ Check logs: sudo journalctl -u vpp-snmp-agent -f
  ☐ Check VPP: vppctl show version
  ☐ Check sockets: ls -la /run/vpp/

Problem: High CPU Usage
  ☐ Increase poll period: Change -p 2 to -p 5 or -p 10
  ☐ Check log level: Disable debug mode (-d flag removed)
  ☐ Monitor per-interface load

Problem: Frequent Disconnects
  ☐ Increase timeout: Change -t 5 to -t 10
  ☐ Check VPP load: vppctl show node counters
  ☐ Review VPP logs: journalctl -u vpp -f


ROLLBACK PLAN
═════════════════════════════════════════════════════════════════════════════

If new agent causes issues:
  ☐ Stop new agent:
      sudo systemctl stop vpp-snmp-agent
  
  ☐ Switch back to original:
      sudo systemctl stop vpp-snmp-agent.service  # or original service
      sudo systemctl start vpp-snmp-agent-old     # or original service
  
  ☐ Review logs to understand issue
  
  ☐ Adjust settings or contact support


COMPLETION CHECKLIST
═════════════════════════════════════════════════════════════════════════════

Pre-Production:
  ☐ All tests passing
  ☐ Graphs showing data
  ☐ No high error rates
  ☐ Documentation reviewed
  ☐ Team trained on monitoring
  ☐ Backup of original code
  ☐ Monitoring configured

Production:
  ☐ Service installed and enabled
  ☐ Logs being collected
  ☐ Alerts configured
  ☐ Dashboard created
  ☐ Performance baseline established
  ☐ Team knows troubleshooting steps
  ☐ Rollback plan documented

Post-Deployment:
  ☐ Week 1: Daily monitoring
  ☐ Week 2-4: Every other day
  ☐ After 1 month: Normal monitoring schedule
  ☐ Document any issues and resolutions


PERFORMANCE BASELINE
═════════════════════════════════════════════════════════════════════════════

Expected Metrics:
  Poll Interval:          5 seconds
  Socket Timeout:         1.0-5.0 seconds
  SNMP Response Time:     <100ms (average)
  Update Frequency:       Every 5 seconds
  Graph Smoothness:       Smooth, no gaps
  CPU Usage:              <5% (default settings)
  Memory Usage:           <100MB
  Error Rate:             <1 error per 1000 requests
  Availability:           >99.9% uptime


CONTACTS & REFERENCES
═════════════════════════════════════════════════════════════════════════════

Documentation:
  - SOLUTION.md       : Quick start guide
  - IMPROVEMENTS.md   : Technical analysis
  - README_IMPROVEMENTS.txt : This summary

Testing:
  - test_agent.sh     : Automated testing

Code:
  - snmp_agent_integrated.py : Main agent (RECOMMENDED)
  - snmp_agent_v2.py         : Standalone collector (for testing)
  - vpp-snmp-agent.py        : Original (kept for reference)

Support:
  1. Check logs: sudo journalctl -u vpp-snmp-agent -f
  2. Run tests: ./test_agent.sh
  3. Review documentation
  4. Enable debug: -d flag


NOTES
═════════════════════════════════════════════════════════════════════════════

Deployment Date:      _____________
Deployed By:          _____________
Initial Settings:     -p ____  -t ____  -a ___________
Issues Encountered:   _____________________________________________
Resolution:           _____________________________________________
Final Status:         ☐ SUCCESS ☐ NEEDS ADJUSTMENT

Any issues or customizations made:
__________________________________________________________________________
__________________________________________________________________________


═════════════════════════════════════════════════════════════════════════════
        Deployment Complete! Enjoy smooth monitoring graphs. ✨
═════════════════════════════════════════════════════════════════════════════
EOF
