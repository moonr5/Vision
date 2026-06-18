import io
from datetime import datetime
from typing import Dict, Any

from jinja2 import Environment, BaseLoader
from weasyprint import HTML

REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; color: #1e1b4b; background: #fff; }

  .header {
    background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    color: white;
    padding: 24px 32px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }
  .header h1 { font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }
  .header .sub { font-size: 11px; opacity: 0.85; margin-top: 4px; }
  .header .meta { text-align: right; font-size: 10px; opacity: 0.8; }

  .section { padding: 18px 32px 0; }
  .section-title {
    font-size: 13px; font-weight: 700; color: #4f46e5;
    border-bottom: 2px solid #e0e7ff; padding-bottom: 5px; margin-bottom: 12px;
    text-transform: uppercase; letter-spacing: 0.8px;
  }

  .kpi-grid { display: flex; gap: 12px; margin-bottom: 6px; }
  .kpi {
    flex: 1; background: #f5f3ff; border: 1px solid #e0e7ff;
    border-radius: 8px; padding: 12px 16px; text-align: center;
  }
  .kpi .value { font-size: 24px; font-weight: 700; color: #4f46e5; }
  .kpi .label { font-size: 9px; color: #6b7280; text-transform: uppercase; margin-top: 2px; }

  table { width: 100%; border-collapse: collapse; font-size: 10px; }
  th {
    background: #4f46e5; color: white;
    padding: 7px 10px; text-align: left;
    font-weight: 600; font-size: 9px; text-transform: uppercase;
  }
  td { padding: 6px 10px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }
  tr:nth-child(even) td { background: #fafafa; }

  .badge {
    display: inline-block; padding: 1px 7px; border-radius: 99px;
    font-size: 8px; font-weight: 600; text-transform: uppercase;
  }
  .badge-active, .badge-online, .badge-in_transit { background: #d1fae5; color: #065f46; }
  .badge-inactive, .badge-offline { background: #fee2e2; color: #991b1b; }
  .badge-pending { background: #fef3c7; color: #92400e; }
  .badge-delivered, .badge-completed { background: #d1fae5; color: #065f46; }
  .badge-critical { background: #fee2e2; color: #991b1b; }
  .badge-warning { background: #fef3c7; color: #92400e; }
  .badge-info, .badge-low { background: #e0e7ff; color: #3730a3; }

  .score-bar { display: inline-block; width: 50px; height: 5px; background: #e0e7ff; border-radius: 3px; vertical-align: middle; margin-right: 4px; overflow: hidden; }
  .score-fill { height: 100%; background: #4f46e5; border-radius: 3px; }

  .footer {
    margin-top: 20px; padding: 12px 32px;
    border-top: 1px solid #e0e7ff;
    font-size: 9px; color: #9ca3af;
    display: flex; justify-content: space-between;
  }

  .page-break { page-break-before: always; }
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="sub">SGU LOGISTICS</div>
    <h1>Fleet Management Report</h1>
    <div class="sub">Operational Intelligence Summary</div>
  </div>
  <div class="meta">
    Generated: {{ generated_at }}<br>
    Period: Last 7 days<br>
    SGU Logistics & Telemetry Dashboard
  </div>
</div>

<!-- KPIs -->
<div class="section" style="margin-top:18px;">
  <div class="section-title">Fleet Overview</div>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="value">{{ stats.active_drivers or 0 }}</div>
      <div class="label">Active Drivers</div>
    </div>
    <div class="kpi">
      <div class="value">{{ stats.active_orders or 0 }}</div>
      <div class="label">Active Orders</div>
    </div>
    <div class="kpi">
      <div class="value">{{ stats.online_devices or 0 }}</div>
      <div class="label">Online Devices</div>
    </div>
    <div class="kpi">
      <div class="value" style="color:{% if (stats.critical_events_24h or 0) > 0 %}#dc2626{% else %}#4f46e5{% endif %}">
        {{ stats.critical_events_24h or 0 }}
      </div>
      <div class="label">Critical Events (24h)</div>
    </div>
  </div>
</div>

<!-- AI Insight -->
{% if ai_summary %}
<div class="section" style="margin-top:14px;">
  <div class="section-title">AI Analysis</div>
  <div style="background:#f5f3ff; border-left:4px solid #4f46e5; padding:12px 16px; border-radius:4px; font-size:11px; line-height:1.6; color:#1e1b4b;">
    {{ ai_summary }}
  </div>
</div>
{% endif %}

<!-- Drivers -->
{% if drivers %}
<div class="section" style="margin-top:18px;">
  <div class="section-title">Driver Performance ({{ drivers | length }})</div>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>License</th>
        <th>Status</th>
        <th>Safety Score</th>
        <th>Trips</th>
        <th>Distance (km)</th>
      </tr>
    </thead>
    <tbody>
      {% for d in drivers %}
      <tr>
        <td><strong>{{ d.name }}</strong></td>
        <td>{{ d.license_number or '—' }}</td>
        <td><span class="badge badge-{{ d.status }}">{{ d.status }}</span></td>
        <td>
          <span class="score-bar"><span class="score-fill" style="width:{{ d.safety_score or 0 }}%"></span></span>
          {{ d.safety_score or '—' }}
        </td>
        <td>{{ d.total_trips or 0 }}</td>
        <td>{{ "%.1f"|format(d.total_distance or 0) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

<!-- Orders -->
{% if orders %}
<div class="section page-break" style="margin-top:18px;">
  <div class="section-title">Recent Orders ({{ orders | length }})</div>
  <table>
    <thead>
      <tr>
        <th>Order #</th>
        <th>Customer</th>
        <th>Status</th>
        <th>Pickup</th>
        <th>Delivery</th>
        <th>Created</th>
      </tr>
    </thead>
    <tbody>
      {% for o in orders %}
      <tr>
        <td><strong>{{ o.order_number }}</strong></td>
        <td>{{ o.customer or '—' }}</td>
        <td><span class="badge badge-{{ o.status }}">{{ o.status }}</span></td>
        <td>{{ o.pickup_location or '—' }}</td>
        <td>{{ o.delivery_location or '—' }}</td>
        <td>{{ o.created_at.strftime('%Y-%m-%d') if o.created_at else '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

<!-- Events -->
{% if events %}
<div class="section" style="margin-top:18px;">
  <div class="section-title">Recent Events — Last 7 Days ({{ events | length }})</div>
  <table>
    <thead>
      <tr>
        <th>Date / Time</th>
        <th>Type</th>
        <th>Severity</th>
        <th>Driver</th>
        <th>Device</th>
        <th>Description</th>
      </tr>
    </thead>
    <tbody>
      {% for e in events %}
      <tr>
        <td>{{ e.timestamp.strftime('%Y-%m-%d %H:%M') if e.timestamp else '—' }}</td>
        <td>{{ e.event_type or '—' }}</td>
        <td><span class="badge badge-{{ (e.severity or 'info').lower() }}">{{ e.severity or 'info' }}</span></td>
        <td>{{ e.driver_name or '—' }}</td>
        <td>{{ e.device_id or '—' }}</td>
        <td>{{ e.description or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

<div class="footer">
  <span>SGU Logistics Fleet Management System</span>
  <span>{{ generated_at }} · Confidential</span>
</div>

</body>
</html>
"""

_jinja_env = Environment(loader=BaseLoader())
_template = _jinja_env.from_string(REPORT_TEMPLATE)


def generate_pdf(data: Dict[str, Any], ai_summary: str = "") -> bytes:
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    html_str = _template.render(
        generated_at=now,
        stats=data.get("stats", {}),
        drivers=data.get("drivers", []),
        orders=data.get("orders", []),
        events=data.get("events", []),
        ai_summary=ai_summary,
    )
    buf = io.BytesIO()
    HTML(string=html_str).write_pdf(buf)
    return buf.getvalue()
