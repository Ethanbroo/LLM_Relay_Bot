const express = require('express');
const { AVAILABLE_MODELS } = require('../services/anthropic');

const router = express.Router();

// GET /api/models — List available Claude models
router.get('/', (req, res) => {
  res.json({ models: AVAILABLE_MODELS });
});

module.exports = router;
