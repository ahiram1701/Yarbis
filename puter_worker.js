// Yarbis LLM gateway — Puter Worker (puter.js)
//
// Pasarela HTTPS para que Yarbis (Python) use puter.ai.chat (500+ modelos, con
// tool-calling) sin API keys de pago. Yarbis hace POST con {model, messages,
// tools, temperature, max_tokens} en formato OpenAI y recibe {message:{content,
// tool_calls}}.
//
// Se llama por HTTP plano (no puter.workers.exec), asi que usamos `me.puter`
// (recursos del DUEÑO del worker = tu cuenta Puter, user-pays). Un secreto
// compartido (header X-Puter-Secret) evita que terceros con la URL gasten tus
// creditos. Reemplaza __YARBIS_WORKER_SECRET__ al desplegar (lo hace Yarbis).
//
// Powered by Puter — https://developer.puter.com

const SECRET = "__YARBIS_WORKER_SECRET__";

async function handleChat({ request }) {
  if (SECRET && SECRET !== "__YARBIS_WORKER_SECRET__") {
    if (request.headers.get("X-Puter-Secret") !== SECRET) {
      return new Response(JSON.stringify({ error: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return new Response(JSON.stringify({ error: "invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const { model, messages, tools, temperature, max_tokens } = body || {};
  if (!Array.isArray(messages) || messages.length === 0) {
    return new Response(JSON.stringify({ error: "messages[] required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
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
    return new Response(
      JSON.stringify({ error: (error && error.message) || String(error) }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}

router.post("/chat", handleChat);
router.post("/*path", handleChat); // aceptar POST en cualquier ruta

router.get("/health", async () => ({ status: "ok", service: "yarbis-llm-gateway" }));
router.get("/*page", async () => ({ status: "ok", service: "yarbis-llm-gateway" }));
