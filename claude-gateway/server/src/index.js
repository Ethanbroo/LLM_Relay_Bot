require('dotenv').config();

const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const { initializeDatabase } = require('./db/init');
const { authMiddleware } = require('./middleware/auth');
const { rateLimiterMiddleware } = require('./middleware/rateLimiter');

// Initialize database on startup
initializeDatabase();

// Ensure personal user exists
const { getDb } = require('./db/init');
const db = getDb();
const personalUser = db.prepare('SELECT id FROM users WHERE id = ?').get('personal');
if (!personalUser) {
  db.prepare('INSERT INTO users (id, mode) VALUES (?, ?)').run('personal', 'personal');
  console.log('Personal user created');
}
db.close();

const app = express();
const PORT = process.env.PORT || 3000;

// Global middleware
app.use(helmet());
app.use(cors());
app.use(express.json({ limit: '1mb' }));

// Health check (no auth)
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    version: '1.0.0',
    modes: ['personal', 'byok'],
  });
});

// Public routes (no auth)
const modelsRouter = require('./routes/models');
const communityRouter = require('./routes/community');
app.use('/api/models', modelsRouter);
app.use('/api/community', communityRouter);

// Protected routes (auth + rate limiting)
const chatRouter = require('./routes/chat');
const conversationsRouter = require('./routes/conversations');
const quickActionsRouter = require('./routes/quickActions');
const presetsRouter = require('./routes/presets');
const usageRouter = require('./routes/usage');

app.use('/api/chat', authMiddleware, rateLimiterMiddleware, chatRouter);
app.use('/api/conversations', authMiddleware, conversationsRouter);
app.use('/api/quick', authMiddleware, rateLimiterMiddleware, quickActionsRouter);
app.use('/api/presets', authMiddleware, presetsRouter);
app.use('/api/usage', authMiddleware, usageRouter);

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: 'Not found' });
});

// Error handler
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({ error: 'Internal server error' });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`\n  Claude Gateway server running on port ${PORT}`);
  console.log(`  LAN access: http://192.168.4.138:${PORT}`);
  console.log(`  Health check: http://localhost:${PORT}/health`);
  console.log(`  Mode: ${process.env.NODE_ENV || 'development'}\n`);
});

module.exports = app;
