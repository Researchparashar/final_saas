# ClipForge — Professional SaaS

Dark theme + lime green UI matching the design system.

## Run

```bash
cd final_saas
pip install -r requirements.txt
export ADMIN_PASSWORD="your-strong-password-here"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000

## Admin Security (Important)

1. **Change the password immediately**
   ```bash
   export ADMIN_PASSWORD="VeryStrongPassword123!"
   ```

2. **Use HTTPS only** in production (Cloudflare, Caddy, or Nginx).

3. **Restrict admin access**
   - Put admin behind IP allowlist if possible
   - Or use a VPN / Cloudflare Access

4. **Session security**
   - Set a long random `SECRET_KEY`
   - Sessions expire on logout

5. **Rate limiting** (recommended)
   - Add slowapi or Cloudflare rate limiting on `/admin/login`

6. **Never commit the admin password** to Git.

## Features

- Landing page matching ClipForge design
- Login page (split layout)
- Dashboard with sidebar
- Caption language selection (English, Hindi, Spanish, Auto, Translate)
- Live stream monitoring (every 10 min)
- Admin panel (full control over banner, pricing, pre-launch, tokens)
- Token / minutes system
- Google login structure ready

## Google Login

The UI has the place for Google. To enable real Google OAuth:

1. Create Google Cloud project
2. Enable OAuth + Drive API
3. Add `authlib` and configure client ID/secret
4. Replace the demo login route

Admin has full control over pricing, banner, hero text, free tokens, and pre-launch mode.
