export function formatKeyValueMap(values: Record<string, string>): string {
  return Object.entries(values)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join('\n')
}

export function parseKeyValueMapInput(input: string): Record<string, string> {
  const values: Record<string, string> = {}
  for (const rawItem of input.split(/[\n,]+/)) {
    const item = rawItem.trim()
    if (!item) continue
    const separatorIndex = item.indexOf('=')
    if (separatorIndex <= 0) {
      throw new Error(`Invalid map item: ${item}`)
    }
    const key = item.slice(0, separatorIndex).trim().toUpperCase().replace(/[/-]/g, '')
    const value = item.slice(separatorIndex + 1).trim()
    if (!key || !value) {
      throw new Error(`Invalid map item: ${item}`)
    }
    values[key] = value
  }
  return values
}

export function parseSymbolListInput(input: string): string[] {
  const seen = new Set<string>()
  const symbols: string[] = []
  for (const rawItem of input.split(/[\s,]+/)) {
    const symbol = rawItem.trim().toUpperCase().replace(/[/-]/g, '')
    if (!symbol || seen.has(symbol)) continue
    seen.add(symbol)
    symbols.push(symbol)
  }
  return symbols
}
