// Yarbis LLM gateway — Puter Worker (puter.js)
//
// Pasarela HTTPS para que Yarbis (Python) use puter.ai.chat (500+ modelos, con
// tool-calling) sin API keys de pago. Yarbis hace POST con {model, messages,
// tools, temperature, max_tokens} en formato OpenAI y recibe {message:{content,
// tool_calls}}.
//
// Se llama por HTTP plano (no puter.workers.exec), asi que usamos `me.puter`
// (recursos del DUENO del worker = tu cuenta Puter, user-pays). Un secreto
// compartido (header X-Puter-Secret) evita que terceros con la URL gasten tus
// creditos. El placeholder de abajo se reemplaza al desplegar (Yarbis inyecta el
// secreto SOLO en la linea `const SECRET = ...`).
//
// FAIL-CLOSED: si el secreto no se configuro (sigue siendo el placeholder), el
// worker RECHAZA todo (503) en vez de quedar abierto. Asi, aunque el despliegue
// sea incorrecto, nunca queda expuesto para que terceros gasten tus creditos.
//
// Powered by Puter — https://developer.puter.com

const SECRET = "__YARBIS_WORKER_SECRET__";
const PLACEHOLDER = "__YARBIS_WORKER_SECRET__";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Devuelve una Response de error si la peticion no esta autorizada, o null si OK.
function authError(request) {
  if (!SECRET || SECRET === PLACEHOLDER) {
    return json({ error: "worker sin secreto configurado" }, 503);
  }
  if (request.headers.get("X-Puter-Secret") !== SECRET) {
    return json({ error: "unauthorized" }, 401);
  }
  return null;
}

async function handleChat({ request }) {
  const denied = authError(request);
  if (denied) return denied;

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ error: "invalid JSON body" }, 400);
  }

  const { model, messages, tools, temperature, max_tokens } = body || {};
  if (!Array.isArray(messages) || messages.length === 0) {
    return json({ error: "messages[] required" }, 400);
  }

  const options = {};
  if (model) options.model = model;
  if (Array.isArray(tools) && tools.length) options.tools = tools;
  if (temperature != null) options.temperature = temperature;
  if (max_tokens != null) options.max_tokens = max_tokens;

  try {
    const resp = await me.puter.ai.chat(messages, options);
    // resp es un ChatResponse: { message: { content, tool_calls } }
    return { message: resp && resp.message ? resp.message : { content: String(resp) } };
  } catch (error) {
    return json({ error: (error && error.message) || String(error) }, 500);
  }
}

router.post("/chat", handleChat);
router.post("/*path", handleChat); // aceptar POST en cualquier ruta

// /health es publico (solo estado, no toca la IA ni filtra secretos).
router.get("/health", async () => json({ status: "ok", service: "yarbis-llm-gateway" }));
router.get("/*page", async () => json({ status: "ok", service: "yarbis-llm-gateway" }));
