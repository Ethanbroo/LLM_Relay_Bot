const express = require('express');
const { v4: uuidv4 } = require('uuid');
const { getDb } = require('../db/init');
const { sendMessage, streamMessage } = require('../services/anthropic');

const router = express.Router();

// POST /api/chat — Send message, get full response
router.post('/', async (req, res) => {
  const { message, conversationId, model, systemPrompt, maxTokens } = req.body;

  if (!message || typeof message !== 'string') {
    return res.status(400).json({ error: 'message is required and must be a string' });
  }

  const db = getDb();
  try {
    let convId = conversationId;
    let existingMessages = [];

    // Load or create conversation
    if (convId) {
      const conv = db.prepare(
        'SELECT * FROM conversations WHERE id = ? AND user_id = ?'
      ).get(convId, req.user.id);

      if (!conv) {
        return res.status(404).json({ error: 'Conversation not found' });
      }

      existingMessages = db.prepare(
        'SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC'
      ).all(convId);
    } else {
      convId = uuidv4();
      const title = message.slice(0, 100);
      db.prepare(
        'INSERT INTO conversations (id, user_id, title, model, system_prompt) VALUES (?, ?, ?, ?, ?)'
      ).run(convId, req.user.id, title, model || process.env.DEFAULT_MODEL || 'claude-sonnet-4-5-20250514', systemPrompt || null);
    }

    // Build messages array
    const messages = [
      ...existingMessages.map((m) => ({ role: m.role, content: m.content })),
      { role: 'user', content: message },
    ];

    // Store user message
    db.prepare(
      'INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)'
    ).run(convId, 'user', message);

    // Call Anthropic
    const result = await sendMessage({
      user: req.user,
      conversationId: convId,
      model,
      systemPrompt,
      messages,
      maxTokens,
    });

    // Store assistant message
    db.prepare(
      'INSERT INTO messages (conversation_id, role, content, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(convId, 'assistant', result.content, result.model, result.usage.input_tokens, result.usage.output_tokens);

    // Update conversation timestamp
    db.prepare(
      'UPDATE conversations SET updated_at = datetime(\'now\') WHERE id = ?'
    ).run(convId);

    res.json({
      conversationId: convId,
      message: result.content,
      model: result.model,
      usage: result.usage,
      stopReason: result.stopReason,
    });
  } catch (err) {
    console.error('Chat error:', err.message);
    if (err.status === 401) {
      return res.status(401).json({ error: 'Invalid API key' });
    }
    if (err.status === 429) {
      return res.status(429).json({ error: 'Anthropic rate limit exceeded. Try again shortly.' });
    }
    res.status(500).json({ error: 'Failed to get response from Claude' });
  } finally {
    db.close();
  }
});

// POST /api/chat/stream — Send message, get SSE stream
router.post('/stream', async (req, res) => {
  const { message, conversationId, model, systemPrompt, maxTokens } = req.body;

  if (!message || typeof message !== 'string') {
    return res.status(400).json({ error: 'message is required and must be a string' });
  }

  // Set up SSE
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const db = getDb();
  try {
    let convId = conversationId;
    let existingMessages = [];

    if (convId) {
      const conv = db.prepare(
        'SELECT * FROM conversations WHERE id = ? AND user_id = ?'
      ).get(convId, req.user.id);

      if (!conv) {
        res.write(`data: ${JSON.stringify({ type: 'error', error: 'Conversation not found' })}\n\n`);
        return res.end();
      }

      existingMessages = db.prepare(
        'SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC'
      ).all(convId);
    } else {
      convId = uuidv4();
      const title = message.slice(0, 100);
      db.prepare(
        'INSERT INTO conversations (id, user_id, title, model, system_prompt) VALUES (?, ?, ?, ?, ?)'
      ).run(convId, req.user.id, title, model || process.env.DEFAULT_MODEL || 'claude-sonnet-4-5-20250514', systemPrompt || null);
    }

    const messages = [
      ...existingMessages.map((m) => ({ role: m.role, content: m.content })),
      { role: 'user', content: message },
    ];

    // Store user message
    db.prepare(
      'INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)'
    ).run(convId, 'user', message);

    // Send conversation ID immediately
    res.write(`data: ${JSON.stringify({ type: 'start', conversationId: convId })}\n\n`);

    // Stream response
    const result = await streamMessage({
      user: req.user,
      model,
      systemPrompt,
      messages,
      maxTokens,
      onText: (text) => {
        res.write(`data: ${JSON.stringify({ type: 'text', text })}\n\n`);
      },
      onDone: (final) => {
        // Store assistant message
        db.prepare(
          'INSERT INTO messages (conversation_id, role, content, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)'
        ).run(convId, 'assistant', final.content, final.model, final.usage.input_tokens, final.usage.output_tokens);

        db.prepare(
          'UPDATE conversations SET updated_at = datetime(\'now\') WHERE id = ?'
        ).run(convId);

        res.write(`data: ${JSON.stringify({ type: 'done', model: final.model, usage: final.usage, stopReason: final.stopReason })}\n\n`);
      },
    });

    res.end();
  } catch (err) {
    console.error('Stream error:', err.message);
    res.write(`data: ${JSON.stringify({ type: 'error', error: err.message })}\n\n`);
    res.end();
  } finally {
    db.close();
  }
});

module.exports = router;
