const express = require('express');
const { v4: uuidv4 } = require('uuid');
const Anthropic = require('@anthropic-ai/sdk');
const { getDb } = require('../db/init');
const { encrypt } = require('../utils/encryption');

const router = express.Router();

// POST /api/community/register — Register a BYOK user with their Anthropic API key
router.post('/register', async (req, res) => {
  const { apiKey } = req.body;

  if (!apiKey || typeof apiKey !== 'string') {
    return res.status(400).json({ error: 'apiKey is required' });
  }

  if (!apiKey.startsWith('sk-ant-')) {
    return res.status(400).json({ error: 'Invalid API key format. Anthropic keys start with sk-ant-' });
  }

  // Validate the key by making a test call
  try {
    const client = new Anthropic({ apiKey });
    await client.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 10,
      messages: [{ role: 'user', content: 'Hi' }],
    });
  } catch (err) {
    if (err.status === 401) {
      return res.status(400).json({ error: 'API key is invalid or expired. Please check your key at console.anthropic.com' });
    }
    if (err.status === 403) {
      return res.status(400).json({ error: 'API key does not have permission. Check your Anthropic account settings.' });
    }
    // Other errors (rate limit, etc.) mean the key is probably valid
    if (err.status !== 429) {
      return res.status(400).json({ error: `Failed to validate API key: ${err.message}` });
    }
  }

  // Encrypt and store the key
  const userId = uuidv4();
  const { encrypted, iv, authTag } = encrypt(apiKey);

  const db = getDb();
  try {
    db.prepare(
      'INSERT INTO users (id, mode, encrypted_api_key, iv, auth_tag) VALUES (?, ?, ?, ?, ?)'
    ).run(userId, 'byok', encrypted, iv, authTag);

    res.status(201).json({
      accessToken: userId,
      message: 'Registration successful. Use this access token as your Bearer token for all API requests. Store it safely — it cannot be recovered.',
    });
  } finally {
    db.close();
  }
});

module.exports = router;
