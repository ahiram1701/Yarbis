// Yarbis mesh relay — Puter Worker (puter.js)
//
// Convierte Puter en un NODO RELAY siempre-encendido y publicamente alcanzable:
// un buzon + roster para que los nodos de Yarbis (detras de NAT, en cualquier
// dispositivo) se encuentren y se pasen mensajes. NO corre codigo de agente: es
// un buzon tonto que guarda y reenvia, gateado por un SECRETO DE RED compartido.
//
// Modelo de confianza (a proposito, simple): todos los nodos enrolados comparten
// el mismo secreto de red y por tanto se confian mutuamente. Enrola SOLO nodos
// que controlas. No hay auto-propagacion: un humano corre el bootstrap en cada
// nodo y aporta el secreto.
//
// Almacen: me.puter.kv (del DUEÑO del worker = tu cuenta Puter).
//   ym:node:<node_id>            -> registro de roster {node_id,name,capabilities,...}
//   ym:msg:<to_node>:<ts>-<rand> -> sobre de mensaje, con TTL
//
// El placeholder de secreto de abajo se reemplaza al desplegar (Yarbis lo inyecta).
//
// Powered by Puter — https://developer.puter.com

const NET_SECRET = "__YARBIS_NET_SECRET__";
const MSG_TTL_SECONDS = 3 * 24 * 3600;   // los mensajes sin recoger expiran en 3 dias
const NODE_TTL_SECONDS = 30 * 24 * 3600;  // un nodo que no se asoma en 30 dias cae del roster
const MAX_POLL = 100;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function authError(request) {
  if (!NET_SECRET || NET_SECRET === "__YARBIS_NET_SECRET__") {
    return json({ ok: false, error: "relay sin secreto configurado" }, 503);
  }
  if (request.headers.get("X-Yarbis-Net") !== NET_SECRET) {
    return json({ ok: false, error: "secreto de red invalido" }, 401);
  }
  return null;
}

function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}

router.get("/health", async () => {
  return json({ ok: true, service: "yarbis-mesh-relay" });
});

router.post("/mesh/enroll", async ({ request }) => {
  const denied = authError(request);
  if (denied) return denied;

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: "JSON invalido" }, 400);
  }
  const nodeId = String((body && body.node_id) || "").trim();
  if (!nodeId) return json({ ok: false, error: "node_id requerido" }, 400);

  const record = {
    node_id: nodeId,
    name: String((body && body.name) || nodeId).slice(0, 200),
    capabilities: (body && body.capabilities) || {},
    enrolled_at: new Date().toISOString(),
    last_seen: new Date().toISOString(),
  };
  await me.puter.kv.set("ym:node:" + nodeId, record, nowSeconds() + NODE_TTL_SECONDS);
  return json({ ok: true, node_id: nodeId });
});

router.post("/mesh/send", async ({ request }) => {
  const denied = authError(request);
  if (denied) return denied;

  let envelope;
  try {
    envelope = await request.json();
  } catch (e) {
    return json({ ok: false, error: "JSON invalido" }, 400);
  }
  const toNode = String((envelope && envelope.to_node) || "").trim();
  if (!toNode) return json({ ok: false, error: "to_node requerido" }, 400);

  const key = "ym:msg:" + toNode + ":" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  envelope.relay_key = key;
  await me.puter.kv.set(key, envelope, nowSeconds() + MSG_TTL_SECONDS);
  return json({ ok: true, id: key });
});

router.get("/mesh/poll", async ({ request }) => {
  const denied = authError(request);
  if (denied) return denied;

  const url = new URL(request.url);
  const nodeId = String(url.searchParams.get("node") || "").trim();
  if (!nodeId) return json({ ok: false, error: "node requerido" }, 400);
  let max = parseInt(url.searchParams.get("max") || "50", 10);
  if (!Number.isFinite(max) || max <= 0) max = 50;
  max = Math.min(max, MAX_POLL);

  const pairs = await me.puter.kv.list("ym:msg:" + nodeId + ":*", true);
  const slice = (pairs || []).slice(0, max);
  const messages = [];
  for (const pair of slice) {
    messages.push(pair.value);
    await me.puter.kv.del(pair.key); // entregar es borrar: cada mensaje una sola vez
  }

  // Refresca el "visto por ultima vez" del nodo si esta en el roster.
  const node = await me.puter.kv.get("ym:node:" + nodeId);
  if (node) {
    node.last_seen = new Date().toISOString();
    await me.puter.kv.set("ym:node:" + nodeId, node, nowSeconds() + NODE_TTL_SECONDS);
  }
  return json({ ok: true, messages });
});

router.get("/mesh/roster", async ({ request }) => {
  const denied = authError(request);
  if (denied) return denied;

  const pairs = await me.puter.kv.list("ym:node:*", true);
  const nodes = (pairs || []).map((pair) => pair.value);
  return json({ ok: true, nodes });
});

router.post("/mesh/leave", async ({ request }) => {
  const denied = authError(request);
  if (denied) return denied;

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: "JSON invalido" }, 400);
  }
  const nodeId = String((body && body.node_id) || "").trim();
  if (!nodeId) return json({ ok: false, error: "node_id requerido" }, 400);
  await me.puter.kv.del("ym:node:" + nodeId);
  return json({ ok: true, node_id: nodeId });
});

router.get("/*page", async () => json({ ok: false, error: "ruta desconocida" }, 404));
router.post("/*page", async () => json({ ok: false, error: "ruta desconocida" }, 404));
