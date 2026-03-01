const express = require('express');
const { getDb } = require('../db/init');

const router = express.Router();

// GET /api/conversations — List recent conversations
router.get('/', (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 50, 100);
  const offset = parseInt(req.query.offset) || 0;

  const db = getDb();
  try {
    const conversations = db.prepare(`
      SELECT c.id, c.title, c.model, c.created_at, c.updated_at,
             COUNT(m.id) AS message_count
      FROM conversations c
      LEFT JOIN messages m ON m.conversation_id = c.id
      WHERE c.user_id = ?
      GROUP BY c.id
      ORDER BY c.updated_at DESC
      LIMIT ? OFFSET ?
    `).all(req.user.id, limit, offset);

    res.json({ conversations, limit, offset });
  } finally {
    db.close();
  }
});

// GET /api/conversations/:id — Get conversation with messages
router.get('/:id', (req, res) => {
  const db = getDb();
  try {
    const conversation = db.prepare(
      'SELECT * FROM conversations WHERE id = ? AND user_id = ?'
    ).get(req.params.id, req.user.id);

    if (!conversation) {
      return res.status(404).json({ error: 'Conversation not found' });
    }

    const messages = db.prepare(
      'SELECT id, role, content, model, input_tokens, output_tokens, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at ASC'
    ).all(req.params.id);

    res.json({ conversation, messages });
  } finally {
    db.close();
  }
});

// DELETE /api/conversations/:id — Delete a conversation
router.delete('/:id', (req, res) => {
  const db = getDb();
  try {
    const conv = db.prepare(
      'SELECT id FROM conversations WHERE id = ? AND user_id = ?'
    ).get(req.params.id, req.user.id);

    if (!conv) {
      return res.status(404).json({ error: 'Conversation not found' });
    }

    db.prepare('DELETE FROM messages WHERE conversation_id = ?').run(req.params.id);
    db.prepare('DELETE FROM conversations WHERE id = ?').run(req.params.id);

    res.json({ deleted: true });
  } finally {
    db.close();
  }
});

module.exports = router;
