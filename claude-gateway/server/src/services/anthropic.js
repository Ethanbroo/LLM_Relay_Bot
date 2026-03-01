const Anthropic = require('@anthropic-ai/sdk');
const { getDb } = require('../db/init');
const { decrypt } = require('../utils/encryption');

const AVAILABLE_MODELS = [
  { id: 'claude-opus-4-6', name: 'Claude Opus 4.6', tier: 'opus', description: 'Most capable — complex analysis, nuanced writing' },
  { id: 'claude-sonnet-4-5-20250514', name: 'Claude Sonnet 4.5', tier: 'sonnet', description: 'Balanced — fast and capable for daily use' },
  { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5', tier: 'haiku', description: 'Fastest — quick tasks, simple questions' },
];

function getApiKey(user) {
  if (user.mode === 'personal') {
    return process.env.ANTHROPIC_API_KEY;
  }

  // BYOK — decrypt the user's stored key
  const db = getDb();
  try {
    const row = db.prepare(
      'SELECT encrypted_api_key, iv, auth_tag FROM users WHERE id = ?'
    ).get(user.id);

    if (!row || !row.encrypted_api_key) {
      throw new Error('No API key found for user');
    }

    return decrypt(row.encrypted_api_key, row.iv, row.auth_tag);
  } finally {
    db.close();
  }
}

function getClient(user) {
  const apiKey = getApiKey(user);
  return new Anthropic({ apiKey });
}

async function sendMessage({ user, conversationId, model, systemPrompt, messages, maxTokens = 4096 }) {
  const client = getClient(user);
  const resolvedModel = model || process.env.DEFAULT_MODEL || 'claude-sonnet-4-5-20250514';

  const params = {
    model: resolvedModel,
    max_tokens: maxTokens,
    messages,
  };

  if (systemPrompt) {
    params.system = systemPrompt;
  }

  const response = await client.messages.create(params);

  // Log usage
  const db = getDb();
  try {
    db.prepare(
      'INSERT INTO usage_log (user_id, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?)'
    ).run(user.id, resolvedModel, response.usage.input_tokens, response.usage.output_tokens);
  } finally {
    db.close();
  }

  return {
    content: response.content[0].text,
    model: response.model,
    usage: response.usage,
    stopReason: response.stop_reason,
  };
}

async function streamMessage({ user, model, systemPrompt, messages, maxTokens = 4096, onText, onDone }) {
  const client = getClient(user);
  const resolvedModel = model || process.env.DEFAULT_MODEL || 'claude-sonnet-4-5-20250514';

  const params = {
    model: resolvedModel,
    max_tokens: maxTokens,
    messages,
    stream: true,
  };

  if (systemPrompt) {
    params.system = systemPrompt;
  }

  const stream = client.messages.stream(params);

  let fullText = '';
  let usage = { input_tokens: 0, output_tokens: 0 };

  stream.on('text', (text) => {
    fullText += text;
    if (onText) onText(text);
  });

  const finalMessage = await stream.finalMessage();
  usage = finalMessage.usage;

  // Log usage
  const db = getDb();
  try {
    db.prepare(
      'INSERT INTO usage_log (user_id, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?)'
    ).run(user.id, resolvedModel, usage.input_tokens, usage.output_tokens);
  } finally {
    db.close();
  }

  if (onDone) {
    onDone({
      content: fullText,
      model: finalMessage.model,
      usage,
      stopReason: finalMessage.stop_reason,
    });
  }

  return { content: fullText, model: finalMessage.model, usage, stopReason: finalMessage.stop_reason };
}

module.exports = { AVAILABLE_MODELS, sendMessage, streamMessage, getClient };
