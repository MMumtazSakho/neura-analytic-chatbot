// Contract fixture, not a semantic model. Query strings select failure scenarios.
const http = require('http');
http.createServer(async (req, res) => {
  const reply = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  };
  if (req.method !== 'POST' || req.url !== '/api/v1/rerank') {
    return reply(501, { error: 'Endpoint not modeled' });
  }
  let body = req.body;
  if (!body) {
    let raw = '';
    for await (const part of req) raw += part;
    try { body = JSON.parse(raw); } catch { return reply(400, { error: 'Invalid JSON' }); }
  }
  if (body.model !== 'qwen/qwen3-reranker-8b' || typeof body.query !== 'string' ||
      !Array.isArray(body.documents) || !body.documents.every(d => typeof d === 'string') ||
      !Number.isInteger(body.top_n) || body.top_n < 1 || body.top_n > body.documents.length) {
    return reply(400, { error: 'Invalid rerank request' });
  }
  if (body.query === 'test-429') return reply(429, { error: 'Rate limited' });
  if (body.query === 'test-503') return reply(503, { error: 'Unavailable' });
  if (body.query === 'test-timeout') await new Promise(resolve => setTimeout(resolve, 500));
  const results = body.documents.map((_, i) => ({
    index: body.documents.length - i - 1, relevance_score: 1 - i / body.documents.length,
  })).slice(0, body.top_n);
  reply(200, { model: body.model, results });
}).listen(process.env.PORT);
