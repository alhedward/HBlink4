# HBlink4 Web Dashboard

Real-time monitoring dashboard for HBlink4 DMR server with modern look and feel.

## Features

- **Real-time Updates**: WebSocket-based live updates every second (no page refreshes required)
- **Repeater Monitoring**: See all connected repeaters with their configurations and connection health
- **Active Streams**: Monitor ongoing DMR transmissions in real-time with duration counters
- **Last Heard Tracking**: View the 10 most recent users with alias display
  - 10-minute user cache with configurable timeout
  - View full cache (up to 50 users) in modal dialog
  - Shows radio ID, callsign/alias, repeater, slot, talkgroup, and time
- **On-Air Event Log**: Track stream starts and ends for user-focused activity monitoring
- **Connection Monitoring**: Visual warnings for repeaters with missed keepalives
- **Statistics**: View total streams, packets, and activity metrics
- **Clean Design**: Dark theme with responsive layout that works on desktop and mobile
- **Configurable Branding**: Customize server name and dashboard title
- **Network Info Button**: Optional button linking to network information page

## Configuration

The dashboard uses two configuration files:
- **HBlink4 config** (`config/config.json`) - Controls event sending
- **Dashboard config** (`dashboard/config.json`) - Controls dashboard behavior and event receiving

Both configs must use the same transport settings (Unix socket for local, TCP for remote).

