// Explicitly generate the existing fictitious UI sample for local integration demos.
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createDemoData } from '../src/data/behavior.js';
import { toCSV } from '../src/data/csv.js';
const target = new URL(
  '../../backend/instance/inbox/demo.csv',
  import.meta.url,
);
mkdirSync(new URL('.', target), { recursive: true });
writeFileSync(
  target,
  toCSV(createDemoData(), [
    { label: 'user_id', value: (r) => r.user },
    { label: 'item_id', value: (r) => r.item },
    { label: 'category_id', value: (r) => r.category },
    { label: 'behavior_type', value: (r) => r.type },
    { label: 'timestamp', value: (r) => r.t },
  ]),
);
console.log('Fictitious demo data generated:', fileURLToPath(target));
