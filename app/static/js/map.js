// Leaflet map: pick the farm location, show nearby disaster events and the river cell used for flood analysis.
/* global L */
import { esc } from './format.js';

let map;
let farmMarker;
const overlays = { events: null, river: null };

const ALERT_COLORS = { Red: '#b3261e', Orange: '#d4561c', Green: '#2e8b57' };

export function initMap(onPick) {
  map = L.map('map', { zoomControl: true, attributionControl: true }).setView([22.6, 79.0], 4);
  const streets = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap contributors',
  });
  const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
  });
  streets.addTo(map);
  L.control.layers({ Map: streets, Satellite: satellite }, null, { position: 'topright' }).addTo(map);
  map.on('click', (e) => onPick(e.latlng.lat, e.latlng.lng, true));
  return map;
}

export function setFarm(lat, lon, zoom) {
  if (!map) return;
  if (!farmMarker) {
    farmMarker = L.marker([lat, lon], { draggable: true, title: 'Your farm' }).addTo(map);
  } else {
    farmMarker.setLatLng([lat, lon]);
  }
  if (zoom) map.setView([lat, lon], Math.max(map.getZoom(), zoom));
  return farmMarker;
}

export function onFarmDrag(handler) {
  if (farmMarker) farmMarker.on('dragend', () => { const p = farmMarker.getLatLng(); handler(p.lat, p.lng, false); });
}

export function showDisasterEvents(events) {
  if (overlays.events) overlays.events.remove();
  overlays.events = L.layerGroup((events || []).map((e) => L.circleMarker([e.lat, e.lon], {
    radius: 8, color: ALERT_COLORS[e.alert_level] || '#3a6ea5', weight: 2, fillOpacity: 0.35,
  }).bindPopup(`<strong>${esc(e.type_name)}</strong> · ${esc(e.alert_level || '')}<br>${esc(e.name || '')}<br>${esc(e.distance_km)} km away`)));
  overlays.events.addTo(map);
}

export function showRiverCell(cell) {
  if (overlays.river) overlays.river.remove();
  if (!cell) return;
  overlays.river = L.circle([cell.lat, cell.lon], { radius: 2500, color: '#3a6ea5', weight: 1.5, dashArray: '4 4', fillOpacity: 0.05 })
    .bindPopup(`GloFAS river cell (~${esc(cell.distance_km)} km)`).addTo(map);
}

export function invalidate() {
  if (map) setTimeout(() => map.invalidateSize(), 50);
}
