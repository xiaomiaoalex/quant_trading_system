import { describe, expect, it } from 'vitest';
import { formatKeyValueMap, parseKeyValueMapInput, parseSymbolListInput } from '@/utils';

describe('crypto risk budget utils', () => {
  it('formats maps in stable key order', () => {
    expect(formatKeyValueMap({ ETHUSDT: '5000', BTCUSDT: '10000' })).toBe(
      'BTCUSDT=10000\nETHUSDT=5000'
    );
  });

  it('parses newline and comma separated key value pairs', () => {
    expect(parseKeyValueMapInput('btc/usdt=10000\neth-usdt=5000, SOLUSDT=2500')).toEqual({
      BTCUSDT: '10000',
      ETHUSDT: '5000',
      SOLUSDT: '2500',
    });
  });

  it('rejects malformed key value pairs', () => {
    expect(() => parseKeyValueMapInput('BTCUSDT:10000')).toThrow('Invalid map item');
  });

  it('normalizes and deduplicates symbol lists', () => {
    expect(parseSymbolListInput('btc/usdt, BTCUSDT eth-usdt')).toEqual(['BTCUSDT', 'ETHUSDT']);
  });
});