For complete configuration details, see the [Configuration Guide](../docs/configuration.md#dashboard-configuration).
"port": 8765
```

The config file is created automatically with defaults on first run. Edit `dashboard/config.json` and restart the dashboard to apply changes.

### Announcement Banner

Display an optional announcement banner at the top of the dashboard to communicate important messages to users:

```json
{
  "announcement": {
    "enabled": true,
    "text": "Welcome to the network! Please review the talkgroup list before transmitting."
  }
}
```

**Configuration Options:**
- `enabled`: Set to `true` to show the banner, `false` to hide it
- `text`: The message to display in the banner

The banner appears as an eye-catching orange gradient banner at the top of the page, perfect for:
- Welcome messages
- Network announcements
- Maintenance notices
- Operational reminders
- Important policy updates

The banner only displays when both `enabled` is `true` and `text` contains content.

### Network Info Button

Add an optional "Network Info" button to the dashboard header that links users to your network's information page:

```json
{
  "network_info": {
    "enabled": true,
    "button_text": "Network Info",
    "url": "https://your-network-site.com/info"
  }
}
```

**Configuration Options:**
- `enabled`: Set to `true` to show the button, `false` to hide it
- `button_text`: Custom text for the button (e.g., "Network Info", "TG List", "Help")
- `url`: Target URL that opens in a new tab when clicked

The button appears as the first item in the header status area with a light blue color to distinguish it from system status indicators. Perfect for linking to talkgroup lists, network rules, connection information, or help pages.

## Usage

The dashboard is started automatically with `./run_all.sh` or can be started separately with `python3 run_dashboard.py`. Access at http://localhost:8080 (or your server IP for remote access).

## Dashboard Components

### Statistics Cards
Top row displays key metrics:
- **Connections**: Total connections (inbound repeaters/hotspots/network + connected outbound)
- **Active Streams**: Number of streams currently in progress (RX and TX)
- **Total Calls Today**: Number of calls received from repeaters today (RX only)
- **Retransmitted Calls Today**: Number of calls forwarded to other repeaters today (TX)
- **Total Traffic Today**: Duration of received traffic today (RX only)

### Last Heard Table
Shows the 10 most recent users:
- Radio ID
- Callsign/Alias (or "-" if not available)
- Repeater ID
- Slot (1 or 2)
- Talkgroup
- Time (e.g., "2 minutes ago")

Click "View Full Cache" to see up to 50 users with cache statistics.

### Active Streams
Real-time view of ongoing transmissions:
- Stream ID
- Radio ID
- Repeater ID
- Slot and Talkgroup
- Duration counter (updates every second)
- Packet count

### Connected Repeaters
Connections are automatically categorized by device type:

- 📶 **Repeaters** - Full duplex repeaters and club sites (generic MMDVM, MMDVM_Unknown)
- 📱 **Hotspots** - Personal hotspots (MMDVM_HS boards, Pi-Star, WPSD, DMO/simplex)
- 🔗 **Network Inbound** - Servers connecting to us (HBlink, FreeDMR)
- ❓ **Other** - Unrecognized connection types

Each category shows:
- Repeater ID and callsign
- IP address
- Connected duration
- Last keepalive time
- Configuration (mode, slots, colorcode)
- Warning indicator if keepalives are being missed

Click any connection card to view detailed information including:
- Software ID and Package ID (for troubleshooting detection)
- Connection Type (repeater, hotspot, network, unknown)
- Location, frequencies, and access control settings

Detection is based on the `package_id` field from MMDVM/Pi-Star/WPSD configuration, with fallback to `software_id`. Customize detection patterns in `config/config.json` under `connection_type_detection`. See the [Configuration Guide](../docs/configuration.md#connection-type-detection) for details.

### Recent Events
User-focused activity log showing:
- Stream starts (user keyed up)
- Stream ends (user unkeyed)
- Excludes system events like keepalives and cache updates


## API Endpoints

The dashboard provides REST API endpoints:

### GET /api/config
Returns dashboard configuration

### GET /api/repeaters
Returns list of all connected repeaters

### GET /api/streams
Returns list of active and recently ended streams

### GET /api/events?limit=50
Returns recent events (default limit: 100)

### GET /api/stats
Returns aggregate statistics

### WebSocket /ws
Real-time updates via WebSocket

## Troubleshooting

**Dashboard shows "Disconnected"**
- Check if HBlink4 is running
- Verify WebSocket connection in browser console

**No events appearing**
- Verify HBlink4 is running and repeaters are connected
- Check browser console for errors

**WebSocket connection fails**
- Verify uvicorn is running with WebSocket support
- Try a different browser

## License

Same as HBlink4: GNU GPLv3

## Administrator Talkgroup Editor

The dashboard has an optional authenticated administration page at `/admin` for
editing repeater talkgroup ACLs in `config/config.json`. The editor projection exposes only
`slot1_talkgroups` and `slot2_talkgroups` from repeater patterns and the optional
default repeater configuration. Before the editor unlocks, the administrator must
download a complete config backup. That explicit backup contains passphrases and
all other HBlink4 settings, so it must be stored securely.

Administration is disabled by default. Generate a password hash interactively:

```bash
python3 scripts/hash_dashboard_password.py
```

Add the resulting hash to `dashboard/config.json` and enable the admin section:

```json
"admin": {
    "enabled": true,
    "username": "admin",
    "password_hash": "pbkdf2_sha256$...",
    "session_timeout_minutes": 60,
    "cookie_secure": false,
    "hblink_config_path": "../config/config.json",
    "backup_on_save": true,
    "restart": {
        "enabled": true,
        "command": ["/usr/bin/systemctl", "restart", "hblink4.service"],
        "status_command": ["/usr/bin/systemctl", "is-active", "hblink4.service"],
        "timeout_seconds": 15,
        "verify_attempts": 6,
        "verify_delay_seconds": 0.5
    }
}
```

`HBLINK4_DASH_ADMIN_PASSWORD_HASH` can be used instead of storing the hash in
`dashboard/config.json`. Set `cookie_secure` to `true` when the dashboard is
served over HTTPS.

The talkgroup editor preserves HBlink4's ACL semantics:

- `null` = allow all talkgroups
- `[]` = deny all talkgroups
- `[91, 505, ...]` = allow only the listed talkgroups

New TG IDs can be added individually or in comma/space-separated groups. Each
administrator login session is gated until `/api/admin/config-backup` has
downloaded the current complete config and the editor verifies that revision.
Saves are revision checked, validated with HBlink4's repeater matcher, written
atomically, and (by default) retain the previous file as `config.json.bak`.

The same admin page can restore one of those complete JSON backups after a rebuild.
Restore is authenticated, CSRF-protected, limited to 2 MiB, validates the HBlink4
repeater configuration structure, and atomically replaces the live file while
retaining the previous live config as `config.json.bak`. HBlink4 must be restarted
before saved or restored configuration becomes active.

### Dashboard restart permission

The browser cannot supply a shell command. The server runs only the fixed
`admin.restart.command` configured locally and verifies the service with the
fixed status command. On systems with a Polkit version that supports JavaScript `.rules`, the supplied
rule can authorize the `cort` dashboard user to restart only `hblink4.service`:

```bash
sudo cp hblink4-dashboard-restart.rules /etc/polkit-1/rules.d/50-hblink4-dashboard-restart.rules
sudo chown root:root /etc/polkit-1/rules.d/50-hblink4-dashboard-restart.rules
sudo chmod 0644 /etc/polkit-1/rules.d/50-hblink4-dashboard-restart.rules
```

If the dashboard service runs under another account, change `subject.user` in
the rule before installing it. Ubuntu 22.04 ships an older Polkit implementation
that does not consume these JavaScript `.rules`; deployments on that release need
a narrowly scoped helper instead. Do not replace this with unrestricted passwordless
`sudo` access.
