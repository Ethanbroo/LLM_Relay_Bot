# Instagram Pipeline - Complete API Keys Guide

## 🔑 Required API Keys & Credentials

This document lists all API keys and credentials needed for the Instagram AI Model Pipeline, organized by stage.

---

## Stage 0: Character Foundation (One-Time Setup)

### 1. fal.ai API Key ✅ REQUIRED
**Purpose**: LoRA training ($3-5 per character) and image generation ($0.055 per image)

**How to Get**:
1. Visit https://fal.ai
2. Sign up for an account
3. Go to Dashboard → API Keys
4. Create new API key
5. Copy the key (starts with `fal_...`)

**Add to `.env`**:
```bash
FAL_API_KEY=your_fal_api_key_here
```

**Cost**: Pay-as-you-go pricing
- LoRA Training: ~$3-5 per character (one-time)
- Image Generation (Flux + LoRA): $0.055 per image
- Estimated monthly for 12 posts: ~$0.66

---

## Stage 2: Post Intent Construction

### 2. Anthropic API Key (Claude) ✅ ALREADY HAVE
**Purpose**: LLM-based content brief expansion

**Status**: Your bot already uses Claude for blog generation via `llm_integration/claude_client.py`

**Environment Variable**: `ANTHROPIC_API_KEY` (already in your `.env`)

**Cost**: Minimal - brief expansion uses ~500-1000 tokens per post
- Estimated monthly for 12 posts: ~$0.05-0.10

---

## Stage 4: Quality Gates

### 3. OpenAI API Key ✅ REQUIRED (for Tier 4 only)
**Purpose**: GPT-4o-mini vision model for final quality review (Tier 4 gate)

**How to Get**:
1. Visit https://platform.openai.com
2. Sign up / Log in
3. Go to API Keys section
4. Create new secret key
5. Copy the key (starts with `sk-...`)

**Add to `.env`**:
```bash
OPENAI_API_KEY=your_openai_key_here
```

**Note**: Tier 4 is OPTIONAL. You can run quality gates with just Tiers 1-3 (all free):
- Tier 1: Heuristic checks (free)
- Tier 2: CLIP alignment (free)
- Tier 3: Identity consistency (free) **← THE CRITICAL GATE**
- Tier 4: LLM vision (GPT-4o-mini, $0.002 per image) ← OPTIONAL

**Cost**: $0.002 per image reviewed (only if Tier 4 enabled)
- Estimated monthly for 12 posts with Tier 4: ~$0.024

**Recommendation**: Start without Tier 4. Enable selectively for hero shots only.

---

## Stage 6: Instagram Publishing

### 4. Instagram Graph API Access ✅ REQUIRED FOR PUBLISHING

Instagram publishing requires multiple steps to set up. Here's the complete guide:

#### A. Facebook Developer Account
1. Go to https://developers.facebook.com
2. Sign up / Log in
3. Create a new app or use existing
4. Select "Business" as app type

#### B. Instagram Business Account
**Requirements**:
- Instagram account must be a **Business** or **Creator** account (not personal)
- Must be linked to a Facebook Page

**How to Convert to Business Account**:
1. Open Instagram app
2. Go to Settings → Account
3. Tap "Switch to Professional Account"
4. Choose "Business" or "Creator"
5. Link to your Facebook Page

#### C. Get Access Token & Account ID

**Method 1: Using Graph API Explorer (Easiest)**

1. Go to https://developers.facebook.com/tools/explorer
2. Select your app from dropdown
3. Click "Generate Access Token"
4. Grant permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
   - `pages_show_list`
5. Copy the **Short-Lived Access Token**
6. Convert to **Long-Lived Token** (see below)

**Method 2: Using OAuth Flow (Production)**

See Meta's official guide: https://developers.facebook.com/docs/instagram-basic-display-api/getting-started

#### D. Convert to Long-Lived Access Token

Short-lived tokens expire in 1 hour. You need a long-lived token (60 days):

```bash
curl -X GET "https://graph.facebook.com/v18.0/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=SHORT_LIVED_TOKEN"
```

Response:
```json
{
  "access_token": "LONG_LIVED_TOKEN",
  "token_type": "bearer",
  "expires_in": 5184000
}
```

#### E. Get Instagram Business Account ID

With your long-lived token:

```bash
curl -X GET "https://graph.facebook.com/v18.0/me/accounts?access_token=YOUR_LONG_LIVED_TOKEN"
```

Find your Facebook Page, then get Instagram account:

```bash
curl -X GET "https://graph.facebook.com/v18.0/PAGE_ID?fields=instagram_business_account&access_token=YOUR_LONG_LIVED_TOKEN"
```

Response:
```json
{
  "instagram_business_account": {
    "id": "17841123456789012"  ← This is what you need
  }
}
```

#### F. Add to .env

```bash
# Instagram Publishing
INSTAGRAM_ACCESS_TOKEN=your_long_lived_access_token_here
INSTAGRAM_BUSINESS_ACCOUNT_ID=17841123456789012
INSTAGRAM_APP_ID=your_facebook_app_id
INSTAGRAM_APP_SECRET=your_facebook_app_secret
```

**Cost**: Free (Meta Graph API has no cost for posting)

---

## Optional: Third-Party Providers (Fallback)

### 5. Replicate API Token (OPTIONAL)
**Purpose**: Fallback image generation provider if fal.ai is down

**How to Get**:
1. Visit https://replicate.com
2. Sign up
3. Go to Account → API Tokens
4. Copy token

