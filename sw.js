// Lets uploads keep running when the app is minimized or closed (Chrome on Android).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

async function tell(msg) {
  const list = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
  list.forEach(c => c.postMessage(msg));
}

self.addEventListener("backgroundfetchsuccess", e => {
  const reg = e.registration;
  e.waitUntil((async () => {
    let ok = true;
    try {
      const records = await reg.matchAll();
      for (const r of records) {
        try { const res = await r.responseReady; if (!res.ok) ok = false; }
        catch (err) { ok = false; }
      }
    } catch (err) { ok = false; }
    await e.updateUI({ title: ok ? "Upload complete ✓" : "Upload failed" });
    await tell({ type: "bg-done", ok, id: reg.id });
  })());
});

self.addEventListener("backgroundfetchfail", e => {
  e.waitUntil((async () => {
    await e.updateUI({ title: "Upload failed" });
    await tell({ type: "bg-done", ok: false, id: e.registration.id });
  })());
});

self.addEventListener("backgroundfetchclick", e => {
  e.waitUntil(self.clients.openWindow("/"));
});
