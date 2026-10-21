/**
 * MeshSOS Rescuer Command Center Client Logic.
 * Handles interactive Leaflet layers, silence inference evidence inspection,
 * live telemetry polling, and simulation triggers.
 */

// Initialize Leaflet Map
const map = L.map('map', {
  center: [37.775, -122.418],
  zoom: 15,
  zoomControl: false,
});

// Custom Position Zoom Control
L.control.zoom({ position: 'bottomright' }).addTo(map);

// Add Dark Matter Tile Layer (with fallback)
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  subdomains: 'abcd',
  maxZoom: 19,
}).addTo(map);

// Layer Groups
const silentZonesLayer = L.layerGroup().addTo(map);
const sosHotspotsLayer = L.layerGroup().addTo(map);
const activeCoverageLayer = L.layerGroup().addTo(map);
const contactGraphLayer = L.layerGroup(); // off by default

// UI References
const valActiveNodes = document.getElementById('val-active-nodes');
const valSilentZones = document.getElementById('val-silent-zones');
const valSosAlerts = document.getElementById('val-sos-alerts');
const sosCountPill = document.getElementById('sos-count-pill');
const incidentsList = document.getElementById('incidents-list');

// Evidence Drawer References
const evidenceDrawer = document.getElementById('evidence-drawer');
const drawerZoneTitle = document.getElementById('drawer-zone-title');
const drawerConfidenceText = document.getElementById('drawer-confidence-text');
const drawerConfidenceFill = document.getElementById('drawer-confidence-fill');
const drawerSilentCount = document.getElementById('drawer-silent-count');
const drawerSpanSec = document.getElementById('drawer-span-sec');
const drawerAliveNeighbours = document.getElementById('drawer-alive-neighbours');
const drawerReasonText = document.getElementById('drawer-reason-text');
const drawerNodeChips = document.getElementById('drawer-node-chips');
const btnCloseDrawer = document.getElementById('btn-close-drawer');

// Layer Toggle Bindings
document.getElementById('toggle-silent-zones').addEventListener('change', (e) => {
  if (e.target.checked) map.addLayer(silentZonesLayer);
  else map.removeLayer(silentZonesLayer);
});

document.getElementById('toggle-sos-hotspots').addEventListener('change', (e) => {
  if (e.target.checked) map.addLayer(sosHotspotsLayer);
  else map.removeLayer(sosHotspotsLayer);
});

document.getElementById('toggle-active-coverage').addEventListener('change', (e) => {
  if (e.target.checked) map.addLayer(activeCoverageLayer);
  else map.removeLayer(activeCoverageLayer);
});

document.getElementById('toggle-contact-graph').addEventListener('change', (e) => {
  if (e.target.checked) map.addLayer(contactGraphLayer);
  else map.removeLayer(contactGraphLayer);
});

btnCloseDrawer.addEventListener('click', () => {
  evidenceDrawer.classList.add('hidden');
});

// Simulation Trigger Buttons
document.getElementById('btn-sim-collapse').addEventListener('click', async () => {
  try {
    const res = await fetch('/api/simulate/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: 'collapse' }),
    });
    const data = await res.json();
    console.log('Simulation collapse response:', data);
    fetchData(); // Immediate refresh
  } catch (err) {
    console.error('Failed to trigger collapse simulation:', err);
  }
});

document.getElementById('btn-sim-battery').addEventListener('click', async () => {
  try {
    await fetch('/api/simulate/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: 'battery' }),
    });
    fetchData();
  } catch (err) {
    console.error('Failed to trigger battery simulation:', err);
  }
});

document.getElementById('btn-sim-sos').addEventListener('click', async () => {
  try {
    await fetch('/api/simulate/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: 'sos' }),
    });
    fetchData();
  } catch (err) {
    console.error('Failed to trigger SOS simulation:', err);
  }
});

document.getElementById('btn-sim-reset').addEventListener('click', async () => {
  try {
    await fetch('/api/reset', { method: 'POST' });
    evidenceDrawer.classList.add('hidden');
    fetchData();
  } catch (err) {
    console.error('Failed to reset:', err);
  }
});

// Show Evidence in Drawer
function showEvidence(props) {
  drawerZoneTitle.textContent = props.zone_id.toUpperCase();
  const confPct = Math.round(props.confidence * 100);
  drawerConfidenceText.textContent = `${confPct}%`;
  drawerConfidenceFill.style.width = `${confPct}%`;
  drawerSilentCount.textContent = props.n_silent;
  drawerSpanSec.textContent = `${props.last_heard_span_sec}s`;
  drawerAliveNeighbours.textContent = props.neighbours_alive;
  drawerReasonText.textContent = props.reason;

  // Populate node chips
  drawerNodeChips.innerHTML = '';
  (props.silent_node_ids || []).forEach((id) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = `#${id.toString(16).toUpperCase()}`;
    drawerNodeChips.appendChild(chip);
  });

  evidenceDrawer.classList.remove('hidden');
}

