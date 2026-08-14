# Deploying to tools.onlinejourno.com on Fly.io

> **Self-hosting?** The app names (`onlinejourno-tools`, `*.fly.dev`) and the
> `tools.onlinejourno.com` domain below are the **project's** canonical deploy.
> Substitute **your own** Fly app names and domain throughout — nothing here uses
> the project's accounts or resources. Bring your own Fly account and (optional) keys.

One subdomain, multiple tools, each at its own path:

```
tools.onlinejourno.com/                    ← tools index page
tools.onlinejourno.com/forage/
tools.onlinejourno.com/web-bloat-checker/
```

Each tool is a separate Fly.io app (deploys and scales independently).
A lightweight nginx proxy app sits at `tools.onlinejourno.com` and routes
paths to the right tool.

---

## Architecture

```
tools.onlinejourno.com   (nginx proxy Fly app — ~$0.50/month)
    /forage/                 →  forage.fly.dev                 (Streamlit, ~$3/month)
    /web-bloat-checker/      →  web-bloat-checker.fly.dev      (existing app)
```

All apps use `auto_stop_machines = true` so you only pay when someone is
actually using them.

---

## Step 1 — Deploy Forage

From the `forage` directory:

```bash
# Create a persistent volume for the audit log (tracks recently audited sites)
fly volumes create crawl_data --size 1 --region sin

# Deploy the Streamlit app
fly deploy --config deploy/fly.toml
```

Your tool is now live at `https://forage.fly.dev`.

---

## Step 2 — Deploy the nginx proxy

```bash
cd deploy/proxy

fly launch --no-deploy --name onlinejourno-tools
fly deploy
```

The proxy is now live at `https://onlinejourno-tools.fly.dev`.

---

## Step 3 — Point tools.onlinejourno.com at the proxy

```bash
fly certs add tools.onlinejourno.com --app onlinejourno-tools
```

Fly will give you a DNS target. In your DNS provider, add:

```
tools.onlinejourno.com  CNAME  onlinejourno-tools.fly.dev
```

SSL is handled automatically. Takes ~5 minutes to propagate.

---

## Step 4 — Add Tools to the OnlineJourno.com nav

In your Next.js app navigation component (likely `components/Nav.tsx`
or `components/Header.tsx`), add:

```tsx
<a href="https://tools.onlinejourno.com">Tools</a>
```

For a full tools index page within the Next.js app at
`app.onlinejourno.com/tools`, create `app/tools/page.tsx` listing
all tools — but the canonical home is `tools.onlinejourno.com`.

---

## Adding a new tool later

1. Deploy the new tool as its own Fly app, e.g. `my-new-tool.fly.dev`
2. Add a location block to `deploy/proxy/nginx.conf`:

```nginx
location /my-new-tool/ {
    proxy_pass https://my-new-tool.fly.dev/;
    proxy_ssl_server_name on;
    proxy_set_header Host my-new-tool.fly.dev;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
}
```

3. Add the tool to `deploy/proxy/index.html`
4. Redeploy the proxy: `cd deploy/proxy && fly deploy`

---

## Cost estimate

| Component | Cost |
|---|---|
| nginx proxy (256MB, auto-stop) | ~$0.50/month |
| Forage (512MB, auto-stop) | ~$3–4/month |
| 1GB persistent volume (audit log) | ~$0.15/month |
| **Total** | **~$4–5/month** |

Auto-stop means machines sleep when idle and wake in ~2 seconds on first
request — you only pay for actual usage.

---

## Environment variables (optional)

Set in Fly dashboard or via `fly secrets set`:

| Variable | What it enables |
|---|---|
| `SERPAPI_KEY` | Real Google/Bing indexed page counts per section |
| `AUDIT_LOG_PATH` | Already set to `/data/audit_log.db` in Dockerfile |

---

## Updating a tool

```bash
# From the forage directory
fly deploy --config deploy/fly.toml
```
