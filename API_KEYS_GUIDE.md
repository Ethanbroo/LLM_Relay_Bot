# LLM Relay Bot - API Keys Setup Guide

This guide explains what API keys you need and where to get them for the LLM Relay Bot.

---

## ✅ Currently Configured

### WordPress
**Status:** ✅ Keys Added
**Purpose:** Create/update/delete posts and pages on WordPress sites
**Keys Added:**
- Username: `friendlyconnections87@gmail.com`
- App Password: `osgo 0IFK XjL5 nMIm PpVO XyND`
- Site URL: (needs to be configured)

**Configuration:**
- Environment variables in `.env`:
  ```bash
  LLM_RELAY_SECRET_WORDPRESS_USERNAME=friendlyconnections87@gmail.com
  LLM_RELAY_SECRET_WORDPRESS_APP_PASSWORD=osgo 0IFK XjL5 nMIm PpVO XyND
  LLM_RELAY_SECRET_WORDPRESS_SITE_URL=https://your-wordpress-site.com
  ```

**Actions Available:**
- `wordpress.create_post` - Create new blog posts
- `wordpress.update_post` - Update existing posts
- `wordpress.delete_post` - Delete posts
- `wordpress.get_post` - Read post content
- `wordpress.list_posts` - List all posts
- `wordpress.create_page` - Create new pages
- `wordpress.update_page` - Update existing pages

### Unsplash
**Status:** ✅ Keys Added
**Purpose:** Search and download free stock photos
**Keys Added:**
- Application ID: `870199`
- Access Key: `XjmpBYO7f43S3Ve70RvUJrijleBB3GcwaF0_rCgQleg`

**Configuration:**
- Environment variables in `.env`:
  ```bash
  LLM_RELAY_SECRET_UNSPLASH_APPLICATION_ID=870199
  LLM_RELAY_SECRET_UNSPLASH_ACCESS_KEY=XjmpBYO7f43S3Ve70RvUJrijleBB3GcwaF0_rCgQleg
  ```

**Actions Available:**
- `unsplash.search_photos` - Search for photos by keyword
- `unsplash.get_photo` - Get specific photo details
- `unsplash.get_random_photo` - Get random photo(s)
- `unsplash.download_photo` - Download photo with tracking
- `unsplash.track_download` - Track download (required by API)

---

## 🔴 Required for Full Functionality

### Phase 6 & Phase 8: LLM API Keys (CRITICAL)

#### 1. OpenAI (ChatGPT) - **REQUIRED**
**Purpose:** Used in Phase 6 orchestration for multi-LLM consensus
**Where to Get:** https://platform.openai.com/api-keys
**Cost:** Pay-as-you-go, ~$0.002 per 1K tokens
**Models Used:** gpt-4, gpt-3.5-turbo

```bash
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Setup Instructions:**
1. Go to https://platform.openai.com/api-keys
2. Sign in or create an account
3. Click "Create new secret key"
4. Copy the key (starts with `sk-proj-` or `sk-`)
5. Add to `.env` file

#### 2. Anthropic (Claude) - **REQUIRED**
**Purpose:** Used in Phase 6 orchestration AND Phase 8 text transformation
**Where to Get:** https://console.anthropic.com/
**Cost:** Pay-as-you-go, ~$0.008 per 1K tokens
**Models Used:** claude-3-5-sonnet-20241022

```bash
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Setup Instructions:**
1. Go to https://console.anthropic.com/
2. Sign in or create an account
3. Navigate to "API Keys" section
4. Click "Create Key"
5. Copy the key (starts with `sk-ant-`)
6. Add to `.env` file

#### 3. Google AI (Gemini) - **REQUIRED**
**Purpose:** Used in Phase 6 orchestration for multi-LLM consensus
**Where to Get:** https://makersuite.google.com/app/apikey
**Cost:** Free tier available, then pay-as-you-go
**Models Used:** gemini-pro

```bash
GOOGLE_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Setup Instructions:**
1. Go to https://makersuite.google.com/app/apikey
2. Sign in with Google account
3. Click "Create API Key"
4. Copy the key (starts with `AIzaSy`)
5. Add to `.env` file

#### 4. DeepSeek - **REQUIRED**
**Purpose:** Used in Phase 6 orchestration for multi-LLM consensus
**Where to Get:** https://platform.deepseek.com/
**Cost:** Very low cost, ~$0.0001 per 1K tokens
**Models Used:** deepseek-chat

```bash
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Setup Instructions:**
1. Go to https://platform.deepseek.com/
2. Sign up for an account
3. Navigate to API keys section
4. Create new API key
5. Copy the key
6. Add to `.env` file

