import { ChatResponse, Conversation, Model, Preset, StreamEvent, UsageStats } from '../types';

const DEFAULT_SERVER_URL = 'http://192.168.4.138:3000';

let serverUrl = DEFAULT_SERVER_URL;
let accessToken = '';

export function configure(url: string, token: string) {
  serverUrl = url.replace(/\/$/, '');
  accessToken = token;
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) {
    h['Authorization'] = `Bearer ${accessToken}`;
  }
  return h;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${serverUrl}${path}`, {
    ...options,
    headers: { ...headers(), ...options?.headers },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(body.error || `HTTP ${res.status}`);
  }

  return res.json();
}

// Health
export async function healthCheck() {
  return request<{ status: string; version: string }>('/health');
}

// Models
export async function getModels() {
  return request<{ models: Model[] }>('/api/models');
}

// Chat
export async function sendMessage(params: {
  message: string;
  conversationId?: string;
  model?: string;
  systemPrompt?: string;
}) {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// Streaming chat
export function streamMessage(
  params: {
    message: string;
    conversationId?: string;
    model?: string;
    systemPrompt?: string;
  },
  onEvent: (event: StreamEvent) => void,
): AbortController {
  const controller = new AbortController();

  fetch(`${serverUrl}/api/chat/stream`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(params),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onEvent({ type: 'error', error: `HTTP ${res.status}` });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event: StreamEvent = JSON.parse(line.slice(6));
              onEvent(event);
            } catch {
              // skip malformed events
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', error: err.message });
      }
    });

  return controller;
}

// Conversations
export async function getConversations(limit = 50, offset = 0) {
  return request<{ conversations: Conversation[] }>(
    `/api/conversations?limit=${limit}&offset=${offset}`
  );
}

export async function getConversation(id: string) {
  return request<{ conversation: Conversation; messages: any[] }>(
    `/api/conversations/${id}`
  );
}

export async function deleteConversation(id: string) {
  return request<{ deleted: boolean }>(`/api/conversations/${id}`, {
    method: 'DELETE',
  });
}

// Quick Actions
export async function quickAction(action: string, message: string, model?: string) {
  return request<ChatResponse>(`/api/quick/${action}`, {
    method: 'POST',
    body: JSON.stringify({ message, model }),
  });
}

// Presets
export async function getPresets() {
  return request<{ presets: Preset[] }>('/api/presets');
}

export async function createPreset(preset: {
  name: string;
  description?: string;
  systemPrompt: string;
  category?: string;
}) {
  return request<Preset>('/api/presets', {
    method: 'POST',
    body: JSON.stringify(preset),
  });
}

// Usage
export async function getUsage(days = 30) {
  return request<UsageStats>(`/api/usage?days=${days}`);
}

// Community registration
export async function registerByok(apiKey: string) {
  return request<{ accessToken: string; message: string }>(
    '/api/community/register',
    {
      method: 'POST',
      body: JSON.stringify({ apiKey }),
    }
  );
}
