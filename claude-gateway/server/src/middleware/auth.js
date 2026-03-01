const { getDb } = require('../db/init');

function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Missing or invalid Authorization header' });
  }

  const token = authHeader.slice(7);

  // Check personal mode
  if (token === process.env.PERSONAL_ACCESS_TOKEN) {
    req.user = { id: 'personal', mode: 'personal' };
    return next();
  }

  // Check BYOK mode — token is the user's UUID
  const db = getDb();
  try {
    const user = db.prepare('SELECT id, mode FROM users WHERE id = ?').get(token);
    if (!user) {
      return res.status(401).json({ error: 'Invalid access token' });
    }

    // Update last_active
    db.prepare('UPDATE users SET last_active = datetime(\'now\') WHERE id = ?').run(token);

    req.user = user;
    next();
  } finally {
    db.close();
  }
}

// Optional auth — allows unauthenticated but attaches user if present
function optionalAuth(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader) {
    req.user = null;
    return next();
  }
  return authMiddleware(req, res, next);
}

module.exports = { authMiddleware, optionalAuth };
