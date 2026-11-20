# Security Guide — ClipForge

## Admin Security

1. **Strong password**
   ```bash
   export ADMIN_PASSWORD="UseAVeryLongRandomPasswordHere123!"
   ```

2. **Secret key for sessions**
   ```bash
   export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
   ```

3. **HTTPS only** — Never run admin over plain HTTP in production.

4. **Restrict access**
   - Use Cloudflare Access or IP allowlist for `/admin`
   - Or put it behind a VPN

5. **Password hashing**
   - User passwords are hashed with salt + SHA-256
   - Never store plain passwords

6. **Rate limiting** (recommended)
   - Add Cloudflare or slowapi on login and admin routes

## Payments (PayPal / Razorpay)

Structure is ready for later integration:
- Plans are stored in settings (admin can change prices)
- Subscription logic can be added by checking `user.plan` and `plan_expires_at`
- Webhooks from PayPal/Razorpay will update the plan

## Referral System

- Controlled from Admin → Referral tab
- Enable/disable
- Set bonus tokens
- Button text
- Users get unique codes (to be fully wired in next update)

## Coupons

- Admin can create coupon codes
- Give bonus tokens or discount %
- Track usage

Change the admin password before launching publicly.
