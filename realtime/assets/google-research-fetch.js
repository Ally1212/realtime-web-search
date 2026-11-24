// Analysis and history share the same tenant quota.
let googleResearchReadTail = Promise.resolve();
let googleResearchLastStart = -Infinity;

function googleResearchRead(url, options = {}) {
  if (options.method && options.method.toUpperCase() !== 'GET') return fetch(url, options);
  const {signal} = options;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const request = googleResearchReadTail.then(async () => {
    for (let attempt = 0; ; attempt++) {
      signal?.throwIfAborted();
      const spacing = 260 - (performance.now() - googleResearchLastStart);
      if (spacing > 0) await wait(spacing);
      signal?.throwIfAborted();
      googleResearchLastStart = performance.now();
      const response = await fetch(url, options);
      if (response.status !== 429 || attempt >= 3) return response;
      const retryAfter = response.headers.get('Retry-After');
      const seconds = retryAfter === null ? NaN : Number(retryAfter);
      const delay = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - Date.now();
      if (delay > 8000) return response;
      await response.body?.cancel();
      await wait(Math.max(1000, Number.isFinite(delay) ? delay : 0) + attempt * 350 + Math.random() * 160);
    }
  });
  googleResearchReadTail = request.catch(() => {});
  return request;
}

function googleResearchError(payload, status) {
  if (status === 429 && payload?.errors?.[0]?.code === 'quota_exceeded') {
    return '读取请求较多，请稍后重试。';
  }
  return '';
}
