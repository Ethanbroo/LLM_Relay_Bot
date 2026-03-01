const express = require('express');
const { v4: uuidv4 } = require('uuid');
const { getDb } = require('../db/init');
const { getBuiltInPresets } = require('../services/presets');

const router = express.Router();

// GET /api/presets — List all presets (built-in + custom)
router.get('/', (req, res) => {
  const builtIn = getBuiltInPresets().map((p) => ({ ...p, source: 'built-in' }));

  const db = getDb();
  try {
    const custom = db.prepare(
      'SELECT id, name, description, system_prompt, category, created_at FROM presets WHERE user_id = ? ORDER BY created_at DESC'
    ).all(req.user.id).map((p) => ({ ...p, systemPrompt: p.system_prompt, source: 'custom' }));

    res.json({ presets: [...builtIn, ...custom] });
  } finally {
    db.close();
  }
});

// POST /api/presets — Create a custom preset
router.post('/', (req, res) => {
  const { name, description, systemPrompt, category } = req.body;

  if (!name || !systemPrompt) {
    return res.status(400).json({ error: 'name and systemPrompt are required' });
  }

  const id = uuidv4();
  const db = getDb();
  try {
    db.prepare(
      'INSERT INTO presets (id, user_id, name, description, system_prompt, category) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(id, req.user.id, name, description || '', systemPrompt, category || 'general');

    res.status(201).json({ id, name, description, systemPrompt, category: category || 'general' });
  } finally {
    db.close();
  }
});

module.exports = router;