**Add to `.env`**:
```bash
REPLICATE_API_TOKEN=your_replicate_token_here
```

**When to Use**:
- Automatic fallback via `ProviderRegistry`
- Only used if fal.ai fails

**Cost**: Similar to fal.ai (~$0.05-0.06 per image with LoRA)

---

## Summary: What You Need RIGHT NOW

### Minimum to Get Started (Stages 0-5)
1. ✅ **fal.ai API Key** - Image generation & LoRA training
2. ✅ **Anthropic API Key** - Already have (for Claude)
3. ⚠️ **OpenAI API Key** - Optional (only for Tier 4 quality gate)

**Total Setup Time**: ~10 minutes
**Total Cost (without Tier 4)**: ~$0.055 per image + $3-5 one-time LoRA training

### For Publishing (Stage 6)
4. ✅ **Instagram Business Account** + **Meta Graph API Setup**
   - Facebook Developer Account
   - Instagram Business Account linked to Facebook Page
   - Long-lived access token
   - Business Account ID

**Total Setup Time**: ~30-45 minutes (if new to Meta Graph API)
**Total Cost**: Free

---

## Complete .env Example

```bash
# === Core LLM Relay Bot (Already Configured) ===
ANTHROPIC_API_KEY=your_claude_key_here

# === Instagram Pipeline - Image Generation ===
FAL_API_KEY=your_fal_key_here
REPLICATE_API_TOKEN=your_replicate_token_here  # Optional fallback

# === Instagram Pipeline - Quality Gates ===
OPENAI_API_KEY=your_openai_key_here  # Optional - only for Tier 4

# === Instagram Pipeline - Publishing ===
INSTAGRAM_ACCESS_TOKEN=your_long_lived_token_here
INSTAGRAM_BUSINESS_ACCOUNT_ID=17841123456789012
INSTAGRAM_APP_ID=your_facebook_app_id
INSTAGRAM_APP_SECRET=your_facebook_app_secret

# === Instagram Pipeline - Configuration ===
DEFAULT_CHARACTER_ID=aurora_v1
INSTAGRAM_OUTPUT_DIR=output/instagram
INSTAGRAM_AUTO_POST_ENABLED=false  # Set true to auto-post approved content
INSTAGRAM_DISCLOSURE_LABEL=true    # Always true per platform policy
```

---

## Verification Commands

Once you have all keys, verify they work:

### Test fal.ai Connection
```python
import fal_client
fal_client.list_models()  # Should return list of available models
```

### Test OpenAI Connection (if using Tier 4)
```python
from openai import OpenAI
client = OpenAI(api_key="your_key")
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Test"}]
)
print(response.choices[0].message.content)
```

### Test Instagram Connection
```python
from connectors.instagram_pipeline.staging.instagram_poster import InstagramPoster

poster = InstagramPoster(
    access_token="your_token",
    instagram_business_account_id="your_id"
)

is_valid = poster.verify_connection()
print(f"Instagram connection: {'✓ Valid' if is_valid else '✗ Failed'}")
```

---

## Cost Breakdown (Monthly Estimate for 12 Posts)

| Service | Cost per Post | Monthly (12 posts) | Notes |
|---------|---------------|-------------------|-------|
| **fal.ai Image Generation** | $0.055-0.165 | $0.66-1.98 | Depends on format (single/carousel) |
| **fal.ai LoRA Training** | $3-5 one-time | $0 | Only once per character |
| **Claude (Anthropic)** | ~$0.005-0.01 | ~$0.06-0.12 | Brief expansion |
| **OpenAI Tier 4** (optional) | $0.002 | $0.024 | Only if enabled |
| **Instagram Publishing** | Free | $0 | Meta Graph API |
| **TOTAL (without Tier 4)** | **~$0.06** | **~$0.72-2.10** | Extremely low cost |

**Note**: Main variable cost is single image vs. carousel (3x images = 3x cost)

---

## Security Best Practices

1. **Never commit API keys to git**
   - Already configured in `.gitignore`
   - Use `.env` file (already in `.gitignore`)

2. **Rotate Instagram tokens every 60 days**
   - Long-lived tokens expire after 60 days
   - Set calendar reminder to refresh

3. **Use environment-specific keys**
   - Development keys for testing
   - Production keys for real posting

4. **Monitor API usage**
   - fal.ai: Dashboard → Usage
   - OpenAI: Platform → Usage
   - Instagram: Graph API Explorer → Debug Mode

---

## What If I Don't Have...

**Don't have Instagram Business Account yet?**
- Can still develop and test Stages 0-5
- Use staging system to save posts locally
- Manually post from staging directory
- Set up publishing later when ready

**Don't want to use OpenAI (Tier 4)?**
- Completely optional!
- Tiers 1-3 (all free) are highly effective
- Tier 3 (identity check) is the critical gate
- Tier 4 is just extra assurance for hero shots

**Don't want to use fal.ai?**
- Switch to Replicate via `ProviderRegistry`
- Or implement your own provider using `AbstractAssetGenerator`
- Provider abstraction makes this trivial

---

## Next Steps

1. **Get fal.ai API key** (10 min)
2. **Optionally get OpenAI key** if you want Tier 4 (5 min)
3. **Test image generation** with sample character
4. **When ready to publish, set up Instagram Business + Meta Graph API** (30-45 min)

You can start generating content RIGHT NOW with just fal.ai + Claude (which you already have)!
