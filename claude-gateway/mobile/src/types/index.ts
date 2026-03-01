export interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  createdAt: string;
}

export interface Conversation {
  id: string;
  title: string;
  model: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface Model {
  id: string;
  name: string;
  tier: 'opus' | 'sonnet' | 'haiku';
  description: string;
}

export interface Preset {
  id: string;
  name: string;
  description: string;
  category: string;
  systemPrompt: string;
  source: 'built-in' | 'custom';
}

export interface UsageStats {
  period: string;
  totals: {
    requests: number;
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
  byModel: Array<{
    model: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
  }>;
  daily: Array<{
    date: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
  }>;
  conversations: number;
}

export interface ChatResponse {
  conversationId: string;
  message: string;
  model: string;
  usage: {
    input_tokens: number;
    output_tokens: number;
  };
  stopReason: string;
}

export interface StreamEvent {
  type: 'start' | 'text' | 'done' | 'error';
  conversationId?: string;
  text?: string;
  model?: string;
  usage?: { input_tokens: number; output_tokens: number };
  stopReason?: string;
  error?: string;
}