---

## 🟡 Recommended Additional Connectors

### Google Workspace (Docs, Drive, Sheets)
**Purpose:** Create/edit Google Docs, manage Drive files, update spreadsheets
**Where to Get:** Google Cloud Console
**Setup:** OAuth 2.0 flow required

```bash
LLM_RELAY_SECRET_GOOGLE_DOCS_CLIENT_ID=your_client_id
LLM_RELAY_SECRET_GOOGLE_DOCS_CLIENT_SECRET=your_client_secret
LLM_RELAY_SECRET_GOOGLE_DOCS_REFRESH_TOKEN=your_refresh_token
```

**Instructions:**
1. Go to https://console.cloud.google.com/
2. Create new project or select existing
3. Enable Google Docs API, Drive API, Sheets API
4. Create OAuth 2.0 credentials
5. Follow OAuth flow to get refresh token
6. **Complexity:** Medium (requires OAuth setup)

### Slack
**Purpose:** Send messages, create channels, manage workspace
**Where to Get:** https://api.slack.com/apps
**Cost:** Free

```bash
LLM_RELAY_SECRET_SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxxx
LLM_RELAY_SECRET_SLACK_SIGNING_SECRET=xxxxxxxxxxxxx
```

**Instructions:**
1. Go to https://api.slack.com/apps
2. Create new app
3. Add bot token scopes (chat:write, channels:read, etc.)
4. Install app to workspace
5. Copy bot token and signing secret

### Airtable
**Purpose:** Manage databases, create/update records
**Where to Get:** https://airtable.com/create/tokens
**Cost:** Free tier available

```bash
LLM_RELAY_SECRET_AIRTABLE_API_KEY=patxxxxxxxxxxxxx
LLM_RELAY_SECRET_AIRTABLE_BASE_ID=appxxxxxxxxxxxxx
```

**Instructions:**
1. Go to https://airtable.com/create/tokens
2. Create personal access token
3. Grant necessary scopes (data.records:read, data.records:write)
4. Copy token and base ID from your base URL

### Notion
**Purpose:** Manage Notion databases and pages
**Where to Get:** https://www.notion.so/my-integrations
**Cost:** Free

```bash
LLM_RELAY_SECRET_NOTION_API_KEY=secret_xxxxxxxxxxxxx
```

**Instructions:**
1. Go to https://www.notion.so/my-integrations
2. Create new integration
3. Grant necessary capabilities
4. Copy the "Internal Integration Token"
5. Share relevant pages/databases with integration

### SendGrid (Email)
**Purpose:** Send transactional emails
**Where to Get:** https://sendgrid.com/
**Cost:** Free tier: 100 emails/day

```bash
LLM_RELAY_SECRET_SENDGRID_API_KEY=SG.xxxxxxxxxxxxx
```

**Instructions:**
1. Sign up at https://sendgrid.com/
2. Navigate to Settings → API Keys
3. Create new API key with "Full Access" or "Mail Send" permissions
4. Copy the key (starts with `SG.`)

### Twilio (SMS)
**Purpose:** Send SMS messages
**Where to Get:** https://www.twilio.com/
**Cost:** Pay-as-you-go, ~$0.0075 per SMS

```bash
LLM_RELAY_SECRET_TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxx
LLM_RELAY_SECRET_TWILIO_AUTH_TOKEN=xxxxxxxxxxxxx
LLM_RELAY_SECRET_TWILIO_PHONE_NUMBER=+1234567890
```

**Instructions:**
1. Sign up at https://www.twilio.com/
2. Get a phone number (free trial available)
3. Find Account SID and Auth Token in console
4. Copy credentials

### GitHub
**Purpose:** Manage repositories, issues, pull requests
**Where to Get:** https://github.com/settings/tokens
**Cost:** Free

```bash
LLM_RELAY_SECRET_GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
```

**Instructions:**
1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes (repo, workflow, etc.)
4. Copy token (starts with `ghp_`)

### Stripe
**Purpose:** Process payments, manage subscriptions
**Where to Get:** https://dashboard.stripe.com/apikeys
**Cost:** 2.9% + $0.30 per transaction

