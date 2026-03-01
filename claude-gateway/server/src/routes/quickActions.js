const express = require('express');
const { v4: uuidv4 } = require('uuid');
const { getDb } = require('../db/init');
const { sendMessage, streamMessage } = require('../services/anthropic');
const { getPresetById } = require('../services/presets');

const router = express.Router();

// POST /api/quick/:action — Execute a quick action with preset system prompt
router.post('/:action', async (req, res) => {
  const { action } = req.params;
  const { message, model, maxTokens, stream } = req.body;

  if (!message || typeof message !== 'string') {
    return res.status(400).json({ error: 'message is required' });
  }

  const preset = getPresetById(action);
  if (!preset) {
    return res.status(404).json({ error: `Unknown quick action: ${action}` });
  }

  const db = getDb();
  try {
    // Create a new conversation for this quick action
    const convId = uuidv4();
    const title = `[${preset.name}] ${message.slice(0, 80)}`;
    db.prepare(
      'INSERT INTO conversations (id, user_id, title, model, system_prompt) VALUES (?, ?, ?, ?, ?)'
    ).run(convId, req.user.id, title, model || process.env.DEFAULT_MODEL, preset.systemPrompt);

    db.prepare(
      'INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)'
    ).run(convId, 'user', message);

    const messages = [{ role: 'user', content: message }];

    if (stream) {
      // SSE streaming response
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');
      res.setHeader('X-Accel-Buffering', 'no');
      res.flushHeaders();

      res.write(`data: ${JSON.stringify({ type: 'start', conversationId: convId, preset: preset.name })}\n\n`);

      await streamMessage({
        user: req.user,
        model,
        systemPrompt: preset.systemPrompt,
        messages,
        maxTokens,
        onText: (text) => {
          res.write(`data: ${JSON.stringify({ type: 'text', text })}\n\n`);
        },
        onDone: (final) => {
          db.prepare(
            'INSERT INTO messages (conversation_id, role, content, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)'
          ).run(convId, 'assistant', final.content, final.model, final.usage.input_tokens, final.usage.output_tokens);

          db.prepare('UPDATE conversations SET updated_at = datetime(\'now\') WHERE id = ?').run(convId);

          res.write(`data: ${JSON.stringify({ type: 'done', model: final.model, usage: final.usage })}\n\n`);
        },
      });

      res.end();
    } else {
      // Regular JSON response
      const result = await sendMessage({
        user: req.user,
        conversationId: convId,
        model,
        systemPrompt: preset.systemPrompt,
        messages,
        maxTokens,
      });

      db.prepare(
        'INSERT INTO messages (conversation_id, role, content, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?)'
      ).run(convId, 'assistant', result.content, result.model, result.usage.input_tokens, result.usage.output_tokens);

      db.prepare('UPDATE conversations SET updated_at = datetime(\'now\') WHERE id = ?').run(convId);

      res.json({
        conversationId: convId,
        preset: preset.name,
        message: result.content,
        model: result.model,
        usage: result.usage,
      });
    }
  } catch (err) {
    console.error('Quick action error:', err.message);
    if (!res.headersSent) {
      res.status(500).json({ error: 'Failed to execute quick action' });
    }
  } finally {
    db.close();
  }
});

module.exports = router;
