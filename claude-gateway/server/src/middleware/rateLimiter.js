const { RateLimiterMemory } = require('rate-limiter-flexible');

const rateLimiter = new RateLimiterMemory({
  points: parseInt(process.env.RATE_LIMIT_POINTS) || 30,
  duration: parseInt(process.env.RATE_LIMIT_DURATION) || 60,
});

async function rateLimiterMiddleware(req, res, next) {
  const key = req.user ? req.user.id : req.ip;
  try {
    await rateLimiter.consume(key);
    next();
  } catch (rejRes) {
    const retryAfter = Math.ceil(rejRes.msBeforeNext / 1000);
    res.set('Retry-After', String(retryAfter));
    res.status(429).json({
      error: 'Too many requests',
      retryAfter,
    });
  }
}

module.exports = { rateLimiterMiddleware };
