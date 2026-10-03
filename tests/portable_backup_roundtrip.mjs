import { readBackup, writeBackup } from '../local-first/backup.mjs';
import * as vendor from '../local-first/vendor/lossless-json-4.3.1/lossless-json.js';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = Buffer.concat(chunks);
if (process.argv.includes('--validate-cases')) {
  const { parse, stringify } = vendor.default;
  const cases = parse(input.toString('utf8')); // Outer test envelope contains only base64 strings.
  process.stdout.write(stringify(cases.map(payload => {
    try { readBackup(Buffer.from(payload, 'base64')); return true; } catch { return false; }
  })));
} else process.stdout.write(writeBackup(readBackup(input)));
