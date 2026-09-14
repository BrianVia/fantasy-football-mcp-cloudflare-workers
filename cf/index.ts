// Bearer-protected front door for the Python FastMCP server running in a Cloudflare Container.
import { Container, getContainer } from "@cloudflare/containers";

type Env = {
  FANTASY: DurableObjectNamespace<FantasyContainer>;
  MCP_BEARER: string;
  YAHOO_CLIENT_ID: string;
  YAHOO_CLIENT_SECRET: string;
  YAHOO_ACCESS_TOKEN: string;
  YAHOO_REFRESH_TOKEN: string;
  YAHOO_GUID: string;
};

export class FantasyContainer extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "15m";

  constructor(ctx: DurableObjectState<Env>, env: Env) {
    super(ctx, env);
    this.envVars = {
      HOST: "0.0.0.0",
      PORT: "8000",
      YAHOO_CLIENT_ID: env.YAHOO_CLIENT_ID ?? "",
      YAHOO_CLIENT_SECRET: env.YAHOO_CLIENT_SECRET ?? "",
      YAHOO_ACCESS_TOKEN: env.YAHOO_ACCESS_TOKEN ?? "",
      YAHOO_REFRESH_TOKEN: env.YAHOO_REFRESH_TOKEN ?? "",
      YAHOO_GUID: env.YAHOO_GUID ?? "",
    };
  }
}

function timingSafeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  if (x.byteLength !== y.byteLength) return false;
  return (crypto.subtle as any).timingSafeEqual(x, y);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") return Response.json({ ok: true });
    // Yahoo OAuth redirect target: just show the one-time code so it can be pasted into the token exchange.
    if (url.pathname === "/callback") {
      const code = url.searchParams.get("code") ?? "(no code in URL)";
      return new Response(`Yahoo code:\n\n${code}\n\nCopy it and paste it back to the agent.`, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    if (url.pathname !== "/mcp") return new Response("Not found", { status: 404 });

    const auth = request.headers.get("authorization") ?? "";
    const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    if (!env.MCP_BEARER || !timingSafeEqual(token, env.MCP_BEARER)) {
      return new Response("Unauthorized", { status: 401 });
    }

    // ponytail: single container instance ("main"); it's a single-user server.
    return getContainer(env.FANTASY, "main").fetch(request);
  },
};