// Render Layers from GeoJSON
function renderSilentZones(geo) {
  silentZonesLayer.clearLayers();
  if (!geo || !geo.features) return;

  L.geoJSON(geo, {
    style: (feature) => {
      const conf = feature.properties.confidence;
      return {
        color: conf > 0.6 ? '#f59e0b' : '#d97706',
        weight: 2.5,
        dashArray: '6, 6',
        fillColor: '#f59e0b',
        fillOpacity: Math.max(0.15, conf * 0.35),
      };
    },
    onEachFeature: (feature, layer) => {
      layer.on('click', () => {
        showEvidence(feature.properties);
      });
      layer.bindTooltip(`
        <strong>Silent Zone (${Math.round(feature.properties.confidence * 100)}% Conf)</strong><br>
        ${feature.properties.n_silent} phones silent<br>
        <em>Click for evidence breakdown</em>
      `);
    },
  }).addTo(silentZonesLayer);
}

function renderSosHotspots(geo) {
  sosHotspotsLayer.clearLayers();
  if (!geo || !geo.features) return;

  L.geoJSON(geo, {
    pointToLayer: (feature, latlng) => {
      const count = feature.properties.count || 1;
      const radius = 12 + Math.min(20, count * 4);
      return L.circleMarker(latlng, {
        radius: radius,
        fillColor: '#ef4444',
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.85,
      });
    },
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      const cats = Object.entries(p.categories || {})
        .map(([k, v]) => `${k}: ${v}`)
        .join(', ');
      layer.bindPopup(`
        <div style="color: #0f172a; font-family: sans-serif;">
          <h4 style="margin: 0 0 4px 0; color: #dc2626;">🚨 SOS Hotspot</h4>
          <strong>Victims:</strong> ${p.count}<br>
          <strong>Categories:</strong> ${cats}<br>
          <strong>Urgency:</strong> ${p.urgency_score}
        </div>
      `);
    },
  }).addTo(sosHotspotsLayer);
}

function renderActiveCoverage(geo) {
  activeCoverageLayer.clearLayers();
  if (!geo || !geo.features) return;

  L.geoJSON(geo, {
    style: () => ({
      color: '#06b6d4',
      weight: 1,
      fillColor: '#06b6d4',
      fillOpacity: 0.12,
    }),
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      layer.bindTooltip(`
        <strong>Cell Coverage: ${p.status}</strong><br>
        Active peers: ${p.node_count}<br>
        Moving: ${p.moving} | Stationary: ${p.stationary}
      `);
    },
  }).addTo(activeCoverageLayer);
}

function renderContactGraph(graph) {
  contactGraphLayer.clearLayers();
  if (!graph || !graph.nodes) return;

  const nodeMap = {};
  graph.nodes.forEach((n) => {
    nodeMap[n.id] = n;
    const isReporting = n.status === 'reporting';
    const marker = L.circleMarker([n.lat, n.lon], {
      radius: isReporting ? 4 : 3,
      fillColor: isReporting ? '#10b981' : '#64748b',
      color: '#ffffff',
      weight: 1,
      fillOpacity: 0.9,
    });
    marker.bindTooltip(`Node #${n.id.toString(16).toUpperCase()}<br>Status: ${n.status}<br>Battery: ${n.battery}%`);
    marker.addTo(contactGraphLayer);
  });

  (graph.edges || []).forEach((e) => {
    const u = nodeMap[e.source];
    const v = nodeMap[e.target];
    if (u && v) {
      L.polyline([[u.lat, u.lon], [v.lat, v.lon]], {
        color: '#8b5cf6',
        weight: 1.5,
        opacity: 0.4,
      }).addTo(contactGraphLayer);
    }
  });
}

function updateIncidentsList(sosGeo) {
  const features = sosGeo?.features || [];
  sosCountPill.textContent = features.length;
  incidentsList.innerHTML = '';

  if (features.length === 0) {
    incidentsList.innerHTML = '<div class="empty-state">No active emergency distress calls.</div>';
    return;
  }

  features.forEach((f) => {
    const p = f.properties;
    const item = document.createElement('div');
    item.className = 'incident-item';
    const catKeys = Object.keys(p.categories || {});
    const primaryCat = catKeys[0] || 'OTHER';

    item.innerHTML = `
      <span class="incident-badge ${primaryCat}">${primaryCat}</span>
      <div><strong>${p.count} Person(s) Calling for Help</strong></div>
      <small style="color: var(--text-dim);">Urgency Score: ${p.urgency_score}</small>
    `;
    item.addEventListener('click', () => {
      const coords = f.geometry.coordinates;
      map.setView([coords[1], coords[0]], 16);
    });
    incidentsList.appendChild(item);
  });
}

// Fetch live telemetry from backend
async function fetchData() {
  try {
    const res = await fetch('/api/layers/all');
    if (!res.ok) return;
    const data = await res.json();

    // Update KPI counters
    const stats = data.stats || {};
    valActiveNodes.textContent = stats.active_nodes_count || 0;
    valSilentZones.textContent = stats.silent_zones_count || 0;
    valSosAlerts.textContent = stats.sos_hotspots_count || 0;

    // Render Layers
    renderSilentZones(data.silent_zones);
    renderSosHotspots(data.sos_hotspots);
    renderActiveCoverage(data.active_coverage);
    renderContactGraph(data.contact_graph);
    updateIncidentsList(data.sos_hotspots);
  } catch (err) {
    console.error('Fetch error:', err);
  }
}

// Initial fetch and 2.5s polling loop
fetchData();
setInterval(fetchData, 2500);
