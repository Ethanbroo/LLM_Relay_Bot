# Claude Gateway

Personal AI Gateway + Community BYOK Platform for Anthropic's Claude.

**This is a standalone project — separate from the LLM Relay system.**

## Architecture

```
claude-gateway/
├── server/          # Node.js + Express backend
│   ├── src/
│   │   ├── db/          # SQLite database initialization
│   │   ├── middleware/  # Auth (personal + BYOK) & rate limiting
│   │   ├── routes/      # API endpoints
│   │   ├── services/    # Anthropic SDK, presets engine
│   │   ├── utils/       # AES-256-GCM encryption
│   │   └── index.js     # Server entry point
│   ├── .env.example
│   └── package.json
└── mobile/          # React Native (Expo) app
    ├── src/
    │   ├── components/  # MessageBubble, ChatInput, ModelSelector
    │   ├── screens/     # Chat, QuickActions, Conversations, Settings
    │   ├── services/    # API client with SSE streaming
    │   ├── theme/       # Claude orange/white color system
    │   └── types/       # TypeScript interfaces
    ├── App.tsx
    └── package.json
```

## Quick Start

### 1. Backend Server

```bash
cd server
cp .env.example .env
# Edit .env with your Anthropic API key and generate tokens
npm install
npm run db:init
npm run dev
```

### 2. Mobile App

```bash
cd mobile
npm install
npx expo start
# Scan QR code with Expo Go on your phone
```

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | /health | None | Health check |
| GET | /api/models | None | List Claude models |
| POST | /api/chat | Bearer | Send message |
| POST | /api/chat/stream | Bearer | SSE streaming chat |
| POST | /api/quick/:action | Bearer | Quick action preset |
| GET | /api/conversations | Bearer | List conversations |
| GET | /api/conversations/:id | Bearer | Get conversation |
| DELETE | /api/conversations/:id | Bearer | Delete conversation |
| GET | /api/presets | Bearer | List presets |
| POST | /api/presets | Bearer | Create preset |
| GET | /api/usage | Bearer | Usage statistics |
| POST | /api/community/register | None | BYOK registration |

## Modes

- **Personal Mode**: Uses `PERSONAL_ACCESS_TOKEN` and server-side `ANTHROPIC_API_KEY`
- **Community BYOK Mode**: Users register with their own Anthropic key (encrypted with AES-256-GCM at rest)

## Quick Actions

Cardinal Sales: Draft Email, Pricing Response, Delivery Update, Order Confirmation, Meeting Scheduler
General: Summarize, Code Help, Project Plan

## Legal

Independent tool — not affiliated with Anthropic. BYOK model is compliant with Anthropic ToS.
