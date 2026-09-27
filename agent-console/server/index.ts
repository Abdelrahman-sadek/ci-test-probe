import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { readFileSync } from "node:fs";
import { createApp } from "./app.js";
import { openDatabase } from "./db.js";

const production = process.env.NODE_ENV === "production";
const adminPassword = process.env.ADMIN_PASSWORD ?? (production ? undefined : "demo");
if (adminPassword === undefined || (production && adminPassword.length < 8)) {
  console.error("ADMIN_PASSWORD must be set (at least 8 characters) in production.");
  process.exit(1);
}

const db = await openDatabase(process.env.DATABASE_PATH ?? "data/console.db");
const app = await createApp({ db, adminPassword, secureCookies: production && process.env.INSECURE_COOKIES !== "1" });

if (production) {
  // The built UI; any non-API path falls back to index.html for client-side routing.
  const index = readFileSync("dist/web/index.html", "utf8");
  app.use("/*", serveStatic({ root: "./dist/web" }));
  app.get("*", (c) => (c.req.path.startsWith("/api/") ? c.json({ error: "Not found." }, 404) : c.html(index)));
}

const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? "127.0.0.1";
serve({ fetch: app.fetch, port, hostname: host }, () => console.log(`agent-console listening on http://${host}:${port}${production ? "" : " (dev password: demo)"}`));