```bash
LLM_RELAY_SECRET_STRIPE_API_KEY=sk_test_xxxxxxxxxxxxx
```

**Instructions:**
1. Sign up at https://stripe.com/
2. Go to Developers → API keys
3. Copy secret key (use test key for development)

### Shopify
**Purpose:** Manage e-commerce store, products, orders
**Where to Get:** Shopify Admin Panel
**Cost:** Part of Shopify subscription

```bash
LLM_RELAY_SECRET_SHOPIFY_API_KEY=xxxxxxxxxxxxx
LLM_RELAY_SECRET_SHOPIFY_API_SECRET=xxxxxxxxxxxxx
LLM_RELAY_SECRET_SHOPIFY_SHOP_NAME=your-shop.myshopify.com
```

**Instructions:**
1. Log into Shopify admin
2. Go to Apps → Manage private apps (or create custom app)
3. Create new app with necessary permissions
4. Copy API credentials

---

## 🔒 Security Best Practices

### DO:
- ✅ Store all API keys in `.env` file (never in code)
- ✅ Add `.env` to `.gitignore` (already done)
- ✅ Use different keys for development, staging, production
- ✅ Rotate keys periodically
- ✅ Use minimum necessary permissions for each key
- ✅ Monitor API usage regularly

### DON'T:
- ❌ Commit API keys to git
- ❌ Share keys in chat/email
- ❌ Use production keys in development
- ❌ Hardcode keys in source code
- ❌ Give keys unnecessary permissions
- ❌ Ignore usage alerts

---

## 📊 Priority Matrix

| Service | Priority | Cost | Complexity | Use Case |
|---------|----------|------|------------|----------|
| **OpenAI** | 🔴 Critical | Medium | Easy | Phase 6 orchestration |
| **Anthropic** | 🔴 Critical | Medium | Easy | Phase 6 & 8 |
| **Google AI** | 🔴 Critical | Low/Free | Easy | Phase 6 orchestration |
| **DeepSeek** | 🔴 Critical | Very Low | Easy | Phase 6 orchestration |
| WordPress | ✅ Added | Free | Easy | Content management |
| Unsplash | ✅ Added | Free | Easy | Stock photos |
| Slack | 🟡 High | Free | Easy | Team communication |
| SendGrid | 🟡 High | Free tier | Easy | Email automation |
| Notion | 🟡 Medium | Free | Medium | Note/database management |
| GitHub | 🟡 Medium | Free | Easy | Code management |
| Airtable | 🟢 Low | Free tier | Medium | Database management |
| Twilio | 🟢 Low | Pay/use | Easy | SMS notifications |
| Stripe | 🟢 Low | % fee | Medium | Payment processing |
| Google Workspace | 🟢 Low | Free | Hard | Document editing |

---

## 🚀 Quick Start

### Minimum Setup (Testing)
1. Add the 4 LLM API keys (OpenAI, Anthropic, Google AI, DeepSeek)
2. Configure your WordPress site URL in `.env`
3. Run the bot in stub mode

### Production Setup
1. Get all 4 LLM API keys
2. Configure WordPress site URL
3. Add any additional connectors you need
4. Test in development environment first
5. Use separate keys for production

---

## 📝 Notes

- **Stub Mode:** The bot works in stub mode without real API keys (set `stub_mode: true` in config)
- **Secrets Redaction:** All secrets are automatically redacted from Phase 3 audit logs
- **Environment Prefix:** All connector secrets must use `LLM_RELAY_SECRET_` prefix
- **API Rate Limits:** Be aware of rate limits for each service
- **Costs:** Monitor API usage to avoid unexpected charges

---

## 🆘 Troubleshooting

### "Invalid API Key" Error
- Verify key is correct (no extra spaces)
- Check key hasn't expired
- Ensure key has necessary permissions
- Try regenerating the key

### "Rate Limit Exceeded"
- Wait for rate limit to reset
- Upgrade to higher tier if available
- Implement request throttling

### "Authentication Failed"
- Check environment variable prefix is correct
- Verify `.env` file is being loaded
- Ensure no typos in variable names

---

## 📞 Support

For additional help:
- Check connector-specific documentation
- Review API provider's documentation
- Check Phase 5 connector implementation in `connectors/` directory
- Review audit logs in Phase 3 for error details
