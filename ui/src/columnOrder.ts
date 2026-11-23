export function mergeColumnOrder(defaults: readonly string[], saved: readonly string[] = []): string[] {
  const available = new Set(defaults);
  return [...new Set([...saved.filter(column => available.has(column)), ...defaults])];
}

export function moveColumn(order: string[], visible: string[], from: string, to: string): string[] {
  const start = visible.indexOf(from);
  const end = visible.indexOf(to);
  if (start < 0 || end < 0 || start === end) return order;
  const next = [...visible];
  next.splice(start, 1);
  next.splice(end, 0, from);
  const shown = new Set(visible);
  let index = 0;
  return order.map(column => shown.has(column) ? next[index++] : column);
}
