const express = require('express');
const { getDb } = require('../db/init');

const router = express.Router();

// GET /api/usage — Usage statistics & token counts
router.get('/', (req, res) => {
  const days = Math.min(parseInt(req.query.days) || 30, 90);

  const db = getDb();
  try {
    // Total usage
    const totals = db.prepare(`
      SELECT
        COUNT(*) AS total_requests,
        COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
        COALESCE(SUM(output_tokens), 0) AS total_output_tokens
      FROM usage_log
      WHERE user_id = ?
        AND created_at >= datetime('now', ?)
    `).get(req.user.id, `-${days} days`);

    // Usage by model
    const byModel = db.prepare(`
      SELECT
        model,
        COUNT(*) AS requests,
        COALESCE(SUM(input_tokens), 0) AS input_tokens,
        COALESCE(SUM(output_tokens), 0) AS output_tokens
      FROM usage_log
      WHERE user_id = ?
        AND created_at >= datetime('now', ?)
      GROUP BY model
      ORDER BY requests DESC
    `).all(req.user.id, `-${days} days`);

    // Daily usage (last 7 days)
    const daily = db.prepare(`
      SELECT
        date(created_at) AS date,
        COUNT(*) AS requests,
        COALESCE(SUM(input_tokens), 0) AS input_tokens,
        COALESCE(SUM(output_tokens), 0) AS output_tokens
      FROM usage_log
      WHERE user_id = ?
        AND created_at >= datetime('now', '-7 days')
      GROUP BY date(created_at)
      ORDER BY date DESC
    `).all(req.user.id);

    // Conversation count
    const convCount = db.prepare(
      'SELECT COUNT(*) AS count FROM conversations WHERE user_id = ?'
    ).get(req.user.id);

    res.json({
      period: `${days} days`,
      totals: {
        requests: totals.total_requests,
        inputTokens: totals.total_input_tokens,
        outputTokens: totals.total_output_tokens,
        totalTokens: totals.total_input_tokens + totals.total_output_tokens,
      },
      byModel,
      daily,
      conversations: convCount.count,
    });
  } finally {
    db.close();
  }
});

module.exports = router;
