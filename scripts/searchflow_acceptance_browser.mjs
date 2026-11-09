// Invoked by searchflow_acceptance.py inside the official ego-browser runtime.
const fs = await import('node:fs/promises');
const os = await import('node:os');
const keyDirectory = command.keyDirectory || os.homedir() + '/.config/searchflow';
const task = await taskSpace(command.space);
const page = task.page('p1');
const results = [];
for (const request of command.requests) {
  const headers = {};
  if (request.auth) {
    const filename = request.auth === 'operator' ? 'operator-key' : 'customer-key';
    headers.Authorization = 'Bearer ' + (await fs.readFile(keyDirectory + '/' + filename, 'utf8')).trim();
  }
  if (request.payload !== undefined) headers['Content-Type'] = 'application/json';
  if (request.idempotency) headers['Idempotency-Key'] = request.idempotency;
  const start = Date.now();
  const response = await page.fetch(request.path, {
    method: request.method || 'GET', headers,
    ...(request.payload !== undefined ? { body: JSON.stringify(request.payload) } : {}), timeout: 25000,
  });
  let body;
  try { body = JSON.parse(response.body); } catch { body = response.body; }
  results.push({ path: request.path, status: response.status, ms: Date.now() - start, body });
  // Persist after each response so later failures cannot erase confirmed writes.
  await fs.writeFile(command.destination, JSON.stringify({ at: new Date().toISOString(), results }, null, 2));
}
console.log(results.map(r => ({ path: r.path, status: r.status, ms: r.ms })));
